"""The web app (webapp/build.py): the bundle plus manifest, icons and service worker."""

import importlib.util
import json
import os
import re
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("webapp_build", ROOT / "webapp" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cdn_build_has_the_pwa_files(tmp_path):
    mod = _load()
    out = mod.build(tmp_path / "dist", cdn=True)
    index = (out / "index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="manifest.webmanifest">' in index and 'serviceWorker.register("sw.js")' in index
    assert '<meta name="theme-color"' in index and "viewport-fit=cover" in index
    assert '"sessions": true' in index and '"rememberZoom": true' in index    # the same options as the app
    manifest = json.loads((out / "manifest.webmanifest").read_text())
    assert manifest["display"] == "standalone" and manifest["start_url"] == "./index.html"
    for icon in manifest["icons"]:
        assert (out / icon["src"]).exists(), icon
    png = (out / "icon-512.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000
    sw = (out / "sw.js").read_text()
    files = json.loads(re.search(r"var FILES = (\[.*?\]);", sw).group(1))
    assert "./index.html" in files and "./manifest.webmanifest" in files and "./sw.js" not in files
    assert re.search(r'var CACHE = "sympy-editor-[0-9a-f]{12}"', sw)
    # a rebuilt, identical bundle keeps its cache name; a different page changes it
    assert mod.build(tmp_path / "dist2", cdn=True) and (tmp_path / "dist2" / "sw.js").read_text() == sw


def test_service_worker_installs_and_caches(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    mod = _load()
    out = mod.build(tmp_path / "dist", cdn=True)
    import http.server, functools
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{httpd.server_address[1]}/index.html")
            page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
            page.wait_for_function("navigator.serviceWorker.ready.then(() => true)", timeout=30000)
            keys = page.evaluate("caches.keys()")
            assert any(k.startswith("sympy-editor-") for k in keys), keys
            cached = page.evaluate("caches.keys().then(ks => caches.open(ks.find(k => k.startsWith('sympy-editor-'))).then(c => c.keys())).then(rs => rs.map(r => r.url))")
            assert any(u.endswith("/index.html") for u in cached) and any(u.endswith("/manifest.webmanifest") for u in cached)
            assert page.evaluate("fetch('manifest.webmanifest').then(r => r.json()).then(m => m.name)") == "SymPy editor"
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()

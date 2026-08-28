"""The shared mobile web bundle (mobile/build_www.py)."""

import http.server
import importlib.util
import os
import socketserver
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_www", ROOT / "mobile" / "build_www.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cdn_bundle(tmp_path):
    mod = _load_builder()
    out = mod.build(tmp_path / "www", cdn=True)
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "SympyEditor.mount" in page and "https://cdn.jsdelivr.net" in page
    assert "Integral" in page and not (out / "vendor").exists()


@pytest.mark.skipif(not os.environ.get("SYMPY_EDITOR_SLOW_TESTS"), reason="set SYMPY_EDITOR_SLOW_TESTS=1")
def test_vendored_bundle_is_self_contained(tmp_path):
    """Build the offline bundle and edit in it with every non-local request blocked."""
    playwright = pytest.importorskip("playwright.sync_api")
    mod = _load_builder()
    out = mod.build(tmp_path / "www")
    for name in ("vendor/katex/katex.min.js", "vendor/pyodide/pyodide.asm.wasm", "vendor/pyodide/python_stdlib.zip", "vendor/NOTICE.txt"):
        assert (out / name).exists(), name
    assert any(p.name.startswith("sympy-") for p in (out / "vendor" / "pyodide").iterdir())

    handler = type("H", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None})
    handler.extensions_map.update({".wasm": "application/wasm", ".whl": "application/zip", ".mjs": "text/javascript"})
    srv = socketserver.TCPServer(("127.0.0.1", 0), lambda *a, **k: handler(*a, directory=str(out), **k))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    external = []
    try:
        with playwright.sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("**/*", lambda route: (external.append(route.request.url), route.abort())
                       if "127.0.0.1" not in route.request.url else route.continue_())
            page.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html")
            page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
            page.wait_for_function("document.querySelector('.se-loading').hidden", timeout=240000)
            assert page.evaluate("document.fonts.check('12px KaTeX_Main')")
            page.locator('[data-path="/"]').click(force=True)   # selects the glyph under the centre
            page.keyboard.press("Escape")                        # clear it...
            page.keyboard.press("ArrowDown")                     # ...and select the whole expression
            assert page.locator(".se-status").inner_text().startswith("Add:")
            page.keyboard.press("Enter")
            page.keyboard.press("Control+a")
            page.keyboard.type("x**2 + 1")
            page.keyboard.press("Enter")
            page.wait_for_function("document.querySelector('.se-source').textContent === 'x**2 + 1'", timeout=240000)
            assert page.locator(".se-error").is_hidden()
            assert errors == []
            b.close()
    finally:
        srv.shutdown()
        srv.server_close()
    assert external == [], f"the bundle reached out to {external}"


def test_native_project_files_are_well_formed():
    import xml.dom.minidom
    xml.dom.minidom.parse(str(ROOT / "mobile" / "android" / "app" / "src" / "main" / "AndroidManifest.xml"))
    xml.dom.minidom.parse(str(ROOT / "mobile" / "ios" / "ExportOptions.plist"))
    import yaml  # PyYAML ships with Jupyter; skip quietly without it
    yaml.safe_load((ROOT / "mobile" / "ios" / "project.yml").read_text(encoding="utf-8"))

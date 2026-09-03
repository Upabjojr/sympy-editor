"""The web app (webapp/build.py): the bundle plus manifest, icons and service worker."""

import importlib.util
import json
import os
import re
import shutil
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


def test_the_showcase_carries_the_shelf_and_one_copy_of_the_editor(tmp_path):
    """`--shelf` builds the page that introduces the project with every
    derivation embedded.  Ten separate exports would carry ten copies of the
    editor's code; this carries one, which is what keeps it a third of a
    megabyte instead of three."""
    build = _load()
    out = build.derivations_page(tmp_path / "shelf", urls=None, editor_href="editor.html")
    assert out is not None and out.name == "index.html"
    page = out.read_text(encoding="utf-8")
    assert page.count("SympyEditor.mountHistory(") >= 8          # one viewer per derivation
    assert page.count("var SympyEditor") <= 1                    # and one copy of the code
    assert 'href="editor.html"' in page                          # the editor is a click away
    assert "SymPy editor" in page and "history" in page          # the project is introduced
    assert '"hideTitle": true' in page                           # the card names it, the report need not
    assert len(page) < 900_000, len(page)


def test_the_web_app_wears_the_app_s_own_icon(tmp_path):
    """The PWA's icons are the logo the phone app wears, rendered from the
    one SVG - no second drawing of the same thing, and no PNG in the
    repository: they are made at build time."""
    build = _load()
    out = tmp_path / "icons"
    out.mkdir()
    build.write_icons(out)
    logo = (ROOT / "mobile/icon/icon.svg").read_text(encoding="utf-8")
    assert (out / "icon.svg").read_text(encoding="utf-8") == logo
    if not shutil.which("rsvg-convert"):
        pytest.skip("needs librsvg to render the PNGs")
    from PIL import Image

    for size in (192, 512):
        with Image.open(out / f"icon-{size}.png") as image:
            assert image.size == (size, size)
    manifest = build.manifest()
    assert [i["src"] for i in manifest["icons"]] == ["icon.svg", "icon-192.png", "icon-512.png"]


def test_the_shelf_s_editor_wears_the_mark_beside_its_title(tmp_path):
    """The editor the site links to is the project's own page, and shows it:
    the mark sits on the title's line, as it does in the apps.  It was the one
    page built without a logo, and the site showed a bare heading."""
    build = _load()
    out = build.shelf_site(tmp_path / "shelf", cdn=True)
    page = (out / "editor.html").read_text(encoding="utf-8")
    assert '<h1><span class="page-logo" aria-hidden="true"><svg' in page
    mark = (ROOT / "mobile/icon/icon.svg").read_text(encoding="utf-8").split("?>", 1)[-1].strip()
    assert mark in page                                          # the same drawing the launcher shows
    assert page.index(mark) < page.index("SymPy editor</h1>")     # beside the title, not after it


def test_the_shelf_carries_the_licence_and_the_privacy_statement(tmp_path):
    """A store listing and a curious visitor both ask for these pages, and
    they must say only what is true: no collection by the app, the stores'
    own collection under their policies, the CDN fetches of the editor page,
    and GitHub for anyone who writes."""
    build = _load()
    out = build.derivations_page(tmp_path / "shelf", urls=None, editor_href="editor.html")
    folder = out.parent
    licence = (folder / "license.html").read_text(encoding="utf-8")
    assert "BSD 3-Clause License" in licence and "Redistribution and use" in licence
    assert (folder / "LICENSE.txt").read_text(encoding="utf-8") == (build.ROOT / "LICENSE").read_text(encoding="utf-8")
    privacy = (folder / "privacy.html").read_text(encoding="utf-8")
    for said in ("no accounts, no cookies, no analytics", "make no network\nrequests",
                 "Google Play or the App Store", "GitHub Pages", "jsDelivr",
                 "no chat and collects no messages", "github.com/Upabjojr/sympy-editor"):
        assert said in privacy, said
    # both wear the mark and the shelf's dress
    for page in (licence, privacy):
        assert '<img src="icon.svg"' in page and "prefers-color-scheme: dark" in page
        assert '<svg viewBox="0 0 24 24"' in page                        # a drawn icon per card
    # and the shelf links to them, to the repository and to PyPI
    index = out.read_text(encoding="utf-8")
    assert "Francesco Bonazzi" in index                          # the author, named on the page
    for href in ('href="license.html"', 'href="privacy.html"',
                 'href="https://github.com/Upabjojr/sympy-editor"', 'href="https://pypi.org/project/sympy-editor/"'):
        assert href in index, href


def test_the_shelf_teaches_and_shows_the_notebook_only_when_it_can(tmp_path):
    """The page carries the code that uses the package, and the notebook
    screenshots are content that lives beside it: the section appears only
    where the images are, so a bare rebuild is never broken pictures."""
    build = _load()
    out = build.derivations_page(tmp_path / "bare", urls=None, editor_href="editor.html")
    page = out.read_text(encoding="utf-8")
    for said in ("pip install", "sympy-editor[jupyter]", "save_html", "History", "on_change"):
        assert said in page, said
    assert "jupyter-widget.png" not in page                     # no image, no section
    shot = tmp_path / "with"
    shot.mkdir()
    (shot / "jupyter-widget.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    page = build.derivations_page(shot, urls=None, editor_href="editor.html").read_text(encoding="utf-8")
    assert '<img src="jupyter-widget.png"' in page and "In the notebook" in page
    assert "jupyter-plot.png" not in page                       # only the images that are there
    heading = '<h2 class="shelf">On a phone</h2>'
    assert heading not in page                                  # and no phone section without its shots
    (shot / "android-editor.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    page = build.derivations_page(shot, urls=None, editor_href="editor.html").read_text(encoding="utf-8")
    assert heading in page and '<span class="phone"><img src="android-editor.png"' in page
    assert "android-history.png" not in page

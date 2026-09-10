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
            assert page.evaluate("fetch('manifest.webmanifest').then(r => r.json()).then(m => m.name)") == "SymPy Editor"
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
    assert "SymPy Editor" in page and "history" in page          # the project is introduced
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
    assert page.index(mark) < page.index("SymPy Editor</h1>")     # beside the title, not after it


def test_the_shelf_opens_with_an_editor_of_its_own(tmp_path):
    """The page is about an editor, so it starts with one: a live editor above
    everything else, sharing the copy of editor.js the viewers already carry,
    and the button beside the title now says which editor it opens instead.
    Python is not loaded until somebody edits something (`preload` false), so
    a visitor who only reads pays nothing for it."""
    build = _load()
    out = build.shelf_site(tmp_path / "shelf", cdn=True)
    page = (out / "index.html").read_text(encoding="utf-8")
    assert page.index("</header>") < page.index('<h2 class="shelf">Try it</h2>') < page.index(">Use it<")
    assert '<div id="try-the-editor"></div>' in page
    mount = 'SympyEditor.mount(document.getElementById("try-the-editor"), '
    assert mount in page
    line = page.split(mount, 1)[1].splitlines()[0]              # the config is one line of JSON
    cfg = json.loads(line.removesuffix(");").replace("\\u003c", "<"))
    assert cfg["backend"] == "pyodide" and cfg["options"]["preload"] is False
    assert cfg["sources"] and cfg["srepr"]                       # it computes, and knows what to start from
    assert ">Open standalone editor</a>" in page                 # the button names the other one
    assert "Open the editor" not in page
    # one copy of the editor's code for the whole page, embedded editor included
    assert page.count(mount) == 1 and page.count("function mountHistory(") == 1
    # ...and the application's own derivations page keeps its neighbour instead
    bare = build.derivations_page(tmp_path / "bare", urls=None, editor_href="../index.html")
    assert "try-the-editor" not in bare.read_text(encoding="utf-8")


def test_the_shelf_s_editor_asks_to_be_touched_once(tmp_path):
    """The formula is the interface, and a still box does not say so: the
    editing area wears a ring that swells and glows until the first edit
    lands, and then never again (a reload asks once more).  The pulse is on a
    ring laid over the box - scaling the formula would soften the type - and
    it lets the clicks through."""
    build = _load()
    out = build.shelf_site(tmp_path / "shelf", cdn=True)
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "@keyframes se-view-notice" in page
    ring = page.split("section.try .se-stage::after {", 1)[1].split("}", 1)[0]
    for said in ("position: absolute", "inset: 0", "pointer-events: none",   # over the box, not in its way
                 "animation: se-view-notice 1.2s ease-in-out infinite"):
        assert said in ring, said
    assert "section.try .se-stage:hover::after" in page          # still under the pointer
    assert "section.try .se-edited .se-stage::after" in page     # and over, once it has been used
    reduced = (page.split("section.try .se-edited .se-stage::after", 1)[1]
               .split("@media (prefers-reduced-motion: reduce) {", 1)[1].split("}\n}", 1)[0])
    assert "animation: none" in reduced
    # The end of it: undo comes alive with the first edit and nothing else.
    # It is born enabled and turned off by the first state the editor draws,
    # so that first switch must not count - the invitation would never show.
    script = page.split('// The editor\'s box asks to be used', 1)[1].split("</script>", 1)[0]
    assert 'document.getElementById("try-the-editor")' in script   # the editor this page embeds
    assert "if (undo.disabled) { armed = true; return false; }" in script
    assert "if (!armed) return false;" in script
    assert 'host.classList.add("se-edited");' in script
    assert 'attributeFilter: ["disabled"]' in script
    # ...and no watch at all on a page with no editor to invite anybody into
    # (the keyframes come with the stylesheet either way, and match nothing there)
    bare = build.derivations_page(tmp_path / "bare", urls=None, editor_href="../index.html").read_text(encoding="utf-8")
    assert "The editor's box asks to be used" not in bare
    assert 'classList.add("se-edited")' not in bare


def test_the_shelf_s_play_buttons_ask_to_be_pressed(tmp_path):
    """A shelf of still viewers reads as pictures: the Play buttons pulse -
    larger and bluer and back, about once a second - and go on doing it, so
    the tenth card asks as plainly as the first.  A button that is playing
    holds still (it says Pause, and a Pause has nothing to ask for), and so
    does the one under the pointer or the keyboard's focus while it is
    there; a visitor who asked for less motion gets the colour without the
    movement."""
    build = _load()
    page = build.derivations_page(tmp_path / "shelf", urls=None, editor_href="editor.html").read_text(encoding="utf-8")
    assert "@keyframes se-play-notice" in page
    assert "transform: scale(1)" in page and "transform: scale(1.07)" in page
    assert (".card .se-history-head .se-play:not(:disabled):not(.se-playing)"
            " { animation: se-play-notice 1.1s ease-in-out infinite; }") in page
    for still in (".card .se-history-head .se-play:hover",           # not under the pointer
                  ".card .se-history-head .se-play:focus-visible"):  # nor under the keyboard
        assert still in page, still
    # The class the strip puts on a button that is playing, and reads back off
    # it when the slideshow ends: what the label says, said where CSS can see.
    assert 'play.classList.toggle("se-playing", !!st.playing);' in page
    # ...and nothing else ever stops it: pressing Play used to quiet the whole
    # page, which left every card below it saying nothing.
    assert "se-played" not in page
    # (the editor's own stylesheet has a reduced-motion block too: take the shelf's, below it)
    reduced = (page.split(".se-play:focus-visible", 1)[1]
               .split("@media (prefers-reduced-motion: reduce) {", 1)[1].split("}\n}", 1)[0])
    assert "animation: none" in reduced and "border-color: rgba(var(--se-accent)" in reduced
    # the colours are the editor's own variables, so the strip dresses for the dark with it
    notice = page.split("@keyframes se-play-notice {", 1)[1].split("}\n}", 1)[0]
    for token in ("var(--se-border)", "var(--se-btn)", "rgba(var(--se-accent)", "rgb(var(--se-accent))"):
        assert token in notice, token
    assert "#3b82f6" not in notice                       # no colour of its own to go stale


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
    heading = '<h2 class="shelf" id="on-a-phone">On a phone</h2>'   # the README links to this anchor
    assert heading not in page                                  # and no phone section without its shots
    (shot / "android-editor.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    page = build.derivations_page(shot, urls=None, editor_href="editor.html").read_text(encoding="utf-8")
    assert heading in page and '<span class="phone"><img src="android-editor.png"' in page
    assert "android-history.png" not in page


def _every_addons_packages():
    """Skip unless this Python has every bundled add-on's own packages.  The
    site is built where it has them (webapp.yml installs lark and
    sympy-matching), and opens with all four on; a checkout tested without
    them cannot switch those two on, so there is no claim to check."""
    pytest.importorskip("lark")
    pytest.importorskip("sympy_matching")


def test_the_site_opens_with_every_add_on_switched_on(tmp_path):
    """The site is the shop window: everything the editor can do is on when it
    opens, rather than waiting behind a menu nobody has been told about.  An
    app builds the same bundle with them merely available, and remembers what
    its owner leaves on, so the flag is the web site's alone."""
    _every_addons_packages()
    mod = _load()
    out = mod.build(tmp_path / "dist", cdn=True)
    index = (out / "index.html").read_text(encoding="utf-8")
    snapshot = json.loads(re.search(r'"snapshot":\s*(\{.*?\}),\s*"options"', index, re.S).group(1)) \
        if re.search(r'"snapshot":\s*(\{.*?\}),\s*"options"', index, re.S) else None
    on = re.search(r'"addons":\s*(\[[^\]]*\])', index)
    assert on, "the page says nothing about which add-ons are on"
    names = json.loads(on.group(1))
    assert sorted(names) == ["latex", "matching", "plot", "tree"], names
    # and every one of them is listed as available too, so they can be switched off
    available = re.findall(r'"name":\s*"([a-z]+)",\s*"label"', index)
    for name in names:
        assert name in available, (name, available)


def test_a_bundle_leaves_the_add_ons_off_unless_asked(tmp_path):
    """What the apps build: the add-ons are there to switch on, but the editor
    opens without them (and the app remembers the choice from last time)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_www", ROOT / "mobile" / "build_www.py")
    build_www = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_www)
    out = build_www.build(tmp_path / "app", cdn=True)
    index = (out / "index.html").read_text(encoding="utf-8")
    on = re.search(r'"addons":\s*(\[[^\]]*\])', index)
    assert on and json.loads(on.group(1)) == [], on.group(1) if on else "no addons key"
    assert '"name": "plot"' in index          # ... but they are all there to be switched on


def test_the_showcase_site_opens_with_the_add_ons_on(tmp_path):
    """shelf_site builds what upabjojr.github.io/sympy-editor serves: the
    front page with an editor to try, and editor.html beside it.  Both should
    open with the add-ons switched on - the site is where somebody sees what
    the editor can do - and both must name the packages the browser installs
    for them."""
    _every_addons_packages()
    mod = _load()
    out = mod.shelf_site(tmp_path / "shelf", cdn=True)
    for name in ("index.html", "editor.html"):
        page = (out / name).read_text(encoding="utf-8")
        on = re.search(r'"addons":\s*(\[[^\]]*\])', page)
        assert on, (name, "the page does not say which add-ons are on")
        assert sorted(json.loads(on.group(1))) == ["latex", "matching", "plot", "tree"], (name, on.group(1))
        # the two that need something from PyPI say so, or the browser cannot
        # install them and they would come up switched on but broken
        micropip = re.search(r'"micropip":\s*(\[[^\]]*\])', page)
        assert micropip and "lark" in micropip.group(1), (name, micropip.group(1) if micropip else None)


def test_a_missing_add_on_requirement_does_not_stop_the_build(tmp_path, monkeypatch):
    """Switching an add-on on imports it.  A machine without lark (which is
    what CI is) must still build the site - saying which add-on stayed off -
    rather than failing outright, as it did once."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_www", ROOT / "mobile" / "build_www.py")
    build_www = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_www)

    from sympy import Symbol
    real_enable = build_www.Document.enable

    def refuse(self, spec_name, *a, **k):
        if "latex" in str(spec_name):
            raise ImportError("no lark here")
        return real_enable(self, spec_name, *a, **k)

    monkeypatch.setattr(build_www.Document, "enable", refuse)
    doc = build_www.document_with_addons(Symbol("x"), enable=True)
    on = list(doc.addons)
    assert "latex" not in on, on                       # it stayed off
    assert on, "the others should still be on"
    assert any(a["name"] == "latex" for a in doc.available_addons())   # still there to switch on

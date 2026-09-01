"""The shared mobile web bundle (mobile/build_www.py)."""

import http.server
import importlib.util
import re
import shutil
import sys
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
    yaml = pytest.importorskip("yaml")   # PyYAML ships with Jupyter; skipped without it
    yaml.safe_load((ROOT / "mobile" / "ios" / "project.yml").read_text(encoding="utf-8"))


def test_the_ios_export_options_name_the_profile_and_its_certificate():
    import plistlib
    sys.path.insert(0, str(ROOT / "mobile"))
    from build import export_options
    automatic = plistlib.loads(export_options("development", "ABCDE12345"))
    assert automatic["signingStyle"] == "automatic" and automatic["teamID"] == "ABCDE12345"
    assert "provisioningProfiles" not in automatic
    manual = plistlib.loads(export_options("app-store-connect", "ABCDE12345", "SymPy editor App Store"))
    assert manual["method"] == "app-store-connect" and manual["signingStyle"] == "manual"
    assert manual["signingCertificate"] == "Apple Distribution"
    assert manual["provisioningProfiles"] == {"org.sympy.editor": "SymPy editor App Store"}
    assert plistlib.loads(export_options("development", "T", "p"))["signingCertificate"] == "Apple Development"
    with pytest.raises(SystemExit):
        export_options("enterprise", "T")


def test_the_app_plist_leads_the_ipa(tmp_path):
    """altool reads the bundle id from the first Info.plist of the archive."""
    import zipfile
    sys.path.insert(0, str(ROOT / "mobile"))
    from build import app_plist_first
    ipa = tmp_path / "x.ipa"
    with zipfile.ZipFile(ipa, "w") as z:
        z.writestr("Payload/X.app/Frameworks/_socket.framework/Info.plist", "socket")
        z.writestr("Payload/X.app/Frameworks/_socket.framework/_socket", "bits")
        z.writestr("Payload/X.app/Info.plist", "app")
        z.writestr("Payload/X.app/X", "main")
    app_plist_first(ipa)
    with zipfile.ZipFile(ipa) as z:
        names = z.namelist()
        assert names[0] == "Payload/X.app/Info.plist" and z.read(names[0]) == b"app"
        assert sorted(names) == sorted(["Payload/X.app/Frameworks/_socket.framework/Info.plist",
                                        "Payload/X.app/Frameworks/_socket.framework/_socket",
                                        "Payload/X.app/Info.plist", "Payload/X.app/X"])
        assert names[1:] == ["Payload/X.app/Frameworks/_socket.framework/Info.plist",
                             "Payload/X.app/Frameworks/_socket.framework/_socket", "Payload/X.app/X"]


def _load_app_module():
    """The module both apps run (mobile/app, staged into each by build.py)."""
    path = ROOT / "mobile" / "app" / "sympy_editor_app.py"
    spec = importlib.util.spec_from_file_location("sympy_editor_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_app_python_module_edits_documents():
    """What the apps' own CPython runs: JSON in, snapshots out.

    The app ships Python and SymPy (Chaquopy) instead of loading Pyodide in
    the WebView; MainActivity.PythonBridge calls exactly these functions.
    """
    import json

    from sympy import srepr, symbols

    app = _load_app_module()
    x, y = symbols("x y")
    snap = json.loads(app.new_doc("d1", srepr(x + y), "{}"))
    assert snap["src"] == "x + y" and snap["error"] is None
    snap = json.loads(app.handle("d1", json.dumps({"action": "replace", "path": "/0", "src": "z**2"})))
    assert snap["src"] == "y + z**2" and snap["error"] is None
    # a computation, like the app's menus ask for
    snap = json.loads(app.handle("d1", json.dumps({"action": "call", "path": "/", "func": "diff(y)"})))
    assert snap["error"] is None and snap["src"] == "1"
    # an edit that cannot work comes back as an error inside the snapshot
    snap = json.loads(app.handle("d1", json.dumps({"action": "set", "src": "x +"})))
    assert snap["error"] and "parse" in snap["error"].lower()
    # documents are independent, and unknown ones are refused
    json.loads(app.new_doc("d2", srepr(x * y), "{}"))
    assert json.loads(app.handle("d1", '{"action": "snapshot"}'))["src"] == "1"
    assert json.loads(app.handle("d2", '{"action": "snapshot"}'))["src"] == "x*y"
    app.close("d2")
    with pytest.raises(KeyError):
        app.handle("d2", '{"action": "snapshot"}')
    # settings travel as JSON: a session's history comes back with it
    state = json.dumps({"history": [srepr(x), srepr(x + 1)], "index": 1})
    snap = json.loads(app.new_doc("d3", srepr(x + 1), state))
    assert snap["src"] == "x + 1" and snap["can_undo"]
    assert json.loads(app.version())["sympy"]


def test_native_bundle_has_no_pyodide(tmp_path):
    """The Android bundle edits through the app's Python, not in the page."""
    mod = _load_builder()
    out = mod.build(tmp_path / "www", native=True)
    page = (out / "index.html").read_text(encoding="utf-8")
    assert '"backend": "native"' in page
    assert '"pyodideJs"' not in page and "vendor/pyodide" not in page   # nothing of Pyodide to load
    assert (out / "vendor" / "katex" / "katex.min.js").exists()      # KaTeX is still vendored
    assert not (out / "vendor" / "pyodide").exists()
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    assert size < 5e6, size          # ~1 MB, against ~24 MB with Pyodide


def test_the_android_app_is_configured_for_its_own_python():
    """The Gradle setup that puts CPython and SymPy in the APK."""
    root = (ROOT / "mobile" / "android" / "build.gradle.kts").read_text(encoding="utf-8")
    app = (ROOT / "mobile" / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert "com.chaquo.python" in root and "com.chaquo.python" in app
    assert "chaquopy {" in app and "install(\"sympy" in app
    assert "minSdk = 24" in app                      # what Chaquopy 16 requires
    kotlin = (ROOT / "mobile" / "android" / "app" / "src" / "main" / "java" / "org" / "sympy" / "editor"
              / "MainActivity.kt").read_text(encoding="utf-8")
    assert "SympyEditorPy" in kotlin and "__sympyEditorNative" in kotlin
    assert "AndroidPlatform" in kotlin and "Executors.newSingleThreadExecutor" in kotlin
    src = (ROOT / "src" / "sympy_editor" / "static" / "editor.js").read_text(encoding="utf-8")
    assert "native: nativeBackend" in src


def test_the_ios_app_is_configured_for_its_own_python():
    """The Xcode setup that puts CPython and SymPy in the .app.

    iOS cannot run Pyodide the way Android could have: the interpreter is
    CPython built for iOS (Python.xcframework), and the bridge to the page is
    the same protocol MainActivity speaks."""
    yaml = pytest.importorskip("yaml")
    spec = yaml.safe_load((ROOT / "mobile" / "ios" / "project.yml").read_text(encoding="utf-8"))
    target = spec["targets"]["SymPyEditor"]
    assert any(d.get("framework") == "Python.xcframework" and d.get("embed") for d in target["dependencies"])
    folders = {s["path"] for s in target["sources"] if isinstance(s, dict)}
    assert {"app", "app_packages", "../www"} <= folders     # the app's Python, SymPy, and the page
    # the standard library is installed into the bundle by the script the
    # support package ships, and every extension module made into a framework
    script = "\n".join(s["script"] for s in target["postBuildScripts"])
    assert "install_python Python.xcframework app app_packages" in script

    swift = (ROOT / "mobile" / "ios" / "SymPyEditor" / "EditorView.swift").read_text(encoding="utf-8")
    assert "SympyEditorPy" in swift and "__sympyEditorNative" in swift
    assert "DispatchQueue(label:" in swift              # Python on one thread of its own
    objc = (ROOT / "mobile" / "ios" / "SymPyEditor" / "PythonRuntime.m").read_text(encoding="utf-8")
    assert "Py_InitializeFromConfig" in objc and "PyGILState_Ensure" in objc
    assert "sympy_editor_app" in objc

    build = (ROOT / "mobile" / "build.py").read_text(encoding="utf-8")
    assert "PYTHON_APPLE_SUPPORT" in build              # the interpreter is pinned, and downloaded
    assert "build_www(cdn, native=True)" in build       # so the page never asks for Pyodide


def test_both_bridges_offer_what_the_page_calls():
    """Both apps inject `window.SympyEditorPy`; every method the page calls
    has to exist on each side and reach a function of sympy_editor_app.

    (Each bridge offers `version` too, which the page keeps in reserve for an
    about box - hence a subset, not an equality.)"""
    src = (ROOT / "src" / "sympy_editor" / "static" / "editor.js").read_text(encoding="utf-8")
    called = set(re.findall(r'call\("(\w+)"', src))
    assert called == {"newDoc", "handle"}, called
    for bridge in ("mobile/ios/SymPyEditor/EditorView.swift",
                   "mobile/android/app/src/main/java/org/sympy/editor/MainActivity.kt"):
        text = (ROOT / bridge).read_text(encoding="utf-8")
        for method in called:
            assert method in text, (bridge, method)
    mod = _load_app_module()
    for function in ("new_doc", "handle", "version", "close"):
        assert callable(getattr(mod, function))


def test_the_history_is_written_into_its_frame_not_handed_to_it():
    """Both apps serve the bundle from an origin of their own - a custom URL
    scheme on iOS, an https asset host on Android - and a `srcdoc` frame under
    a custom scheme loads, calls itself complete, and stays empty: the history
    opened on a header with no steps under it.  The report is written into the
    frame's document instead, which works everywhere."""
    src = (ROOT / "src" / "sympy_editor" / "static" / "editor.js").read_text(encoding="utf-8")
    start = src.index("async showHistory()")
    view = src[start:src.index("showHelp() {", start)]
    assert "doc.open();" in view and "doc.write(html);" in view and "doc.close();" in view
    # the frame is dressed once, by whichever of the two paths arrives first
    assert "frame.addEventListener(\"load\", dress)" in view and "dress();" in view
    assert "if (dressed || !d || !d.body || !d.body.firstChild) return;" in view


def test_the_toolbar_only_uses_glyphs_every_platform_has():
    """iOS has no glyph for these, and a button that shows an empty box says
    nothing: the icons without a character everywhere are drawn instead (as
    the arrows and the keyboard are), and the rest were chosen from what all
    three platforms carry."""
    missing_on_ios = {"\u21b6": "undo", "\u21b7": "redo", "\u2328": "keyboard",
                      "\u2630": "drawer", "\u2715": "close"}
    src = (ROOT / "src" / "sympy_editor" / "static" / "editor.js").read_text(encoding="utf-8")
    for glyph, what in missing_on_ios.items():
        assert glyph not in src, f"{what}: U+{ord(glyph):04X} does not render on iOS"
    assert "function keyboardSvg()" in src              # the one with no replacement is drawn


def test_the_app_has_an_icon_of_its_own():
    """Without one Android shows the default robot, and a store listing has
    nothing to put on its card.  The PNGs are drawn by mobile/make_icons.py
    and never committed, so what is checked here is the wiring around them -
    and the files themselves when a build has made them."""
    res = ROOT / "mobile/android/app/src/main/res"
    # adaptive icons (API 26+): a masked foreground over a flat background
    adaptive = (res / "mipmap-anydpi-v26/ic_launcher.xml").read_text(encoding="utf-8")
    assert "<adaptive-icon" in adaptive and "@mipmap/ic_launcher_foreground" in adaptive
    assert "ic_launcher_background" in (res / "values/ic_launcher_background.xml").read_text(encoding="utf-8")
    manifest = (ROOT / "mobile/android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert 'android:icon="@mipmap/ic_launcher"' in manifest
    assert 'android:roundIcon="@mipmap/ic_launcher_round"' in manifest
    # the art is built from SymPy's own mark, which travels with the repo
    mark = (ROOT / "mobile/icon/sympy-mark.svg").read_text(encoding="utf-8")
    assert "Fredrik Johansson" in mark and "SymPy_text" not in mark      # the wordmark is off
    ios = ROOT / "mobile/ios/SymPyEditor/Assets.xcassets/AppIcon.appiconset"
    assert '"size" : "1024x1024"' in (ios / "Contents.json").read_text(encoding="utf-8")
    assert "ASSETCATALOG_COMPILER_APPICON_NAME" in (ROOT / "mobile/ios/project.yml").read_text(encoding="utf-8")
    # the art itself: SymPy's mark, wordmark off, with the note that lets us use it
    mark = (ROOT / "mobile/icon/sympy-mark.svg").read_text(encoding="utf-8")
    assert "Fredrik Johansson" in mark and "SymPy_text" not in mark
    assert (ROOT / "mobile/make_icons.py").is_file()


def test_the_app_view_wears_the_icon_and_is_the_same_on_both_phones(tmp_path):
    """A page in a WebView has no title bar to say whose window it is, so the
    bundle carries the app's own icon and shows it in the corner of the
    toolbar.  And there is one bundle: both apps are a bare WebView over it,
    with no native chrome of their own, so the view is the same on either
    phone."""
    mod = _load_builder()
    logo = mod.app_logo()
    assert logo.startswith("<svg") and "SymPy editor" in logo      # the icon, inline, no XML header
    assert logo in (ROOT / "mobile/icon/icon.svg").read_text(encoding="utf-8")   # the launcher's own art

    page = mod.build(tmp_path / "www", cdn=True).joinpath("index.html").read_text(encoding="utf-8")
    # on the title's line, in the page itself - not in the editor's options:
    # the mark belongs to the window, not to the tools
    assert '<h1><span class="page-logo" aria-hidden="true"><svg' in page
    assert "</svg></span>SymPy editor</h1>" in page
    assert '"logo"' not in page.split("</h1>", 1)[1]

    # neither app puts anything of its own around the page
    manifest = (ROOT / "mobile/android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert "NoActionBar" in manifest
    swift = (ROOT / "mobile/ios/SymPyEditor/SymPyEditorApp.swift").read_text(encoding="utf-8")
    assert "NavigationView" not in swift and "toolbar" not in swift
    # ...and neither trims the view differently: Android pads its WebView with
    # the window insets, so iOS must not hand the page an edge Android keeps
    assert "ignoresSafeArea" not in swift
    view = (ROOT / "mobile/ios/SymPyEditor/EditorView.swift").read_text(encoding="utf-8")
    assert "app://www/index.html" in view                          # the same bundle, by name


def test_the_webview_shows_the_bundle_and_nothing_else():
    """The two bridges are injected into whatever page the WebView loads, and
    the Python one evaluates what it is handed: a page from anywhere else
    must never get them.  Links to other places open outside the app."""
    kt = (ROOT / "mobile/android/app/src/main/java/org/sympy/editor/MainActivity.kt").read_text(encoding="utf-8")
    assert "override fun shouldOverrideUrlLoading" in kt
    assert 'BUNDLE_HOST = "appassets.androidplatform.net"' in kt and "url.host == BUNDLE_HOST" in kt
    assert "Intent.ACTION_VIEW" in kt

    # the same rule on iOS, where the bridge is a user script and would be
    # injected into any page the view were allowed to reach
    swift = (ROOT / "mobile/ios/SymPyEditor/EditorView.swift").read_text(encoding="utf-8")
    assert "WKNavigationDelegate" in swift and "decidePolicyFor" in swift
    assert "url.scheme == EditorView.scheme && url.host == EditorView.host" in swift
    assert "decisionHandler(.cancel)" in swift and "UIApplication.shared.open(url)" in swift
    # and a request may not climb out of the bundle it is served from
    assert "standardizedFileURL" in swift and 'hasPrefix(root.path + "/")' in swift


def test_no_image_is_committed():
    """Images are drawn, not kept: `mobile/make_icons.py` makes every one of
    them from the SVGs, and a build calls it."""
    import subprocess

    tracked = subprocess.run(["git", "ls-files", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert tracked == [], tracked
    build = (ROOT / "mobile/build.py").read_text(encoding="utf-8")
    assert "make_icons(" in build           # ...and a build draws them when they are missing


@pytest.mark.skipif(not shutil.which("rsvg-convert"), reason="needs librsvg (rsvg-convert)")
def test_make_icons_draws_every_size(tmp_path):
    """What the two stores ask for, and what the launchers do."""
    import subprocess

    from PIL import Image

    subprocess.run([sys.executable, str(ROOT / "mobile/make_icons.py")], cwd=ROOT, check=True,
                   capture_output=True)
    res = ROOT / "mobile/android/app/src/main/res"
    for density in ("mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"):
        for name in ("ic_launcher.png", "ic_launcher_round.png", "ic_launcher_foreground.png"):
            assert (res / f"mipmap-{density}" / name).is_file(), (density, name)
    for name, size in (("icon-512.png", 512), ("icon-1024.png", 1024)):     # Google Play, the App Store
        with Image.open(ROOT / "mobile/icon" / name) as image:
            assert image.size == (size, size), name
    ios = ROOT / "mobile/ios/SymPyEditor/Assets.xcassets/AppIcon.appiconset/icon-1024.png"
    with Image.open(ios) as image:
        assert image.size == (1024, 1024) and image.mode == "RGB"   # the App Store refuses alpha
    # the adaptive foreground keeps its art inside the 72dp a launcher must show
    with Image.open(res / "mipmap-xxhdpi/ic_launcher_foreground.png") as image:
        side = image.size[0]
        box = [v * 108 / side for v in image.split()[-1].getbbox()]
    assert box[0] >= 18 and box[1] >= 18 and box[2] <= 90 and box[3] <= 90, box

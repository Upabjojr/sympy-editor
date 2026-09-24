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
    # the add-ons' requirements, with what they depend on, are wheels in the
    # bundle, each named in the NOTICE with its licence
    wheels = {p.name.split("-")[0].lower() for p in (out / "vendor" / "pyodide").glob("*.whl")}
    assert {"lark", "sympy_matching", "omnimatch", "multiset"} <= wheels, wheels
    listed = (out / "vendor" / "NOTICE.txt").read_text(encoding="utf-8")
    assert "lark " in listed and "sympy-matching " in listed and "MIT" in listed

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
            warnings = []
            page.on("console", lambda m: warnings.append(m.text) if m.type in ("warning", "error") else None)
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
            # The add-ons' requirements (lark, sympy-matching and what it needs)
            # came from the bundle, not from PyPI: both add-ons work offline.
            ed = "document.querySelector('.sympy-editor').__sympyEditor"
            page.evaluate(ed + ".send({action: 'addons', enable: ['latex', 'matching']})")
            page.wait_for_selector(".se-addon-matching .mt-field", timeout=60000)
            read = page.evaluate("(ed) => eval(ed).send({action: 'addon', addon: 'latex', method: 'read', "
                                 "latex: '\\\\frac{x}{2}'}).then(s => s.query.result.reading || s.query.result)", ed)
            assert read["ok"] and read["src"] == "x/2", read
            page.locator(".mt-field").fill("x**2 -> y")
            page.locator(".mt-field").press("Enter")
            page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 1", timeout=60000)
            assert not [m for m in warnings if "could not be installed" in m], warnings
            # and what the plot add-on loads from a CDN comes from the bundle
            from sympy_editor_plot import PLOTLY_JS
            assert page.evaluate("(u) => SympyEditor.loadScript(u).then(() => !!window.Plotly)", PLOTLY_JS)
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
    manual = plistlib.loads(export_options("app-store-connect", "ABCDE12345", "SymPy Editor App Store"))
    assert manual["method"] == "app-store-connect" and manual["signingStyle"] == "manual"
    assert manual["signingCertificate"] == "Apple Distribution"
    assert manual["provisioningProfiles"] == {"org.sympy.editor": "SymPy Editor App Store"}
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
    # a session saved by a newer app, with a setting this one does not know: opened all the same
    state = json.dumps({"history": [srepr(x), srepr(x + 1)], "index": 1, "a_setting_from_a_newer_app": True})
    snap = json.loads(app.new_doc("d4", srepr(x + 1), state))
    assert snap["src"] == "x + 1" and snap["can_undo"] and snap["error"] is None
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
    addons = out / "vendor" / "addons"          # the add-ons' CDN files (Plotly, ~4.6 MB): offline
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file() and addons not in p.parents)
    assert size < 5e6, size          # ~1 MB, against ~24 MB with Pyodide
    assert sum(p.stat().st_size for p in addons.rglob("*") if p.is_file()) < 8e6


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
    assert "install_stdlib Python.xcframework" in script
    for folder in ("lib-dynload", "app", "app_packages"):
        assert folder in script[script.index("process_dylibs") - 200:]     # each of them made into frameworks

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
    assert {"newDoc", "handle", "interrupt"} <= called <= {"newDoc", "handle", "interrupt", "close"}, called
    # `close` is bridged whether or not this page calls it yet: a document
    # the page leaves must not stay in the app's Python for ever
    offered = called | {"close"}
    kotlin = (ROOT / "mobile/android/app/src/main/java/org/sympy/editor/MainActivity.kt").read_text(encoding="utf-8")
    swift = (ROOT / "mobile/ios/SymPyEditor/EditorView.swift").read_text(encoding="utf-8")
    injected = swift[swift.index("window.SympyEditorPy = {"):swift.index("};", swift.index("window.SympyEditorPy = {"))]
    functions = swift[swift.index("private static let functions"):]
    functions = functions[:functions.index("]") + 1]
    python = {"newDoc": "new_doc", "handle": "handle", "interrupt": "interrupt", "close": "close"}
    for method in offered:
        assert re.search(r"@JavascriptInterface\s+fun " + method + r"\(req: String", kotlin), ("android", method)
        assert method + ': forward("' + method + '")' in injected, ("ios", method)
        assert '"%s": "%s"' % (method, python[method]) in functions, ("ios", method)
        assert 'callAttr("%s"' % python[method] in kotlin, ("android", method)
    mod = _load_app_module()
    for function in ("new_doc", "handle", "version", "close", "interrupt"):
        assert callable(getattr(mod, function))


def test_both_hosts_offer_the_files_the_page_asks_of_them():
    """What the page asks of the app that is not Python - keeping a formula in
    a file, opening one, sharing what it writes out - is `window.SympyEditorApp`,
    and both hosts answer with the same names: Kotlin in MainActivity's
    ReportBridge, Swift in FilesBridge.  The answer to an opening comes back
    through `SympyEditor.openedFile`, which both hosts call by that name."""
    src = (ROOT / "src" / "sympy_editor" / "static" / "editor.js").read_text(encoding="utf-8")
    asked = set(re.findall(r"app\.(\w+)\(", src))
    assert {"saveFile", "shareFile", "openFile", "keepRead", "keepWrite"} <= asked, asked
    hosts = {"ios": ROOT / "mobile/ios/SymPyEditor/FilesBridge.swift",
             "android": ROOT / "mobile/android/app/src/main/java/org/sympy/editor/MainActivity.kt"}
    for name, path in hosts.items():
        text = path.read_text(encoding="utf-8")
        for method in ("saveFile", "shareFile", "openFile", "keepRead", "keepWrite", "showKeyboard"):
            assert method in text, (name, method)
        assert "SympyEditor.openedFile" in text, name
        assert "SympyEditor.keptValue" in text, name
        assert "hostError" in text, name
    # and the page has somewhere for those answers to arrive
    assert "openedFile: function (token, name, text)" in src
    assert "keptValue: function (token, text)" in src
    assert "hostError: function (message)" in src


def test_both_hosts_answer_every_native_call_the_page_makes():
    """Everything the page hands to the platform - the clipboard, haptics,
    printing, full screen, Back, the flush on going to the background, a
    file opened with the app from elsewhere - has an answer in both hosts:
    a method of the injected SympyEditorApp for what the page asks
    (Host.tell / Host.ask in editor.js), and a call into the page for what
    the host tells it (SympyEditor.back, flush, openText, hostAnswer)."""
    src = (ROOT / "src" / "sympy_editor" / "static" / "editor.js").read_text(encoding="utf-8")
    asked = set(re.findall(r'Host\.(?:tell|ask)\("(\w+)"', src))
    assert {"copyText", "pasteText", "haptic", "printHtml"} <= asked, asked
    kotlin = (ROOT / "mobile/android/app/src/main/java/org/sympy/editor/MainActivity.kt").read_text(encoding="utf-8")
    swift = (ROOT / "mobile/ios/SymPyEditor/FilesBridge.swift").read_text(encoding="utf-8")
    injected = swift[swift.index("window.SympyEditorApp = {"):swift.index("};", swift.index("window.SympyEditorApp = {"))]
    for method in asked | {"setFullscreen"}:
        assert re.search(r"@JavascriptInterface\s+fun " + method + r"\(", kotlin), ("android", method)
        assert method + ': forward("' + method + '")' in injected, ("ios", method)
        assert 'case "' + method + '"' in swift, ("ios", method)
    for call in ("SympyEditor.hostAnswer", "SympyEditor.openText", "SympyEditor.flush"):
        assert call in kotlin and call in swift, call
    assert "SympyEditor.back()" in kotlin                           # the one Back button there is
    for name in ("hostAnswer: function (token, value)", "flush: function ()", "back: function ()",
                 "openText: function (name, text)"):
        assert name in src, name


def test_a_saved_formula_opens_with_the_apps():
    """A .sympy file tapped in a file manager or a mail opens in the app:
    Android's manifest takes it by type and by extension (and a formula
    shared as text), and iOS and the Mac declare it as a type of their own,
    which the pickers already offer by that name."""
    manifest = (ROOT / "mobile/android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert 'android:launchMode="singleTask"' in manifest       # a second tap reaches the running app
    assert "android.intent.action.VIEW" in manifest and "android.intent.action.SEND" in manifest
    assert 'android:mimeType="application/x-sympy-editor+json"' in manifest
    assert 'android:pathPattern=".*\\\\.sympy"' in manifest
    yaml = pytest.importorskip("yaml")
    swift = (ROOT / "mobile/ios/SymPyEditor/FilesBridge.swift").read_text(encoding="utf-8")
    assert 'UTType("org.sympy.editor.formula")' in swift
    for spec in ("mobile/ios/project.yml", "desktop/macos/project.yml"):
        info = yaml.safe_load((ROOT / spec).read_text(encoding="utf-8"))["targets"]["SymPyEditor"]["info"]["properties"]
        exported = info["UTExportedTypeDeclarations"][0]
        assert exported["UTTypeIdentifier"] == "org.sympy.editor.formula", spec
        assert exported["UTTypeTagSpecification"]["public.filename-extension"] == ["sympy"], spec
        assert info["CFBundleDocumentTypes"][0]["LSItemContentTypes"] == ["org.sympy.editor.formula"], spec


def test_the_mac_app_builds_every_swift_file_the_shell_uses():
    """The Mac app lists the iOS shell's files one by one; one left out is a
    type the others name that does not exist - FilesBridge was missing, and
    the Mac app did not compile.  And the sandboxed (App Store) build may
    use the panels and the printer it offers."""
    yaml = pytest.importorskip("yaml")
    spec = yaml.safe_load((ROOT / "desktop/macos/project.yml").read_text(encoding="utf-8"))
    listed = {Path(s["path"]).name for s in spec["targets"]["SymPyEditor"]["sources"]}
    for swift in (ROOT / "mobile/ios/SymPyEditor").glob("*.swift"):
        assert swift.name in listed, swift.name
    mas = (ROOT / "desktop/macos/SymPyEditor/SymPyEditorMAS.entitlements").read_text(encoding="utf-8")
    assert "com.apple.security.files.user-selected.read-write" in mas
    assert "com.apple.security.print" in mas



def test_the_app_interrupts_a_long_message_from_another_thread():
    """Issue #27: the apps had no Interrupt button.  Their Python runs on one
    thread of its own; the button reaches it from another (the bridge's),
    through interrupt(), and the message answers with the document as it
    was and the reason."""
    import json
    import threading

    from sympy import Symbol, srepr

    mod = _load_app_module()
    assert json.loads(mod.interrupt()) is False                     # nothing running: nothing to stop
    mod.new_doc("slow", srepr(Symbol("x")), "{}")
    started = threading.Event()

    def forever(message):                                           # a computation that does not end by itself
        started.set()
        while True:
            pass

    mod._documents["slow"].handle = forever
    out = {}
    worker = threading.Thread(target=lambda: out.update(snap=json.loads(mod.handle("slow", '{"action": "snapshot"}'))))
    worker.start()
    assert started.wait(10)
    assert json.loads(mod.interrupt()) is True
    worker.join(10)
    assert not worker.is_alive()
    assert out["snap"]["error"] == "Interrupted" and out["snap"]["src"] == "x"
    assert json.loads(mod.interrupt()) is False
    mod.close("slow")


def test_an_interrupt_that_comes_too_late_stops_nothing_else():
    """The race: the button is pressed as a message finishes.  interrupt()
    used to read which thread was running, and deliver the exception a
    moment later - by then into the *next* message, whose edit came back as
    "Interrupted" although nobody had asked.  Now the exception is only
    delivered while that same message runs, and one that arrives as it ends
    is taken back before the thread goes on."""
    import json
    import threading
    import time

    from sympy import Symbol, srepr

    mod = _load_app_module()
    mod.new_doc("race", srepr(Symbol("x")), "{}")
    busy, asked, finished, delivered = (threading.Event() for _ in range(4))
    real = mod.interrupt_thread
    calls = []

    def first(message):                    # the message the button was meant for
        busy.set()
        asked.wait(10)                     # ...which finishes just as the button is pressed
        return {"n": 1}

    def second(message):                   # the next one, which nobody interrupted
        deadline = time.monotonic() + 5
        while not delivered.is_set() and time.monotonic() < deadline:
            pass
        return {"n": 2}

    def late(ident):                       # the delivery, a moment after the check
        asked.set()
        finished.wait(0.5)
        try:
            return real(ident)
        finally:
            delivered.set()

    mod._documents["race"].handle = lambda message: (first if not calls else second)(calls.append(1) or message)
    mod.interrupt_thread = late
    out = {}

    def run():
        try:
            out["first"] = json.loads(mod.handle("race", "{}"))
            finished.set()
            out["second"] = json.loads(mod.handle("race", "{}"))
            end = time.monotonic() + 0.3   # nothing still pending in this thread
            while time.monotonic() < end:
                pass
        except BaseException as exc:       # an Interrupted that escaped: the host would answer ok=false
            out["escaped"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert busy.wait(10)
    mod.interrupt()
    worker.join(15)
    assert not worker.is_alive()
    assert "escaped" not in out, out
    assert out["first"] in ({"n": 1},) or out["first"].get("error") == "Interrupted"
    assert out["second"] == {"n": 2}, out
    mod.close("race")


def test_an_interrupt_names_the_document_it_is_for():
    """Each Mac window has documents of its own in the one interpreter (the
    bridge puts the window's name in front of the page's ids: `w2/doc1`), so
    a window's Interrupt must stop its own message and nobody else's."""
    import json
    import threading

    from sympy import Symbol, srepr

    mod = _load_app_module()
    mod.new_doc("w2/doc1", srepr(Symbol("x")), "{}")
    started = threading.Event()

    def forever(message):
        started.set()
        while True:
            pass

    mod._documents["w2/doc1"].handle = forever
    out = {}
    worker = threading.Thread(target=lambda: out.update(snap=json.loads(mod.handle("w2/doc1", "{}"))), daemon=True)
    worker.start()
    assert started.wait(10)
    assert json.loads(mod.interrupt("w1")) is False                 # another window's button
    assert json.loads(mod.interrupt("w2/doc2")) is False            # another document
    assert worker.is_alive()
    assert json.loads(mod.interrupt("w2")) is True                  # this window's
    worker.join(10)
    assert not worker.is_alive() and out["snap"]["error"] == "Interrupted"
    mod.close("w2/doc1")


def test_every_mac_window_shares_the_one_python():
    """CPython is initialized once per process.  Each window of the Mac app
    made a PythonRuntime of its own, and the second window's
    Py_InitializeFromConfig failed: it never had a working Python.  The
    runtime is a singleton, one queue serves every window, and each window's
    bridge puts its name in front of the page's document ids (every page
    starts at doc1) and interrupts only its own."""
    objc = (ROOT / "mobile/ios/SymPyEditor/PythonRuntime.m").read_text(encoding="utf-8")
    header = (ROOT / "mobile/ios/SymPyEditor/PythonRuntime.h").read_text(encoding="utf-8")
    swift = (ROOT / "mobile/ios/SymPyEditor/EditorView.swift").read_text(encoding="utf-8")
    assert "@property (class, nonatomic, readonly) PythonRuntime *shared;" in header
    assert "dispatch_once" in objc and "Py_IsInitialized()" in objc
    assert "static PyObject *sympyEditorApp" in objc          # process-wide, not per instance
    assert "PythonRuntime()" not in swift                     # nobody makes a second one
    assert "PythonRuntime.shared" in swift and "static let shared = PythonHost()" in swift
    assert swift.count("DispatchQueue(label:") == 1           # one Python thread for all windows
    assert 'rest[0] = window + "/" + rest[0]' in swift        # the window's ids
    assert "let scope = [window]" in swift                    # its own interrupt
    assert '"close", arguments: [id]' in swift                # its documents go with it


def test_the_hosts_survive_the_page_s_process_dying():
    """The system may end a WebView's content process (memory) or it may
    crash.  Unhandled, Android ends the app with it and WebKit leaves a blank
    view; both hosts load the page again instead."""
    kotlin = (ROOT / "mobile/android/app/src/main/java/org/sympy/editor/MainActivity.kt").read_text(encoding="utf-8")
    swift = (ROOT / "mobile/ios/SymPyEditor/EditorView.swift").read_text(encoding="utf-8")
    assert kotlin.count("override fun onRenderProcessGone(") == 2   # the page's WebView and the printer's
    gone = kotlin[kotlin.index("override fun onRenderProcessGone("):]
    assert "recreate()" in gone[:600] and "return true" in gone[:600]
    assert "func webViewWebContentProcessDidTerminate(_ webView: WKWebView)" in swift
    assert "webView.reload()" in swift


def test_the_android_activity_survives_being_made_again():
    """A fold, a resize, a keyboard or the density changing recreated the
    activity - the page reloaded mid-edit, and a save dialog's answer arrived
    at an activity that had forgotten the text.  The configuration changes
    are the activity's to handle, and what a dialog waits for is kept in the
    saved state (the text in a cache file)."""
    manifest = (ROOT / "mobile/android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    changes = set(re.search(r'android:configChanges="([^"]+)"', manifest).group(1).split("|"))
    assert {"orientation", "screenSize", "smallestScreenSize", "screenLayout", "density", "fontScale",
            "keyboard", "keyboardHidden", "navigation", "uiMode"} <= changes, changes
    kotlin = (ROOT / "mobile/android/app/src/main/java/org/sympy/editor/MainActivity.kt").read_text(encoding="utf-8")
    saved = kotlin[kotlin.index("override fun onSaveInstanceState"):]
    saved = saved[:saved.index("\n    }\n")]
    for key in ("STATE_PENDING_PATH", "STATE_PENDING_MIME", "STATE_OPENING"):
        assert key in saved, key
        assert "state.getString(" + key + ")" in kotlin, key
    assert "PendingSave(val mime: String, val path: String)" in kotlin     # the text waits on disk
    destroy = kotlin[kotlin.index("override fun onDestroy()"):]
    destroy = destroy[:destroy.index("\n    }\n")]
    assert "removeView(web)" in destroy and "web.destroy()" in destroy
    assert destroy.index("removeView(web)") < destroy.index("web.destroy()")
    assert "pythonThread.shutdown()" not in kotlin                   # the process's thread, not the activity's


def test_the_mac_app_keeps_the_work_before_it_quits():
    """Keeping a session is a round trip through Python; a flush from
    willTerminate never came back before the app was gone.  Quitting now
    waits (terminateLater) for every window to have written, or 1.5 s; a
    window closing flushes too, holding its web view meanwhile."""
    app = (ROOT / "mobile/ios/SymPyEditor/SymPyEditorApp.swift").read_text(encoding="utf-8")
    files = (ROOT / "mobile/ios/SymPyEditor/FilesBridge.swift").read_text(encoding="utf-8")
    assert "@NSApplicationDelegateAdaptor(AppDelegate.self)" in app
    assert "func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply" in app
    assert "return .terminateLater" in app and "reply(toApplicationShouldTerminate: true)" in app
    assert "Timer(timeInterval: 1.5" in app and "FilesBridge.live" in app
    assert "NSWindow.willCloseNotification" in files and "keptSomething()" in files


def test_the_mac_printer_lives_as_long_as_its_sheet():
    """runModal(for:) returns with the print sheet still up; releasing the
    printer there took the web view it prints from.  The printer goes when
    the sheet says it is done."""
    files = (ROOT / "mobile/ios/SymPyEditor/FilesBridge.swift").read_text(encoding="utf-8")
    assert "didRun: #selector(printOperationDidRun(_:success:contextInfo:))" in files
    assert "@objc func printOperationDidRun(_ operation: NSPrintOperation, success: Bool," in files
    assert "delegate: nil, didRun: nil" not in files


def test_the_history_is_written_into_its_frame_not_handed_to_it():
    """Both apps serve the bundle from an origin of their own - a custom URL
    scheme on iOS, an https asset host on Android - and a `srcdoc` frame under
    a custom scheme loads, calls itself complete, and stays empty: the history
    opened on a header with no steps under it.  The report is written into the
    frame's document instead, which works everywhere."""
    src = (ROOT / "src" / "sympy_editor" / "static" / "editor.js").read_text(encoding="utf-8")
    start = src.index("async showHistory()")
    view = src[start:src.index("showHelp(", start)]
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
    assert logo.startswith("<svg") and "SymPy Editor" in logo      # the icon, inline, no XML header
    assert logo in (ROOT / "mobile/icon/icon.svg").read_text(encoding="utf-8")   # the launcher's own art

    page = mod.build(tmp_path / "www", cdn=True).joinpath("index.html").read_text(encoding="utf-8")
    # on the title's line, in the page itself - not in the editor's options:
    # the mark belongs to the window, not to the tools
    assert '<h1><span class="page-logo" aria-hidden="true"><svg' in page
    assert "</svg></span>SymPy Editor</h1>" in page
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


def test_the_mac_app_is_the_same_shell_in_a_window():
    """desktop/macos: the Mac app builds the very sources of the iOS one (a
    WKWebView is a WKWebView), with the page, the app's Python and SymPy as
    mobile/build.py stages them.  Its interpreter is the macOS build of the
    same release, which carries the standard library inside the framework -
    so there is no install step, and nothing of the iOS one's."""
    yaml = pytest.importorskip("yaml")
    spec = yaml.safe_load((ROOT / "desktop" / "macos" / "project.yml").read_text(encoding="utf-8"))
    target = spec["targets"]["SymPyEditor"]
    assert target["platform"] == "macOS"
    assert any(d.get("framework") == "Python.xcframework" and d.get("embed") for d in target["dependencies"])
    paths = {s["path"] for s in target["sources"] if isinstance(s, dict)}
    # the shell, shared with iOS, and the three folders the phone build stages
    assert "../../mobile/ios/SymPyEditor/EditorView.swift" in paths
    assert {"../../mobile/www", "../../mobile/ios/app", "../../mobile/ios/app_packages"} <= paths
    assert "postBuildScripts" not in target                      # nothing to install: the framework has it all
    assert target["settings"]["base"]["CODE_SIGN_ENTITLEMENTS"]
    assert "disable-library-validation" in (ROOT / "desktop/macos/SymPyEditor/SymPyEditor.entitlements").read_text(encoding="utf-8")

    # the shared sources carry their Mac branches, and the iOS ones with them
    view = (ROOT / "mobile/ios/SymPyEditor/EditorView.swift").read_text(encoding="utf-8")
    assert "NSViewRepresentable" in view and "UIViewRepresentable" in view
    assert "NSWorkspace.shared.open(url)" in view
    objc = (ROOT / "mobile/ios/SymPyEditor/PythonRuntime.m").read_text(encoding="utf-8")
    assert "TARGET_OS_OSX" in objc and "Python.framework/Versions/Current" in objc

    build = (ROOT / "desktop" / "build.py").read_text(encoding="utf-8")
    assert "import build as mobile" in build                     # one staging, both apps
    assert "macOS-support" in build                              # the macOS flavour of the pinned release
    assert "mobile.build_www(cdn, native=True)" in build         # the page edits in the app's own Python


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


def test_the_ios_app_leaves_openssl_behind():
    """App Store review reads OpenSSL, in CPython's _ssl and _hashlib, as a
    third-party SDK owing a privacy manifest (ITMS-91061): the build phase
    drops both before they become frameworks, and says it must not encrypt."""
    project = (ROOT / "mobile" / "ios" / "project.yml").read_text(encoding="utf-8")
    script = project[project.index("install_stdlib"):project.index("process_dylibs")]
    assert "_ssl" in script and "_hashlib" in script and "Frameworks" in script
    assert "install_python" not in project.replace("# install_python", "")
    assert "ITSAppUsesNonExemptEncryption: false" in project
    assert "CFBundleVersion: ${IOS_BUILD_NUMBER}" in project


def test_the_ios_build_number_counts_the_commits():
    # by path, as the other tests load it: `mobile` is a directory of scripts,
    # not an importable package, so an installed checkout has no such module
    spec = importlib.util.spec_from_file_location("mobile_build", ROOT / "mobile" / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert build.build_number().isdigit()


def test_the_apps_bundle_the_addons_one_folder_each(tmp_path):
    """mobile/build.py stages the add-on folders beside the app's Python -
    manifest and package, no tests - and the app's module counts them as
    installed: a document lists them, switches one on, and the page gets the
    add-on's front end with the answer."""
    import json
    import subprocess

    spec = importlib.util.spec_from_file_location("mobile_build", ROOT / "mobile" / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    dest = build.copy_python_sources(tmp_path / "python")
    folders = sorted(p.name for p in (dest / "addons").iterdir())
    assert folders == ["sympy_editor_latex", "sympy_editor_matching", "sympy_editor_plot", "sympy_editor_tree"]   # the template is not shipped
    for folder in folders:
        assert (dest / "addons" / folder / "addon.json").is_file()
        assert not (dest / "addons" / folder / "tests").exists()            # nothing of the test suites
    assert (dest / "addons" / "sympy_editor_tree" / "sympy_editor_tree" / "static" / "tree.js").is_file()
    assert build.addon_requirements() == ["lark>=1.1", "sympy-matching>=0.0.4"]
    # the staged module, in a process of its own, as the app runs it
    code = f"""
import json, sys
sys.path.insert(0, {str(dest)!r}); sys.path.insert(0, {str(ROOT / 'src')!r})
import sympy_editor_app as app
print(json.dumps(sorted(json.loads(app.version())["addons"])))
snap = json.loads(app.new_doc("d", "Add(Symbol('x'), Symbol('y'))", json.dumps({{"available": ["sympy_editor_tree"]}})))   # the page named one; the app carries all
print(json.dumps(sorted(a["name"] for a in snap["addons_available"])), json.dumps(snap["addons"]))
snap = json.loads(app.handle("d", json.dumps({{"action": "addons", "enable": ["tree"]}})))
print(json.dumps(snap["addons"]), snap["tree"]["head"], "registerAddon" in snap["addon_clients"][0]["js"])
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    assert json.loads(lines[0]) == ["latex", "matching", "plot", "tree"]
    assert lines[1] == '["latex", "matching", "plot", "tree"] []'                               # listed, all off
    assert lines[2] == '["tree"] Add True'


def test_the_native_bundle_names_the_addons_and_remembers_the_switches(tmp_path):
    mod = _load_builder()
    out = mod.build(tmp_path / "www", native=True)
    page = (out / "index.html").read_text(encoding="utf-8")
    assert all(m in page for m in ("sympy_editor_latex", "sympy_editor_matching", "sympy_editor_plot", "sympy_editor_tree"))
    assert "sympy_editor_addon_template" not in page                        # the template is not shipped
    assert '"rememberAddons": true' in page and '"addons": []' in page             # off at start, a click away


def test_the_android_app_installs_what_the_addons_require():
    """The add-ons' pip requirements (their manifests) are what Chaquopy
    installs beside SymPy: the two lists must agree."""
    spec = importlib.util.spec_from_file_location("mobile_build", ROOT / "mobile" / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    gradle = (ROOT / "mobile" / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    for req in build.addon_requirements():
        assert f'install("{req}")' in gradle, req


def test_a_debug_build_is_its_own_application():
    """A debug APK is signed with the debug key, which no release is, and
    Android refuses to update an app with a differently signed one: sharing
    the application id would mean uninstalling the store app - and its
    sessions with it - to try a build.  The debug build is its own
    application instead, named apart on the launcher, and the FileProvider's
    authority follows the id so the two never collide."""
    gradle = (ROOT / "mobile" / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    manifest = (ROOT / "mobile" / "android" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    assert 'applicationId = "org.sympy.editor"' in gradle
    assert 'applicationIdSuffix = ".debug"' in gradle and 'versionNameSuffix = "-debug"' in gradle
    assert 'manifestPlaceholders["appLabel"] = "SymPy Editor"' in gradle          # the release's name
    assert 'manifestPlaceholders["appLabel"] = "SymPy Editor (debug)"' in gradle  # and the debug one's
    assert 'android:label="${appLabel}"' in manifest
    assert 'android:authorities="${applicationId}.fileprovider"' in manifest
    kotlin = (ROOT / "mobile/android/app/src/main/java/org/sympy/editor/MainActivity.kt").read_text(encoding="utf-8")
    assert '"$packageName.fileprovider"' in kotlin        # the id it was installed under, not a written-out one


@pytest.mark.skipif(not shutil.which("rsvg-convert"), reason="needs librsvg (rsvg-convert)")
def test_a_debug_build_says_debug_everywhere_it_is_named(tmp_path):
    """The debug build is a second application on the phone, so each place
    that names it says which one it is: the launcher (its label), the page
    over the formula (the bundle's title) and the icon (a bug badge, drawn
    into the debug source set, which Android merges over the main one)."""
    import subprocess

    Image = pytest.importorskip("PIL.Image", reason="needs Pillow to read the icons")

    spec = importlib.util.spec_from_file_location("mobile_build", ROOT / "mobile" / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert build.DEBUG_TITLE == "SymPy Editor (debug)"
    # the bundle takes the title it is given
    mod = _load_builder()
    out = mod.build(tmp_path / "www", native=True, cdn=True, debug=True)
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "<title>SymPy Editor (debug)</title>" in page and ">SymPy Editor (debug)<" in page
    plain = mod.build(tmp_path / "www2", native=True, cdn=True).joinpath("index.html").read_text(encoding="utf-8")
    assert "<title>SymPy Editor</title>" in plain
    # the icon beside the title wears the badge too, so the running app is
    # told apart at a glance and not only on the launcher
    assert "(debug)</title>" in page and page.count("#c0392b") >= 1 and "#c0392b" not in plain
    # and the icons: the debug source set has its own, badged, at every density
    subprocess.run([sys.executable, str(ROOT / "mobile/make_icons.py")], cwd=ROOT, check=True, capture_output=True)
    main_res, debug_res = ROOT / "mobile/android/app/src/main/res", ROOT / "mobile/android/app/src/debug/res"
    for density in ("mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"):
        for name in ("ic_launcher.png", "ic_launcher_round.png", "ic_launcher_foreground.png"):
            badged, plain_icon = debug_res / f"mipmap-{density}" / name, main_res / f"mipmap-{density}" / name
            assert badged.is_file(), (density, name)
            assert badged.read_bytes() != plain_icon.read_bytes(), (density, name)   # the badge is there
    # the badge is red, in the bottom-right corner, and inside the 72dp a
    # launcher must show (the foreground may be masked to any shape)
    with Image.open(debug_res / "mipmap-xxhdpi/ic_launcher_foreground.png") as image:
        side, rgb = image.size[0], image.convert("RGB")
        px = rgb.getpixel((int(side * 0.685), int(side * 0.685)))
    assert px[0] > 140 and px[1] < 90 and px[2] < 90, px
    with Image.open(debug_res / "mipmap-xxhdpi/ic_launcher_foreground.png") as image:
        box = [v * 108 / image.size[0] for v in image.split()[-1].getbbox()]
    assert box[0] >= 18 and box[1] >= 18 and box[2] <= 90 and box[3] <= 90, box


def test_the_bundle_carries_what_its_add_ons_load_from_a_cdn(tmp_path):
    """The apps' bundle (native) has Plotly for the plot add-on beside the
    page, and the page loads that copy: a CDN script in an app is a plot that
    never comes without a network."""
    mod = _load_builder()
    out = mod.build(tmp_path / "www", native=True)
    from sympy_editor_plot import PLOTLY_JS
    copy = out / "vendor" / "addons" / PLOTLY_JS.split("://", 1)[1]
    assert copy.is_file() and copy.stat().st_size > 1_000_000
    page = (out / "index.html").read_text(encoding="utf-8")
    assert '"localAssets"' in page and PLOTLY_JS in page
    assert "Plotly.js (the plot add-on)" in (out / "vendor" / "NOTICE.txt").read_text(encoding="utf-8")


def test_the_apps_have_no_network():
    """The privacy statement says the apps send nothing, and they must not be
    able to: ONNX Runtime's Android library asks for INTERNET and starts a
    telemetry uploader to Microsoft at launch.  Android takes both
    permissions and the uploader out of the merged manifest, opts out of the
    WebView's metrics and Safe Browsing, and tells ONNX Runtime to send
    nothing; iOS and the Mac block every http(s)/ws(s) load in WebKit; the
    handwriting Python disables ONNX Runtime's telemetry before importing
    it; and no app build may load from a CDN."""
    manifest = (ROOT / "mobile/android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    for removed in ('android:name="android.permission.INTERNET" tools:node="remove"',
                    'android:name="android.permission.ACCESS_NETWORK_STATE" tools:node="remove"',
                    'android:name="ai.onnxruntime.TelemetryInitializer"'):
        assert removed in manifest, removed
    assert manifest.count('tools:node="remove"') == 3
    assert '<uses-permission android:name="android.permission.INTERNET" />' not in manifest
    assert 'android.webkit.WebView.MetricsOptOut" android:value="true"' in manifest
    assert 'android.webkit.WebView.EnableSafeBrowsing" android:value="false"' in manifest
    kotlin = (ROOT / "mobile/android/app/src/main/java/org/sympy/editor/MainActivity.kt").read_text(encoding="utf-8")
    assert 'Os.setenv("ORT_DISABLE_TELEMETRY", "1", true)' in kotlin
    swift = (ROOT / "mobile/ios/SymPyEditor/EditorView.swift").read_text(encoding="utf-8")
    assert '"^https?://"' in swift and '"^wss?://"' in swift and "compileContentRuleList" in swift
    recognizer = (ROOT / "addons/sympy_editor_handwriting/sympy_editor_handwriting/recognizer.py").read_text(encoding="utf-8")
    assert recognizer.index('setdefault("ORT_DISABLE_TELEMETRY", "1")') < recognizer.index("import onnxruntime")
    assert "setTelemetry(False)" in recognizer and "disable_telemetry_events" in recognizer
    import subprocess
    out = subprocess.run([sys.executable, str(ROOT / "mobile/build.py"), "android", "--cdn"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "never use the network" in out.stderr

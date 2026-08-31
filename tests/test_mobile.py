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
    yaml = pytest.importorskip("yaml")   # PyYAML ships with Jupyter; skipped without it
    yaml.safe_load((ROOT / "mobile" / "ios" / "project.yml").read_text(encoding="utf-8"))


def _load_app_module():
    """The module the Android app runs (android/app/src/main/python)."""
    path = ROOT / "mobile" / "android" / "app" / "src" / "main" / "python" / "sympy_editor_app.py"
    spec = importlib.util.spec_from_file_location("sympy_editor_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_app_python_module_edits_documents():
    """What the Android app's CPython runs: JSON in, snapshots out.

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

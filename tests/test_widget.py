"""Tests for the anywidget integration (skipped when anywidget is missing)."""
import json

import pytest
from sympy import cos, sin, symbols

anywidget = pytest.importorskip("anywidget")

from sympy_editor import Document, edit  # noqa: E402
from sympy_editor.widget import SympyEditorWidget  # noqa: E402

x, y = symbols("x y")


def test_widget_initial_snapshot_and_js_bundle():
    w = SympyEditorWidget(x**2 + y)
    snap = json.loads(w.snapshot)
    assert snap["src"] == "x**2 + y"
    assert "/" in snap["nodes"]
    assert w.options["katexJs"].startswith("https://")
    # editor.js (plain script) followed by the anywidget entry point
    assert "var SympyEditor" in w._esm and "export default" in w._esm
    assert ".sympy-editor" in w._css


def test_widget_roundtrip_messages():
    w = SympyEditorWidget(sin(x))
    seen = []
    w.on_change(seen.append)
    w._on_msg(w, {"action": "replace", "path": "/", "src": "cos(x)"}, [])
    w.wait(5)                                       # messages run on a thread of their own
    assert w.expr == cos(x)
    assert json.loads(w.snapshot)["src"] == "cos(x)"
    assert seen == [cos(x)]
    w._on_msg(w, {"action": "undo"}, [])
    w.wait(5)
    assert w.expr == sin(x)
    # errors are reported in the snapshot, state untouched
    w._on_msg(w, {"action": "replace", "path": "/", "src": "sin("}, [])
    w.wait(5)
    assert json.loads(w.snapshot)["error"]
    assert w.expr == sin(x)
    # unrelated messages are ignored
    w._on_msg(w, {"hello": 1}, [])
    w.wait(5)


def test_widget_interrupts_a_long_computation():
    import time
    from sympy_editor.ops import Op

    def forever(expr):
        while True:
            time.sleep(0.001)

    w = SympyEditorWidget(Document(x, ops={"forever": Op("forever", "Forever", forever)}))
    w._on_msg(w, {"action": "apply", "path": "/", "op": "forever"}, [])
    assert w._worker.is_alive()
    w._on_msg(w, {"action": "interrupt"}, [])
    w.wait(5)
    assert not w._worker.is_alive()
    assert json.loads(w.snapshot)["error"].startswith("Interrupted") and w.expr == x
    w._on_msg(w, {"action": "interrupt"}, [])       # nothing running: harmless
    w._on_msg(w, {"action": "preview", "src": "x + 1"}, [])
    w.wait(5)
    assert json.loads(w.snapshot)["preview"] is True and w.expr == x


def test_widget_expr_setter_and_document_input():
    doc = Document(x)
    w = SympyEditorWidget(doc, options={"displayMode": False})
    assert w.document is doc
    assert w.options["displayMode"] is False
    w.expr = y + 1
    assert doc.expr == y + 1
    assert json.loads(w.snapshot)["src"] == "y + 1"


def test_edit_helper_returns_widget():
    assert isinstance(edit(x), SympyEditorWidget)


def test_edit_backend_switch():
    from IPython.display import HTML
    assert isinstance(edit(x), SympyEditorWidget)                    # auto: the kernel widget
    assert isinstance(edit(x, backend="kernel"), SympyEditorWidget)
    html = edit(x, backend="pyodide")                                # explicit Pyodide page
    assert isinstance(html, HTML) and "pyodide" in html.data and "SympyEditor.mount" in html.data
    with pytest.raises(ValueError):
        edit(x, backend="server")


def test_document_options_cannot_accompany_a_document():
    with pytest.raises(TypeError):
        SympyEditorWidget(Document(x), parser="implicit")
    w = SympyEditorWidget(x, parser="implicit")
    assert w.document.parser == "implicit"

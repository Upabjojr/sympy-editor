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


def test_widget_answers_every_message_by_its_request_id():
    """The front end pairs answers with messages by the id it sends; a
    message that got no answer (a worker dying on an unprintable result)
    left its promise hanging and the editor busy for good."""
    w = SympyEditorWidget(x**2 + x)
    w._on_msg(w, {"action": "call", "path": "/", "func": "Tuple(1, [x])", "_req": 7}, [])
    w.wait(5)
    snap = json.loads(w.snapshot)
    assert snap["_req"] == 7 and "cannot be shown" in snap["error"] and w.expr == x**2 + x
    w.expr = x                                       # a push from the kernel answers nothing
    assert "_req" not in json.loads(w.snapshot)
    seq = json.loads(w.snapshot)["seq"]
    # even a message whose handling blows up is answered, with a new seq
    w.document.handle = lambda message: (_ for _ in ()).throw(RuntimeError("boom"))
    w._on_msg(w, {"action": "snapshot", "_req": 8}, [])
    w.wait(5)
    snap = json.loads(w.snapshot)
    assert snap["_req"] == 8 and "boom" in snap["error"] and snap["seq"] > seq and snap["src"] == "x"


def test_widget_interrupts_the_message_that_is_running():
    """Interrupt used to hit the latest thread started - often one waiting
    for the lock behind the long computation (the session autosave) - so
    the autosave died and the computation ran on."""
    import time
    from sympy_editor.ops import Op

    def forever(expr):
        while True:
            time.sleep(0.001)

    w = SympyEditorWidget(Document(x, ops={"forever": Op("forever", "Forever", forever)}))
    seen = []
    w.observe(lambda change: seen.append(json.loads(change.new)), names="snapshot")
    w._on_msg(w, {"action": "apply", "path": "/", "op": "forever", "_req": 1}, [])
    deadline = time.time() + 5
    while w._running is None and time.time() < deadline:
        time.sleep(0.01)
    assert w._running is not None
    w._on_msg(w, {"action": "export", "_req": 2}, [])          # queued behind it
    time.sleep(0.2)
    w._on_msg(w, {"action": "interrupt"}, [])
    w.wait(5)
    assert [s["_req"] for s in seen] == [1, 2]
    assert seen[0]["error"].startswith("Interrupted") and w.expr == x
    assert seen[1]["error"] is None and "export" in seen[1]

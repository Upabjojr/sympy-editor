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


def test_widget_offers_addons_and_sends_the_front_end_when_switched_on():
    """What the notebook examples do: available= lists add-ons for the reader
    without loading them, and switching one on at run time answers with its
    front end, since the page had none to start with."""
    from sympy_editor import Addon

    class Demo(Addon):
        name = "demo"
        label = "Demo"
        js = 'SympyEditor.registerAddon("demo", {mount: function () { return {}; }});'

    demo = Demo()
    w = SympyEditorWidget(sin(x), available=[demo])
    assert w.options["addons"] == []                       # listed, not loaded
    listed = {a["name"]: a for a in w.document.available_addons()}
    assert listed["demo"]["label"] == "Demo" and not listed["demo"]["on"]

    w._on_msg(w, {"action": "addons", "enable": ["demo"], "_req": 3}, [])
    w.wait(5)
    snap = json.loads(w.snapshot)
    assert snap["_req"] == 3
    assert [a["name"] for a in snap["addon_clients"]] == ["demo"]
    assert snap["addon_clients"][0]["js"] == demo.js
    assert list(w.document.addons) == ["demo"]


def _answers(w):
    """What the widget sends back as messages of their own (keep, writefile)."""
    sent = []
    w.send = lambda content, buffers=None: sent.append(content)
    return sent


def test_the_widget_keeps_what_the_page_keeps_in_the_kernel(tmp_path):
    """The sessions, the add-on switches, the zoom are kept by the kernel -
    in the same kind of store as serve()'s - not in the browser storage of
    whatever page shows the notebook; and the answer is a message of its
    own, so the snapshot trait never holds anything but a snapshot."""
    w = SympyEditorWidget(x, store=tmp_path)
    sent = _answers(w)
    before = w.snapshot
    w._on_msg(w, {"action": "keep", "key": "zoom", "value": "1.5", "_req": 7}, [])
    assert (tmp_path / "zoom.json").read_text(encoding="utf-8") == "1.5"
    w._on_msg(w, {"action": "keep", "key": "zoom", "_req": 8}, [])
    w._on_msg(w, {"action": "keep", "key": "sessions", "_req": 9}, [])
    assert sent == [{"keep": None, "_req": 7}, {"keep": "1.5", "_req": 8}, {"keep": None, "_req": 9}]
    assert w.snapshot == before
    # a widget told to keep nothing says so, and the page falls back
    quiet = SympyEditorWidget(x, store=False)
    got = _answers(quiet)
    quiet._on_msg(quiet, {"action": "keep", "key": "zoom", "value": "2", "_req": 1}, [])
    assert got == [{"keep": None, "_req": 1}] and not any(tmp_path.glob("*.new"))


def test_the_widget_saves_files_next_to_the_notebook(tmp_path):
    """File -> Save in a notebook writes the file where the notebook is,
    under a name not taken yet; w.save() and w.open() are the same from
    Python (save_formula, open_formula)."""
    w = SympyEditorWidget(sin(x) + y, save_dir=tmp_path)
    sent = _answers(w)
    w._on_msg(w, {"action": "writefile", "name": "f.sympy", "text": "one", "_req": 3}, [])
    w._on_msg(w, {"action": "writefile", "name": "f.sympy", "text": "two", "_req": 4}, [])
    assert sent == [{"saved": str(tmp_path / "f.sympy"), "_req": 3},
                    {"saved": str(tmp_path / "f-2.sympy"), "_req": 4}]
    assert (tmp_path / "f.sympy").read_text(encoding="utf-8") == "one"
    # a name from the page cannot leave the folder
    w._on_msg(w, {"action": "writefile", "name": "../../escape.py", "text": "x", "_req": 5}, [])
    assert sent[-1]["saved"] == str(tmp_path / "escape.py")

    path = w.save_formula()
    assert path.parent == tmp_path and path.suffix == ".sympy"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["expr"] == "y + sin(x)"
    w.expr = cos(x)
    assert w.open_formula(path) == sin(x) + y and json.loads(w.snapshot)["src"] == "y + sin(x)"

"""Tests for the anywidget integration (skipped when anywidget is missing)."""
import json
import threading

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
    sent = _answers(w)
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
    # errors are reported to the view that asked (not the trait), state untouched
    w._on_msg(w, {"action": "replace", "path": "/", "src": "sin("}, [])
    w.wait(5)
    assert sent[-1]["error"] and json.loads(w.snapshot)["error"] is None
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
    sent = _answers(w)
    w._on_msg(w, {"action": "apply", "path": "/", "op": "forever"}, [])
    assert w._worker.is_alive()
    deadline = time.time() + 5
    while w._running is None and time.time() < deadline:
        time.sleep(0.01)
    w._on_msg(w, {"action": "interrupt"}, [])
    w.wait(5)
    assert not w._worker.is_alive()
    assert sent[-1]["error"].startswith("Interrupted") and w.expr == x
    w._on_msg(w, {"action": "interrupt"}, [])       # nothing running: harmless
    w._on_msg(w, {"action": "preview", "src": "x + 1"}, [])
    w.wait(5)
    assert sent[-1]["preview"] is True and w.expr == x
    assert "preview" not in json.loads(w.snapshot)  # a preview is the asking view's, not every display's


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
    sent = _answers(w)
    w._on_msg(w, {"action": "call", "path": "/", "func": "Tuple(1, [x])", "_req": 7}, [])
    w.wait(5)
    snap = sent[-1]
    assert snap["_req"] == 7 and "cannot be shown" in snap["error"] and w.expr == x**2 + x
    w.expr = x                                       # a push from the kernel answers nothing
    assert "_req" not in json.loads(w.snapshot)
    seq = json.loads(w.snapshot)["seq"]
    # even a message whose handling blows up is answered, with a new seq
    w.document.handle = lambda message: (_ for _ in ()).throw(RuntimeError("boom"))
    w._on_msg(w, {"action": "snapshot", "_req": 8}, [])
    w.wait(5)
    snap = sent[-1]
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
    seen = _answers(w)          # an error and a session to keep: answers for the view, not the trait
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
    assert "keep" not in got[0] and "keeps nothing" in got[0]["error"] and not any(tmp_path.glob("*.new"))


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


def test_what_is_for_one_view_never_goes_in_the_trait():
    """The trait is what every display of the widget draws: a preview, the
    function list, a signature, the session to keep, the Python script held
    there were drawn by a second display, and an error fallback built from
    the last pushed snapshot could bring a stale preview back.  They go to
    the view that asked, as messages of their own; edits still go in the trait."""
    w = SympyEditorWidget(sin(x))
    sent = _answers(w)
    before = w.snapshot
    for req, message in enumerate([{"action": "preview", "src": "x + 1"},
                                   {"action": "functions"},
                                   {"action": "methods", "path": "/"},
                                   {"action": "signature", "name": "diff"},
                                   {"action": "export"},
                                   {"action": "script"},
                                   {"action": "savefile", "name": "f"}], start=1):
        w._on_msg(w, dict(message, _req=req), [])
        w.wait(5)
        assert sent[-1]["_req"] == req, message
        assert w.snapshot == before, message
    assert sent[0]["preview"] and "functions" in sent[1] and "export" in sent[4] and "script" in sent[5]
    # an edit is everybody's
    w._on_msg(w, {"action": "replace", "path": "/", "src": "cos(x)", "_req": 20}, [])
    w.wait(5)
    assert json.loads(w.snapshot)["_req"] == 20 and json.loads(w.snapshot)["src"] == "cos(x)"
    # a failure after a preview answers with the committed state, not the preview
    w._on_msg(w, {"action": "preview", "src": "x + 7", "_req": 21}, [])
    w.wait(5)
    w.document.handle = lambda message: (_ for _ in ()).throw(RuntimeError("boom"))
    w._on_msg(w, {"action": "snapshot", "_req": 22}, [])
    w.wait(5)
    assert sent[-1]["_req"] == 22 and not sent[-1].get("preview") and sent[-1]["src"] == "cos(x)"


def test_a_session_is_opened_in_the_widget(tmp_path):
    """The widget held one document and could not open a session, so a
    notebook never saved one: `load` swaps in the session's Document (the
    old one's settings and listeners kept) and answers in the trait - every
    display shows the session now; a session it cannot read changes nothing."""
    w = SympyEditorWidget(sin(x), store=tmp_path)
    sent = _answers(w)
    seen = []
    w.on_change(seen.append)
    other = Document(y)
    other.replace("/", "y**2")
    w._on_msg(w, {"action": "load", "state": other.export(), "_req": 1}, [])
    w.wait(5)
    snap = json.loads(w.snapshot)
    assert snap["_req"] == 1 and snap["src"] == "y**2" and snap["can_undo"] and w.expr == y**2
    w._on_msg(w, {"action": "undo", "_req": 2}, [])
    w.wait(5)
    assert w.expr == y and seen[-1] == y
    w._on_msg(w, {"action": "load", "state": {"history": ["Integer(1)"], "symbols": ["garbage("]}, "_req": 3}, [])
    w.wait(5)
    assert sent[-1]["_req"] == 3 and "could not be opened" in sent[-1]["error"] and w.expr == y


def test_a_late_interrupt_still_lets_the_answer_out(monkeypatch):
    """The interrupt read the running thread, then delivered: arriving after
    the message was done, it went off while the answer was being sent - the
    thread died and the editor stayed busy for good.  Here the delivery is
    held until the message has returned, as a slow scheduler would."""
    import time
    import sympy_editor.document as document_module
    import sympy_editor.server as server_module
    import sympy_editor.widget as widget_module
    real = document_module.interrupt_thread
    entered, left = threading.Event(), threading.Event()

    def slow(ident):
        entered.set()
        left.wait(2)
        time.sleep(0.05)
        return real(ident)

    for module in (server_module, widget_module):
        if hasattr(module, "interrupt_thread"):
            monkeypatch.setattr(module, "interrupt_thread", slow)
    w = SympyEditorWidget(x)
    got = []

    def record(content, buffers=None):
        time.sleep(0.3)                      # sending the answer takes a moment
        got.append(content)

    w.send = record
    w.observe(lambda change: record(json.loads(change.new)), names="snapshot")
    handle = w.document.handle

    def late(message):
        snap = handle(message)
        threading.Thread(target=w._on_msg, args=(w, {"action": "interrupt"}, []), daemon=True).start()
        entered.wait(2)
        left.set()
        return snap

    w.document.handle = late
    w._on_msg(w, {"action": "replace", "path": "/", "src": "x + 1", "_req": 1}, [])
    w.wait(5)
    assert got and got[-1]["_req"] == 1
    assert w._running is None and w.expr == x + 1

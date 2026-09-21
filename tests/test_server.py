import json
import threading
from pathlib import Path
from contextlib import contextmanager
import urllib.request
import urllib.error

import pytest
from sympy import symbols

from sympy_editor import Document
from sympy_editor.server import EditorServer

x, y = symbols("x y")


@pytest.fixture
def server():
    srv = EditorServer(Document(x + 1), port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _post(srv, message, token=None):
    req = urllib.request.Request(
        srv.url + "api",
        data=json.dumps(message).encode(),
        headers={"Content-Type": "application/json", "X-SymPy-Editor-Token": srv.token if token is None else token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def test_page_and_api(server):
    with urllib.request.urlopen(server.url, timeout=5) as resp:
        page = resp.read().decode()
    assert "SympyEditor.mount" in page and server.token in page
    snap = _post(server, {"action": "replace", "path": "/", "src": "x*y"})
    assert snap["src"] == "x*y" and snap["error"] is None
    assert server.document.expr == x * y


def test_token_required(server):
    with pytest.raises(urllib.error.HTTPError) as info:
        _post(server, {"action": "undo"}, token="wrong")
    assert info.value.code == 403


def test_close_shuts_down():
    srv = EditorServer(Document(x), port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    snap = _post(srv, {"action": "close"})
    assert snap["closed"] is True
    t.join(timeout=5)
    assert not t.is_alive()
    srv.server_close()


def test_host_header_must_be_loopback(server):
    # DNS rebinding: a page at evil.example resolving to 127.0.0.1 could fetch
    # the editor page (and the token in it) - unless the Host header is checked.
    req = urllib.request.Request(server.url, headers={"Host": "evil.example"})
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(req, timeout=5)
    assert info.value.code == 403
    req = urllib.request.Request(server.url + "api", data=b"{}", method="POST",
                                 headers={"Host": "evil.example:80", "X-SymPy-Editor-Token": server.token})
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(req, timeout=5)
    assert info.value.code == 403
    # the browser's own requests name the server
    port = server.server_address[1]
    for host in (f"127.0.0.1:{port}", f"localhost:{port}", "localhost", f"[::1]:{port}"):
        assert server.accepts_host(host), host
    assert not server.accepts_host(None) and not server.accepts_host("127.0.0.1.evil.example")
    with urllib.request.urlopen(urllib.request.Request(server.url, headers={"Host": f"localhost:{port}"}), timeout=5) as resp:
        assert resp.status == 200
    # a server exposed on purpose (non-loopback bind) accepts any host
    exposed = EditorServer(Document(x), host="0.0.0.0", port=0)
    try:
        assert exposed.accepts_host("192.168.1.10:8000") and exposed.accepts_host("my-laptop.local")
    finally:
        exposed.server_close()


def test_interrupt_stops_a_long_computation():
    import time
    from sympy_editor.ops import Op

    def forever(expr):
        while True:            # pure Python: interruptible at any bytecode
            time.sleep(0.001)

    doc = Document(x + 1, ops={"forever": Op("forever", "Forever", forever)})
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        result = {}
        t = threading.Thread(target=lambda: result.update(snap=_post(srv, {"action": "apply", "path": "/", "op": "forever"})))
        t.start()
        for _ in range(100):
            if srv.working is not None:
                break
            time.sleep(0.02)
        assert srv.working is not None
        assert _post(srv, {"action": "interrupt"}) == {"interrupted": True}
        t.join(timeout=5)
        assert not t.is_alive()
        assert result["snap"]["error"].startswith("Interrupted") and result["snap"]["src"] == "x + 1"
        assert srv.working is None and doc.expr == x + 1 and not doc.can_undo
        assert _post(srv, {"action": "interrupt"}) == {"interrupted": False}     # nothing running
        assert _post(srv, {"action": "replace", "path": "/", "src": "x*y"})["src"] == "x*y"   # still serving
    finally:
        srv.shutdown()
        srv.server_close()


@contextmanager
def _serving(srv):
    """Serve ``srv`` on a thread of its own for the length of the block."""
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_server_keeps_what_the_page_keeps(tmp_path):
    """The sessions, each with its history, are the server's to keep: a file
    of its own, not the browser's storage, so they are there whichever
    browser opens the page.  A name from the page cannot reach out of the
    store, and `store=False` keeps nothing at all."""
    srv = EditorServer(Document(x), port=0, store=tmp_path)
    with _serving(srv):
        assert _post(srv, {"action": "keep", "key": "sessions"}) == {"keep": None}
        assert _post(srv, {"action": "keep", "key": "sessions", "value": '{"list": []}'}) == {"keep": None}
        assert _post(srv, {"action": "keep", "key": "sessions"}) == {"keep": '{"list": []}'}
        assert (tmp_path / "sessions.json").read_text(encoding="utf-8") == '{"list": []}'
        # written again, and the document is untouched by any of it
        _post(srv, {"action": "keep", "key": "sessions", "value": "second"})
        assert _post(srv, {"action": "keep", "key": "sessions"})["keep"] == "second"
        assert str(srv.document.expr) == "x"
        # a name that tries to climb out stays in the store
        assert srv._store_file("../../etc/passwd").parent == tmp_path

    quiet = EditorServer(Document(x), port=0, store=False)
    with _serving(quiet):
        assert quiet.store is None
        # it says so - an answer with no "keep" - and the page keeps its own copy
        assert "keeps nothing" in _post(quiet, {"action": "keep", "key": "sessions", "value": "x"})["error"]
        assert "keep" not in _post(quiet, {"action": "keep", "key": "sessions"})


def test_the_store_goes_where_each_platform_keeps_such_things(tmp_path, monkeypatch):
    """Windows keeps it in LOCALAPPDATA, macOS in Application Support, the
    rest under XDG_STATE_HOME (~/.local/state by default); and a name
    Windows keeps for a device (con, nul, com1...) is still a file."""
    import sys

    from sympy_editor.server import default_store

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    monkeypatch.setattr(sys, "platform", "linux")
    assert default_store() == home / ".local" / "state" / "sympy-editor"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert default_store() == home / "Library" / "Application Support" / "sympy-editor"
    monkeypatch.setattr(sys, "platform", "win32")
    assert default_store() == home / "AppData" / "Local" / "sympy-editor"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert default_store() == tmp_path / "Local" / "sympy-editor"
    # and a home laid out the XDG way is honoured wherever it is
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert default_store() == tmp_path / "state" / "sympy-editor"

    srv = EditorServer(Document(x), port=0, store=tmp_path)
    try:
        assert srv._store_file("con").name == "_con.json"       # a file on Windows too
        assert srv._store_file("COM1").name == "_COM1.json"
        assert srv._store_file("sessions").name == "sessions.json"
    finally:
        srv.server_close()


def _raw(srv, headers, body=b"", timeout=5):
    """A POST to /api with the headers as given (Content-Length included),
    answering (status, body) - or raising on a timeout."""
    import http.client
    host, port = srv.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.putrequest("POST", "/api", skip_accept_encoding=True)
        conn.putheader("X-SymPy-Editor-Token", srv.token)
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.endheaders()
        if body:
            conn.send(body)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def test_a_body_length_that_cannot_be_is_refused(server):
    """`Content-Length: -1` read until the client hung up - a thread of the
    server blocked for good - and a huge one tried to allocate it all."""
    assert _raw(server, {"Content-Length": "-1"}, timeout=3)[0] == 400
    assert _raw(server, {"Content-Length": str(10**12)}, timeout=3)[0] == 413
    assert _raw(server, {"Content-Length": "many"}, timeout=3)[0] == 400
    assert _post(server, {"action": "snapshot"})["src"] == "x + 1"        # and it still serves


def test_any_loopback_address_checks_the_host_name():
    """Bound to 127.0.0.2 (loopback too) the server took any Host - the
    rebinding check only knew 127.0.0.1."""
    srv = EditorServer(Document(x), host="127.0.0.2", port=0)
    try:
        assert not srv.accepts_host("evil.example") and not srv.accepts_host("evil.example:80")
        port = srv.server_address[1]
        assert srv.accepts_host(f"127.0.0.2:{port}") and srv.accepts_host(f"localhost:{port}")
    finally:
        srv.server_close()


def test_the_server_serves_on_ipv6_loopback():
    """`::1` was in the list of loopback names, but the server could only
    bind IPv4: it now takes an IPv6 address, and names itself with brackets."""
    import socket
    if not socket.has_ipv6:
        pytest.skip("no IPv6")
    try:
        srv = EditorServer(Document(x + 1), host="::1", port=0)
    except OSError as exc:
        pytest.skip(f"no IPv6 loopback here: {exc}")
    with _serving(srv):
        assert srv.url.startswith("http://[::1]:")
        assert not srv.accepts_host("evil.example")
        assert _post(srv, {"action": "snapshot"})["src"] == "x + 1"


def test_parallel_writes_of_one_name_all_land(tmp_path):
    """Two saves of the sessions at once shared one temporary file: one
    renamed it from under the other, whose error told the page this store
    keeps nothing - and the page stopped using it for good."""
    from sympy_editor.store import Store
    store = Store(tmp_path)
    answers = []
    barrier = threading.Barrier(8)

    def write(i):
        barrier.wait()
        for j in range(25):
            answers.append(store.answer({"key": "sessions", "value": f"{i}-{j}"}))

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert all(a == {"keep": None} for a in answers), [a for a in answers if a != {"keep": None}][:3]
    assert (tmp_path / "sessions.json").read_text(encoding="utf-8").endswith("-24")
    assert not list(tmp_path.glob("*.new"))                               # no temporary file left behind


def test_the_rename_is_tried_again_when_windows_refuses_it(tmp_path, monkeypatch):
    """Windows refuses to replace a file someone has open (a scanner, a
    reader): the rename is tried again for a moment instead of failing."""
    import os
    from sympy_editor import store as store_module
    real = os.replace
    refusals = []

    def busy(src, dst):
        if len(refusals) < 2:
            refusals.append(dst)
            raise PermissionError("in use")
        return real(src, dst)

    monkeypatch.setattr(store_module.os, "replace", busy)
    s = store_module.Store(tmp_path)
    assert s.answer({"key": "zoom", "value": "1.5"}) == {"keep": None}
    assert len(refusals) == 2 and (tmp_path / "zoom.json").read_text(encoding="utf-8") == "1.5"


def test_a_session_is_opened_in_the_server(tmp_path):
    """The server held one document and could not open a session, so the
    page never saved one.  `load` swaps in a Document holding the session,
    with the settings and listeners of the old one; a session it cannot
    read leaves the document as it was."""
    doc = Document(x + 1)
    seen = []
    doc.on_change(seen.append)
    srv = EditorServer(doc, port=0, store=tmp_path)
    with _serving(srv):
        other = Document(y)
        other.replace("/", "y**2")
        snap = _post(srv, {"action": "load", "state": other.export()})
        assert snap["error"] is None and snap["src"] == "y**2" and snap["can_undo"]
        assert srv.document is not doc and srv.document.expr == y**2
        _post(srv, {"action": "undo"})
        assert srv.document.expr == y and seen[-1] == y                   # the old listeners hear the new one
        # refused: a symbol that is not one, no history, not a session - nothing changes
        for bad in ({"history": ["Integer(1)"], "symbols": ["garbage("]},
                    {"history": []}, "nonsense"):
            snap = _post(srv, {"action": "load", "state": bad})
            assert snap["error"] and "could not be opened" in snap["error"], bad
            assert srv.document.expr == y
        assert _post(srv, {"action": "snapshot"})["src"] == "y"


def test_serve_returns_the_document_the_page_ended_with(monkeypatch):
    """With sessions, the page can swap the server's document: Done has to
    hand back the one it shows, not the one serve() was given."""
    import sympy_editor.server as server_module
    opened = []
    monkeypatch.setattr(server_module.webbrowser, "open", opened.append)
    result = {}
    t = threading.Thread(target=lambda: result.update(expr=server_module.serve(x, store=False)), daemon=True)
    t.start()
    for _ in range(100):
        if opened:
            break
        threading.Event().wait(0.02)
    url = opened[0]
    token = urllib.request.urlopen(url, timeout=5).read().decode().split('"token": "', 1)[1].split('"', 1)[0]

    def post(message):
        req = urllib.request.Request(url + "api", data=json.dumps(message).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-SymPy-Editor-Token": token})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    post({"action": "load", "state": {"history": ["Symbol('y')"], "index": 0}})
    post({"action": "close"})
    t.join(5)
    assert result["expr"] == y


def test_an_interrupt_that_comes_late_does_not_kill_the_answer():
    """The interrupt read the running thread and delivered later: arriving
    after the message was done it went off in the code sending the answer,
    and the request got none.  Here the interrupt comes at the very end of
    the message: an answer still goes out, and the server goes on."""
    doc = Document(x + 1)
    srv = EditorServer(doc, port=0)
    real = doc.handle

    def handle(message):
        snap = real(message)
        if message.get("late"):
            srv.interrupt()               # delivered as the message returns
        return snap

    doc.handle = handle
    with _serving(srv):
        for _ in range(5):
            snap = _post(srv, {"action": "replace", "path": "/", "src": "x*y", "late": True})
            assert "src" in snap
        assert srv.working is None
        assert _post(srv, {"action": "snapshot"})["src"] in ("x*y", "x + 1")
        assert not srv.interrupt()                                        # nothing running: nothing hit

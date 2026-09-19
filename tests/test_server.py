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
        assert _post(quiet, {"action": "keep", "key": "sessions", "value": "x"}) == {"keep": None}
        assert _post(quiet, {"action": "keep", "key": "sessions"}) == {"keep": None}


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

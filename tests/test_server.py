import json
import threading
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

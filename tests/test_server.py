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

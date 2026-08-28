"""Standalone editing with a tiny local HTTP server (standard library only).

``serve(expr)`` opens the editor in the default browser, blocks until the user
presses *Done* (or Ctrl+C), and returns the edited expression.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Union

from sympy import Basic

from .document import Document
from .html import build_config, new_token, render_page

__all__ = ["EditorServer", "serve"]


class _Handler(BaseHTTPRequestHandler):
    server_version = "sympy-editor/0.1"

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path.split("?", 1)[0] in ("/", "/index.html"):
            self._reply(200, "text/html; charset=utf-8", self.server.page.encode("utf-8"))
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        srv: "EditorServer" = self.server  # type: ignore[assignment]
        if self.path != "/api":
            self.send_error(404)
            return
        # The token (embedded in the page, sent as a custom header) blocks
        # cross-site requests from other pages open in the browser.
        if self.headers.get("X-SymPy-Editor-Token") != srv.token:
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            message = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(message, dict):
                raise ValueError("expected a JSON object")
        except ValueError:
            self.send_error(400)
            return
        with srv.lock:
            if message.get("action") == "close":
                snapshot = srv.document.snapshot()
                snapshot["closed"] = True
                srv.closing = True
            else:
                snapshot = srv.document.handle(message)
        self._reply(200, "application/json", json.dumps(snapshot).encode("utf-8"))
        if srv.closing:
            threading.Thread(target=srv.shutdown, daemon=True).start()

    def _reply(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:  # noqa: A002
        if getattr(self.server, "verbose", False):
            super().log_message(format, *args)


class EditorServer(ThreadingHTTPServer):
    """HTTP server hosting one editor for one :class:`Document`."""

    daemon_threads = True

    def __init__(
        self,
        document: Document,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        title: str = "SymPy editor",
        options: Optional[Dict[str, Any]] = None,
        urls: Optional[Dict[str, str]] = None,
        verbose: bool = False,
    ):
        super().__init__((host, port), _Handler)
        self.document = document
        self.token = new_token()
        self.lock = threading.Lock()
        self.closing = False
        self.verbose = verbose
        config = build_config(document, backend="http", options=options, urls=urls, api_url="/api", token=self.token)
        self.page = render_page(config, title)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"


def serve(
    expr: Union[Basic, str, Document],
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    block: bool = True,
    verbose: bool = False,
    title: str = "SymPy editor",
    options: Optional[Dict[str, Any]] = None,
    urls: Optional[Dict[str, str]] = None,
    **document_kwargs,
):
    """Edit ``expr`` in the browser using a local server.

    With ``block=True`` (default) this returns the edited expression once the
    user clicks *Done* or the process receives Ctrl+C.  With ``block=False``
    it returns the running :class:`EditorServer` (serving in a background
    thread); read ``server.document.expr`` whenever you like and call
    ``server.shutdown()`` when done.
    """
    document = expr if isinstance(expr, Document) else Document(expr, **document_kwargs)
    server = EditorServer(document, host=host, port=port, title=title, options=options, urls=urls, verbose=verbose)
    print(f"SymPy editor running at {server.url}" + (" (press Done in the browser or Ctrl+C to finish)" if block else ""))
    if open_browser:
        webbrowser.open(server.url)
    if not block:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return document.expr

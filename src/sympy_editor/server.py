"""Standalone editing with a tiny local HTTP server (standard library only).

``serve(expr)`` opens the editor in the default browser, blocks until the user
presses *Done* (or Ctrl+C), and returns the edited expression.
"""

from __future__ import annotations

import ctypes
import ipaddress
import json
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from sympy import Basic

from .document import Document, Interrupted, interrupt_thread
from .html import build_config, new_token, render_page
from .store import Store, default_store  # noqa: F401 - default_store is part of this module's API


__all__ = ["EditorServer", "serve", "load_session"]

#: Host names under which a loopback-bound server accepts requests (besides
#: any loopback address written out: 127.0.0.2, [::1]).
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

#: The largest request body read, in bytes: a formula with its whole
#: history is a few hundred kilobytes; anything near this is not the page.
MAX_BODY = 64 * 1024 * 1024

#: The fields of a session (``Document.export()``) that ``load`` takes.
SESSION_FIELDS = ("history", "index", "labels", "symbols", "addon_state", "allow_invalid", "format")


def _loopback(address: str) -> bool:
    """Whether ``address`` is a loopback address written out (``127.0.0.2``,
    ``::1``) - or the name ``localhost``."""
    if address in _LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def load_session(document: Document, state: Dict[str, Any]) -> Document:
    """A new :class:`Document` holding the session ``state`` (what
    ``Document.export()`` gives, as the page keeps it), made with the same
    settings as ``document`` - printer, parser, operations, add-ons - and the
    same ``on_change`` listeners.  The server and the widget hold one
    document, and opening a session swaps this one in; a state the Document
    refuses raises, and the old one stays."""
    if not isinstance(state, dict):
        raise ValueError("a session is a JSON object")
    kwargs = {k: state[k] for k in SESSION_FIELDS if k in state and state[k] is not None}
    history = kwargs.get("history")
    if not isinstance(history, list) or not history or not all(isinstance(h, str) for h in history):
        raise ValueError("a session needs its history: a list of srepr strings")
    index = kwargs.get("index")
    at = len(history) - 1 if index is None else max(0, min(int(index), len(history) - 1))
    new = Document(history[at],
                   printer_settings=document.printer_settings, parser=document.parser, ops=document.ops,
                   max_history=document.max_history, addons=list(document.addons.values()),
                   available=[], **kwargs)
    # The same catalogue of add-ons, entry for entry (given as available=,
    # a "module:object" spec would be listed under that string, not its name).
    for name, spec in document._catalog.items():
        new._catalog.setdefault(name, spec)
    for name, got in document._loaded.items():
        new._loaded.setdefault(name, got)
    new._listeners = document._listeners           # the same list: a callback added later reaches both
    return new


class _Running:
    """Which thread is inside a Document message, and the interrupt for it.

    Setting and clearing the thread and delivering an interrupt happen
    under one lock, so an interrupt reaches the message it was meant for or
    nothing: read first and delivered later, it landed after the message had
    returned - in the code sending the answer, which then never went out.
    One that was delivered but not yet raised when the message ends is
    taken back (``PyThreadState_SetAsyncExc`` with no exception)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.ident: Optional[int] = None

    def interrupt(self) -> bool:
        with self._lock:
            return self.ident is not None and interrupt_thread(self.ident)

    def run(self, fn: Callable[[], Any]) -> Any:
        """``fn()`` as the running message; raises :class:`Interrupted` if
        it was interrupted and did not say so itself."""
        ident = threading.get_ident()
        with self._lock:
            self.ident = ident
        try:
            return fn()
        finally:
            self._stop(ident)

    def _stop(self, ident: int) -> None:
        while True:
            try:
                with self._lock:
                    self.ident = None
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), None)
                return
            except Interrupted:        # it came on the way out: the lock is released, clear again
                continue


class _Handler(BaseHTTPRequestHandler):
    server_version = "sympy-editor/0.1"

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if not self._host_ok():
            return
        if self.path.split("?", 1)[0] in ("/", "/index.html"):
            with self.server.lock:                     # the current state, not the one at start-up
                page = self.server.render_page()
            self._reply(200, "text/html; charset=utf-8", page.encode("utf-8"))
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        srv: "EditorServer" = self.server  # type: ignore[assignment]
        if not self._host_ok():
            return
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
        except ValueError:
            self.send_error(400)
            return
        # A negative length read until the client hung up (a thread blocked
        # for good), and a huge one tried to allocate it all.
        if length < 0:
            self.send_error(400, "Bad Content-Length")
            return
        if length > MAX_BODY:
            self.send_error(413, "Request too large")
            return
        try:
            message = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(message, dict):
                raise ValueError("expected a JSON object")
        except ValueError:
            self.send_error(400)
            return
        if message.get("action") == "keep":
            # What the page keeps between visits - the sessions, each with its
            # history - in a file of this server's own, not the browser's
            # storage: it is the same work whichever browser opens the page.
            self._reply(200, "application/json", json.dumps(srv.keeper.answer(message)).encode("utf-8"))
            return
        if message.get("action") == "interrupt":
            # Served on its own thread while the computing one holds the lock:
            # that thread gets Interrupted, and answers its request with the error.
            self._reply(200, "application/json", json.dumps({"interrupted": srv.interrupt()}).encode("utf-8"))
            return
        with srv.lock:
            if message.get("action") == "close":
                snapshot = srv.document.snapshot()
                snapshot["closed"] = True
                srv.closing = True
            elif message.get("action") == "load":
                snapshot = srv.load(message.get("state"))
            else:
                try:
                    snapshot = srv.running.run(lambda: srv.document.handle(message))
                except Interrupted as exc:     # delivered between two lines of ours, not inside handle
                    snapshot = srv.document.snapshot(error=f"Interrupted: {exc}".rstrip(": "))
        self._reply(200, "application/json", json.dumps(snapshot).encode("utf-8"))
        if srv.closing:
            threading.Thread(target=srv.shutdown, daemon=True).start()

    def _host_ok(self) -> bool:
        """Reject requests whose ``Host`` header does not name this server.

        A page on the open internet cannot read the token, but with DNS
        rebinding it could reach a loopback server under its own host name:
        a request for the page would then hand it the token.  While bound to
        a loopback address, only loopback host names are accepted."""
        srv: "EditorServer" = self.server  # type: ignore[assignment]
        if not srv.accepts_host(self.headers.get("Host")):
            self.send_error(403, "Unexpected Host header")
            return False
        return True

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
        title: str = "SymPy Editor",
        options: Optional[Dict[str, Any]] = None,
        urls: Optional[Dict[str, str]] = None,
        logo: str = "",
        verbose: bool = False,
        store: Optional[Union[str, Path, bool]] = None,
    ):
        # An IPv6 address needs a socket of its family (http.server's is IPv4).
        self.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        super().__init__((host, port), _Handler)
        self.document = document
        #: Where what the page keeps is written (see :meth:`keep`): one file
        #: per name.  ``store=False`` keeps nothing, and the page falls back
        #: to the browser's own storage.
        self.keeper = Store(store)
        self.store: Optional[Path] = self.keeper.folder
        self.token = new_token()
        self.lock = threading.Lock()
        #: The thread running a Document message, while one does (see ``working``).
        self.running = _Running()
        self.closing = False
        self.verbose = verbose
        self._page_args = (options, urls, title, logo)

    # -- what the page keeps ------------------------------------------------

    def _store_file(self, key: str) -> Path:
        """The file ``key`` is kept in (see :meth:`Store.file`)."""
        return self.keeper.file(key)

    def kept(self, key: str) -> Optional[str]:
        """What the page kept under ``key``, or ``None``."""
        return self.keeper.kept(key)

    def keep(self, key: str, value: str) -> None:
        """Keep ``value`` under ``key`` (see :meth:`Store.keep`)."""
        self.keeper.keep(key, value)

    @property
    def working(self) -> Optional[int]:
        """ident of the thread running a Document message, while one does."""
        return self.running.ident

    def interrupt(self) -> bool:
        """Interrupt the message being processed, if any (see
        :func:`~sympy_editor.document.interrupt_thread`)."""
        return self.running.interrupt()

    def load(self, state: Any) -> Dict[str, Any]:
        """Open a session (the ``load`` message): this server's document
        becomes one holding ``state`` (see :func:`load_session`), and the
        answer is its snapshot.  A state it cannot read is refused with the
        document left as it was.  Called with ``lock`` held."""
        try:
            self.document = load_session(self.document, state)
        except Exception as exc:
            return self.document.snapshot(error=f"The session could not be opened: {type(exc).__name__}: {exc}")
        return self.document.snapshot()

    def accepts_host(self, host: Optional[str]) -> bool:
        """Whether a request with this ``Host`` header is for this server
        (see ``_Handler._host_ok``).  Servers bound to a non-loopback
        address were exposed on purpose and accept any host name."""
        bound = self.server_address[0]
        if not _loopback(bound):
            return True
        name = (host or "").strip().lower()
        if name.startswith("["):                       # [::1]:port
            name = name[1:name.find("]")] if "]" in name else name[1:]
        else:
            name = name.rsplit(":", 1)[0] if name.count(":") == 1 else name
        return name == bound or _loopback(name)

    def render_page(self) -> str:
        """The editor page for the document's current state."""
        options, urls, title, logo = self._page_args
        config = build_config(self.document, backend="http", options=options, urls=urls, api_url="/api", token=self.token)
        return render_page(config, title, logo=logo)

    @property
    def page(self) -> str:
        return self.render_page()

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{port}/"


def serve(
    expr: Union[Basic, str, Document],
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    block: bool = True,
    verbose: bool = False,
    title: str = "SymPy Editor",
    options: Optional[Dict[str, Any]] = None,
    urls: Optional[Dict[str, str]] = None,
    store: Optional[Union[str, Path, bool]] = None,
    **document_kwargs,
):
    """Edit ``expr`` in the browser using a local server.

    With ``block=True`` (default) this returns the edited expression once the
    user clicks *Done* or the process receives Ctrl+C.  With ``block=False``
    it returns the running :class:`EditorServer` (serving in a background
    thread); read ``server.document.expr`` whenever you like (opening a
    session in the page swaps the document, so read it there) and call
    ``server.shutdown()`` when done.

    ``store`` is where what the page keeps - its sessions, each with the
    history behind it - is written (a folder; ``False`` keeps nothing, and
    the browser's own storage is used instead).  It defaults to the user's
    state directory: see :func:`default_store`.
    """
    document = expr if isinstance(expr, Document) else Document(expr, **document_kwargs)
    server = EditorServer(document, host=host, port=port, title=title, options=options, urls=urls,
                          verbose=verbose, store=store)
    print(f"SymPy Editor running at {server.url}" + (" (press Done in the browser or Ctrl+C to finish)" if block else ""))
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
    return server.document.expr        # a session opened in the page is the document now

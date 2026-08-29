"""Jupyter integration through `anywidget <https://anywidget.dev>`_ (MIT).

The widget's JavaScript is the concatenation of ``static/editor.js`` and
``static/widget.js``; no bundler or node.js is involved.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, Optional, Union

import anywidget
import traitlets
from sympy import Basic

from .document import Document, interrupt_thread
from .html import default_urls, read_static

__all__ = ["SympyEditorWidget"]


class SympyEditorWidget(anywidget.AnyWidget):
    """Interactive editor widget.

    >>> w = SympyEditorWidget(x**2 + y)   # doctest: +SKIP
    >>> w                                 # display it
    >>> w.expr                            # current (edited) expression
    """

    _esm = read_static("editor.js") + "\n" + read_static("widget.js")
    _css = read_static("editor.css")

    #: JSON snapshot pushed to the front end (see Document.snapshot()).
    snapshot = traitlets.Unicode("").tag(sync=True)
    #: Front-end options (see DEFAULTS in editor.js) plus KaTeX URLs.
    options = traitlets.Dict({}).tag(sync=True)

    def __init__(
        self,
        expr: Union[Basic, str, Document],
        *,
        options: Optional[Dict[str, Any]] = None,
        urls: Optional[Dict[str, str]] = None,
        **kwargs,
    ):
        document_kwargs = {k: kwargs.pop(k) for k in ("printer_settings", "parser", "ops", "max_history", "symbols") if k in kwargs}
        if isinstance(expr, Document):
            if document_kwargs:   # same rule as to_html(): they would be silently ignored
                raise TypeError("Document options cannot be combined with an existing Document")
            self.document = expr
        else:
            self.document = Document(expr, **document_kwargs)
        all_urls = default_urls()
        all_urls.update(urls or {})
        opts = {"katexJs": all_urls["katexJs"], "katexCss": all_urls["katexCss"]}
        opts.update(options or {})
        super().__init__(options=opts, **kwargs)
        self._lock = threading.Lock()          # one message at a time
        self._worker: Optional[threading.Thread] = None
        self._push(self.document.snapshot())
        self.on_msg(self._on_msg)

    # -- kernel <-> browser -------------------------------------------------

    def _on_msg(self, widget, content, buffers) -> None:
        """Messages run on a thread of their own: the kernel stays free to
        receive the next one - in particular ``interrupt``, which stops a
        long computation (:func:`~sympy_editor.document.interrupt_thread`).
        Snapshots are pushed from that thread; :meth:`wait` joins it."""
        if not isinstance(content, dict) or "action" not in content:
            return
        if content["action"] == "interrupt":
            worker = self._worker
            if worker is not None and worker.is_alive():
                interrupt_thread(worker.ident)
            return
        self._worker = threading.Thread(target=self._run, args=(content,), daemon=True)
        self._worker.start()

    def _run(self, content: Dict[str, Any]) -> None:
        with self._lock:
            self._push(self.document.handle(content))

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until the message being processed (if any) is done."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout)

    def _push(self, snap: Dict[str, Any]) -> None:
        self.snapshot = json.dumps(snap)

    def refresh(self) -> None:
        """Re-render (e.g. after mutating ``self.document`` directly)."""
        self._push(self.document.snapshot())

    # -- convenience --------------------------------------------------------

    @property
    def expr(self) -> Basic:
        """The current expression (live)."""
        return self.document.expr

    @expr.setter
    def expr(self, value) -> None:
        self.document.set(value)
        self.refresh()

    def on_change(self, callback: Callable[[Basic], None]) -> Callable[[Basic], None]:
        """Call ``callback(expr)`` after every edit made in the browser."""
        return self.document.on_change(callback)

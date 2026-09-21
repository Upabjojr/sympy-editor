"""Jupyter integration through `anywidget <https://anywidget.dev>`_ (MIT).

The widget's JavaScript is the concatenation of ``static/editor.js`` and
``static/widget.js``; no bundler or node.js is involved.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

import anywidget
import traitlets
from sympy import Basic

from .document import SAVE_EXT, Document, interrupt_thread
from .html import addon_clients, default_urls, read_static
from .store import Store, unused_path

__all__ = ["SympyEditorWidget"]


class SympyEditorWidget(anywidget.AnyWidget):
    """Interactive editor widget.

    >>> w = SympyEditorWidget(x**2 + y)   # doctest: +SKIP
    >>> w                                 # display it
    >>> w.expr                            # current (edited) expression

    Add-ons come the way they do for a page - ``addons=`` switched on,
    ``available=`` the ones to list instead of every add-on installed in
    this kernel (``available=[]`` lists none) - and each may be named by
    module, by add-on name, or given as an object:

    >>> SympyEditorWidget(sin(x), addons=["sympy_editor_plot"])      # doctest: +SKIP
    >>> SympyEditorWidget(sin(x), available=["plot", "tree"])        # doctest: +SKIP

    Their Python runs in this kernel, beside the document: an add-on's own
    methods answer over the same channel as an edit, so the plot's samples
    are the kernel's SymPy at work, not a copy of it in the browser.

    What the editor keeps between runs - its sessions, which add-ons are on,
    the zoom, an add-on's own library - is kept by this kernel in ``store``
    (a folder; by default the one :func:`sympy_editor.serve` uses, so a
    notebook and the server share their sessions; ``False`` leaves it to the
    browser's storage).  A formula or a history saved from the editor is
    written next to the notebook - in ``save_dir``, the kernel's working
    directory unless given - under a name that is not taken yet:

    >>> w.save_formula()                  # doctest: +SKIP
    PosixPath('formula.sympy')
    >>> w.open_formula("formula.sympy")   # doctest: +SKIP
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
        store: Optional[Union[str, Path, bool]] = None,
        save_dir: Optional[Union[str, Path]] = None,
        **kwargs,
    ):
        document_kwargs = {k: kwargs.pop(k) for k in ("printer_settings", "parser", "ops", "max_history", "symbols",
                                                       "addons", "available", "addon_state") if k in kwargs}
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
        opts["addons"] = addon_clients(self.document)     # their front ends, loaded by the Editor
        super().__init__(options=opts, **kwargs)
        #: Where what the page keeps is written (see :class:`~sympy_editor.store.Store`).
        self.keeper = Store(store)
        #: Where a file saved from the editor goes; None is the working directory.
        self.save_dir: Optional[Path] = Path(save_dir) if save_dir is not None else None
        self._lock = threading.Lock()          # one message at a time
        self._worker: Optional[threading.Thread] = None
        #: ident of the thread inside ``Document.handle`` right now - the one
        #: an interrupt is for.  Not the latest thread started: that one may
        #: be waiting for the lock behind it (an autosave, a preview), and
        #: interrupting it would kill the wrong message and let the long
        #: computation run on.
        self._running: Optional[int] = None
        self._last: Dict[str, Any] = {}
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
            ident = self._running
            if ident is not None:
                interrupt_thread(ident)
            return
        if content["action"] in ("keep", "writefile"):
            # Not the document's business, and not a snapshot either: the
            # answer goes back as a message of its own, paired by its request
            # id, so the ``snapshot`` trait only ever holds a snapshot (it is
            # what a second display of this widget draws).
            answer = self._keep(content) if content["action"] == "keep" else self._write_file(content)
            answer["_req"] = content.get("_req")
            self.send(answer)
            return
        self._worker = threading.Thread(target=self._run, args=(content,), daemon=True)
        self._worker.start()

    def _run(self, content: Dict[str, Any]) -> None:
        """Answer one message - always.  The front end pairs each answer with
        the message it sent by the request id it put in (``_req``), so a
        message that got no answer would leave its promise hanging and the
        editor busy for good: whatever goes wrong, a snapshot goes back."""
        req = content.get("_req")
        try:
            with self._lock:
                self._running = threading.get_ident()
                try:
                    snap = self.document.handle(content)
                finally:
                    self._running = None
        except BaseException as exc:            # Interrupted while waiting, a broken document...
            snap = dict(self._last, error=f"{type(exc).__name__}: {exc}", seq=self._last.get("seq", 0) + 1)
        if req is not None:
            snap["_req"] = req
        self._push(snap)

    def _keep(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """What the page keeps, kept by the kernel (see :class:`Store`)."""
        return self.keeper.answer(content)

    def _write_file(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """A file the editor saves (a formula, the history as Python or as a
        page), written next to the notebook under a name not taken yet."""
        try:
            path = unused_path(self.save_dir or Path.cwd(), str(content.get("name") or "formula" + SAVE_EXT))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content.get("text") or ""), encoding="utf-8")
        except OSError as exc:
            return {"error": f"The file could not be written: {exc}"}
        return {"saved": str(path)}

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until the message being processed (if any) is done."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout)

    def _push(self, snap: Dict[str, Any]) -> None:
        self._last = snap
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

    def save_formula(self, path: Optional[Union[str, Path]] = None) -> Path:
        """Write the formula, with its whole history, to ``path`` (by default
        a new ``formula.sympy``, ``formula-2.sympy``... in ``save_dir``) and
        return where it went.  The same file **File → Save formula…** writes;
        see docs/file-format.md."""
        if path is None:
            path = unused_path(self.save_dir or Path.cwd(), "formula" + SAVE_EXT)
        path = Path(path)
        path.write_text(self.document.save_text(path.stem), encoding="utf-8")
        return path

    def open_formula(self, path: Union[str, Path]) -> Basic:
        """Open a formula from a file - one :meth:`save_formula` wrote, with its
        history, or anything simpler (see :meth:`Document.open_text`) - in
        place of the one shown, and return its expression.  (Not ``open``:
        that is the widget's own, which opens its channel to the page.)"""
        expr = self.document.open_text(Path(path).read_text(encoding="utf-8"))
        self.refresh()
        return expr

    def on_change(self, callback: Callable[[Basic], None]) -> Callable[[Basic], None]:
        """Call ``callback(expr)`` after every edit made in the browser."""
        return self.document.on_change(callback)

    @property
    def addon_state(self) -> Dict[str, Dict[str, Any]]:
        """What each add-on keeps about this document, by add-on name, as
        Python objects - ``w.addon_state["matching"]["rules"]`` is the live
        list of rules of the rewrite-rules panel.  The same dict the add-on
        reads, so a change here shows at the next message."""
        return self.document.addon_state

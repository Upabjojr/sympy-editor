"""Jupyter integration through `anywidget <https://anywidget.dev>`_ (MIT).

The widget's JavaScript is the concatenation of ``static/editor.js`` and
``static/widget.js``; no bundler or node.js is involved.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional, Union

import anywidget
import traitlets
from sympy import Basic

from .document import Document
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
            self.document = expr
        else:
            self.document = Document(expr, **document_kwargs)
        all_urls = default_urls()
        all_urls.update(urls or {})
        opts = {"katexJs": all_urls["katexJs"], "katexCss": all_urls["katexCss"]}
        opts.update(options or {})
        super().__init__(options=opts, **kwargs)
        self.on_msg(self._on_msg)
        self._push(self.document.snapshot())

    # -- kernel <-> browser -------------------------------------------------

    def _on_msg(self, widget, content, buffers) -> None:
        if isinstance(content, dict) and "action" in content:
            self._push(self.document.handle(content))

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

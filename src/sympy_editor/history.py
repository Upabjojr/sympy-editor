"""A history of expressions, shown step by step - with or without the editor.

The editor keeps a history of its own, and its **History** view shows every
step with what changed.  That view needs nothing from the editor, though: it
needs a list of expressions and, for each, a word about what produced it.  So
it is available on its own here, for a derivation carried out in Python -
the steps of an integration, a sequence of rewrites, the output of somebody
else's algorithm:

    >>> from sympy import symbols, sin, cos, Integral
    >>> from sympy_editor import History, save_history_html
    >>> x = symbols("x")
    >>> steps = History([
    ...     Integral(x * sin(x), x),
    ...     (x * -cos(x) - Integral(-cos(x), x), "integration by parts"),
    ...     (-x * cos(x) + sin(x), "the remaining integral"),
    ... ], title="An integration by parts")
    >>> _ = save_history_html(steps, "steps.html")     # doctest: +SKIP

:meth:`History.payload` is what the viewer in ``static/editor.js`` consumes
(``SympyEditor.mountHistory``); :func:`sympy_editor.to_history_html` wraps it
in a page and :func:`sympy_editor.display_history` shows it in a notebook.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from sympy import Basic, sympify

from .document import render_step

__all__ = ["History"]

Step = Union[Basic, str, Tuple[Any, ...]]


class History:
    """A sequence of expressions and what turned each into the next.

    ``steps`` is an iterable of expressions (SymPy objects or strings), each
    optionally paired with the action that produced it: ``expr`` or
    ``(expr, "what happened")``.  The action of the first step, which nothing
    produced, is only a caption for it.

    ``index`` marks one step as the current one (the viewer highlights it);
    ``title`` names the history; ``printer_settings`` are the SymPy LaTeX
    printer's, as in :class:`~sympy_editor.Document`.
    """

    def __init__(
        self,
        steps: Iterable[Step] = (),
        actions: Optional[Sequence[Optional[str]]] = None,
        *,
        title: Optional[str] = None,
        index: Optional[int] = None,
        printer_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.title = title
        self.index = index
        self.printer_settings = dict(printer_settings or {})
        self._exprs: List[Basic] = []
        self._actions: List[Optional[str]] = []
        for i, step in enumerate(steps):
            expr, action = step if isinstance(step, tuple) else (step, None)
            if actions is not None and i < len(actions) and action is None:
                action = actions[i]
            self.add(expr, action)
        if actions is not None and len(actions) > len(self._exprs):
            raise ValueError(f"{len(actions)} actions for {len(self._exprs)} steps")

    # -- building -----------------------------------------------------------

    def add(self, expr: Union[Basic, str], action: Optional[str] = None) -> Basic:
        """Append a step - the expression and what produced it."""
        expr = expr if isinstance(expr, Basic) else sympify(expr)
        self._exprs.append(expr)
        self._actions.append(action)
        return expr

    @classmethod
    def from_document(cls, doc, **kwargs) -> "History":
        """The history a :class:`~sympy_editor.Document` has accumulated."""
        hist = doc.history_labels()
        out = cls(printer_settings=getattr(doc, "printer_settings", None), **kwargs)
        out._exprs = list(doc._history)
        out._actions = list(hist["actions"])
        out.index = kwargs.get("index", hist["index"])
        return out

    # -- reading ------------------------------------------------------------

    @property
    def steps(self) -> List[Basic]:
        """The expressions, oldest first."""
        return list(self._exprs)

    @property
    def actions(self) -> List[Optional[str]]:
        """What produced each step (``actions[i]`` turned step ``i-1`` into
        step ``i``)."""
        return list(self._actions)

    def __len__(self) -> int:
        return len(self._exprs)

    def __iter__(self):
        return iter(self._exprs)

    def __getitem__(self, i):
        return self._exprs[i]

    def __repr__(self) -> str:
        name = f" {self.title!r}" if self.title else ""
        return f"<History{name}: {len(self)} step{'' if len(self) == 1 else 's'}>"

    # -- the viewer ---------------------------------------------------------

    def payload(self) -> Dict[str, Any]:
        """What the viewer consumes: ``{"steps": [{"latex", "nodes"}],
        "labels": [SymPy source of each step], "actions", "index"}`` - the
        same shape :meth:`Document.history_labels` produces, so one viewer
        serves both."""
        if not self._exprs:
            raise ValueError("An empty history has nothing to show")
        return {
            "steps": [render_step(e, self.printer_settings) for e in self._exprs],
            "labels": [str(e) for e in self._exprs],
            "actions": ["" if a is None else str(a) for a in self._actions],
            "index": self.index,
        }

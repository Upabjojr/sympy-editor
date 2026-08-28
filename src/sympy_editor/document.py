"""The editable document: current expression, history and edit operations.

:class:`Document` is the single source of truth shared by every front end
(Jupyter widget, Pyodide page, HTTP server).  Front ends talk to it through
:meth:`Document.handle`, which takes a JSON-able message and returns a JSON-able
snapshot, so the same JavaScript works everywhere.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Union

from sympy import Basic, Dummy, IndexedBase, MatrixSymbol, Symbol, sympify, srepr
from sympy.core.function import AppliedUndef
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

from .ops import Op, get_ops
from .printer import (
    Path,
    annotate,
    delete_at,
    format_path,
    get_at,
    parse_path,
    plain_latex,
    replace_at,
)

__all__ = ["Document"]

PathLike = Union[str, Path]


class Document:
    """An editable SymPy expression with undo history.

    Parameters
    ----------
    expr
        A SymPy object or a string (``sympify``-d; ``srepr`` output round-trips).
    printer_settings
        Extra :func:`sympy.latex` settings.
    parser
        ``"strict"`` (default) or ``"implicit"`` (allows ``2x``, ``sin x``).
    ops
        Mapping of op name to :class:`~sympy_editor.ops.Op`; defaults to the
        global registry.
    max_history
        Maximum number of undo steps kept.
    """

    def __init__(
        self,
        expr: Union[Basic, str],
        *,
        printer_settings: Optional[Dict[str, Any]] = None,
        parser: str = "strict",
        ops: Optional[Dict[str, Op]] = None,
        max_history: int = 200,
    ):
        if parser not in ("strict", "implicit"):
            raise ValueError("parser must be 'strict' or 'implicit'")
        self.printer_settings = dict(printer_settings or {})
        self.parser = parser
        self.ops: Dict[str, Op] = dict(ops) if ops is not None else get_ops()
        self.max_history = max_history
        self._history: List[Basic] = []
        self._index = -1
        self._seq = 0
        self._listeners: List[Callable[[Basic], None]] = []
        self._commit(self._coerce(expr))

    # -- state --------------------------------------------------------------

    @property
    def expr(self) -> Basic:
        """The current expression."""
        return self._history[self._index]

    @expr.setter
    def expr(self, value) -> None:
        self.set(value)

    def set(self, expr: Union[Basic, str]) -> Basic:
        """Replace the whole expression (recorded in history)."""
        return self._commit(self._coerce(expr))

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._history) - 1

    def undo(self) -> Basic:
        if self.can_undo:
            self._index -= 1
            self._notify()
        return self.expr

    def redo(self) -> Basic:
        if self.can_redo:
            self._index += 1
            self._notify()
        return self.expr

    def on_change(self, callback: Callable[[Basic], None]) -> Callable[[Basic], None]:
        """Call ``callback(expr)`` after every change.  Returns ``callback``."""
        self._listeners.append(callback)
        return callback

    # -- editing ------------------------------------------------------------

    def get(self, path: PathLike) -> Basic:
        return get_at(self.expr, self._path(path))

    def replace(self, path: PathLike, new: Union[Basic, str]) -> Basic:
        """Replace the node at ``path`` with ``new`` (parsed if a string)."""
        new_expr = self.parse(new) if isinstance(new, str) else sympify(new)
        return self._commit(replace_at(self.expr, self._path(path), new_expr))

    def delete(self, path: PathLike) -> Basic:
        """Remove the node at ``path`` from its parent's arguments."""
        return self._commit(delete_at(self.expr, self._path(path)))

    def apply(self, path: PathLike, op: Union[str, Callable]) -> Basic:
        """Apply a registered op (by name) or a callable to the node at ``path``."""
        if isinstance(op, str):
            try:
                func = self.ops[op].func
            except KeyError:
                raise ValueError(f"Unknown operation: {op!r}") from None
        else:
            func = op
        p = self._path(path)
        return self._commit(replace_at(self.expr, p, sympify(func(get_at(self.expr, p)))))

    def namespace(self) -> Dict[str, Any]:
        """Symbols, matrix symbols, indexed bases and undefined functions of
        the current expression, by name, so that typed input reuses them
        (assumptions included)."""
        ns: Dict[str, Any] = {}
        for s in self.expr.atoms(Symbol, MatrixSymbol, IndexedBase):
            if isinstance(s, Dummy):
                continue
            name = s.label if isinstance(s, IndexedBase) else s.name
            ns.setdefault(str(name), s)
        for f in self.expr.atoms(AppliedUndef):
            ns.setdefault(f.func.__name__, f.func)
        return ns

    def parse(self, src: str) -> Basic:
        """Parse user input in the context of the current expression."""
        src = (src or "").strip()
        if not src:
            raise ValueError("Empty input")
        transformations = standard_transformations + (convert_xor,)
        if self.parser == "implicit":
            transformations = transformations + (implicit_multiplication_application,)
        try:
            return sympify(parse_expr(src, local_dict=self.namespace(), transformations=transformations))
        except Exception as exc:  # SyntaxError, TokenError, TypeError, ...
            raise ValueError(f"Could not parse {src!r}: {exc}") from None

    # -- serialisation ------------------------------------------------------

    def snapshot(self, error: Optional[str] = None) -> Dict[str, Any]:
        """JSON-able description of the current state for the front end."""
        self._seq += 1
        expr = self.expr
        tex, nodes = annotate(expr, **self.printer_settings)
        return {
            "seq": self._seq,
            "latex": tex,
            "latex_plain": plain_latex(expr, **self.printer_settings),
            "src": str(expr),
            "srepr": srepr(expr),
            "nodes": {
                format_path(path): {"src": str(node), "type": type(node).__name__}
                for path, node in nodes.items()
            },
            "can_undo": self.can_undo,
            "can_redo": self.can_redo,
            "ops": [{"name": op.name, "label": op.label} for op in self.ops.values()],
            "error": error,
        }

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Process a front-end message and return a snapshot.

        Messages: ``{"action": "replace", "path": "/0", "src": "y**2"}``,
        ``{"action": "apply", "path": "/", "op": "expand"}``,
        ``{"action": "delete", "path": "/1"}``, ``{"action": "set", "src": ...}``,
        ``{"action": "undo"}``, ``{"action": "redo"}``, ``{"action": "snapshot"}``.
        Errors are reported in the snapshot's ``"error"`` field.
        """
        try:
            action = message.get("action")
            path = message.get("path", "/")
            if action == "replace":
                self.replace(path, str(message.get("src", "")))
            elif action == "set":
                self.replace("/", str(message.get("src", "")))
            elif action == "apply":
                self.apply(path, str(message.get("op", "")))
            elif action == "delete":
                self.delete(path)
            elif action == "undo":
                self.undo()
            elif action == "redo":
                self.redo()
            elif action == "snapshot":
                pass
            else:
                raise ValueError(f"Unknown action: {action!r}")
            return self.snapshot()
        except Exception as exc:
            return self.snapshot(error=f"{type(exc).__name__}: {exc}")

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _path(path: PathLike) -> Path:
        return parse_path(path) if isinstance(path, str) else tuple(path)

    @staticmethod
    def _coerce(expr) -> Basic:
        result = sympify(expr)
        if not isinstance(result, Basic):
            raise TypeError(f"Cannot edit {type(expr).__name__} objects")
        return result

    def _commit(self, expr: Basic) -> Basic:
        del self._history[self._index + 1:]
        self._history.append(expr)
        if len(self._history) > self.max_history:
            del self._history[: len(self._history) - self.max_history]
        self._index = len(self._history) - 1
        self._notify()
        return expr

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb(self.expr)

    def __repr__(self) -> str:
        return f"Document({self.expr!r})"

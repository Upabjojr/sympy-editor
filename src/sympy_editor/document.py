"""The editable document: current expression, history and edit operations.

:class:`Document` is the single source of truth shared by every front end
(Jupyter widget, Pyodide page, HTTP server).  Front ends talk to it through
:meth:`Document.handle`, which takes a JSON-able message and returns a JSON-able
snapshot, so the same JavaScript works everywhere.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Union

import io
import keyword
import tokenize

import sympy
from sympy import Basic, Dummy, IndexedBase, MatrixSymbol, Pow, S, Symbol, sympify, srepr
from sympy.core.function import AppliedUndef
from sympy.core.symbol import Str
from sympy.matrices.expressions import MatrixExpr
from sympy.matrices.matrixbase import MatrixBase
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

from .ops import Op, get_ops, node_kind
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

    def replace(self, path: PathLike, new: Union[Basic, str], reciprocal: bool = False) -> Basic:
        """Replace the node at ``path`` with ``new`` (parsed if a string, in the
        context of the node being replaced: new names in a matrix slot become
        ``MatrixSymbol``s of its shape).

        ``reciprocal``: ``new`` is the printed form of a denominator - the
        node at ``path`` is the tree's ``Pow(b, -n)`` shown as ``b**n`` under
        the fraction bar (``snapshot()`` flags such nodes) - so the node
        becomes ``1/new``.
        """
        p = self._path(path)
        if isinstance(new, str):
            new_expr = self.parse(new, context=get_at(self.expr, p))
        else:
            new_expr = sympify(new)
        if reciprocal:
            new_expr = S.One / new_expr
        return self._commit(replace_at(self.expr, p, new_expr))

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

    def parse(self, src: str, context: Optional[Basic] = None) -> Basic:
        """Parse user input in the context of the current expression.

        ``context`` is the node the input replaces, if any.  When it is a
        matrix (a ``MatrixExpr`` or an explicit matrix), names that do not
        occur in the expression are read as ``MatrixSymbol``s of its shape
        rather than as plain symbols - so typing ``C.T`` over ``B`` in
        ``A*B`` works, and ``C`` in a matrix product is a matrix.
        """
        src = (src or "").strip()
        if not src:
            raise ValueError("Empty input")
        transformations = standard_transformations + (convert_xor,)
        if self.parser == "implicit":
            transformations = transformations + (implicit_multiplication_application,)
        local = self.namespace()
        shape = getattr(context, "shape", None) if isinstance(context, (MatrixExpr, MatrixBase)) else None
        if shape is not None and len(shape) == 2:
            for name in self._new_names(src, local):
                local[name] = MatrixSymbol(name, *shape)
        try:
            return sympify(parse_expr(src, local_dict=local, transformations=transformations))
        except Exception as exc:  # SyntaxError, TokenError, TypeError, ...
            raise ValueError(f"Could not parse {src!r}: {exc}") from None

    @staticmethod
    def _new_names(src: str, local: Dict[str, Any]) -> List[str]:
        """The identifiers in ``src`` that ``parse_expr`` would turn into new
        symbols: not in ``local``, not SymPy names (``sin``, ``pi``, ``I``...),
        not attribute names (``.T``) and not called (``f(x)``)."""
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
        except (tokenize.TokenError, SyntaxError):
            return []
        names: List[str] = []
        prev = ""
        for i, tok in enumerate(tokens):
            nxt = tokens[i + 1].string if i + 1 < len(tokens) else ""
            if (tok.type == tokenize.NAME and not keyword.iskeyword(tok.string)
                    and prev != "." and nxt != "(" and tok.string not in local
                    and not hasattr(sympy, tok.string) and tok.string not in names):
                names.append(tok.string)
            prev = tok.string
        return names

    # -- symbols ------------------------------------------------------------

    def symbol_info(self) -> List[Dict[str, Any]]:
        """The names in the expression with what they stand for, for the
        front end's symbols panel: ``[{"name": "A", "type": "MatrixSymbol",
        "shape": ["2", "2"]}, {"name": "x", "type": "Symbol",
        "assumptions": ["positive"]}, ...]``."""
        out: List[Dict[str, Any]] = []
        for name, obj in sorted(self.namespace().items()):
            info: Dict[str, Any] = {"name": name}
            if isinstance(obj, MatrixSymbol):
                info.update(type="MatrixSymbol", shape=[str(obj.rows), str(obj.cols)])
            elif isinstance(obj, IndexedBase):
                info["type"] = "IndexedBase"
            elif isinstance(obj, Symbol):
                info.update(type="Symbol", assumptions=sorted(
                    k for k, v in obj.assumptions0.items() if v and k != "commutative"))
            elif isinstance(obj, type):
                info["type"] = "Function"
            else:
                info["type"] = type(obj).__name__
            out.append(info)
        return out

    def retype(self, name: str, kind: str, rows: Any = None, cols: Any = None) -> Basic:
        """Change what ``name`` stands for, everywhere in the expression:
        ``"Symbol"``, ``"MatrixSymbol"`` (``rows`` x ``cols``, symbolic
        dimensions allowed) or ``"Matrix"`` (an explicit ``rows`` x ``cols``
        matrix of ``name[i, j]`` elements).  Ancestors are rebuilt, so a
        product of two names becomes a ``MatMul`` when both become matrices;
        the reverse (a matrix back to a scalar under a transpose, say) fails
        with SymPy's error, which ``handle`` reports."""
        ns = self.namespace()
        if name not in ns:
            raise ValueError(f"No symbol named {name!r} in the expression")
        old = ns[name]
        if kind == "Symbol":
            new: Basic = Symbol(name)
        elif kind in ("MatrixSymbol", "Matrix"):
            old_shape = getattr(old, "shape", (2, 2))
            r = sympify(rows) if rows not in (None, "") else old_shape[0]
            c = sympify(cols) if cols not in (None, "") else old_shape[1]
            new = MatrixSymbol(name, r, c)
            if kind == "Matrix":
                new = new.as_explicit()
        else:
            raise ValueError(f"Unknown symbol type {kind!r}; use Symbol, MatrixSymbol or Matrix")
        if new == old:
            return self.expr
        new_expr = self._coerce(self.expr.xreplace({old: new}))
        # xreplace rebuilds without the constructors' checks, so a matrix
        # turned back into a scalar can leave a Transpose(Symbol) behind - a
        # tree nothing can print.  Refuse it rather than commit it.
        try:
            plain_latex(new_expr, **self.printer_settings)
        except Exception as exc:
            raise ValueError(f"{name} cannot become a {kind} where it is used: {exc}") from None
        return self._commit(new_expr)

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
            "nodes": {format_path(path): self._node_info(path, node) for path, node in nodes.items()},
            "symbols": self.symbol_info(),
            "can_undo": self.can_undo,
            "can_redo": self.can_redo,
            "ops": [{"name": op.name, "label": op.label, "kinds": list(op.kinds) if op.kinds else None}
                    for op in self.ops.values()],
            "error": error,
        }

    def _node_info(self, path: Path, node: Basic) -> Dict[str, Any]:
        """A node's entry in the snapshot.  ``node`` is what the printer
        printed at ``path``; for a denominator raised to a power that is the
        reciprocal of the tree's node, flagged so that an edit replaces the
        denominator rather than the whole ``Pow``."""
        info: Dict[str, Any] = {"src": str(node), "type": type(node).__name__, "kind": node_kind(node)}
        try:
            actual = get_at(self.expr, path)
        except (IndexError, AttributeError):
            return info
        if actual is not node and isinstance(actual, Pow) and isinstance(node, Pow) \
                and actual.base == node.base and actual.exp == -node.exp:
            info["reciprocal"] = True
        return info

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Process a front-end message and return a snapshot.

        Messages: ``{"action": "replace", "path": "/0", "src": "y**2"}`` (with
        ``"reciprocal": true`` for a node the snapshot flagged so),
        ``{"action": "apply", "path": "/", "op": "expand"}``,
        ``{"action": "delete", "path": "/1"}``, ``{"action": "set", "src": ...}``,
        ``{"action": "retype", "name": "A", "type": "MatrixSymbol", "rows": 2, "cols": 2}``,
        ``{"action": "undo"}``, ``{"action": "redo"}``, ``{"action": "snapshot"}``.
        Errors are reported in the snapshot's ``"error"`` field.
        """
        try:
            action = message.get("action")
            path = message.get("path", "/")
            if action == "replace":
                self.replace(path, str(message.get("src", "")), reciprocal=bool(message.get("reciprocal")))
            elif action == "retype":
                self.retype(str(message.get("name", "")), str(message.get("type", "")),
                            message.get("rows"), message.get("cols"))
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
        # srepr output names Str (a MatrixSymbol's name) which SymPy < 1.14
        # does not export: without it in scope, sympify reads Str('A') as an
        # undefined function of A, and the matrix symbol's name becomes "Str".
        result = sympify(expr, locals={"Str": Str}) if isinstance(expr, str) else sympify(expr)
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

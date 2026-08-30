"""The editable document: current expression, history and edit operations.

:class:`Document` is the single source of truth shared by every front end
(Jupyter widget, Pyodide page, HTTP server).  Front ends talk to it through
:meth:`Document.handle`, which takes a JSON-able message and returns a JSON-able
snapshot, so the same JavaScript works everywhere.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple as TypingTuple, Union

import io
import keyword
import tokenize

import sympy
from sympy import Add, Basic, Dummy, Function, IndexedBase, MatrixSymbol, Mul, Symbol, Tuple, sympify, srepr
from sympy.core.function import AppliedUndef
from sympy.core.symbol import Str
from sympy.matrices.expressions import MatrixExpr
from sympy.matrices import MatrixBase
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

from .ops import KIND_LABELS, Op, get_ops, node_kind, node_kinds
from .printer import (
    Path,
    annotate,
    annotate_str,
    delete_at,
    format_path,
    get_at,
    delete_range,
    extract_range,
    insert_at,
    is_insertable,
    is_rangeable,
    replace_range,
    parse_path,
    plain_latex,
    rebuild,
    replace_at,
    view_parts,
)

__all__ = ["Document", "SYMBOL_TYPES", "Interrupted", "interrupt_thread"]


class Interrupted(Exception):
    """Raised inside a computation the user interrupted (see
    :func:`interrupt_thread`); :meth:`Document.handle` reports it like any
    other error, with the document unchanged."""


def interrupt_thread(ident: int) -> bool:
    """Raise :class:`Interrupted` asynchronously in the thread ``ident``
    (``threading.get_ident()`` of the thread running a ``Document`` message).

    SymPy is pure Python, so the exception is delivered at the next bytecode
    the thread executes.  Returns whether a thread was found.  Not available
    in Pyodide (no threads there: the runtime is restarted instead)."""
    import ctypes
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), ctypes.py_object(Interrupted))
    if res > 1:   # more than one thread affected: undo (should not happen)
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), None)
        return False
    return res == 1

#: Types a name can be declared as (see :meth:`Document.declare`).
SYMBOL_TYPES = ("Symbol", "MatrixSymbol", "Matrix", "Function")

PathLike = Union[str, Path]


def _split_args(text: str) -> List[str]:
    """Split ``"x, (a, b), f(1, 2)"`` on top-level commas."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return [a.strip() for a in out if a.strip()]


#: Names offered first in the function box (any callable of sympy works).
COMMON_FUNCTIONS = (
    "simplify", "expand", "factor", "cancel", "apart", "together", "collect", "trigsimp", "expand_trig",
    "powsimp", "logcombine", "expand_log", "radsimp", "nsimplify", "refine", "diff", "integrate", "series",
    "limit", "summation", "solve", "solveset", "roots", "subs", "evalf", "N", "doit", "rewrite", "sqrt",
    "exp", "log", "Abs", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh", "conjugate",
    "re", "im", "arg", "gamma", "factorial", "binomial", "Rational", "Poly", "degree", "LC", "gcd", "lcm",
    "primitive", "sqf", "discriminant", "resultant", "det", "inv", "T", "transpose", "trace", "rank",
    "eigenvals", "eigenvects", "nullspace", "rref", "norm", "Matrix", "eye", "zeros", "ones",
)


#: Parameter prompts for functions whose Python signature does not say enough
#: (``*args``): name -> list of (label, kind) with kind "symbol" or "text".
PARAM_HINTS: Dict[str, List] = {
    # name -> [(label, kind, optional, gap-fill default)]
    "subs": [("old", "text", False, None), ("new", "text", False, None)],
    "diff": [("variable", "symbol", False, None), ("n (times)", "text", True, None)],
    "integrate": [("variable", "symbol", False, None), ("lower limit", "text", True, None), ("upper limit", "text", True, None)],
    "series": [("variable", "symbol", False, None), ("x0", "text", True, "0"), ("n (order)", "text", True, None)],
    "limit": [("variable", "symbol", False, None), ("x0", "text", False, None), ("dir", "text", True, None)],
    "solve": [("symbol", "symbol", False, None)],
    "solveset": [("symbol", "symbol", False, None), ("domain", "text", True, None)],
    "roots": [("symbol", "symbol", False, None)],
    "collect": [("symbol", "symbol", False, None)],
    "apart": [("symbol", "symbol", False, None)],
    "rewrite": [("function", "text", False, None)],
    "summation": [("variable", "symbol", False, None), ("lower", "text", False, None), ("upper", "text", False, None)],
    "evalf": [("n (digits)", "text", True, None)],
    "N": [("n (digits)", "text", True, None)],
    "Poly": [("generator", "symbol", False, None)],
    "degree": [("generator", "symbol", False, None)],
    "LC": [("generator", "symbol", False, None)],
}
SYMBOL_PARAM_NAMES = {"symbol", "symbols", "x", "var", "variable", "variables", "gen", "gens", "sym", "syms", "s", "dep", "wrt"}


def function_signature(name: str, target: Optional[Basic] = None) -> Dict[str, Any]:
    """Parameter prompts for ``name`` (a sympy callable, or a method/attribute
    of ``target`` when ``name`` starts with ``.``): ``{"name", "params":
    [{"name", "kind": "symbol"|"text", "default"}], "doc", "callable"}``."""
    import inspect
    dotted = name.startswith(".")
    bare = name.lstrip(".")
    if not dotted and callable(getattr(sympy, bare, None)):
        fn = getattr(sympy, bare)
        skip = 1
    elif target is not None and not bare.startswith("_") and hasattr(target, bare):
        fn = getattr(target, bare)
        skip = 0
        if not callable(fn):
            return {"name": name, "params": [], "doc": f"attribute of {type(target).__name__}", "callable": False}
    else:
        raise ValueError(f"Unknown SymPy function or method: {bare!r}")
    doc = (inspect.getdoc(fn) or "").strip().split("\n", 1)[0][:160]
    if bare in PARAM_HINTS:
        params = [{"name": label, "kind": kind, "default": fill, "optional": optional}
                  for label, kind, optional, fill in PARAM_HINTS[bare]]
        return {"name": name, "params": params, "doc": doc, "callable": True, "hinted": True}
    params = []
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {"name": name, "params": [{"name": "arguments", "kind": "text", "default": None, "optional": True}],
                "doc": doc, "callable": True, "hinted": False}
    for i, prm in enumerate(sig.parameters.values()):
        if i < skip or prm.name in ("self", "cls"):
            continue
        if prm.kind == prm.VAR_KEYWORD:
            continue
        if prm.kind == prm.VAR_POSITIONAL:
            params.append({"name": prm.name, "kind": "symbol" if prm.name in SYMBOL_PARAM_NAMES else "text",
                           "default": None, "optional": True, "varargs": True})
            continue
        default = None if prm.default is inspect.Parameter.empty else repr(prm.default)
        kind = "symbol" if prm.name in SYMBOL_PARAM_NAMES else "text"
        params.append({"name": prm.name, "kind": kind, "default": default, "optional": default is not None})
    return {"name": name, "params": params[:6], "doc": doc, "callable": True, "hinted": False}


def sympy_functions() -> List[str]:
    """Names of SymPy's public callables, the common ones first."""
    names = [n for n in dir(sympy) if not n.startswith("_") and callable(getattr(sympy, n, None))]
    rest = sorted(n for n in names if n not in COMMON_FUNCTIONS)
    return [n for n in COMMON_FUNCTIONS if n in names or n in ("subs", "evalf", "doit", "rewrite", "det", "inv", "T", "transpose", "trace", "rank", "eigenvals", "eigenvects", "nullspace", "rref", "norm", "degree", "LC")] + rest


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
    symbols
        Symbol-like objects (``Symbol``, ``MatrixSymbol``, undefined
        ``Function``; ``srepr`` strings are accepted too) put in scope for
        typed input before they occur in the expression; see :meth:`declare`.
    history, index, labels
        A saved undo history (expressions or ``srepr`` strings, oldest first),
        the position of the current expression in it and what produced each
        step, as given by :meth:`export`; ``expr`` is ignored when a history
        is given.
    """

    def __init__(
        self,
        expr: Union[Basic, str],
        *,
        printer_settings: Optional[Dict[str, Any]] = None,
        parser: str = "strict",
        ops: Optional[Dict[str, Op]] = None,
        max_history: int = 200,
        symbols=(),
        history=None,
        index: Optional[int] = None,
        labels=None,
    ):
        if parser not in ("strict", "implicit"):
            raise ValueError("parser must be 'strict' or 'implicit'")
        self.printer_settings = dict(printer_settings or {})
        self.parser = parser
        self.ops: Dict[str, Op] = dict(ops) if ops is not None else get_ops()
        self.max_history = max_history
        self._history: List[Basic] = []
        #: What produced each step of the history (None for the start, or an
        #: edit made from Python): set by ``handle`` from the message.
        self._labels: List[Optional[str]] = []
        self._action_label: Optional[str] = None
        self._index = -1
        self._seq = 0
        self._listeners: List[Callable[[Basic], None]] = []
        #: Declared names (see :meth:`declare`): name -> object.
        self.declared: Dict[str, Any] = {}
        self.last_note: Optional[str] = None
        for obj in symbols:
            if isinstance(obj, str):  # srepr text; Str is not exported by SymPy < 1.14
                obj = sympify(obj, locals={"Str": Str})
            self.declared[self._symbol_name(obj)] = obj
        if history:
            self._history = [self._coerce(e) for e in history][-self.max_history:]
            given = list(labels or [])[-len(self._history):]
            self._labels = [None] * (len(self._history) - len(given)) + [None if not l else str(l) for l in given]
            self._index = len(self._history) - 1 if index is None else max(0, min(int(index), len(self._history) - 1))
        else:
            self._commit(self._coerce(expr))

    def export(self) -> Dict[str, Any]:
        """The state that :class:`Document` takes back: ``{"history": [srepr,
        ...], "index", "symbols": [srepr of the declared names]}`` (a
        session's editing history, kept by the front end)."""
        return {"history": [srepr(e) for e in self._history], "index": self._index, "labels": list(self._labels),
                "symbols": [srepr(obj) for obj in self.declared.values()]}

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

    def goto(self, index: int) -> Basic:
        """Make step ``index`` of the history (0 = oldest) the current
        expression, like a series of undos or redos."""
        index = int(index)
        if not 0 <= index < len(self._history):
            raise ValueError(f"No history step {index} (there are {len(self._history)})")
        if index != self._index:
            self._index = index
            self._notify()
        return self.expr

    def history_labels(self) -> Dict[str, Any]:
        """The history for the front end's list: ``{"labels": [str of every
        step, oldest first], "index", "steps": [{"latex", "nodes"}]}`` -
        ``steps`` carry the annotated LaTeX and the node table of each step,
        so consecutive steps can be shown as a diff (renders are cached per
        expression)."""
        steps = []
        for e in self._history:
            steps.append(self._render_cache_get(e))
        return {"labels": [str(e) for e in self._history], "index": self._index, "steps": steps,
                "actions": list(self._labels)}

    def _render_cache_get(self, expr: Basic) -> Dict[str, Any]:
        cache = self.__dict__.setdefault("_render_cache", {})
        try:
            hit = cache.get(expr)
        except TypeError:          # unhashable
            hit = None
        if hit is None:
            tex, nodes = annotate(expr, **self.printer_settings)
            hit = {"latex": tex, "nodes": {format_path(p): {"src": str(n), "type": type(n).__name__, "nargs": len(n.args)}
                                            for p, n in nodes.items()}}
            try:
                if len(cache) > 400:
                    cache.clear()
                cache[expr] = hit
            except TypeError:
                pass
        return hit

    def on_change(self, callback: Callable[[Basic], None]) -> Callable[[Basic], None]:
        """Call ``callback(expr)`` after every change.  Returns ``callback``."""
        self._listeners.append(callback)
        return callback

    # -- the view tree ------------------------------------------------------
    # The path functions of ``printer`` bound to the printer settings, which
    # decide the virtual parts of the view tree (``root_notation``...).

    def _get_at(self, expr, path):
        return get_at(expr, path, self.printer_settings)

    def _replace_at(self, expr, path, new):
        return replace_at(expr, path, new, self.printer_settings)

    def _delete_at(self, expr, path):
        return delete_at(expr, path, self.printer_settings)

    def _insert_at(self, expr, path, index, new):
        return insert_at(expr, path, index, new, self.printer_settings)

    def _extract_range(self, expr, path, indices):
        return extract_range(expr, path, indices, self.printer_settings)

    def _replace_range(self, expr, path, indices, new):
        return replace_range(expr, path, indices, new, self.printer_settings)

    def _delete_range(self, expr, path, indices):
        return delete_range(expr, path, indices, self.printer_settings)

    def _parts(self, node) -> Optional[List[Tuple[str, Basic]]]:
        return view_parts(node, self.printer_settings)

    # -- editing ------------------------------------------------------------

    def get(self, path: PathLike) -> Basic:
        return self._get_at(self.expr, self._path(path))

    def replace(self, path: PathLike, new: Union[Basic, str], children=None) -> Basic:
        """Replace the node at ``path`` with ``new`` (parsed if a string, in the
        context of the node being replaced: new names in a matrix slot become
        ``MatrixSymbol``s of its shape).

        A path may address a virtual part of the view tree (the ``1`` of
        ``1/n`` is ``/n``, the denominator ``2 e`` of ``1/(2e)`` is ``/d``, the
        product after the minus of ``- 2 x`` is ``/neg``; see
        :func:`~sympy_editor.printer.view_parts`): the node is rebuilt around
        the new part.

        ``children``: argument indices of the node at ``path`` (an ``Add``,
        ``Mul``...) - the *range* of arguments they form is replaced by
        ``new`` instead of the node itself.
        """
        p = self._path(path)
        context = self._get_at(self.expr, p)
        if children is not None:
            context = self._extract_range(self.expr, p, children)
        if isinstance(new, str):
            new_expr = self.parse(new, context=context)
        else:
            new_expr = sympify(new)
        if children is not None:
            return self._commit(self._replace_range(self.expr, p, children, new_expr))
        return self._commit(self._replace_at(self.expr, p, new_expr))

    def delete(self, path: PathLike, children=None) -> Basic:
        """Remove the node at ``path`` from its parent's arguments (or, with
        ``children``, those arguments of the node at ``path``)."""
        if children is not None:
            return self._commit(self._delete_range(self.expr, self._path(path), children))
        return self._commit(self._delete_at(self.expr, self._path(path)))

    def insert(self, path: PathLike, index: int, new: Union[Basic, str],
               left: Optional[int] = None, right: Optional[int] = None,
               attach: Optional[str] = None) -> Basic:
        """Type at a caret between the arguments of the node at ``path``.

        ``left``/``right`` are the indices of the arguments next to the caret
        (``index`` is where a plain new argument would go) and ``attach``
        (``"left"``/``"right"``) which of them the caret belongs to.

        The text is *spliced* between its neighbours like in a text editor:
        an operator typed at a junction is used as written, a missing one
        means juxtaposition (multiplication) with the attached neighbour, and
        ``+``/``-`` bind at the level of the sum - in a product they split it
        at the caret (``x*z`` with ``+y+`` typed between gives ``x + y + z``).
        ``,`` makes a new argument.  A SymPy object is inserted as is.
        """
        p = self._path(path)
        parent = self._get_at(self.expr, p)
        args = parent.args
        if not isinstance(new, str):
            return self._commit(self._insert_at(self.expr, p, int(index), sympify(new)))
        text = new.strip()
        if not text:
            raise ValueError("Empty input")
        ops = "+-*/^,"
        lead = text[0] if text[0] in ops else ""
        trail = text[-1] if text[-1] in ops else ""

        def parse(src, context=None):
            return self.parse(src, context=context if context is not None else parent)

        if lead == "," or trail == ",":
            return self._commit(self._insert_at(self.expr, p, int(index), parse(text.strip(",").strip())))
        n = len(args)
        L = left if left is not None and 0 <= left < n else None
        R = right if right is not None and 0 <= right < n else None
        is_sum, is_prod = bool(parent.is_Add), bool(parent.is_Mul)

        if not (is_sum or is_prod) or (L is None and R is None):
            # Function arguments, sets, or no neighbours: one neighbour at most.
            if lead in ("+", "-") and L is None and R is None:
                if is_sum:
                    return self._commit(self._insert_at(self.expr, p, int(index), parse(text)))
                return self._commit(self._replace_at(self.expr, p, parent + parse(text)))
            if L is None and R is None:
                if lead == "*":                     # "* c" with nothing next to the caret: multiply the node
                    return self._commit(self._replace_at(self.expr, p, parent * parse(text[1:].strip())))
                return self._commit(self._insert_at(self.expr, p, int(index), parse(text)))
            side, k = ("left", L) if (L is not None and attach != "right") else ("right", R if R is not None else L)
            nb = args[k]
            if lead == "*" and side == "left":
                combined = f"({nb})*{text[1:].strip()}"
            elif trail == "*" and side == "right":
                combined = f"{text[:-1].strip()}*({nb})"
            elif lead in ("/", "^") and side == "left":
                combined = f"({nb}){text}"
            elif lead in ("+", "-"):
                combined = f"({nb}){text}"
            elif trail in ("+", "-"):
                combined = f"{text}({nb})"
            else:
                combined = f"({nb})*({text})" if side == "left" else f"({text})*({nb})"
            return self._commit(self._replace_at(self.expr, p + (k,), parse(combined, nb)))

        # Sums and products: splice the text between its neighbours.
        def piece(indices):
            sub = args[indices[0]] if len(indices) == 1 else rebuild(parent, [args[i] for i in indices])
            return f"({sub})"

        if lead in ("+", "-"):
            left_idx = (list(range(0, L + 1)) if is_prod else [L]) if L is not None else []
        else:
            left_idx = [L] if L is not None and attach != "right" else []
        if trail in ("+", "-"):
            right_idx = (list(range(R, n)) if is_prod else [R]) if R is not None else []
        else:
            right_idx = [R] if R is not None and (attach == "right" or not left_idx) else []
        combined = text
        if left_idx:
            combined = piece(left_idx) + ("" if lead else "*") + combined
        if right_idx:
            combined = combined + ("" if trail else "*") + piece(right_idx)
        new_expr = parse(combined)
        consumed = sorted(set(left_idx + right_idx))
        if not consumed:
            return self._commit(self._insert_at(self.expr, p, int(index), new_expr))
        return self._commit(self._replace_range(self.expr, p, consumed, new_expr))

    def apply(self, path: PathLike, op: Union[str, Callable], children=None) -> Basic:
        """Apply a registered op (by name) or a callable to the node at ``path``
        (or, with ``children``, to the range of those arguments of it)."""
        if isinstance(op, str):
            try:
                func = self.ops[op].func
            except KeyError:
                raise ValueError(f"Unknown operation: {op!r}") from None
        else:
            func = op
        p = self._path(path)
        if children is not None:
            result = sympify(func(self._extract_range(self.expr, p, children)))
            return self._commit(self._replace_range(self.expr, p, children, result))
        return self._commit(self._replace_at(self.expr, p, sympify(func(self._get_at(self.expr, p)))))

    def call(self, path: PathLike, func: str, children=None) -> Basic:
        """Apply a SymPy function or a method to the node at ``path`` (or to
        the range ``children`` of it): ``func`` is ``"diff(x)"``,
        ``"series(x, 0, 5)"``, ``"subs(x, 1)"``, ``"factor"``, ``".T"``,
        ``".det()"``...  A name that is a SymPy function is called as
        ``name(node, *args)``; a name starting with ``.`` (or that is only an
        attribute of the node) is looked up on the node.  Extra arguments are
        parsed in the expression's namespace.
        """
        m = re.match(r"^\s*(\.?)([A-Za-z_][A-Za-z_0-9]*)\s*(?:\((.*)\))?\s*$", func or "", re.S)
        if not m:
            raise ValueError(f"Not a function call: {func!r} (try diff(x), series(x, 0, 5) or .T)")
        dotted, name, argsrc = m.group(1), m.group(2), m.group(3)
        p = self._path(path)
        target = self._extract_range(self.expr, p, children) if children is not None else self._get_at(self.expr, p)
        args = [self.parse(a, context=target) for a in _split_args(argsrc)] if argsrc else []
        fn = None
        if not dotted and not name.startswith("_") and callable(getattr(sympy, name, None)):
            fn = getattr(sympy, name)
            result = fn(target, *args)
        elif not name.startswith("_") and hasattr(target, name):
            attr = getattr(target, name)
            result = attr(*args) if callable(attr) else attr
        else:
            raise ValueError(f"Unknown SymPy function or method: {name!r}")
        if isinstance(result, (list, tuple, set, frozenset)):
            result = sympy.FiniteSet(*result) if all(isinstance(r, Basic) for r in result) else sympify(result)
        result = sympify(result)
        if not isinstance(result, Basic):
            raise ValueError(f"{name} returned {type(result).__name__}, not an expression")
        if children is not None:
            return self._commit(self._replace_range(self.expr, p, children, result))
        return self._commit(self._replace_at(self.expr, p, result))

    def isolate(self, path: PathLike, children=None) -> Basic:
        """Make the node at ``path`` (or the range ``children`` of it) the
        whole expression, dropping everything around it."""
        p = self._path(path)
        sub = self._extract_range(self.expr, p, children) if children is not None else self._get_at(self.expr, p)
        return self._commit(sub)

    def _keep_candidates(self, node: Basic) -> List[TypingTuple[Union[int, str], Basic]]:
        """What ``unwrap`` could leave in the node's place, as (``keep`` key,
        value): the virtual parts of a node shown as a fraction or after a
        minus sign, otherwise the arguments that are expressions (the limits
        of an integral are a ``Tuple``, not something that can stand alone).
        More than one means there is a real choice to offer - ``x**2`` can
        leave the base or the exponent - rather than a natural default."""
        parts = self._parts(node) or ()
        source: Iterable[TypingTuple[Union[int, str], Any]] = (
            [(name, value) for name, value in parts] if parts else list(enumerate(node.args)))
        return [(key, value) for key, value in source
                if isinstance(value, Basic) and not isinstance(value, Tuple)]

    def unwrap(self, path: PathLike, keep: Union[int, str, None] = None) -> Basic:
        """Remove the node at ``path`` but keep one of its arguments in its
        place: ``cos(x)`` becomes ``x``, ``Integral(f, (x, a, b))`` becomes
        ``f``, ``x**2`` becomes ``x``.  ``keep`` is the index of the argument
        to keep - or the name of a virtual part (``"n"``, ``"d"``, ``"neg"``)
        for a node shown as a fraction or after a minus sign; by default the
        natural one (the function body, the base, the first argument, the
        product after the sign).  A sum, a product with several terms or a
        fraction needs ``keep``.
        """
        p = self._path(path)
        node = self._get_at(self.expr, p)
        parts = dict(self._parts(node) or ())
        args = node.args
        if not args and not parts:
            raise ValueError(f"{node} has nothing inside to keep")
        if keep is None:
            if parts and "neg" in parts:
                keep = "neg"
            elif parts:
                raise ValueError(f"{node} is a fraction: select the numerator or denominator to keep, press ↑, then Backspace")
            elif isinstance(node, (Add, Mul)) and len(args) > 1:   # MatAdd/MatMul are Add/Mul too
                raise ValueError(f"{type(node).__name__} has {len(args)} terms: select the one to keep, press ↑, then Backspace "
                                 "(or Delete the others)")
            else:
                keep = 0
        if isinstance(keep, str) and not keep.lstrip("-").isdigit():
            if keep not in parts:
                raise ValueError(f"Invalid part {keep!r} for {node}")
            kept = parts[keep]
        else:
            if not 0 <= int(keep) < len(args):
                raise ValueError(f"Invalid argument {keep} for {node}")
            kept = args[int(keep)]
        if isinstance(kept, Tuple):
            raise ValueError(f"Cannot keep {kept}: it is not an expression")
        return self._commit(self._replace_at(self.expr, p, kept))

    def wrap(self, path: PathLike, func: str, args: str = "", children=None) -> Basic:
        """Put the node at ``path`` (or the range ``children`` of it) inside a
        function - the inverse of :meth:`unwrap`.  ``x`` wrapped in ``"cos"``
        becomes ``cos(x)``, in ``"sqrt"`` ``sqrt(x)``; ``"Integral"`` with
        ``args="x"`` gives ``Integral(x, x)``.  ``func`` is a SymPy function, a
        function already in the expression or declared, or any other name - an
        unknown one becomes an undefined ``Function``, so ``"f"`` gives
        ``f(x)``.  ``args`` holds any further arguments, parsed in the
        expression's namespace and placed after the node.

        Nothing is evaluated: wrapping ``4`` in ``sqrt`` gives ``sqrt(4)``, not
        ``2`` - this builds the expression, :meth:`call` computes with it.
        """
        if not (func or "").strip():
            raise ValueError("No function to wrap in (cos, sqrt, Integral, f...)")
        m = re.match(r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*(?:\((.*)\))?\s*$", func or "", re.S)
        if not m:
            raise ValueError(f"Not a function name: {func!r} (wrap in cos, sqrt, Integral, f...)")
        name, in_call = m.group(1), m.group(2)
        argsrc = ", ".join(a for a in ((args or "").strip(), (in_call or "").strip()) if a)
        p = self._path(path)
        target = self._extract_range(self.expr, p, children) if children is not None else self._get_at(self.expr, p)
        extra = [self.parse(a, context=target) for a in _split_args(argsrc)] if argsrc else []
        known = self.namespace().get(name)
        fn = known if callable(known) else getattr(sympy, name, None)
        if not callable(fn) or name.startswith("_"):
            fn = Function(name)      # not a function anywhere: an undefined one, f(x)
        try:
            result = sympify(fn(target, *extra))
            # Wrapping builds, it does not compute: when the node is no longer
            # inside what came back (sqrt of 4 is 2, cos of 0 is 1), keep the
            # application unevaluated so the editor shows what was asked for.
            if target not in getattr(result, "args", ()):
                with sympy.evaluate(False):
                    result = sympify(fn(target, *extra))
        except Exception as exc:     # noqa: BLE001 - a wrong number of arguments, mostly
            raise ValueError(f"Cannot wrap in {name}: {exc}") from None
        if not isinstance(result, Basic):
            raise ValueError(f"{name} returned {type(result).__name__}, not an expression")
        if children is not None:
            return self._commit(self._replace_range(self.expr, p, children, result))
        return self._commit(self._replace_at(self.expr, p, result))

    def extend(self, path: PathLike, side: str, src: str) -> Basic:
        """Type next to the node at ``path`` (an entry of a matrix, the base of a
        power...): ``src`` is combined with the node - after it for
        ``side="after"``, before it otherwise.  An operator in ``src`` at the
        junction is used (``"+ 1"`` gives ``node + 1``); without one the two
        are multiplied (``"y"`` after ``x`` gives ``x*y``)."""
        p = self._path(path)
        node = self._get_at(self.expr, p)
        text = (src or "").strip()
        if not text:
            raise ValueError("Empty input")
        node_src = f"({node})"
        if side == "after":
            joiner = " " if text[0] in "+-*/^" else "*"
            combined = node_src + joiner + text
        else:
            joiner = " " if text[-1] in "+-*/^(" else "*"
            combined = text + joiner + node_src
        return self._commit(self._replace_at(self.expr, p, self.parse(combined, context=node)))

    def used_symbols(self) -> Dict[str, Any]:
        """Symbols, matrix symbols, indexed bases and undefined functions
        occurring in the current expression, by name."""
        ns: Dict[str, Any] = {}
        for s in self.expr.atoms(Symbol, MatrixSymbol, IndexedBase):
            if not isinstance(s, Dummy):
                ns.setdefault(self._symbol_name(s), s)
        for f in self.expr.atoms(AppliedUndef):
            ns.setdefault(f.func.__name__, f.func)
        return ns

    def namespace(self) -> Dict[str, Any]:
        """Names available to typed input: everything occurring in the
        expression (assumptions included) plus the declared names."""
        ns = self.used_symbols()
        for name, obj in self.declared.items():
            ns.setdefault(name, obj)
        return ns

    @staticmethod
    def _symbol_name(obj) -> str:
        if isinstance(obj, IndexedBase):
            return str(obj.label)
        if isinstance(obj, type):  # undefined function class
            return obj.__name__
        return str(obj.name)

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
        # `name` in backticks is a variable even if SymPy has a function or a
        # constant of that name (`sin`, `E`, `gamma`...); the backticks go.
        for name in set(re.findall(r"`([A-Za-z_][A-Za-z_0-9]*)`", src)):
            local.setdefault(name, Symbol(name))
        src = re.sub(r"`([A-Za-z_][A-Za-z_0-9]*)`", r"\1", src)
        shape = getattr(context, "shape", None) if isinstance(context, (MatrixExpr, MatrixBase)) else None
        if shape is not None and len(shape) == 2:
            for name in self._new_names(src, local):
                local[name] = MatrixSymbol(name, *shape)
        self.last_note = self._collision_note(src, local)
        try:
            return sympify(parse_expr(src, local_dict=local, transformations=transformations))
        except Exception as exc:  # SyntaxError, TokenError, TypeError, ...
            raise ValueError(f"Could not parse {src!r}: {exc}") from None

    @staticmethod
    def _collision_note(src: str, local: Dict[str, Any]) -> Optional[str]:
        """A hint when a typed name that is not a known symbol was read as one
        of SymPy's functions or constants (``E``, ``I``, ``gamma``...)."""
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
        except (tokenize.TokenError, SyntaxError):
            return None
        taken = []
        for i, tok in enumerate(tokens):
            if tok.type != tokenize.NAME or keyword.iskeyword(tok.string) or tok.string in local:
                continue
            prev = tokens[i - 1].string if i else ""
            nxt = tokens[i + 1].string if i + 1 < len(tokens) else ""
            if prev == "." or nxt == "(":          # attributes and calls are meant as functions
                continue
            obj = getattr(sympy, tok.string, None)
            if obj is not None and tok.string not in taken:
                taken.append(tok.string)
        if not taken:
            return None
        names = ", ".join(taken)
        return (f"{names}: read as SymPy's {'constant/function' if len(taken) == 1 else 'constants/functions'}; "
                f"for a variable write `{taken[0]}` in backticks or declare it in Symbols")

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
        used = self.used_symbols()
        for name, obj in sorted(self.namespace().items()):
            info: Dict[str, Any] = {"name": name, "used": name in used}
            if isinstance(obj, MatrixSymbol):
                info.update(type="MatrixSymbol", shape=[str(obj.rows), str(obj.cols)])
            elif isinstance(obj, IndexedBase):
                info["type"] = "IndexedBase"
            elif isinstance(obj, Symbol):
                info.update(type="Symbol", assumptions=sorted(
                    k for k, v in obj.assumptions0.items() if v and k != "commutative"))
            elif isinstance(obj, type):
                info["type"] = "Function"
            elif isinstance(obj, MatrixBase):
                info.update(type="Matrix", shape=[str(d) for d in obj.shape])
            else:
                info["type"] = type(obj).__name__
            out.append(info)
        return out

    def _make(self, name: str, kind: str, rows: Any, cols: Any, assumptions: Any, old: Any = None) -> Any:
        """The object a name is (re)declared as."""
        if isinstance(assumptions, str):
            assumptions = [a.strip() for a in assumptions.split(",")]
        if isinstance(assumptions, dict):
            flags = {str(k): bool(v) for k, v in assumptions.items() if v is not None}
        else:
            flags = {str(a): True for a in (assumptions or []) if str(a).strip()}
        if kind == "Symbol":
            return Symbol(name, **flags)
        if kind == "Function":
            return Function(name)
        if kind in ("MatrixSymbol", "Matrix"):
            old_shape = getattr(old, "shape", (2, 2))
            ns = self.namespace()
            r = sympify(rows, locals=ns) if rows not in (None, "") else old_shape[0]
            c = sympify(cols, locals=ns) if cols not in (None, "") else old_shape[1]
            new = MatrixSymbol(name, r, c)
            return new.as_explicit() if kind == "Matrix" else new
        raise ValueError(f"Unknown symbol type {kind!r}; use one of {', '.join(SYMBOL_TYPES)}")

    def declare(self, name: str, kind: str = "Symbol", rows: Any = None, cols: Any = None,
                assumptions: Any = None) -> Any:
        """Put ``name`` in scope for typed input as a ``Symbol`` (with
        ``assumptions``, e.g. ``["positive"]``), a ``MatrixSymbol`` /
        explicit ``Matrix`` of ``rows`` x ``cols`` or an undefined
        ``Function``.  A name already in the expression is changed
        everywhere (see :meth:`retype`)."""
        name = (name or "").strip()
        if not name.isidentifier():
            raise ValueError(f"Invalid symbol name: {name!r}")
        if name in self.namespace():
            return self.retype(name, kind, rows, cols, assumptions)
        new = self._make(name, kind, rows, cols, assumptions)
        self.declared[name] = new
        return new

    def undeclare(self, name: str) -> None:
        """Forget a declared name (it must not occur in the expression)."""
        if name in self.used_symbols():
            raise ValueError(f"{name} occurs in the expression; remove it there first")
        if name not in self.declared:
            raise ValueError(f"No declared symbol named {name!r}")
        del self.declared[name]

    def retype(self, name: str, kind: str, rows: Any = None, cols: Any = None,
               assumptions: Any = None) -> Basic:
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
        new = self._make(name, kind, rows, cols, assumptions, old)
        if new == old:
            return self.expr
        if name not in self.used_symbols():
            self.declared[name] = new
            return self.expr
        if isinstance(old, type) or isinstance(new, type):
            raise ValueError(f"{name} is used as a {'function' if isinstance(old, type) else 'symbol'}; "
                             "remove those uses before changing it")
        new_expr = self._coerce(self.expr.xreplace({old: new}))
        # xreplace rebuilds without the constructors' checks, so a matrix
        # turned back into a scalar can leave a Transpose(Symbol) behind - a
        # tree nothing can print.  Refuse it rather than commit it.
        try:
            plain_latex(new_expr, **self.printer_settings)
        except Exception as exc:
            raise ValueError(f"{name} cannot become a {kind} where it is used: {exc}") from None
        # Only a change that went through is recorded: a refused one must not
        # leave the panel (and typed input) believing the name has changed.
        self.declared[name] = new
        return self._commit(new_expr)

    # -- serialisation ------------------------------------------------------

    def snapshot(self, error: Optional[str] = None, expr: Optional[Basic] = None) -> Dict[str, Any]:
        """JSON-able description of the current state for the front end
        (of ``expr`` instead of the current expression, for a preview)."""
        self._seq += 1
        if expr is None:
            expr = self.expr
        tex, nodes = annotate(expr, **self.printer_settings)
        return {
            "seq": self._seq,
            "latex": tex,
            "latex_plain": plain_latex(expr, **self.printer_settings),
            "src": str(expr),
            "spans": {path: list(span) for path, span in annotate_str(expr)[1].items()},
            "srepr": srepr(expr),
            "declared": [srepr(obj) for obj in self.declared.values()],   # to rebuild the document elsewhere
            "nodes": {format_path(path): self._node_info(path, node, expr) for path, node in nodes.items()},
            "symbols": self.symbol_info(),
            "can_undo": self.can_undo,
            "can_redo": self.can_redo,
            "ops": [{"name": op.name, "label": op.label, "kinds": list(op.kinds) if op.kinds else None}
                    for op in self.ops.values()],
            "kind_labels": dict(KIND_LABELS),
            "error": error,
        }

    def _node_info(self, path: Path, node: Basic, expr: Optional[Basic] = None) -> Dict[str, Any]:
        """A node's entry in the snapshot.  ``node`` is the view-tree node
        printed at ``path``.  ``parts`` lists the names of its virtual parts
        when it is shown as a fraction or after a minus sign; such a node is
        neither ``insertable`` nor ``rangeable`` (its arguments are not what
        is shown - the parts are, and they may be)."""
        parts = self._parts(node)
        info: Dict[str, Any] = {"src": str(node), "type": type(node).__name__, "kind": node_kind(node),
                                "kinds": node_kinds(node),
                                "nargs": len(node.args), "insertable": is_insertable(node) and not parts,
                                "rangeable": is_rangeable(node) and not parts,
                                "free": sorted(str(s) for s in getattr(node, "free_symbols", ()))[:12]}
        if parts:
            info["parts"] = [name for name, _value in parts]
        # What unwrap could keep, when there is more than one candidate: the
        # front end asks which argument to leave instead of picking for the user.
        choices = self._keep_candidates(node)
        if len(choices) > 1:
            info["keep_choices"] = [{"key": key, "src": str(value)} for key, value in choices]
        return info

    def preview(self, src: str) -> Dict[str, Any]:
        """A snapshot of ``src`` parsed as the whole expression, without
        committing it (the source line shows it while it is typed; Enter
        commits with ``set``).  Flagged ``preview``; a string that does not
        parse gives the current snapshot with ``error`` and the flag."""
        try:
            snap = self.snapshot(expr=self.parse(src))
        except Exception as exc:
            snap = self.snapshot(error=f"{type(exc).__name__}: {exc}")
        snap["preview"] = True
        if self.last_note and not snap["error"]:
            snap["note"] = self.last_note
        return snap

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Process a front-end message and return a snapshot.

        Messages: ``{"action": "replace", "path": "/0", "src": "y**2"}``
        (``replace``, ``delete`` and ``apply`` take ``"children": [i, j]`` to
        act on the range of those arguments of the node at ``path``),
        ``{"action": "apply", "path": "/", "op": "expand"}``,
        ``{"action": "delete", "path": "/1"}``, ``{"action": "set", "src": ...}``,
        ``{"action": "insert", "path": "/", "index": 2, "src": "y", "left": 1}``,
        ``{"action": "extend", "path": "/2/0", "side": "after", "src": "+ 1"}``,
        ``{"action": "unwrap", "path": "/1", "keep": 0}`` (keep an argument, drop the node),
        ``{"action": "wrap", "path": "/1", "func": "cos"}`` (the node becomes the
        argument of a function; ``"args"`` supplies any further ones),
        ``{"action": "isolate", "path": "/1"}`` (the node becomes the whole expression),
        ``{"action": "call", "path": "/", "func": "diff(x)"}`` (any SymPy function/method),
        ``{"action": "functions"}`` (a snapshot with the list of SymPy function names),
        ``{"action": "retype", "name": "A", "type": "MatrixSymbol", "rows": 2, "cols": 2}``
        (``"assumptions": ["positive"]`` for a Symbol), ``{"action": "declare", ...}``
        with the same fields for a name not yet in the expression,
        ``{"action": "undeclare", "name": "A"}``,
        ``{"action": "preview", "src": ...}`` (a snapshot of the parsed source,
        not committed), ``{"action": "undo"}``, ``{"action": "redo"}``,
        ``{"action": "snapshot"}``.  Errors are reported in the snapshot's
        ``"error"`` field.
        """
        self.last_note = None
        try:
            action = message.get("action")
            path = message.get("path", "/")
            children = message.get("children")
            self._action_label = self._describe(message)
            if action == "preview":
                return self.preview(str(message.get("src", "")))
            if action == "export":
                snap = self.snapshot()
                snap["export"] = self.export()
                snap["history"] = self.history_labels()
                return snap
            if action == "goto":
                self.goto(message.get("index", 0))
            if action == "replace":
                self.replace(path, str(message.get("src", "")), children=children)
            elif action == "retype":
                self.retype(str(message.get("name", "")), str(message.get("type", "")),
                            message.get("rows"), message.get("cols"), message.get("assumptions"))
            elif action == "declare":
                self.declare(str(message.get("name", "")), str(message.get("type", "Symbol")),
                             message.get("rows"), message.get("cols"), message.get("assumptions"))
            elif action == "undeclare":
                self.undeclare(str(message.get("name", "")))
            elif action == "insert":
                self.insert(path, int(message.get("index", 0)), str(message.get("src", "")),
                            left=message.get("left"), right=message.get("right"), attach=message.get("attach"))
            elif action == "unwrap":
                self.unwrap(path, message.get("keep"))
            elif action == "wrap":
                self.wrap(path, str(message.get("func", "")), str(message.get("args", "") or ""), children=children)
            elif action == "isolate":
                self.isolate(path, children=children)
            elif action == "call":
                self.call(path, str(message.get("func", "")), children=children)
            elif action == "functions":
                snap = self.snapshot()
                snap["functions"] = sympy_functions()
                snap["signatures"] = {}
                for name in COMMON_FUNCTIONS:
                    try:
                        snap["signatures"][name] = function_signature(name)
                    except ValueError:
                        pass
                return snap
            elif action == "signature":
                target = self._extract_range(self.expr, self._path(path), children) if children is not None else self._get_at(self.expr, self._path(path))
                snap = self.snapshot()
                snap["signature"] = function_signature(str(message.get("name", "")), target)
                return snap
            elif action == "extend":
                self.extend(path, str(message.get("side", "after")), str(message.get("src", "")))
            elif action == "set":
                self.replace("/", str(message.get("src", "")))
            elif action == "apply":
                self.apply(path, str(message.get("op", "")), children=children)
            elif action == "delete":
                self.delete(path, children=children)
            elif action == "undo":
                self.undo()
            elif action == "redo":
                self.redo()
            elif action == "snapshot":
                pass
            else:
                raise ValueError(f"Unknown action: {action!r}")
            snap = self.snapshot()
            if self.last_note:
                snap["note"] = self.last_note
            return snap
        except Exception as exc:
            return self.snapshot(error=f"{type(exc).__name__}: {exc}")
        finally:
            self._action_label = None

    def _describe(self, message: Dict[str, Any]) -> Optional[str]:
        """What a message does, for the history ("Transform: Simplify",
        "SymPy: diff(x)", "Edit: y → cos(y)"...)."""
        def short(text: Any, n: int = 48) -> str:
            text = str(text).replace("\n", " ")
            return text if len(text) <= n else text[: n - 1] + "…"

        def node() -> str:
            try:
                p = self._path(message.get("path", "/"))
                if message.get("children") is not None:
                    return short(self._extract_range(self.expr, p, message["children"]))
                return short(self._get_at(self.expr, p))
            except Exception:
                return "…"

        action = message.get("action")
        try:
            if action == "replace":
                return f"Edit: {node()} → {short(message.get('src', ''))}"
            if action == "set":
                return f"Type the whole expression: {short(message.get('src', ''))}"
            if action == "apply":
                op = self.ops.get(str(message.get("op", "")))
                return f"Transform: {op.label if op else message.get('op')}"
            if action == "call":
                return f"SymPy: {short(message.get('func', ''))}"
            if action == "insert":
                return f"Insert \"{short(message.get('src', ''))}\" in {node()}"
            if action == "extend":
                return f"Type \"{short(message.get('src', ''))}\" {'after' if message.get('side', 'after') == 'after' else 'before'} {node()}"
            if action == "delete":
                return f"Delete {node()}"
            if action == "unwrap":
                return f"Unwrap {node()}"
            if action == "isolate":
                return f"Isolate {node()}"
            if action in ("retype", "declare"):
                return f"{'Retype' if action == 'retype' else 'Declare'} {message.get('name')} as {message.get('type', 'Symbol')}"
        except Exception:
            pass
        return str(action).capitalize() if action else None

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
        del self._labels[self._index + 1:]
        self._history.append(expr)
        self._labels.append(self._action_label)
        if len(self._history) > self.max_history:
            del self._history[: len(self._history) - self.max_history]
            del self._labels[: len(self._labels) - self.max_history]
        self._index = len(self._history) - 1
        self._notify()
        return expr

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb(self.expr)

    def __repr__(self) -> str:
        return f"Document({self.expr!r})"

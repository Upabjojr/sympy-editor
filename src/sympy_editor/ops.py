"""Registry of transformations that can be applied to a selected sub-expression.

Every op is a callable ``expr -> expr``.  Register your own with::

    from sympy_editor import register_op

    @register_op("mysimp", label="My simplification")
    def mysimp(expr):
        ...

An op may be limited to some *kinds* of selection - ``kinds=("matrix",)`` for
one that only makes sense on a matrix, say.  The kinds are named in
:data:`KINDS`, which maps each to the SymPy types it covers; :func:`node_kind`
classifies a node, and the front end only offers an op when the selected
node's kind is among the op's (ops registered without kinds are offered
everywhere).  Add a kind by extending :data:`KINDS` - first match wins, so
put the more specific types first.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Dict, NamedTuple, Optional, Tuple

import sympy
from sympy import Derivative, Integral, Limit, Product, Sum
from sympy.core.expr import Expr
from sympy.core.relational import Relational
from sympy.matrices.expressions import Adjoint, Determinant, Inverse, MatrixExpr, MatrixSymbol, Trace, Transpose
from sympy.matrices import MatrixBase
from sympy.tensor.array import NDimArray
from sympy.tensor.array.expressions import (ArraySymbol, Reshape, convert_array_to_matrix,
                                            convert_matrix_to_array)
# Symbolic arrays - an ArraySymbol, or a PermuteDims/ArrayContraction/... over
# one - have no public base class in common: _ArrayExpr covers the leaves and
# _CodegenArrayAbstract the operations.  Both are Expr, so without them here an
# array symbol would be classified "scalar" and offered none of its own tools.
from sympy.tensor.array.expressions.array_expressions import _ArrayExpr, _CodegenArrayAbstract

ARRAY_EXPR = (_ArrayExpr, _CodegenArrayAbstract)

__all__ = ["Op", "make_op", "register_op", "get_ops", "default_ops", "KINDS", "KIND_LABELS", "add_kind",
           "node_kind", "node_kinds"]

#: Kind name -> SymPy types, in order of precedence (a ``MatrixExpr`` is an
#: ``Expr`` too, so "matrix" must come before "scalar").
KINDS: "OrderedDict[str, Tuple[type, ...]]" = OrderedDict([
    ("integral", (Integral,)),
    ("sum", (Sum, Product)),
    ("derivative", (Derivative,)),
    ("limit", (Limit,)),
    ("relational", (Relational,)),
    ("matrix", (MatrixExpr, MatrixBase)),
    ("array", (NDimArray,) + ARRAY_EXPR),
    ("scalar", (Expr,)),
])

#: Kind name -> label of the type menu in the front end.
KIND_LABELS = {
    "integral": "Integral", "sum": "Sum", "derivative": "Derivative", "limit": "Limit",
    "relational": "Equation", "matrix": "Matrix", "array": "Array", "scalar": "Expression",
}


def add_kind(name: str, types: Tuple[type, ...], label: Optional[str] = None, before: str = "scalar") -> None:
    """Add a kind (an add-on's node type) to :data:`KINDS`, ahead of
    ``before`` so that it wins over the general ones - a class that is an
    ``Expr`` would otherwise be a "scalar".  Adding a kind that exists
    replaces its types."""
    if not name or not isinstance(name, str):
        raise ValueError("A kind needs a name")
    types = tuple(types)
    if not all(isinstance(t, type) for t in types):
        raise TypeError(f"Kind {name!r}: types must be classes")
    items = [(k, v) for k, v in KINDS.items() if k != name]
    at = next((i for i, (k, _v) in enumerate(items) if k == before), len(items))
    items.insert(at, (name, types))
    KINDS.clear()
    KINDS.update(items)
    KIND_LABELS[name] = label or name.capitalize()


def node_kinds(expr) -> list:
    """All kinds of ``expr`` (keys of :data:`KINDS`), most specific first -
    an ``Integral`` is ``["integral", "scalar"]``; ``["other"]`` if none."""
    kinds = [kind for kind, types in KINDS.items() if isinstance(expr, types)]
    return kinds or ["other"]


def node_kind(expr) -> str:
    """The most specific kind of ``expr`` (a key of :data:`KINDS`), or ``"other"``."""
    return node_kinds(expr)[0]


class Op(NamedTuple):
    name: str
    label: str
    func: Callable
    #: Kinds of selection the op applies to; ``None`` for any.
    kinds: Optional[Tuple[str, ...]] = None
    #: Values the op needs besides the expression, in the shape the front end
    #: already uses for function parameters: ``{"name", "kind": "symbol"|"text",
    #: "default", "optional"}``.  They are parsed in the expression's namespace
    #: and passed to ``func`` after it (``func(expr, *values)``); an op without
    #: parameters is ``func(expr)`` as before.
    params: Tuple[Dict[str, object], ...] = ()
    #: One line about what the op does, shown over the parameter form.
    doc: str = ""
    #: The op's *unevaluated* form (``Determinant(M)`` for the determinant),
    #: used when the front end's "unevaluated" toggle is on; None for an op
    #: without one (a simplification), which is then applied as usual.
    lazy: Optional[Callable] = None
    #: The op wants the document too: ``func(expr, *values, doc=document)``
    #: (an add-on's op that reads state kept on the document).
    context: bool = False


_REGISTRY: "OrderedDict[str, Op]" = OrderedDict()


def make_op(name: str, func: Callable, *, label: Optional[str] = None,
            kinds: Optional[Tuple[str, ...]] = None, params: Optional[Tuple] = None,
            doc: str = "", lazy: Optional[Callable] = None, context: bool = False) -> Op:
    """An :class:`Op` without registering it - what an add-on lists in its
    ``ops``.  The arguments are :func:`register_op`'s; with ``context`` the
    op is called as ``func(expr, *values, doc=document)``.  The kinds are
    not checked here: an add-on's op may name a kind the add-on itself adds
    when it is activated (an op with a kind nothing has is merely never
    offered)."""
    if kinds is not None:
        kinds = tuple(kinds)

    shaped = tuple({"name": p[0], "kind": p[1] if len(p) > 1 else "text",
                    "optional": bool(p[2]) if len(p) > 2 else False,
                    "default": p[3] if len(p) > 3 else None}
                   for p in (params or ()))
    return Op(name, label or name, func, kinds, shaped, doc, lazy, context)


def register_op(name: str, func: Optional[Callable] = None, *, label: Optional[str] = None,
                kinds: Optional[Tuple[str, ...]] = None, params: Optional[Tuple] = None,
                doc: str = "", lazy: Optional[Callable] = None):
    """Register ``func`` under ``name``.  Usable as a decorator.  ``kinds``
    limits the op to selections of those kinds (see :data:`KINDS`); ``params``
    declares values to ask the user for - ``[("permutation", "text", False,
    None)]``, i.e. (label, kind, optional, default) - which are parsed and
    passed to ``func`` after the expression.  ``lazy`` is the op's unevaluated
    form (``Determinant`` for a determinant), called instead of ``func`` when
    the user asks for the result unevaluated."""
    if kinds is not None:
        unknown = [k for k in kinds if k not in KINDS]
        if unknown:
            raise ValueError(f"Unknown kinds {unknown}; known: {list(KINDS)}")

    def deco(f: Callable) -> Callable:
        _REGISTRY[name] = make_op(name, f, label=label, kinds=kinds, params=params, doc=doc, lazy=lazy)
        return f

    return deco(func) if func is not None else deco


def get_ops() -> Dict[str, Op]:
    """A copy of the registry (insertion ordered)."""
    return OrderedDict(_REGISTRY)


def default_ops() -> Dict[str, Op]:
    return get_ops()


def _register_defaults() -> None:
    register_op("simplify", sympy.simplify, label="Simplify")
    register_op("expand", sympy.expand, label="Expand")
    register_op("factor", sympy.factor, label="Factor")
    register_op("cancel", sympy.cancel, label="Cancel")
    register_op("together", sympy.together, label="Together")
    register_op("apart", sympy.apart, label="Apart (partial fractions)")
    register_op("collect_terms", lambda e: sympy.collect(e, list(e.free_symbols)), label="Collect")
    register_op("trigsimp", sympy.trigsimp, label="Trig simplify")
    register_op("expand_trig", sympy.expand_trig, label="Expand trig")
    register_op("powsimp", sympy.powsimp, label="Power simplify")
    register_op("expand_log", lambda e: sympy.expand_log(e, force=True), label="Expand log")
    register_op("logcombine", lambda e: sympy.logcombine(e, force=True), label="Combine logs")
    register_op("radsimp", sympy.radsimp, label="Rationalize denominator")
    register_op("nsimplify", sympy.nsimplify, label="Nsimplify (find exact form)")
    register_op("doit", lambda e: e.doit(), label="Evaluate (doit)")
    register_op("evalf", lambda e: e.evalf(), label="Numeric (evalf)")
    register_op("negate", lambda e: -e, label="Negate", lazy=lambda e: sympy.Mul(-1, e, evaluate=False))
    # Not tied to a kind: an expression, a matrix or an array can all be
    # differentiated by a list of symbols, and the result gains their axes.
    register_op("derive_by_array", _derive_by_array, label="Derive by array…",
                params=[("by, e.g. x or [x, y]", "text", False, None)],
                doc="Differentiate by each of those, adding their axes to the result: "
                    "an expression by [x, y] becomes its gradient, a matrix an array.")
    _register_matrix_ops()
    _register_calculus_ops()


# Matrix ops: the selection is a MatrixExpr (MatrixSymbol algebra, where the
# result stays symbolic - Trace(A), Determinant(A), Inverse(A)) or an explicit
# matrix (where it is computed).  Each is a method or a function that makes
# no sense for a scalar, hence kinds=("matrix",).

def _explicit(e):
    return isinstance(e, MatrixBase)


def _register_matrix_ops() -> None:
    m = ("matrix",)
    register_op("transpose", lambda e: e.T, label="Transpose", kinds=m, lazy=Transpose)
    register_op("adjoint", lambda e: e.adjoint(), label="Adjoint (conjugate transpose)", kinds=m, lazy=Adjoint)
    register_op("inverse", lambda e: e.inv() if _explicit(e) else Inverse(e), label="Inverse", kinds=m, lazy=Inverse)
    register_op("trace", lambda e: e.trace() if _explicit(e) else Trace(e), label="Trace", kinds=m, lazy=Trace)
    register_op("determinant", lambda e: e.det() if _explicit(e) else Determinant(e),
                label="Determinant", kinds=m, lazy=Determinant)
    register_op("as_explicit", lambda e: e if _explicit(e) else e.as_explicit(),
                label="Explicit matrix (as_explicit)", kinds=m)
    register_op("transpose_conj", lambda e: e.conjugate(), label="Conjugate", kinds=m)
    register_op("to_array", _to_array, label="As array", kinds=m,
                doc="The same thing as an array: a matrix symbol becomes an array symbol (its entries stay implicit), "
                    "an explicit matrix an explicit array.")
    register_op("reshape", _reshape, label="Reshape…", kinds=m,
                params=[("new shape, e.g. (3, 2)", "text", False, None)],
                doc="The same entries in another shape; a shape with other than two dimensions gives an array.")
    _register_array_ops()


# Array ops: an NDimArray of any rank.  The three that take indices are the
# array's own tools - a matrix has a transpose, an array has a permutation of
# its axes, a contraction and a diagonal over any pair of them.

def _array_indices(value) -> tuple:
    """A parameter such as ``(1, 0)`` or ``0, 1`` as a tuple of plain ints."""
    items = list(value) if isinstance(value, (tuple, list, sympy.Tuple)) else [value]
    out = []
    for it in items:
        n = sympy.sympify(it)
        if not (getattr(n, "is_Integer", False) or isinstance(n, int)):
            raise ValueError(f"{it} is not an index (0, 1, 2...)")
        out.append(int(n))
    return tuple(out)


def _symbolic_array(e) -> bool:
    return isinstance(e, ARRAY_EXPR)


def _to_array(e):
    """A matrix as an array, keeping symbols symbolic: a ``MatrixSymbol``
    becomes an ``ArraySymbol`` of the same name and shape rather than a grid
    of its entries."""
    if isinstance(e, MatrixSymbol):
        return ArraySymbol(e.name, tuple(e.shape))
    if isinstance(e, MatrixBase):
        return sympy.Array(e)
    return convert_matrix_to_array(e)


def _to_matrix(e):
    """A rank-2 array as a matrix, the inverse of :func:`_to_array`."""
    shape = tuple(e.shape)
    if len(shape) != 2:
        raise ValueError(f"Only a rank-2 array is a matrix; this one has rank {len(shape)} "
                         f"(shape {shape}) - contract, diagonalise or reshape it first")
    if isinstance(e, ArraySymbol):
        return MatrixSymbol(str(e.name), *shape)
    if isinstance(e, NDimArray):
        return e.tomatrix()
    return convert_array_to_matrix(e)


def _reshape(e, shape):
    """The same entries in another shape.  A matrix reshaped to two dimensions
    stays a matrix; anything else is an array, since only an array can have a
    rank other than 2."""
    dims = _array_indices(shape)
    if not dims:
        raise ValueError("A shape needs at least one dimension, e.g. (3, 2)")
    try:
        if isinstance(e, MatrixBase):
            return e.reshape(*dims) if len(dims) == 2 else sympy.Array(e).reshape(*dims)
        if isinstance(e, NDimArray):
            return e.reshape(*dims)
        return Reshape(_to_array(e) if isinstance(e, MatrixExpr) else e, dims)
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Cannot reshape {tuple(e.shape)} into {dims}: {exc}") from None


def _derive_by_array(e, wrt):
    """Differentiate by one symbol, or by a list/array of them - the result
    gains their axes (a matrix by [x, y] becomes a rank-3 array)."""
    by = list(wrt) if isinstance(wrt, (sympy.Tuple, tuple, list, NDimArray)) else wrt
    return sympy.derive_by_array(e, by)


def _register_array_ops() -> None:
    a = ("array",)
    register_op("tomatrix", _to_matrix, label="As matrix (rank 2)", kinds=a,
                doc="A rank-2 array is the same thing as a matrix.")
    register_op("permutedims", lambda e, perm: sympy.permutedims(e, _array_indices(perm)),
                label="Permute axes (permutedims)…", kinds=a,
                params=[("permutation, e.g. (1, 0)", "text", False, None)],
                doc="Reorder the axes: (1, 0) transposes a rank-2 array, (2, 0, 1) rolls a rank-3 one.")
    register_op("contraction", lambda e, axes: sympy.tensorcontraction(e, _array_indices(axes)),
                label="Contract axes (tensorcontraction)…", kinds=a,
                params=[("axes to sum over, e.g. (0, 1)", "text", False, None)],
                doc="Sum over a pair of axes, as the trace sums a matrix over its two.")
    register_op("diagonal", lambda e, axes: sympy.tensordiagonal(e, _array_indices(axes)),
                label="Diagonal over axes (tensordiagonal)…", kinds=a,
                params=[("axes to take the diagonal of, e.g. (0, 1)", "text", False, None)],
                doc="Keep the entries whose indices on those axes are equal, as a matrix diagonal does.")
    register_op("array_rank", lambda e: sympy.Integer(len(e.shape)), label="Rank (number of axes)", kinds=a,
                doc="How many axes the array has.")
    register_op("reshape_array", _reshape, label="Reshape…", kinds=a,
                params=[("new shape, e.g. (4, 1)", "text", False, None)],
                doc="The same entries in another shape.")
    register_op("array_as_explicit", lambda e: e.as_explicit() if _symbolic_array(e) else e,
                label="Explicit entries (as_explicit)", kinds=a,
                doc="An array symbol written out as the grid of its entries, S[0, 0], S[0, 1]...")


def _with_function(e, f):
    """``e`` (an Integral, Sum, Derivative...) with ``f`` applied to its function."""
    return e.func(f(e.args[0]), *e.args[1:])


def _register_calculus_ops() -> None:
    ev = ("integral", "sum", "derivative", "limit")
    register_op("evaluate", lambda e: e.doit(), label="Evaluate", kinds=ev)
    register_op("numeric", lambda e: e.evalf(), label="Numeric value", kinds=ev)
    register_op("expand_inside", lambda e: _with_function(e, sympy.expand), label="Expand the function inside", kinds=ev)
    register_op("simplify_inside", lambda e: _with_function(e, sympy.simplify), label="Simplify the function inside", kinds=ev)
    r = ("relational",)
    register_op("to_left", lambda e: e.func(e.lhs - e.rhs, 0), label="Move everything to the left", kinds=r)
    register_op("swap_sides", lambda e: e.reversed, label="Swap sides", kinds=r)
    register_op("simplify_sides", lambda e: e.func(sympy.simplify(e.lhs), sympy.simplify(e.rhs)), label="Simplify both sides", kinds=r)
    register_op("expand_sides", lambda e: e.func(sympy.expand(e.lhs), sympy.expand(e.rhs)), label="Expand both sides", kinds=r)


_register_defaults()

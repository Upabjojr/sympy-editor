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
from sympy.core.expr import Expr
from sympy.matrices.expressions import Determinant, Inverse, MatrixExpr, Trace
from sympy.matrices.matrixbase import MatrixBase
from sympy.tensor.array import NDimArray

__all__ = ["Op", "register_op", "get_ops", "default_ops", "KINDS", "node_kind"]

#: Kind name -> SymPy types, in order of precedence (a ``MatrixExpr`` is an
#: ``Expr`` too, so "matrix" must come before "scalar").
KINDS: "OrderedDict[str, Tuple[type, ...]]" = OrderedDict([
    ("matrix", (MatrixExpr, MatrixBase)),
    ("array", (NDimArray,)),
    ("scalar", (Expr,)),
])

#: Kind name -> label for the group of ops in the front end's dropdown.
KIND_LABELS = {"matrix": "Matrix", "array": "Array", "scalar": "Scalar"}


def node_kind(expr) -> str:
    """The kind of ``expr`` (a key of :data:`KINDS`), or ``"other"``."""
    for kind, types in KINDS.items():
        if isinstance(expr, types):
            return kind
    return "other"


class Op(NamedTuple):
    name: str
    label: str
    func: Callable
    #: Kinds of selection the op applies to; ``None`` for any.
    kinds: Optional[Tuple[str, ...]] = None


_REGISTRY: "OrderedDict[str, Op]" = OrderedDict()


def register_op(name: str, func: Optional[Callable] = None, *, label: Optional[str] = None,
                kinds: Optional[Tuple[str, ...]] = None):
    """Register ``func`` under ``name``.  Usable as a decorator.  ``kinds``
    limits the op to selections of those kinds (see :data:`KINDS`)."""
    if kinds is not None:
        unknown = [k for k in kinds if k not in KINDS]
        if unknown:
            raise ValueError(f"Unknown kinds {unknown}; known: {list(KINDS)}")
        kinds = tuple(kinds)

    def deco(f: Callable) -> Callable:
        _REGISTRY[name] = Op(name, label or name, f, kinds)
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
    register_op("negate", lambda e: -e, label="Negate")
    _register_matrix_ops()


# Matrix ops: the selection is a MatrixExpr (MatrixSymbol algebra, where the
# result stays symbolic - Trace(A), Determinant(A), Inverse(A)) or an explicit
# matrix (where it is computed).  Each is a method or a function that makes
# no sense for a scalar, hence kinds=("matrix",).

def _explicit(e):
    return isinstance(e, MatrixBase)


def _register_matrix_ops() -> None:
    m = ("matrix",)
    register_op("transpose", lambda e: e.T, label="Transpose", kinds=m)
    register_op("adjoint", lambda e: e.adjoint(), label="Adjoint (conjugate transpose)", kinds=m)
    register_op("inverse", lambda e: e.inv() if _explicit(e) else Inverse(e), label="Inverse", kinds=m)
    register_op("trace", lambda e: e.trace() if _explicit(e) else Trace(e), label="Trace", kinds=m)
    register_op("determinant", lambda e: e.det() if _explicit(e) else Determinant(e),
                label="Determinant", kinds=m)
    register_op("as_explicit", lambda e: e if _explicit(e) else e.as_explicit(),
                label="Explicit matrix (as_explicit)", kinds=m)
    register_op("transpose_conj", lambda e: e.conjugate(), label="Conjugate", kinds=m)


_register_defaults()

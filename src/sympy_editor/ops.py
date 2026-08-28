"""Registry of transformations that can be applied to a selected sub-expression.

Every op is a callable ``expr -> expr``.  Register your own with::

    from sympy_editor import register_op

    @register_op("mysimp", label="My simplification")
    def mysimp(expr):
        ...
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Dict, NamedTuple, Optional

import sympy

__all__ = ["Op", "register_op", "get_ops", "default_ops"]


class Op(NamedTuple):
    name: str
    label: str
    func: Callable


_REGISTRY: "OrderedDict[str, Op]" = OrderedDict()


def register_op(name: str, func: Optional[Callable] = None, *, label: Optional[str] = None):
    """Register ``func`` under ``name``.  Usable as a decorator."""

    def deco(f: Callable) -> Callable:
        _REGISTRY[name] = Op(name, label or name, f)
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


_register_defaults()

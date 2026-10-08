"""Is one step the same mathematics as the one before it?

:func:`compare` answers with a verdict - a dict with ``status`` (``equal``,
``different`` or ``unknown``), ``how`` (the method that decided) and
``text`` (the answer in words) - and, for a difference, the ``point`` where
the two steps disagree and the two ``values`` there.

Nothing here may hang: every call into SymPy goes through :func:`attempt`,
which stops it at a deadline.  The deadline is cooperative - a trace
function (``sys.settrace``) that raises :class:`TimedOut` at the next Python
call once the time is up - because it has to work everywhere the editor's
Python runs: the kernel, the HTTP server's threads, Pyodide (no threads to
abandon there) and the apps.  ``signal.alarm`` only works on the main thread.

The order is cheap first: identical, ``a - b`` evaluated to zero, a numeric
spot check at random points (a difference found there is a counterexample,
and the answer), then ``simplify(a - b)`` and ``a.equals(b)``.  Two steps
the spot check finds equal but SymPy cannot prove equal are reported as
equal *numerically*, in so many words.
"""

from __future__ import annotations

import math
import random
import sys
import threading
import time
import zlib
from typing import Any, Callable, Dict, List, Optional, Tuple

from sympy import (And, Basic, EmptySet, Expr, FiniteSet, ImmutableMatrix, Interval, Not, Or, Rational, S,
                   Symbol, Union, simplify, solve, solveset, srepr)
from sympy.core.relational import Relational
from sympy.logic.boolalg import BooleanAtom
from sympy.matrices import MatrixBase
from sympy.matrices.expressions import MatrixExpr, MatrixSymbol
from sympy.tensor.array import NDimArray

__all__ = ["TimedOut", "attempt", "compare", "numeric_check"]


class TimedOut(BaseException):
    """Raised inside SymPy when a comparison's time is up.  A BaseException,
    so that SymPy's own ``except Exception`` clauses do not swallow it."""


_local = threading.local()


def _deadlines() -> List[float]:
    stack = getattr(_local, "deadlines", None)
    if stack is None:
        stack = _local.deadlines = []
    return stack


def _tracer(frame, event, arg):
    # Only "call" events reach a global trace function that returns None:
    # one cheap check per Python call, nothing per line.
    if event == "call":
        stack = getattr(_local, "deadlines", None)
        if stack and time.monotonic() > stack[-1]:
            raise TimedOut()
    return None


def _interrupted(exc: BaseException) -> bool:
    """The editor's own interrupt (``sympy_editor.document.Interrupted``)
    must go through: the user pressed the button."""
    return type(exc).__name__ == "Interrupted"


def attempt(fn: Callable[[], Any], seconds: float) -> Tuple[Any, Optional[str]]:
    """``(fn(), None)``, or ``(None, "time")`` when it ran out of time, or
    ``(None, "error")`` when it raised.  Nested calls never outlive the
    enclosing one's deadline."""
    stack = _deadlines()
    end = time.monotonic() + max(0.0, seconds)
    if stack:
        end = min(end, stack[-1])
    if time.monotonic() >= end:
        return None, "time"
    stack.append(end)
    old = sys.gettrace()
    sys.settrace(_tracer)
    try:
        return fn(), None
    except TimedOut:
        return None, "time"
    except Exception as exc:
        if _interrupted(exc):
            raise
        return None, "error"
    finally:
        stack.pop()
        sys.settrace(old)


# -- numbers ---------------------------------------------------------------

#: How close two values must be to count as the same.
REL_TOL = 1e-8


def _close(u: complex, v: complex) -> bool:
    return abs(u - v) <= REL_TOL * max(1.0, abs(u), abs(v))


def _fmt_number(v: complex) -> str:
    if abs(v.imag) <= 1e-12 * max(1.0, abs(v.real)):
        return f"{v.real:.6g}"
    if abs(v.real) <= 1e-12 * max(1.0, abs(v.imag)):
        return f"{v.imag:.6g}*I"
    return f"{v.real:.6g} {'+' if v.imag >= 0 else '-'} {abs(v.imag):.6g}*I"


def _fmt_value(vals: Tuple[complex, ...], shape: Optional[Tuple[int, ...]]) -> str:
    if shape is None:
        return _fmt_number(vals[0])
    if len(shape) == 2:
        r, c = shape
        return "[" + ", ".join("[" + ", ".join(_fmt_number(vals[i * c + j]) for j in range(c)) + "]" for i in range(r)) + "]"
    return "[" + ", ".join(_fmt_number(v) for v in vals) + "]"


def _fmt_point(env: Dict[Basic, Any]) -> Dict[str, str]:
    out = {}
    for sym, val in sorted(env.items(), key=lambda kv: str(kv[0])):
        if isinstance(val, Rational) and not val.is_Integer:
            out[str(sym)] = f"{float(val):g}"
        else:
            out[str(sym)] = str(val)
    return out


def _sample(sym: Basic, rng: random.Random) -> Optional[Basic]:
    """A random value that ``sym``'s assumptions allow: a small integer for
    an integer, a positive number for a positive symbol, a nonzero real with
    one decimal otherwise (a real point is a point of every domain); a
    matrix of small integers for a matrix symbol of a known shape."""
    if isinstance(sym, MatrixSymbol):
        try:
            r, c = int(sym.shape[0]), int(sym.shape[1])
        except (TypeError, ValueError):
            return None
        if r * c > 64:
            return None
        return ImmutableMatrix(r, c, [rng.choice([-3, -2, -1, 1, 2, 3, 4]) for _ in range(r * c)])
    if not isinstance(sym, Symbol):
        return None
    if sym.is_integer:
        lo = 1 if (sym.is_positive or sym.is_nonnegative) else -6
        hi = -1 if (sym.is_negative or sym.is_nonpositive) else 6
        v = 0
        while v == 0:
            v = rng.randint(lo, hi)
        return S(v)
    mag = Rational(rng.randint(2, 29), 10)
    if sym.is_negative or sym.is_nonpositive:
        return -mag
    if sym.is_positive or sym.is_nonnegative:
        return mag
    return mag if rng.random() < 0.5 else -mag


def _symbols(*exprs: Basic) -> List[Basic]:
    syms = set()
    for e in exprs:
        syms |= {s for s in e.free_symbols if isinstance(s, (Symbol, MatrixSymbol))}
    return sorted(syms, key=lambda s: (str(s), srepr(s)))


def _shape(e: Basic) -> Optional[Tuple[int, ...]]:
    if isinstance(e, (MatrixBase, MatrixExpr, NDimArray)):
        try:
            return tuple(int(n) for n in e.shape)
        except (TypeError, ValueError):
            return ()
    return None


def _values(e: Basic, env: Dict[Basic, Any]) -> Optional[Tuple[complex, ...]]:
    """``e`` at the point ``env`` as complex numbers (one per entry for a
    matrix), or None where it has no finite value."""
    v = e.subs(env) if env else e
    if isinstance(v, MatrixExpr) and not isinstance(v, MatrixBase):
        v = v.doit()
        if not isinstance(v, MatrixBase):
            v = v.as_explicit()
    if isinstance(v, (MatrixBase, NDimArray)):
        items = list(v) if isinstance(v, MatrixBase) else _flat(v)
    else:
        items = [v]
    out = []
    for item in items:
        item = item.doit() if hasattr(item, "doit") else item
        n = item.evalf(30)
        if n.free_symbols:
            return None
        try:
            c = complex(n)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(c.real) and math.isfinite(c.imag)):
            return None
        out.append(c)
    return tuple(out)


def _flat(a: NDimArray) -> List[Basic]:
    from sympy.utilities.iterables import flatten
    return flatten(a.tolist()) if a.rank() else [a[()]]


def numeric_check(a: Basic, b: Basic, points: int = 6, seconds: float = 1.5) -> Dict[str, Any]:
    """Evaluate ``a`` and ``b`` at random points.  ``{"status": "different",
    "point", "values", "entry"}`` at the first point where they differ,
    ``{"status": "equal", "points": n}`` when they agree wherever both have
    a value (at least three points), ``{"status": "unknown"}`` otherwise."""
    syms = _symbols(a, b)
    rng = random.Random(zlib.crc32((srepr(a) + "|" + srepr(b)).encode("utf-8")))
    end = time.monotonic() + seconds
    agreed = 0
    tries = points if syms else 1
    shape = _shape(a)
    for _ in range(tries * 2):
        if agreed >= tries or time.monotonic() >= end:
            break
        env = {}
        for s in syms:
            v = _sample(s, rng)
            if v is None:
                return {"status": "unknown", "reason": f"{s} cannot be given a value"}
            env[s] = v
        left = time.monotonic()
        va, why = attempt(lambda: _values(a, env), min(0.6, end - left))
        if va is None:
            continue
        vb, why = attempt(lambda: _values(b, env), min(0.6, end - time.monotonic()))
        if vb is None:
            continue
        if len(va) != len(vb):
            return {"status": "different", "point": _fmt_point(env), "values": [str(len(va)), str(len(vb))],
                    "entry": None, "reason": "a different number of entries"}
        for k, (u, w) in enumerate(zip(va, vb)):
            if not _close(u, w):
                entry = None
                if shape and len(shape) == 2 and shape[1]:
                    entry = f"({k // shape[1] + 1}, {k % shape[1] + 1})"
                elif shape:
                    entry = str(k + 1)
                return {"status": "different", "point": _fmt_point(env),
                        "values": [_fmt_value(va, shape), _fmt_value(vb, shape)], "entry": entry}
        agreed += 1
    if agreed >= min(3, tries):
        return {"status": "equal", "points": agreed}
    return {"status": "unknown", "reason": "no value at the points tried"}


# -- expressions -----------------------------------------------------------

def _is_zero(d: Any) -> bool:
    if d is None:
        return False
    if isinstance(d, (MatrixBase, NDimArray)):
        return all(x == 0 for x in (list(d) if isinstance(d, MatrixBase) else _flat(d)))
    if isinstance(d, MatrixExpr):
        return bool(getattr(d, "is_ZeroMatrix", False))
    return d == 0


def _simplified(d: Basic) -> Basic:
    d = d.doit() if hasattr(d, "doit") else d
    if isinstance(d, (MatrixBase, NDimArray)):
        return d.applyfunc(simplify)
    return simplify(d)


def _equal(how: str, text: str, **extra) -> Dict[str, Any]:
    return dict(status="equal", how=how, text=text, **extra)


def _different(num: Dict[str, Any], before: str = "the step before", after: str = "this step") -> Dict[str, Any]:
    point = ", ".join(f"{k} = {v}" for k, v in num["point"].items())
    at = f"at {point}" if point else "as numbers"
    entry = f" (entry {num['entry']})" if num.get("entry") else ""
    text = f"Not the same: {at}{entry} {before} is {num['values'][0]} and {after} is {num['values'][1]}."
    return {"status": "different", "how": "numeric", "text": text, "point": num["point"], "values": num["values"]}


def _compare_values(a: Basic, b: Basic, seconds: float) -> Dict[str, Any]:
    """Two expressions, matrices or arrays."""
    sa, sb = _shape(a), _shape(b)
    if (sa is None) != (sb is None):
        return {"status": "unknown", "how": "kinds",
                "text": "One step is a matrix or an array and the other is not: they are not compared."}
    if sa and sb and sa != sb:
        fmt = lambda s: "×".join(str(n) for n in s)                              # noqa: E731
        return {"status": "different", "how": "shape", "text": f"Not the same: the shapes differ ({fmt(sa)} and {fmt(sb)})."}
    end = time.monotonic() + seconds
    left = lambda: max(0.0, end - time.monotonic())                             # noqa: E731
    d, _ = attempt(lambda: a - b, min(0.5, left()))
    if d is not None and _is_zero(d):
        return _equal("evaluate", "Equal: SymPy's evaluation of the difference of the two steps gives 0.")
    num = numeric_check(a, b, seconds=min(left() * 0.4, 1.5))
    if num["status"] == "different":
        return _different(num)
    if d is not None:
        z, _ = attempt(lambda: _simplified(d), left() * 0.7)
        if z is not None and _is_zero(z):
            return _equal("simplify", "Equal: simplify(a − b) = 0.")
    if isinstance(a, Expr) and isinstance(b, Expr) and not isinstance(a, MatrixExpr):
        eq, _ = attempt(lambda: a.equals(b), left())
        if eq is True:
            return _equal("equals", "Equal: a.equals(b) is True.")
    if num["status"] == "equal":
        n = num["points"]
        if not _symbols(a, b):
            return _equal("numeric", "Equal as numbers (to 30 digits), though SymPy could not simplify the difference to 0.")
        return _equal("numeric", f"Equal at {n} random points, though SymPy could not prove it (simplify(a − b) did not give 0).",
                      points=n)
    if d is None:
        return {"status": "unknown", "how": "none", "text": "Could not decide: SymPy cannot subtract one step from the other."}
    return {"status": "unknown", "how": "none",
            "text": "Could not decide: SymPy did not simplify the difference to 0 in time, and the steps could not be "
                    "evaluated at random points."}


# -- relations -------------------------------------------------------------

_KIND = {"==": "eq", "!=": "ne", "<": "lt", "<=": "le", ">": "lt", ">=": "le"}


def _normal(rel: Relational) -> Tuple[str, Basic]:
    """``rel`` as ``(kind, d)``: ``rel`` holds exactly when ``d (kind) 0``."""
    op = rel.rel_op
    if op in (">", ">="):
        return _KIND[op], rel.rhs - rel.lhs
    return _KIND[op], rel.lhs - rel.rhs


def _is_logic(e: Basic) -> bool:
    if isinstance(e, Relational):
        return True
    if isinstance(e, (And, Or, Not)):
        return all(_is_logic(arg) for arg in e.args)
    return isinstance(e, BooleanAtom)


def _solution_set(e: Basic, v: Symbol, domain):
    if isinstance(e, BooleanAtom):
        return domain if e else EmptySet
    if isinstance(e, Or):
        return Union(*[_solution_set(arg, v, domain) for arg in e.args])
    if isinstance(e, And):
        out = domain
        for arg in e.args:
            out = out.intersect(_solution_set(arg, v, domain))
        return out
    if isinstance(e, Not):
        return domain - _solution_set(e.args[0], v, domain)
    return solveset(e, v, domain)


def _holds(e: Basic, env: Dict[Basic, Any]) -> Optional[bool]:
    t = e.subs(env)
    if isinstance(t, BooleanAtom):
        return bool(t)
    t = t.doit() if hasattr(t, "doit") else t
    if isinstance(t, Relational):
        try:
            lhs, rhs = complex(t.lhs.evalf(30)), complex(t.rhs.evalf(30))
        except (TypeError, ValueError):
            return None
        if t.rel_op in ("==", "!="):
            same = _close(lhs, rhs)
            return same if t.rel_op == "==" else not same
        if abs(lhs.imag) > 1e-12 or abs(rhs.imag) > 1e-12:
            return None
        return {"<": lhs.real < rhs.real, "<=": lhs.real <= rhs.real,
                ">": lhs.real > rhs.real, ">=": lhs.real >= rhs.real}[t.rel_op]
    if isinstance(t, (And, Or, Not)):
        parts = [_holds(arg, {}) for arg in t.args]
        if None in parts:
            return None
        return all(parts) if isinstance(t, And) else any(parts) if isinstance(t, Or) else not parts[0]
    return None


def _witness(sdiff, v: Symbol) -> Optional[Basic]:
    """A point of a set that is not empty, if one is easy to name."""
    if isinstance(sdiff, FiniteSet) and sdiff.args:
        return sorted(sdiff.args, key=str)[0]
    if isinstance(sdiff, Interval):
        lo, hi = sdiff.inf, sdiff.sup
        if lo.is_finite and hi.is_finite:
            return (lo + hi) / 2
        if lo.is_finite:
            return lo + 1
        if hi.is_finite:
            return hi - 1
        return S.Zero
    if isinstance(sdiff, Union) and sdiff.args:
        return _witness(sdiff.args[0], v)
    return None


def _compare_logic(a: Basic, b: Basic, seconds: float) -> Dict[str, Any]:
    end = time.monotonic() + seconds
    left = lambda: max(0.0, end - time.monotonic())                             # noqa: E731
    if isinstance(a, Relational) and isinstance(b, Relational):
        (ka, da), (kb, db) = _normal(a), _normal(b)
        if ka == kb:
            z, _ = attempt(lambda: simplify(da - db), min(left() * 0.3, 1.0))
            if z is not None and z == 0:
                return _equal("sides", "Equal: the two sides differ by the same amount in both steps.")
            if ka in ("eq", "ne"):
                z, _ = attempt(lambda: simplify(da + db), min(left() * 0.3, 1.0))
                if z is not None and z == 0:
                    return _equal("sides", "Equal: the same relation with the sides swapped.")
            q, _ = attempt(lambda: simplify(da / db), min(left() * 0.3, 1.0))
            if q is not None and not q.free_symbols and q.is_number and q.is_finite and q.is_zero is False:
                if ka in ("eq", "ne") or q.is_positive:
                    return _equal("factor", f"Equal: the step before is this step with both sides multiplied by {q}, which is not 0.")
    syms = _symbols(a, b)
    if any(not isinstance(s, Symbol) for s in syms):
        return {"status": "unknown", "how": "none", "text": "Could not decide: relations between matrices are not compared."}
    ordered = any(isinstance(r, Relational) and r.rel_op not in ("==", "!=") for r in (a, b))
    if len(syms) == 1:
        v = syms[0]
        domain = S.Reals if (ordered or v.is_real) else S.Complexes
        where = "real" if domain is S.Reals else "complex"
        sa, _ = attempt(lambda: _solution_set(a, v, domain), left() * 0.45)
        sb, _ = attempt(lambda: _solution_set(b, v, domain), left() * 0.8)
        if sa is not None and sb is not None:
            if sa == sb:
                return _equal("solutions", f"Equal: the same {where} solutions for {v}: {sa}.")
            sdiff, _ = attempt(lambda: sa.symmetric_difference(sb), left() * 0.5)
            if sdiff is EmptySet or sdiff == S.EmptySet:
                return _equal("solutions", f"Equal: the same {where} solutions for {v}.")
            w, _ = attempt(lambda: _witness(sdiff, v), 0.3)
            if w is not None:
                ha, _ = attempt(lambda: _holds(a, {v: w}), 0.4)
                hb, _ = attempt(lambda: _holds(b, {v: w}), 0.4)
                if ha is not None and hb is not None and ha != hb:
                    first, second = ("the step before", "this step") if ha else ("this step", "the step before")
                    return {"status": "different", "how": "solutions",
                            "text": f"Not the same: {v} = {w} satisfies {first} but not {second} "
                                    f"(solutions: {sa} before, {sb} now).",
                            "point": {str(v): str(w)}, "values": [str(ha), str(hb)]}
            return {"status": "unknown", "how": "solutions",
                    "text": f"Could not decide: the solution sets {sa} and {sb} look different, but no point "
                            f"that tells them apart was found."}
        return {"status": "unknown", "how": "none", "text": f"Could not decide: SymPy did not solve the steps for {v} in time."}
    if isinstance(a, Relational) and isinstance(b, Relational) and a.rel_op == "==" and b.rel_op == "==":
        return _compare_equations(a, b, syms, left())
    if ordered:
        # Inequalities in several variables: the two hold at the same random points, or a point where only one does
        rng = random.Random(zlib.crc32((srepr(a) + "|" + srepr(b)).encode("utf-8")))
        agreed = 0
        for _ in range(16):
            if time.monotonic() >= end:
                break
            env = {s: _sample(s, rng) for s in syms}
            ha, _ = attempt(lambda: _holds(a, env), 0.3)
            hb, _ = attempt(lambda: _holds(b, env), 0.3)
            if ha is None or hb is None:
                continue
            if ha != hb:
                point = _fmt_point(env)
                at = ", ".join(f"{k} = {v}" for k, v in point.items())
                first, second = ("the step before", "this step") if ha else ("this step", "the step before")
                return {"status": "different", "how": "numeric", "text": f"Not the same: at {at} {first} holds and {second} does not.",
                        "point": point, "values": [str(ha), str(hb)]}
            agreed += 1
        if agreed >= 8:
            return _equal("numeric", f"Equal at {agreed} random points (both hold or both fail), though not proven.", points=agreed)
    return {"status": "unknown", "how": "none", "text": "Could not decide whether the two steps say the same thing."}


def _compare_equations(a: Relational, b: Relational, syms: List[Symbol], seconds: float) -> Dict[str, Any]:
    """Equations in several unknowns: solved for one variable both share,
    the roots compared at random values of the others."""
    end = time.monotonic() + seconds
    da, db = a.lhs - a.rhs, b.lhs - b.rhs
    common = [s for s in syms if s in da.free_symbols and s in db.free_symbols]
    for v in common[:2]:
        ra, _ = attempt(lambda: solve(da, v), max(0.0, end - time.monotonic()) * 0.3)
        rb, _ = attempt(lambda: solve(db, v), max(0.0, end - time.monotonic()) * 0.4)
        if not isinstance(ra, list) or not isinstance(rb, list) or not ra or not rb:
            continue
        others = [s for s in syms if s != v]
        rng = random.Random(zlib.crc32((srepr(a) + "|" + srepr(b)).encode("utf-8")))
        agreed = 0
        for _ in range(5):
            if time.monotonic() >= end:
                break
            env = {s: _sample(s, rng) for s in others}
            va = [attempt(lambda r=r: _values(r, env), 0.3)[0] for r in ra]
            vb = [attempt(lambda r=r: _values(r, env), 0.3)[0] for r in rb]
            if any(x is None for x in va + vb):
                continue
            va1, vb1 = [x[0] for x in va], [x[0] for x in vb]
            lone = [u for u in va1 if not any(_close(u, w) for w in vb1)]
            extra = [w for w in vb1 if not any(_close(u, w) for u in va1)]
            if lone or extra:
                point = _fmt_point(env)
                at = ", ".join(f"{k} = {x}" for k, x in point.items())
                if lone:
                    what = f"{v} = {_fmt_number(lone[0])} solves the step before but not this step"
                else:
                    what = f"{v} = {_fmt_number(extra[0])} solves this step but not the step before"
                return {"status": "different", "how": "solutions", "text": f"Not the same: with {at}, {what}.",
                        "point": point, "values": [", ".join(_fmt_number(u) for u in va1), ", ".join(_fmt_number(w) for w in vb1)]}
            agreed += 1
        if agreed >= 3:
            return _equal("solutions", f"Equal: the same solutions for {v} at {agreed} random values of "
                                        f"{', '.join(str(s) for s in others)}.", points=agreed)
    return {"status": "unknown", "how": "none", "text": "Could not decide: the equations were not solved in time."}


# -- the entry point ---------------------------------------------------------

def compare(a: Basic, b: Basic, seconds: float = 2.5) -> Dict[str, Any]:
    """Whether ``b`` (a step) says the same as ``a`` (the step before it).
    Never takes much longer than ``seconds``."""
    if a == b:
        return _equal("identical", "Equal: the two steps are the same expression.")
    if _is_logic(a) and _is_logic(b):
        return _compare_logic(a, b, seconds)
    if _is_logic(a) or _is_logic(b):
        return {"status": "unknown", "how": "kinds",
                "text": "One step is an equation or inequality and the other is not: they are not compared."}
    if isinstance(a, (Expr, MatrixBase, NDimArray)) and isinstance(b, (Expr, MatrixBase, NDimArray)):
        return _compare_values(a, b, seconds)
    return {"status": "unknown", "how": "kinds", "text": f"Could not decide: a {type(a).__name__} and a "
                                                        f"{type(b).__name__} are not compared beyond being the same object."}

"""The one-sided z-transform, which SymPy does not have.

``z_transform(f, n, z)`` is ``Sum(f(n) * z**(-n), (n, 0, oo))`` in closed
form, with the region where the sum converges: ``|z| > R``.  A sequence made
of terms ``c * n**k * b**n`` (``b**n`` may be any power or exponential whose
exponent is linear in ``n``; sines and cosines of ``n`` are such terms once
written with exponentials) and of Kronecker deltas ``KroneckerDelta(n, m)``
is transformed term by term from the table

    Z{b**n}           = z / (z - b)              |z| > |b|
    Z{n * x[n]}       = -z d/dz X(z)
    Z{delta[n - m]}   = z**(-m)                  z != 0

and anything else is left to ``summation``, whose ``Piecewise`` answer gives
the condition.

``inverse_z_transform(F, z, n)`` inverts a rational function of ``z`` by
partial fractions of ``F(z)/z``: a pole ``p`` of order ``m`` contributes
``binomial(n, m - 1) * p**(n - m + 1)`` (``KroneckerDelta(n, m - 1)`` for a
pole at 0), the sequence for ``n >= 0``.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from sympy import (Abs, acos, apart, pi, sqrt, Add, Basic, Expr, Integer, KroneckerDelta, Max, Mul, Piecewise, Poly, Pow, S, Sum, Symbol,
                   binomial, cancel, cos, diff, exp, expand, expand_func, factor, factorial, fraction, oo, powsimp, roots, simplify, sin, summation,
                   together)
from sympy.functions.elementary.hyperbolic import HyperbolicFunction

__all__ = ["z_transform", "inverse_z_transform", "ZTransformError"]


class ZTransformError(ValueError):
    """The transform could not be found (said in words)."""


def _linear(e: Expr, n: Symbol) -> Optional[Tuple[Expr, Expr]]:
    """``e`` as ``(slope, rest)`` with ``e == slope * n + rest``, or None."""
    e = expand(e)
    slope, rest = e.coeff(n), e.subs(n, 0)
    if slope.has(n) or expand(slope * n + rest - e) != 0:
        return None
    return slope, rest


def _geometric(term: Expr, n: Symbol):
    """``term`` as ``(c, k, b, wave)`` with ``term == c * n**k * b**n * wave``,
    where ``wave`` is 1, or ``("sin" | "cos", w, phase)`` for one sine or
    cosine of ``w*n + phase``; None when it has another shape."""
    c, k, b, wave = S.One, 0, S.One, None
    for factor in Mul.make_args(powsimp(term, combine="exp")):
        if not factor.has(n):
            c *= factor
            continue
        if factor == n:
            k += 1
            continue
        if isinstance(factor, Pow) and factor.base == n and factor.exp.is_Integer and factor.exp > 0:
            k += int(factor.exp)
            continue
        if isinstance(factor, exp):
            lin = _linear(factor.args[0], n)
            if lin is None:
                return None
            b *= exp(lin[0])
            c *= exp(lin[1])
            continue
        if isinstance(factor, Pow) and not factor.base.has(n):
            lin = _linear(factor.exp, n)
            if lin is None:
                return None
            b *= factor.base ** lin[0]
            c *= factor.base ** lin[1]
            continue
        if isinstance(factor, (sin, cos)) and wave is None:
            lin = _linear(factor.args[0], n)
            if lin is None:
                return None
            wave = ("sin" if isinstance(factor, sin) else "cos", lin[0], lin[1])
            continue
        return None
    return c, k, b, wave


def _delta(term: Expr, n: Symbol) -> Optional[Tuple[Expr, Expr]]:
    """``term`` as ``(c, m)`` with ``term == c * KroneckerDelta(n, m)``."""
    deltas = [f for f in Mul.make_args(term) if isinstance(f, KroneckerDelta)]
    if len(deltas) != 1:
        return None
    d = deltas[0]
    i, j = d.args
    m = j if i == n else i if j == n else None
    if m is None or m.has(n) or not (m.is_integer and m.is_nonnegative):
        return None
    c = (term / d).subs(n, m)
    return c, m


def _table(f: Expr, n: Symbol, z: Symbol) -> Optional[Tuple[Expr, List[Expr], bool]]:
    """``f`` term by term from the table: (F, the bases b whose |b| bound |z|
    from below, whether z = 0 is excluded), or None when a term is not in it."""
    if f.has(HyperbolicFunction):
        f = f.rewrite(exp)
    out, radii, nonzero = S.Zero, [], False
    for term in Add.make_args(expand(f)):
        found = _delta(term, n)
        if found is not None:
            c, m = found
            out += c * z ** (-m)
            nonzero = nonzero or m != 0
            continue
        found = _geometric(term, n)
        if found is None:
            return None
        c, k, b, wave = found
        if wave is None:
            F = z / (z - b)
        else:
            # b**n sin(w n + p) = b**n (sin(w n) cos(p) + cos(w n) sin(p))
            kind, w, p = wave
            den = z ** 2 - 2 * b * z * cos(w) + b ** 2
            S_, C_ = b * z * sin(w) / den, z * (z - b * cos(w)) / den
            F = S_ * cos(p) + C_ * sin(p) if kind == "sin" else C_ * cos(p) - S_ * sin(p)
            if not (w.is_real or w.is_real is None):
                return None
        for _ in range(k):
            F = -z * diff(F, z)
        out += c * F
        radii.append(b)
    return out, radii, nonzero


def _tidy(F: Expr, z: Symbol) -> Expr:
    """A rational function of z as one fraction; exponentials of i*w as
    cosines and sines when the imaginary unit then goes away."""
    F = cancel(together(F))
    if F.has(S.ImaginaryUnit):
        G = simplify(F.rewrite(cos))
        if not G.has(S.ImaginaryUnit):
            F = cancel(together(G))
            num, den = fraction(F)
            F = simplify(num) / simplify(den)
    if not F.has(S.ImaginaryUnit):
        try:
            F = factor(F, z)              # z/(z - 1)**2 rather than z/(z**2 - 2*z + 1)
        except Exception:
            pass
    return F


def _radius(radii: List[Expr]) -> Expr:
    sizes = [simplify(Abs(b)) for b in radii]
    if not sizes:
        return S.Zero
    return sizes[0] if len(sizes) == 1 else Max(*sizes)


def _radius_from(cond, z: Symbol) -> Optional[Expr]:
    """The R of ``|z| > R`` in a condition summation gave, when it has that shape."""
    r = Symbol("r", positive=True)
    c = cond.subs(Abs(z), r).subs(z, r)
    rel = getattr(c, "rel_op", None)
    if rel in ("<", "<="):
        small, big = c.lhs, c.rhs
    elif rel in (">", ">="):
        small, big = c.rhs, c.lhs
    else:
        return None
    if big.has(r) or not small.has(r):
        return None
    K = simplify(small * r)
    if K.has(r):
        return None
    return simplify(K / big)


def z_transform(f: Expr, n: Symbol, z: Symbol, simplify_result: bool = True) -> Tuple[Expr, Expr, bool]:
    """``(F(z), R, nonzero)``: the one-sided z-transform of the sequence
    ``f(n)``, converging for ``|z| > R`` (and ``z != 0`` when ``nonzero``)."""
    f = S(f)
    found = _table(f, n, z)
    if found is not None:
        F, radii, nonzero = found
        return (_tidy(F, z) if simplify_result else F), _radius(radii), nonzero
    s = summation(f * z ** (-n), (n, 0, oo))
    if isinstance(s, Piecewise):
        (F, cond) = s.args[0]
        if F.has(Sum):
            raise ZTransformError(f"SymPy could not sum {f} * z**(-n) over n >= 0")
        R = _radius_from(cond, z) if cond is not S.true else S.Zero
        if R is None:
            raise ZTransformError(f"the sum converges where {cond}, which is not a region |z| > R")
        return (_tidy(F, z) if simplify_result else F), R, False
    if s.has(Sum):
        raise ZTransformError(f"SymPy could not sum {f} * z**(-n) over n >= 0")
    return (_tidy(s, z) if simplify_result else s), S.Zero, False


def _from_poles(G: Expr, z: Symbol, n: Symbol) -> Expr:
    """The sequence of ``z * G(z)``, ``G`` a proper rational function, from
    its poles: ``A z / (z - p)**j  <->  A binomial(n, j - 1) p**(n - j + 1)``
    (``A KroneckerDelta(n, j - 1)`` for a pole at 0)."""
    num, den = fraction(cancel(G))
    pd = Poly(den, z)
    poles = roots(pd)
    if sum(poles.values()) != pd.degree():
        raise ZTransformError(f"the poles - the roots of {pd.as_expr()} = 0 - cannot be found in closed form")
    total = S.Zero
    for p, m in poles.items():
        H = cancel(G * (z - p) ** m)
        for j in range(1, m + 1):
            A = cancel(diff(H, z, m - j).subs(z, p) / factorial(m - j))
            if A == 0:
                continue
            if p == 0:
                total += A * KroneckerDelta(n, j - 1)
            else:
                total += A * binomial(n, j - 1) * p ** (n - j + 1)
    return expand_func(total)


def _oscillation(t: Expr, z: Symbol, n: Symbol) -> Optional[Expr]:
    """The sequence of ``z * t``, ``t = (A z + B) / (z**2 + beta z + gamma)``
    with complex poles ``r e^(+-i theta)``: ``A r**n cos(theta n) + (B + A r
    cos(theta)) / (r sin(theta)) r**n sin(theta n)``; None otherwise."""
    num, den = fraction(cancel(t))
    pd = Poly(den, z)
    if pd.degree() != 2:
        return None
    pn = Poly(num, z)
    if pn.degree() > 1:
        return None
    lead = pd.LC()
    _, beta, gamma = [cf / lead for cf in pd.all_coeffs()]
    A, B = (pn.all_coeffs() if pn.degree() == 1 else [S.Zero, pn.LC()])
    A, B = A / lead, B / lead
    if gamma.is_positive is False or gamma.is_zero:
        return None
    r = simplify(sqrt(gamma))
    c = simplify(-beta / (2 * r))
    if (c ** 2 - 1).is_nonnegative is not False and not isinstance(c, cos) and not isinstance(-c, cos):
        return None                      # real poles, or no telling
    if isinstance(c, cos):
        theta, s_ = c.args[0], sin(c.args[0])
    elif isinstance(-c, cos):
        theta, s_ = pi - (-c).args[0], sin((-c).args[0])   # -cos(x) = cos(pi - x)
    else:
        theta, s_ = acos(c), sqrt(1 - c ** 2)
    return A * r ** n * cos(theta * n) + (B + A * r * c) / (r * s_) * r ** n * sin(theta * n)


def inverse_z_transform(F: Expr, z: Symbol, n: Symbol) -> Expr:
    """The causal sequence ``f(n)`` (n >= 0) whose z-transform is the rational
    function ``F(z)``."""
    F = cancel(together(S(F)))
    if not F.has(z):
        return F * KroneckerDelta(n, 0)
    if not F.is_rational_function(z):
        raise ZTransformError(f"{F} is not a rational function of {z}")
    top, bottom = fraction(F)
    if Poly(top, z).degree() > Poly(bottom, z).degree():
        raise ZTransformError(f"{F} grows like a power of {z}: no causal sequence (n >= 0) has it as its z-transform")
    G = cancel(F / z)
    total = S.Zero
    for t in Add.make_args(apart(G, z)):
        wave = _oscillation(t, z, n)
        total += wave if wave is not None else _from_poles(t, z, n)
    tidy = simplify(total)
    if tidy.has(S.ImaginaryUnit):
        alt = simplify(powsimp(tidy, force=True).rewrite(cos))
        if not alt.has(S.ImaginaryUnit):
            tidy = alt
    return tidy

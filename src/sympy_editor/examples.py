"""Example expressions: what a new session can start from (the drawer's
"New session" chooser), and the demos of ``examples/``.

Each entry is ``(name, expression)``; :func:`examples` gives them as
JSON-able ``{"name", "src", "srepr"}`` records (``srepr`` rebuilds the
expression in a Pyodide document).
"""

from __future__ import annotations

from typing import Any, Dict, List

from sympy import (Array, Determinant, Eq, Function, ImmutableMatrix, Integral, Limit, MatrixSymbol, Rational,
                   Sum, Trace, cos, exp, eye, oo, pi, sin, sqrt, srepr, symbols)

__all__ = ["EXAMPLES", "examples"]

x, y, z, t, n, a, b, c, theta = symbols("x y z t n a b c theta")
f = Function("f")
A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)

EXAMPLES = [
    ("Gaussian integral and a series", Integral(exp(-(x**2) / 2) / sqrt(2 * pi), (x, -oo, y)) + Sum(f(n) / n**2, (n, 1, oo)) - sin(x) / (x + 1)),
    ("Quadratic formula", Eq(x, (-b + sqrt(b**2 - 4 * a * c)) / (2 * a))),
    ("A limit", Limit((1 + 1 / n) ** n, n, oo)),
    ("Trigonometric identity", Eq(sin(x) ** 2 + cos(x) ** 2, 1)),
    ("Rational arithmetic", Rational(1, 2) + Rational(1, 3) * x - Rational(3, 4) * x**2),
    # (immutable matrices: their srepr rebuilds them; a BlockMatrix's does not)
    ("Rotation matrix times a vector", ImmutableMatrix([[cos(theta), -sin(theta)], [sin(theta), cos(theta)]]) * ImmutableMatrix([x, y])),
    ("Dense matrix", ImmutableMatrix([[x, y], [z, t]])),
    ("Matrix symbols", A * B + 2 * A.T - A.I),
    ("Determinant and trace", Determinant(ImmutableMatrix([[x, y], [z, t]])) + Trace(A) + x * Determinant(eye(2))),
    ("3-D array", Array([[[x, 1], [y, 2]], [[z, 3], [t, 4]]])),
]


def examples() -> List[Dict[str, Any]]:
    """The examples as ``{"name", "src", "srepr"}`` records."""
    return [{"name": name, "src": str(expr), "srepr": srepr(expr)} for name, expr in EXAMPLES]

"""A shelf of derivations, each one a `History` the viewer can play.

These are the sort of thing the history viewer is for: a result nobody
believes in one step, written out as the steps somebody would actually take.
They are ordinary Python - a list of expressions and a word about what turned
each into the next - so they double as an answer to "what would I use this
for?".

Every derivation is a function returning a :class:`sympy_editor.History`;
:data:`DERIVATIONS` collects them.  ``python examples/derivations/build.py``
writes one page per derivation and an index.
"""

from __future__ import annotations

from sympy import (Derivative, Determinant, Eq, Function, I, Integral, Inverse, Limit, Matrix,
                   Integer, MatrixSymbol, Rational, Sum, Symbol, ZeroMatrix, cos, exp, eye, factorial, log, oo, pi,
                   sin, sqrt, symbols)

x, y, r, t, h, n, k = symbols("x y r t h n k", real=True)
a, b, c = symbols("a b c", real=True)
theta = Symbol("theta", real=True)
lam = Symbol("lambda")
m, omega = symbols("m omega", positive=True)


def quadratic_formula():
    """Completing the square, which is where the formula comes from."""
    from sympy_editor import History

    return History([
        Eq(a * x**2 + b * x + c, 0),
        (Eq(x**2 + b * x / a + c / a, 0), "divide by a: the square is monic now"),
        (Eq(x**2 + b * x / a, -c / a), "the constant to the other side"),
        (Eq(x**2 + b * x / a + b**2 / (4 * a**2), b**2 / (4 * a**2) - c / a),
         "add (b/2a)² to both sides - completing the square"),
        (Eq((x + b / (2 * a))**2, (b**2 - 4 * a * c) / (4 * a**2)), "the left is a square, the right one fraction"),
        (Eq(x + b / (2 * a), sqrt(b**2 - 4 * a * c) / (2 * a)), "take the root (the ± is the second solution)"),
        (Eq(x, (-b + sqrt(b**2 - 4 * a * c)) / (2 * a)), "and x is alone"),
    ], title="The quadratic formula, by completing the square")


def gaussian_integral():
    """The trick worth knowing: square it, and the plane is polar."""
    from sympy_editor import History

    u = Symbol("u", positive=True)
    return History([
        Integral(exp(-x**2), (x, -oo, oo)),
        (Integral(exp(-x**2), (x, -oo, oo)) * Integral(exp(-y**2), (y, -oo, oo)),
         "square it: the second copy is in another variable"),
        (Integral(Integral(exp(-x**2 - y**2), (x, -oo, oo)), (y, -oo, oo)),
         "one integral over the plane"),
        (Integral(Integral(exp(-r**2) * r, (r, 0, oo)), (theta, 0, 2 * pi)),
         "polar coordinates: x² + y² = r², and the area element brings an r"),
        (2 * pi * Integral(exp(-r**2) * r, (r, 0, oo)), "nothing depends on θ"),
        (2 * pi * Integral(exp(-u) / 2, (u, 0, oo)), "u = r², so r dr = du/2"),
        (pi, "the integral of e^-u is 1, so the square of what we want is π"),
        (sqrt(pi), "and the integral itself is its root"),
    ], title="The Gaussian integral")


def geometric_series():
    """Why 1/(1-x), in the two lines it takes."""
    from sympy_editor import History

    S = Function("S")
    return History([
        Eq(S(x), Sum(x**n, (n, 0, oo))),
        (Eq(x * S(x), Sum(x**(n + 1), (n, 0, oo))), "multiply by x: every power moves up one"),
        (Eq(S(x) - x * S(x), 1), "subtract - everything cancels but the first term"),
        (Eq(S(x) * (1 - x), 1), "S is a common factor"),
        (Eq(S(x), 1 / (1 - x)), "so the sum is 1/(1-x), for |x| < 1"),
    ], title="The geometric series")


def eulers_identity():
    """The most famous identity, through the series that make it obvious."""
    from sympy_editor import History

    return History([
        exp(I * x),
        (Sum((I * x)**n / factorial(n), (n, 0, oo)), "the exponential is its series"),
        (Sum((-1)**n * x**(2 * n) / factorial(2 * n), (n, 0, oo))
         + I * Sum((-1)**n * x**(2 * n + 1) / factorial(2 * n + 1), (n, 0, oo)),
         "i² = -1 sorts the terms into two series: the even ones real, the odd ones imaginary"),
        (cos(x) + I * sin(x), "and those two are cos and sin"),
        (Eq(exp(I * x), cos(x) + I * sin(x)), "which is Euler's formula"),
        # `evaluate=False` or SymPy answers the question before it is asked:
        # exp(I*pi) is -1 the moment it is built.
        (Eq(exp(I * pi, evaluate=False), Integer(-1), evaluate=False),
         "at x = π, where cos π = -1 and sin π = 0"),
        (Eq(exp(I * pi, evaluate=False) + 1, Integer(0), evaluate=False), "which is Euler's identity"),
    ], title="Euler's identity")


def derivative_from_first_principles():
    """What a derivative is, before any rule for computing one."""
    from sympy_editor import History

    # dir="+-": the limit is two-sided, as a derivative's is - SymPy's default
    # is the one from the right, and it says so with a "+" in the rendering.
    return History([
        Derivative(x**3, x),
        (Limit(((x + h)**3 - x**3) / h, h, 0, dir="+-"), "the definition: the slope of a chord, as it closes"),
        (Limit((3 * x**2 * h + 3 * x * h**2 + h**3) / h, h, 0, dir="+-"), "expand the cube; the x³ cancels"),
        (Limit(3 * x**2 + 3 * x * h + h**2, h, 0, dir="+-"), "divide through by h - legal, h is not yet 0"),
        (3 * x**2, "now let h go: what is left is the derivative"),
    ], title="The derivative of x³ from first principles")


def partial_fractions():
    """Splitting a fraction is what makes it integrable."""
    from sympy_editor import History

    return History([
        Integral(1 / (x**2 - 1), x),
        (Integral(1 / ((x - 1) * (x + 1)), x), "factor the denominator"),
        (Integral(Rational(1, 2) / (x - 1) - Rational(1, 2) / (x + 1), x),
         "partial fractions: one term per root"),
        (Integral(Rational(1, 2) / (x - 1), x) - Integral(Rational(1, 2) / (x + 1), x),
         "an integral of a sum is a sum of integrals"),
        (log(x - 1) / 2 - log(x + 1) / 2, "and each is a logarithm"),
        (log((x - 1) / (x + 1)) / 2, "one logarithm, by the quotient rule"),
    ], title="Partial fractions, and the integral they unlock")


def eigenvalues_of_a_matrix():
    """Where eigenvalues come from: a determinant that must vanish."""
    from sympy_editor import History

    M = Matrix([[2, 1], [1, 2]])
    return History([
        M,
        (M - lam * eye(2), "subtract λ from the diagonal"),
        (Determinant(M - lam * eye(2)), "an eigenvector needs this matrix to be singular"),
        (Eq((2 - lam)**2 - 1, 0), "so its determinant is zero: the characteristic equation"),
        (Eq(lam**2 - 4 * lam + 3, 0), "expand it"),
        (Eq((lam - 1) * (lam - 3), 0), "factor it"),
        (Matrix([1, 3]), "the eigenvalues, λ = 1 and λ = 3"),
        (Matrix([[1, 1], [-1, 1]]), "and their eigenvectors, (1, -1) and (1, 1)"),
    ], title="The eigenvalues of a 2×2 matrix")


def gaussian_elimination():
    """A system of equations, solved the way it is solved on paper."""
    from sympy_editor import History

    return History([
        Matrix([[2, 1, -1, 8], [-3, -1, 2, -11], [-2, 1, 2, -3]]),
        (Matrix([[2, 1, -1, 8], [0, Rational(1, 2), Rational(1, 2), 1], [-2, 1, 2, -3]]),
         "row 2 + 3/2 · row 1: the first column is clear below the pivot"),
        (Matrix([[2, 1, -1, 8], [0, Rational(1, 2), Rational(1, 2), 1], [0, 2, 1, 5]]),
         "row 3 + row 1"),
        (Matrix([[2, 1, -1, 8], [0, Rational(1, 2), Rational(1, 2), 1], [0, 0, -1, 1]]),
         "row 3 - 4 · row 2: an upper triangle - the system is solved by substitution now"),
        (Matrix([[2, 1, 0, 7], [0, Rational(1, 2), 0, Rational(3, 2)], [0, 0, -1, 1]]),
         "back-substitute z = -1"),
        (Matrix([[1, 0, 0, 2], [0, 1, 0, 3], [0, 0, 1, -1]]),
         "and y = 3, x = 2: the reduced row echelon form"),
        (Matrix([2, 3, -1]), "which is the solution"),
    ], title="Gaussian elimination on a 3×3 system")


def harmonic_oscillator():
    """Physics in one page: a Lagrangian in, an equation of motion out."""
    from sympy_editor import History

    q, L = Function("x"), Function("L")
    v = Symbol("v")
    return History([
        Eq(L(x, v), m * v**2 / 2 - m * omega**2 * x**2 / 2),
        (Eq(Derivative(L(x, v), v), m * v), "the momentum: ∂L/∂v"),
        (Eq(Derivative(L(x, v), x), -m * omega**2 * x), "the force: ∂L/∂x"),
        (Eq(Derivative(m * Derivative(q(t), t), t), -m * omega**2 * q(t)),
         "Euler-Lagrange: d/dt (∂L/∂v) = ∂L/∂x"),
        (Eq(Derivative(q(t), t, 2), -omega**2 * q(t)), "the mass divides out"),
        (Eq(q(t), Symbol("A") * cos(omega * t + Symbol("phi"))),
         "and the solution is a cosine of frequency ω"),
    ], title="The harmonic oscillator, from its Lagrangian")


def least_squares():
    """Why the normal equations look the way they do."""
    from sympy_editor import History

    rows, cols = symbols("m n", positive=True, integer=True)
    A = MatrixSymbol("A", rows, cols)
    bb = MatrixSymbol("b", rows, 1)
    xx = MatrixSymbol("x", cols, 1)
    return History([
        (A * xx - bb).T * (A * xx - bb),
        (xx.T * A.T * A * xx - 2 * xx.T * A.T * bb + bb.T * bb,
         "expand: the two cross terms are equal, being one number each"),
        # `evaluate=False`: SymPy can see the two sides are not the same
        # expression and answers "False" - which is not what an equation to
        # be solved means.
        (Eq(2 * A.T * A * xx - 2 * A.T * bb, ZeroMatrix(cols, 1), evaluate=False),
         "at the minimum the gradient in x vanishes"),
        (Eq(A.T * A * xx, A.T * bb, evaluate=False), "the normal equations"),
        (Eq(xx, Inverse(A.T * A) * A.T * bb, evaluate=False), "and the least-squares solution"),
    ], title="Least squares and the normal equations")


#: Every derivation on the shelf, in the order the index shows them.
DERIVATIONS = [
    ("quadratic-formula", quadratic_formula),
    ("gaussian-integral", gaussian_integral),
    ("geometric-series", geometric_series),
    ("eulers-identity", eulers_identity),
    ("derivative-from-first-principles", derivative_from_first_principles),
    ("partial-fractions", partial_fractions),
    ("eigenvalues", eigenvalues_of_a_matrix),
    ("gaussian-elimination", gaussian_elimination),
    ("harmonic-oscillator", harmonic_oscillator),
    ("least-squares", least_squares),
]

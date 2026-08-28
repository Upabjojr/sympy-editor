import pytest
from sympy import (
    Pow,
    Abs, Derivative, Eq, Function, ImmutableMatrix, Integral, Lambda, Limit, Matrix,
    Piecewise, Rational, Subs, Sum, cos, exp, latex, log, oo, pi, sin, sqrt, symbols, sympify,
)

from sympy_editor.printer import (
    annotate, delete_at, format_path, get_at, parse_path, replace_at, strip_annotations,
)

x, y, z = symbols("x y z")
f = Function("f")

EXPRS = [
    x,
    x - y,
    -x + y,
    x / y**2,
    2 * x / y**2,
    x**2 / y**2 + 1,
    sin(x) / cos(y) - exp(-x),
    Integral(f(x) ** 2, (x, 0, oo)),
    Integral(x**2, (x, 0, 2)),
    Eq(x**2 - 1, (x - 1) * (x + 1)),
    ImmutableMatrix([[x, 1 / y], [2, -z]]),
    Matrix([[2, 2], [2, 2]]),
    sqrt(x) + x ** Rational(-1, 2),
    Sum(1 / x**y, (y, 1, oo)),
    Sum(x**y, (y, 1, oo)),
    Sum(y, (y, 1, oo)),
    (x + y) * z - 1 / (x + 1),
    Piecewise((x, x > 0), (-x, True)),
    Rational(1, 3) * x - 2 * y / 3,
    Derivative(f(x, y), x, y),
    Derivative(f(x), (x, 2)),
    x ** (-y),
    exp(-(x**2) / 2) / sqrt(2 * pi),
    log(x) / log(2),
    Abs(x - 1),
    (x - 1) ** -1 * (y - 1) ** -1,
    Lambda((x, y), x + y),
    Subs(f(x), x, 0),
    Limit(sin(x) / x, x, 0),
]


@pytest.mark.parametrize("expr", EXPRS, ids=str)
def test_annotations_are_transparent(expr):
    tex, nodes = annotate(expr)
    assert strip_annotations(tex) == latex(expr)


@pytest.mark.parametrize("expr", EXPRS, ids=str)
def test_paths_point_to_the_printed_nodes(expr):
    expr = sympify(expr)
    tex, nodes = annotate(expr)
    assert () in nodes and nodes[()] == expr
    for path, node in nodes.items():
        actual = get_at(expr, path)
        # A denominator raised to a power is printed as the reciprocal of the
        # tree's node (Pow(b, -n) shown as b**n under the fraction bar).
        reciprocal = (isinstance(actual, Pow) and isinstance(node, Pow)
                      and actual.base == node.base and actual.exp == -node.exp)
        assert actual == node or reciprocal
        assert r"\htmlData{path=%s}{" % format_path(path) in tex


def test_negative_add_term_is_annotated_with_sign():
    tex, nodes = annotate(x - y)
    assert r"\htmlData{path=/1}{- \htmlData{path=/1/1}{y}}" in tex
    assert nodes[(1,)] == -y


def test_bound_variables_map_to_limits_not_body():
    tex, _ = annotate(Sum(x**y, (y, 1, oo)))
    assert r"\sum_{\htmlData{path=/1/0}{y}=" in tex
    assert r"^{\htmlData{path=/0/1}{y}}" in tex
    tex, _ = annotate(Integral(x**2, (x, 0, 2)))
    assert r"^{\htmlData{path=/1/2}{2}}" in tex          # upper limit
    assert r"\htmlData{path=/0/1}{2}" in tex               # exponent


def test_matrix_elements_are_not_confused_with_shape():
    tex, nodes = annotate(ImmutableMatrix([[x, 1 / y], [2, -z]]))
    assert r"\htmlData{path=/2/2}{2}" in tex
    assert (0,) not in nodes and (1,) not in nodes


def test_denominator_base_is_reachable():
    tex, nodes = annotate(x / y**2)
    assert nodes[(1, 0)] == y


def test_settings_are_forwarded():
    tex, _ = annotate(x * y, mul_symbol="times")
    assert r"\times" in tex


def test_paths_roundtrip():
    assert format_path(()) == "/"
    assert format_path((0, 12)) == "/0/12"
    assert parse_path("/") == ()
    assert parse_path("") == ()
    assert parse_path("/0/12") == (0, 12)
    with pytest.raises(ValueError):
        parse_path("/a")


def test_replace_and_delete():
    e = x**2 + y
    assert replace_at(e, (), z) == z
    new = replace_at(e, (1, 1), 3)  # exponent of x**2 (Add args are ordered: y, x**2)
    assert new == x**3 + y
    assert delete_at(x + y + z, (0,)) == y + z
    with pytest.raises(ValueError):
        delete_at(x, ())
    with pytest.raises(ValueError):
        get_at(x + y, (5,))


def test_strip_annotations_handles_nesting_and_escapes():
    tex = r"\htmlData{path=/}{\left\{\htmlData{path=/0}{x}\right\}}"
    assert strip_annotations(tex) == r"\left\{x\right\}"
    assert strip_annotations("plain") == "plain"

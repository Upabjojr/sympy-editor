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



@pytest.mark.parametrize("expr", EXPRS + [Matrix([[1, 2], [-33, 4]]), Matrix([[x, 1 / y], [2, -z]]), Matrix([[x, y, z]]), Matrix([x, y])], ids=str)
def test_str_spans_match_str(expr):
    from sympy_editor.printer import annotate_str
    expr = sympify(expr)
    text, spans = annotate_str(expr)
    assert text == str(expr)
    assert spans and "/" in spans and spans["/"] == (0, len(text))
    for path, (start, end) in spans.items():
        node = get_at(expr, parse_path(path))
        piece = text[start:end]
        # a negated term is printed without its sign; a denominator as its reciprocal
        forms = {str(node)}
        for make in (lambda n: -n, lambda n: 1 / n):
            try:
                forms.add(str(make(node)))
            except Exception:
                pass
        assert piece in forms, (path, piece, str(node))


def test_latex_and_source_spans_share_keys():
    from sympy_editor import annotate_str, latex_spans
    e = x**2 + sin(x) / 3
    tex, tspans = latex_spans(e)
    src, sspans = annotate_str(e)
    assert tex == latex(e) and src == str(e)
    assert set(tspans) == set(sspans)                   # same node paths on both sides
    p = next(k for k, (a, b) in sspans.items() if src[a:b] == "sin(x)")
    a, b = tspans[p]
    assert tex[a:b] == r"\sin{\left(x \right)}"


def test_rational_numerator_and_denominator_have_paths():
    from sympy import Rational, symbols
    from sympy_editor import annotate, annotate_str, get_at, parse_path, replace_at, strip_annotations
    from sympy_editor.printer import delete_at
    from sympy import latex
    x = symbols("x")
    for expr in (Rational(1, 2), x - Rational(1, 2), -Rational(3, 4), Rational(1, 2) ** 2, x + Rational(3, 4) * x**2):
        tex, nodes = annotate(expr)
        assert strip_annotations(tex) == latex(expr)
        text, spans = annotate_str(expr)
        assert text == str(expr) and spans, expr
        for path, node in nodes.items():
            assert get_at(expr, path) == node
    tex, nodes = annotate(x - Rational(1, 2))
    assert nodes[(0, "n")] == -1 and nodes[(0, "d")] == 2       # the tree's number is -1/2, printed as - 1/2
    assert r"\htmlData{path=/0/n}{1}" in tex and r"\htmlData{path=/0/d}{2}" in tex
    text, spans = annotate_str(x - Rational(1, 2))
    assert text[slice(*spans["/0/n"])] == "1" and text[slice(*spans["/0/d"])] == "2"
    assert parse_path("/0/n") == (0, "n")
    assert replace_at(x - Rational(1, 2), (0, "n"), 3) == x + Rational(3, 2)
    assert replace_at(Rational(1, 2), ("d",), x) == 1 / x
    with pytest.raises(ValueError):
        delete_at(Rational(1, 2), ("n",))
    with pytest.raises(ValueError):
        get_at(x, ("n",))
    # a coefficient is printed as a fraction of the product, not as a number: no parts
    tex, nodes = annotate(Rational(1, 2) * x)
    assert not any("n" in p or "d" in p for p in nodes)

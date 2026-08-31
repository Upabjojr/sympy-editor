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
        assert get_at(expr, path) == node
        assert r"\htmlData{path=%s}{" % format_path(path) in tex


def test_negative_add_term_is_annotated_with_sign():
    tex, nodes = annotate(x - y)
    assert r"\htmlData{path=/1}{- \htmlData{path=/1/neg}{y}}" in tex
    assert nodes[(1,)] == -y and nodes[(1, "neg")] == y


def test_view_parts_follow_what_is_shown():
    """Paths address the view tree: what the printer shows, not the args."""
    from sympy import E, Integer, Rational, exp
    from sympy_editor.printer import delete_at, replace_at, view_parts
    n = symbols("n")
    paths = lambda e: {format_path(p): str(v) for p, v in annotate(e)[1].items()}
    # 1/n is Pow(n, -1): the 1 exists nowhere in the tree
    assert paths(1 / n) == {"/": "1/n", "/n": "1", "/d": "n"}
    assert replace_at(1 / n, ("n",), x) == x / n and replace_at(1 / n, ("d",), x + 1) == 1 / (x + 1)
    # 1/(2e) is Mul(1/2, exp(-1)): shown as 1 over 2e
    e = 1 / (2 * E)
    assert paths(e) == {"/": "exp(-1)/2", "/n": "1", "/d": "2*E", "/d/0": "2", "/d/1": "E"}
    assert get_at(e, ("d",)) == 2 * E and replace_at(e, ("d", 0), 3) == 1 / (3 * E) and replace_at(e, ("d", 1), x) == 1 / (2 * x)
    # a product with a leading minus: the coefficient is shown positive
    assert paths(-2 * x * y) == {"/": "-2*x*y", "/neg": "2*x*y", "/neg/0": "2", "/neg/1": "x", "/neg/2": "y"}
    assert replace_at(-2 * x * y, ("neg", 0), 3) == -3 * x * y and delete_at(-2 * x * y, ("neg", 0)) == -x * y
    # a term of a sum: sign, then the product, then the fraction inside it
    e = x - y / 2
    assert paths(e) == {"/": "x - y/2", "/0": "x", "/1": "-y/2", "/1/neg": "y/2", "/1/neg/n": "y", "/1/neg/d": "2"}
    assert replace_at(e, (1, "neg", "d"), 3) == x - y / 3 and delete_at(e, (1, "neg")) == x
    # a product with numerator and denominator: the numerator is a product of its own
    e = x * y / z
    assert paths(e) == {"/": "x*y/z", "/n": "x*y", "/n/0": "x", "/n/1": "y", "/d": "z"}
    assert view_parts(e) == [("n", get_at(e, ("n",))), ("d", z)] and get_at(e, ("n",)) == x * y
    assert delete_at(e, ("n",)) == 1 / z and delete_at(e, ("d",)) == x * y and delete_at(e, ("n", 0)) == y / z
    # a denominator raised to a power: the tree's Pow(x + 1, -2)
    e = x / (x + 1) ** 2
    assert paths(e)["/d"] == "(x + 1)**2" and paths(e)["/d/1"] == "2" and replace_at(e, ("d", 1), 3) == x / (x + 1) ** 3
    # nodes printed as they are have no parts
    assert view_parts(x * y) is None and view_parts(Integer(3)) is None and view_parts(exp(-x)) is None
    assert view_parts(x ** -Rational(1, 2)) is None                         # 1 over a root: the base is printed as is
    assert view_parts(x ** -Rational(1, 2), {"root_notation": False}) is not None
    tex, nodes = annotate(x ** -Rational(1, 2), root_notation=False)
    assert strip_annotations(tex) == latex(x ** -Rational(1, 2), root_notation=False) and ("d",) in nodes
    # the source line prints 1/(2e) as exp(-1)/2: real arguments are still found
    from sympy_editor.printer import annotate_str
    text, spans = annotate_str(1 / (2 * E))
    assert text[slice(*spans["/1"])] == "exp(-1)" and text[slice(*spans["/d/0"])] == "2"


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
    assert nodes[("d", 0)] == y and nodes[("d",)] == y**2 and (1,) not in nodes


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
    assert nodes[(0, "n")] == 1 and nodes[(0, "d")] == 2        # the tree's number is -1/2, printed as - 1/2
    assert replace_at(x - Rational(1, 2), (0, "n"), 3) == x - Rational(3, 2)     # the sign stays in front
    assert r"\htmlData{path=/0/n}{1}" in tex and r"\htmlData{path=/0/d}{2}" in tex
    text, spans = annotate_str(x - Rational(1, 2))
    assert text[slice(*spans["/0/n"])] == "1" and text[slice(*spans["/0/d"])] == "2"
    assert parse_path("/0/n") == (0, "n")
    assert replace_at(Rational(1, 2), ("d",), x) == 1 / x
    assert delete_at(Rational(3, 2), ("n",)) == Rational(1, 2) and delete_at(Rational(3, 2), ("d",)) == 3
    with pytest.raises(ValueError):
        get_at(x, ("n",))
    # a coefficient is printed as a fraction of the product: the parts are the product's, not the number's
    tex, nodes = annotate(Rational(1, 2) * x)
    assert nodes[("n",)] == x and nodes[("d",)] == 2 and (0, "d") not in nodes and (0,) not in nodes


def test_a_determinant_annotates_the_matrix_it_encloses():
    """SymPy's LatexPrinter draws |...| around a matrix's *contents*, never
    printing the matrix itself, so the matrix node got no annotation: the
    view tree jumped from the Determinant to the entries and ↑/↓ skipped it.
    """
    from sympy import Determinant, Matrix, MatrixSymbol

    x, y = symbols("x y")
    tex, nodes = annotate(Determinant(Matrix([[x]])))
    paths = {format_path(p): n for p, n in nodes.items()}
    assert paths["/"].func is Determinant
    assert paths["/0"] == Matrix([[x]])                      # the matrix is a step of its own
    assert "/0/2/0" in paths and paths["/0/2/0"] == x        # and its entries are still there
    assert r"\htmlData{path=/0}{\begin{matrix}" in tex       # inside the bars, without its own brackets
    assert r"\left[" not in tex

    # bigger matrices, and a determinant of a matrix symbol, work the same
    tex, nodes = annotate(Determinant(Matrix([[x, 1], [2, y]])))
    paths = {format_path(p) for p in nodes}
    assert {"/", "/0", "/0/2/0", "/0/2/3"} <= paths
    tex, nodes = annotate(Determinant(MatrixSymbol("A", 2, 2)))
    assert {format_path(p) for p in nodes} == {"/", "/0"}

    # and the enclosing expression's paths still lead into it
    tex, nodes = annotate(y + Determinant(Matrix([[x]])))
    paths = {format_path(p): n for p, n in nodes.items()}
    assert paths["/1/0"] == Matrix([[x]]) and paths["/1/0/2/0"] == x

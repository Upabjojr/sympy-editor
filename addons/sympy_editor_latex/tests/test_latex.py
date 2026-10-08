"""The LaTeX reader: readings by convention, ambiguities as choices,
constants as switches, and the document methods."""
import sys
from pathlib import Path

import pytest
from sympy import (Derivative, E, Eq, EulerGamma, Function, I, Integral, Limit, Matrix, Mul, Rational, Sum, Symbol, cos, exp, log, oo,
                   pi, sin, sqrt, symbols)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("lark")

from sympy_editor import Document  # noqa: E402
from sympy_editor_latex import ADDON, CONSTANTS, LatexReader, read_latex  # noqa: E402

x, y, z, a, b, c, n, k, r, m = symbols("x y z a b c n k r m")
f, g = Function("f"), Function("g")


@pytest.fixture(scope="module")
def reader():
    return LatexReader()


READINGS = [
    (r"x^2y", x**2 * y),
    (r"2x", 2 * x),
    (r"\sin x", sin(x)),
    (r"\sin^2 x", sin(x) ** 2),
    (r"\sin x \cos y", sin(x) * cos(y)),          # a function stops at another function
    (r"\sin x + 1", sin(x) + 1),                   # ... and at a sum
    (r"\sin 2x", sin(2 * x)),                      # ... but takes the product after it
    (r"\sin(x)^2", sin(x) ** 2),                   # parentheses end the argument
    (r"\ln(x) y", y * log(x)),
    (r"\sin^2 x + \cos^2 x", sin(x) ** 2 + cos(x) ** 2),
    (r"f(x)", f(x)),                               # f, g, h apply
    (r"g(x) h(x)", g(x) * Function("h")(x)),
    (r"a(b+c)", a * (b + c)),                      # other letters multiply
    (r"\frac{d}{dx} x^2", Derivative(x**2, x)),
    (r"\frac{dy}{dx}", Derivative(y, x)),
    (r"\frac{\partial f}{\partial x}", Derivative(Symbol("f"), x)),
    (r"\frac{\partial^2 f}{\partial x^2}", Derivative(Symbol("f"), (x, 2))),
    (r"\int_0^1 x^2 dx", Integral(x**2, (x, 0, 1))),
    (r"\sum_{k=1}^n k^2", Sum(k**2, (k, 1, n))),
    (r"\lim_{x\to 0} \frac{\sin x}{x}", Limit(sin(x) / x, x, 0, dir="+-")),
    (r"e^x", exp(x)),
    (r"e^{i\pi} + 1", 0),                          # e, i and pi are constants by default
    (r"\pi r^2", pi * r**2),
    (r"a/bc", a / (b * c)),
    (r"\sqrt x", sqrt(x)),
    (r"\sqrt[3]{x}", x ** Rational(1, 3)),
    (r"x^2 y^3 z", x**2 * y**3 * z),
    (r"\begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}", Matrix([[1, 2], [3, 4]])),
    (r"E = mc^2", Eq(Symbol("E"), m * c**2)),      # E is a symbol unless switched
    (r"\infty", oo),
    (r"\vec{v} \cdot \hat{x}", Symbol("vvec") * Symbol("xhat")),
    (r"\bar x + \mathbf{A}", Symbol("xbar") + Symbol("Abold")),
    (r"\text{foo} + \operatorname{bar}", Symbol("foo") + Symbol("bar")),
    (r"\mathit{ab} c", Symbol("ab") * c),              # a named symbol, then a factor (was refused)
    (r"\mathit{ab}_d", Symbol("ab_d")),              # ... with a subscript, named as x_d is
    (r"\mathit{ab}_{d}^{2} c", Symbol("ab_d") ** 2 * c),
    (r"\frac{1}{\mathit{ab}\,d}", 1 / (Symbol("ab") * Symbol("d"))),
    (r"\sigma^2 + \Delta x", Symbol("sigma") ** 2 + Symbol("Delta") * x),
    (r"\frac{1}{2} m v^2", m * Symbol("v") ** 2 / 2),
]


@pytest.mark.parametrize("latex,expected", READINGS)
def test_the_first_reading_follows_the_conventions(reader, latex, expected):
    res = reader.read(latex)
    assert res["ok"], res.get("error")
    assert res["expr"] == expected, (res["src"], expected)


def test_ambiguities_are_choices_with_the_whole_under_each(reader):
    res = reader.read(r"\sin x \cos y + f(x) + a(b+c)")
    assert res["ok"] and res["expr"] == sin(x) * cos(y) + f(x) + a * (b + c)
    frags = {amb["fragment"]: amb for amb in res["ambiguities"]}
    assert r"\sin x \cos y" in frags and "f(x)" in frags and "a(b+c)" in frags
    fx = frags["f(x)"]
    assert [o["src"] for o in fx["options"]][fx["choice"]] == str(res["expr"])          # the chosen alternative is the reading
    assert any("f*x" in o["src"] for o in fx["options"])                                  # the other one multiplies
    # picking the other alternative changes just that
    other = next(i for i, o in enumerate(fx["options"]) if "f*x" in o["src"])
    res2 = reader.read(r"\sin x \cos y + f(x) + a(b+c)", choices={fx["key"]: other})
    assert res2["expr"] == sin(x) * cos(y) + Symbol("f") * x + a * (b + c)
    assert {amb["fragment"] for amb in res2["ambiguities"]} == set(frags)               # the same points, still offered
    assert next(amb for amb in res2["ambiguities"] if amb["fragment"] == "f(x)")["choice"] == other
    # every decision is returned; sent back with one changed, the reading is that option, nothing else moves
    assert set(res["choices"]) >= set(frags[k]["key"] for k in frags) and res["choices"][fx["key"]] == fx["choice"]
    res3 = reader.read(r"\sin x \cos y + \pi")
    inner = next(amb for amb in res3["ambiguities"] if amb["fragment"] == r"\sin x \cos y")
    for i, o in enumerate(inner["options"]):
        picked = reader.read(r"\sin x \cos y + \pi", choices={**res3["choices"], inner["key"]: i})
        assert picked["src"] == o["src"]
    # a point whose alternatives all read the same is not offered
    assert all(len({o["src"] for o in amb["options"]}) > 1 for amb in res["ambiguities"])
    # every option renders
    assert all(o["latex"] for amb in res["ambiguities"] for o in amb["options"] if not o.get("invalid"))


def test_constants_are_switches(reader):
    res = reader.read(r"e^{i\pi} + \gamma")
    assert res["ok"] and res["expr"] == Symbol("gamma") - 1
    states = {c["name"]: c["on"] for c in res["constants"]}
    assert states == {"pi": True, "e": True, "i": True, "gamma": False}
    assert all(c["label"] and c["value"] for c in res["constants"])
    res = reader.read(r"e^{i\pi} + \gamma", constants={"pi": False, "gamma": True})
    assert res["expr"] == E ** (I * Symbol("pi")) + EulerGamma
    assert {c["name"]: c["on"] for c in res["constants"]} == {"pi": False, "e": True, "i": True, "gamma": True}
    # a bound i (a summation index) is not a constant, and is not offered
    res = reader.read(r"\sum_{i=1}^n i^2")
    assert res["expr"] == Sum(Symbol("i") ** 2, (Symbol("i"), 1, n)) and res["constants"] == []
    assert set(CONSTANTS) >= {"pi", "e", "i", "gamma"}


def test_errors_are_messages_not_exceptions(reader):
    for bad in ("", r"\frac{x}", r"x +", r"\begin{foo}"):
        res = reader.read(bad)
        assert res["ok"] is False and res["error"]
    assert "position" in reader.read(r"x^2 +* y")["error"]
    assert read_latex(r"\sin x")["src"] == "sin(x)"


def test_unfinished_text_is_not_an_error(reader):
    """The panel reads as the user types: a text that stops in the middle of
    an expression, or of a command, is being typed - not wrong."""
    for unfinished in (r"\frac{x", r"\frac{x}{", r"x +", r"x^", r"\sqrt{", r"\fr", "\\", r"\frac{x}{2} + \sin"):
        res = reader.read(unfinished)
        assert res["ok"] is False and res.get("incomplete") is True and res["error"].startswith("Not finished"), unfinished
    for wrong in (r"x )", r"\foo x", r"x^2 +* y", r"x $$ y"):
        res = reader.read(wrong)
        assert res["ok"] is False and not res.get("incomplete") and "could not be read" in res["error"], wrong


def test_the_parsers_are_ready_before_the_first_reading():
    """Building them takes half a second here and seconds on a phone, which
    the first reading waited for while the user typed.  They are built in
    the background (a reading meanwhile waits for that one build), or by
    "warm"."""
    fresh = LatexReader()
    fresh.warm(background=True)
    assert fresh.read(r"\sin x")["src"] == "sin(x)"
    # switched on in a document, the add-on starts the build by itself
    import time
    from sympy_editor_latex import LatexAddon
    switched = LatexAddon()
    Document(x, addons=[switched])
    for _ in range(200):
        if switched.reader._forest_parser is not None:
            break
        time.sleep(0.05)
    assert switched.reader._forest_parser is not None
    other = LatexReader()
    doc = Document(x, addons=[ADDON])
    assert doc.handle({"action": "addon", "addon": "latex", "method": "warm"})["query"]["result"] == {"ready": True}
    other.warm()
    assert other._forest_parser is not None



def test_the_panel_s_warm_builds_them_where_there_are_no_threads(monkeypatch):
    """Issue #27: the panel asks for the parsers as soon as it is shown
    ("background").  Where there are threads they are built in one and the
    request answers at once; where there are none - Pyodide, where starting
    a thread raises - the request builds them itself."""
    import threading
    from sympy_editor_latex import LatexAddon

    def no_threads(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", no_threads)
    fresh = LatexReader()
    assert fresh.warm(background=True) is False and not fresh.ready
    addon = LatexAddon()
    doc = Document(x, addons=[addon])                   # switched on: no thread to build them in
    assert not addon.reader.ready
    res = doc.handle({"action": "addon", "addon": "latex", "method": "warm", "background": True})["query"]["result"]
    assert res == {"ready": True} and addon.reader.ready




def test_insert_over_a_range_replaces_only_the_range():
    """"Replace the selected range" with 2*x**2 + x selected in x**3 + 2*x**2 + x
    made the whole expression the reading: the range was sent and ignored."""
    doc = Document(x**3 + 2*x**2 + x, addons=[ADDON])
    children = [i for i, arg in enumerate(doc.expr.args) if arg in (2*x**2, x)]
    snap = doc.handle({"action": "addon", "addon": "latex", "method": "insert", "latex": "y", "path": "/", "children": children})
    assert not snap.get("error") and doc.expr == x**3 + y



def test_a_reading_goes_to_the_caret_or_after_the_formula():
    """Without a selection the first button is "Add to cursor" (with a caret)
    or "Add to end" (without one): the reading goes in as if typed there -
    a new argument between two, multiplied next to a node, added to it when
    the LaTeX begins with + or -."""
    def put(doc, **payload):
        snap = doc.handle(dict(payload, action="addon", addon="latex", method="insert"))
        assert not snap.get("error"), snap["error"]

    doc = Document(x + y, addons=[ADDON])
    put(doc, latex="z", end=True)
    assert doc.expr == (x + y) * z
    doc.undo()
    put(doc, latex="+ z", end=True)
    assert doc.expr == x + y + z
    doc.undo()
    put(doc, latex="-2", end=True)
    assert doc.expr == x + y - 2
    doc.undo()
    # a caret between the two terms: a new term
    put(doc, latex=r"\frac{1}{2}", caret={"action": "insert", "path": "/", "index": 1, "left": 0, "right": 1, "attach": "left"})
    assert doc.expr == x + y + Rational(1, 2)
    doc.undo()
    # a caret next to a node: multiplied after it, added before it with a sign
    xp = next(p for p, n in doc.snapshot()["nodes"].items() if n["src"] == "x")
    put(doc, latex="z", caret={"action": "extend", "path": xp, "side": "after"})
    assert doc.expr == x * z + y
    doc.undo()
    put(doc, latex="+ z", caret={"action": "extend", "path": xp, "side": "before"})
    assert doc.expr == x + y + z


def test_a_command_is_not_read_inside_a_longer_one():
    r"""\sinh x was read as sin(h*x) - \sin running into the letter h - and
    that reading came first; the panel showed two menus over the same
    "\sinh xy", each with the whole expression.  A command gives way to a
    longer one the grammar knows; letters glued on that make no command of it
    still read as they did (\sinx, \pix)."""
    from sympy import (Ge, Le, Ne, acosh, acoth, acsch, asech, asinh, atanh, cosh, coth, csch, pi, sech, sin,
                       sinh, tanh)
    reader = LatexReader()
    for tex, want in [(r"\sinh x", sinh(x)), (r"\cosh x", cosh(x)), (r"\tanh x", tanh(x)), (r"\coth x", coth(x)),
                      (r"\sech x", sech(x)), (r"\csch x", csch(x)), (r"\arsinh x", asinh(x)), (r"\arcsinh x", asinh(x)),
                      (r"\arccosh x", acosh(x)), (r"\arctanh x", atanh(x)), (r"\arccoth x", acoth(x)),
                      (r"\arcsech x", asech(x)), (r"\arccsch x", acsch(x)), (r"\sinhx", sinh(x)),
                      (r"x \geq y", Ge(x, y)), (r"x \leq y", Le(x, y)), (r"x \neq y", Ne(x, y)),
                      (r"\left( x \right)", x), (r"\sinx", sin(x)), (r"\pix", pi * x)]:
        res = reader.read(tex)
        assert res["ok"] and res["expr"] == want, (tex, res.get("src"), res.get("error"))
        assert all("(h" not in o["src"] for a in res["ambiguities"] for o in a["options"]), (tex, res["ambiguities"])
    res = reader.read(r"\sinh xy")
    assert res["src"] == "sinh(x*y)"
    assert [[o["src"] for o in a["options"]] for a in res["ambiguities"]] == [["sinh(x*y)", "y*sinh(x)"]]


def test_guard_commands_rewrites_only_the_commands_that_begin_longer_ones():
    import re
    from sympy_editor_latex.parser import GRAMMAR_DIR, guard_commands
    lines = [r'A: "\\sin"', r'B: "\\sinh"', r'C: "\\ge" | "\\geq"', r'D: "\\,"']        # as in a .lark file
    assert guard_commands("\n".join(lines)).split("\n") == [
        r'A: /\\sin(?!h)/', r'B: "\\sinh"', r'C: /\\ge(?!q)/ | "\\geq"', r'D: "\\,"']
    assert guard_commands(r'A: "\\pi"', others=r'P: "\\pix"') == r'A: /\\pi(?!x)/'
    # the Greek letters are imported as they are, unguarded: none may begin another command
    command = re.compile(r'"\\\\([A-Za-z]+)"')
    greek = set(command.findall((GRAMMAR_DIR / "greek_symbols.lark").read_text(encoding="utf-8")))
    every = greek | set(command.findall((GRAMMAR_DIR / "latex.lark").read_text(encoding="utf-8")))
    assert not [(a, b) for a in greek for b in every if a != b and b.startswith(a)]


def test_the_document_s_names_are_reused_and_its_functions_apply():
    xp = Symbol("x", positive=True)
    doc = Document(xp ** 2 + f(y), addons=[ADDON])
    res = ADDON.read(doc, {"latex": r"\sqrt{x} + f(z) + a(z)"})
    assert res["ok"]
    assert xp in res["expr"].free_symbols and Symbol("x") not in res["expr"].free_symbols   # the positive x, not a new one
    assert f(z) in res["expr"].args                                                          # f is a function here
    assert a * z in res["expr"].args                                                          # a is not


def test_the_methods_read_and_insert():
    doc = Document(x + y, addons=[ADDON])
    snap = doc.handle({"action": "addon", "addon": "latex", "method": "read", "latex": r"\pi r^2"})
    res = snap["query"]["result"]
    assert res["ok"] and res["src"] == "pi*r**2" and "expr" not in res and res["latex"]
    assert doc.expr == x + y                                                                  # a query changes nothing
    doc.handle({"action": "addon", "addon": "latex", "method": "insert", "latex": r"\sin x \cos y", "path": "/0"})
    assert doc.expr == sin(x) * cos(y) + y
    assert doc.history_labels()["actions"][-1] == r"LaTeX: \sin x \cos y"
    doc.handle({"action": "addon", "addon": "latex", "method": "insert", "latex": r"e^{i \pi}", "path": "/",
                "constants": {"pi": False, "i": False}})
    assert doc.expr == E ** (Symbol("i") * Symbol("pi"))
    bad = doc.handle({"action": "addon", "addon": "latex", "method": "insert", "latex": r"x^2 +* y", "path": "/"})
    assert "could not be read" in bad["query"]["error"] and doc.expr == E ** (Symbol("i") * Symbol("pi"))
    assert ADDON.client_options()["constants"][0]["name"] == "pi"
    assert "static/grammar/latex.lark" in ADDON.python_sources()                             # the grammar travels to Pyodide pages


def test_the_documents_symbols_are_found_under_their_latex_spelling():
    # x_1 prints x_{1} and lamda \lambda: read back, those are the document's
    # own symbols (assumptions included), not new ones named as printed.
    x1, lam, al, xh = Symbol("x_1", positive=True), Symbol("lamda"), Symbol("alpha_i"), Symbol("xhat")
    doc = Document(x1 + lam + al + xh, addons=[ADDON])
    for tex, want in [(r"x_1 + 1", x1 + 1), (r"x_{1}", x1), (r"\lambda", lam), (r"\alpha_{i}", al), (r"\hat{x}", xh)]:
        got = ADDON.read(doc, {"latex": tex})
        assert got["ok"] and got["expr"] == want, (tex, got)
    assert ADDON.read(doc, {"latex": "x_1"})["expr"].is_positive
    # a lambda the document does not have is still lamda: "lambda" cannot be typed back
    assert read_latex(r"\lambda + 1")["src"] == "lamda + 1"


def test_a_fraction_over_a_differential_that_is_no_derivative_divides(reader):
    d, t = symbols("d t")
    got = reader.read(r"\frac{1}{d x}")
    assert got["ok"] and got["expr"] == 1 / (d * x)
    got = reader.read(r"\frac{y}{d x}")
    assert got["ok"] and got["expr"] == y / (d * x)
    # d times something else on top: a division, or the derivative of the rest -
    # a choice, a derivative by convention only when the d comes first
    got = reader.read(r"\frac{b d}{d t}")
    assert got["ok"] and got["expr"] == b / t
    point = [p for p in got["ambiguities"] if p["key"].startswith("derivative@")]
    assert point and [o["src"] for o in point[0]["options"]] == ["Derivative(b, t)", "b/t"]
    again = reader.read(r"\frac{b d}{d t}", choices={point[0]["key"]: 0})
    assert again["expr"] == Derivative(b, t)
    got = reader.read(r"\frac{d^2 y}{dx^2}")
    assert got["expr"] == Derivative(y, (x, 2)) and any(p["key"].startswith("derivative@") for p in got["ambiguities"])
    plain = reader.read(r"\frac{dy}{dx}")
    assert plain["expr"] == Derivative(y, x)
    assert not any(p["key"].startswith("derivative@") for p in plain["ambiguities"])     # no choice there


def test_numbers_too_large_to_compute_are_kept_as_written(reader):
    r"""``10^{10^{8}}``, ``20000!`` and ``2^{20000}`` were worked out while
    the text was read - a number of a hundred million digits, the editor
    frozen behind a quiet call with no Interrupt to press.  A power or a
    factorial too large to compute is kept as written, and so is what is
    built on it; the small ones are still worked out."""
    import time
    from sympy import Pow, binomial, factorial, srepr
    for tex in (r"10^{10^{8}}", r"9^{9^{8}}", r"20000!", r"2^{20000}", r"\binom{20000}{10000}",
                r"2^{20000} + x + x", r"\frac{d}{dx} 10^{10^{8}}", r"\operatorname{fibonacci}(100000)"):
        start = time.time()
        res = reader.read(tex)
        assert res["ok"], (tex, res.get("error"))
        assert time.time() - start < 5, tex
        assert len(srepr(res["expr"])) < 400 and len(res["latex"]) < 200, tex   # nothing written out
    assert reader.read(r"10^{10^{8}}")["expr"] == Pow(10, 10**8, evaluate=False)
    assert reader.read(r"20000!")["expr"] == factorial(20000, evaluate=False)
    assert reader.read(r"\binom{20000}{10000}")["src"] == "binomial(20000, 10000)"
    assert reader.read(r"2^{10}")["expr"] == 1024 and reader.read(r"5!")["expr"] == 120
    assert reader.read(r"\binom{5}{2}")["expr"] == binomial(5, 2) == 10
    # and it goes into a document as it is
    doc = Document(x, addons=[ADDON])
    snap = doc.handle({"action": "addon", "addon": "latex", "method": "insert", "latex": r"2^{20000}", "path": "/"})
    assert not snap.get("error") and doc.expr == Pow(2, 20000, evaluate=False)


def test_a_differential_outside_an_integral_or_a_derivative_is_a_product(reader):
    r"""``c + d x``, ``b d x`` and ``x dx`` came back with a ``Tuple`` inside
    (the differential SymPy's transformer makes for an integral), which the
    editor could neither show nor edit; a mixed partial derivative raised an
    AttributeError.  Anywhere but under an integral or a fraction bar, ``d x``
    is the product d*x - and a row of differentials under the bar is a
    derivative by each of them."""
    from sympy import srepr
    d, f_ = symbols("d f")
    for tex, want in [(r"c + d x", c + d * x), (r"b d x", b * d * x), (r"x dx", d * x ** 2), (r"d x", d * x),
                      (r"a x + b y + c z + d w", a * x + b * y + c * z + d * Symbol("w")), (r"(c + d) x", (c + d) * x),
                      (r"\frac{\partial^2 f}{\partial x \partial y}", Derivative(f_, x, y)),
                      (r"\frac{d^2 f}{dx dy}", Derivative(f_, x, y)),
                      (r"\frac{\partial^3 f}{\partial x^2 \partial y}", Derivative(f_, (x, 2), y)),
                      (r"\frac{\partial^{2}}{\partial y\partial x} f", Derivative(f_, y, x)),
                      (r"\frac{1}{dx dy}", 1 / (d ** 2 * x * y)),
                      (r"\int x dx", Integral(x, x)), (r"\int \frac{dx}{x}", Integral(1 / x, x))]:
        res = reader.read(tex)
        assert res["ok"], (tex, res.get("error"))
        assert res["expr"] == want, (tex, res["src"])
        if not isinstance(res["expr"], (Derivative, Integral)):
            assert "Tuple" not in srepr(res["expr"]), tex
    # an operator with nothing to act on says so, whatever stands before it
    for tex in (r"\frac{d}{dx}", r"x \frac{d}{dx}", r"\frac{d}{dx} + 1"):
        res = reader.read(tex)
        assert res["ok"] is False and "nothing after it to differentiate" in res["error"], (tex, res)
    # the mixed partial goes into a document, which shows it and saves it
    doc = Document(x, addons=[ADDON])
    snap = doc.handle({"action": "addon", "addon": "latex", "method": "insert",
                       "latex": r"\frac{\partial^2 f}{\partial x \partial y}", "path": "/"})
    assert not snap.get("error") and doc.expr == Derivative(f_, x, y) and doc.snapshot()["latex"]


def _round_trip_corpus():
    """Expressions whose LaTeX, as SymPy's own printer writes it, reads back
    as the expression itself (or one equal to it).  Not in it: what the
    grammar has no rule for (``\\bmod``, ``cases``, ``\\wedge``), and a
    name of several letters printed bare (``speed``), which is a product."""
    from sympy import (Abs, Float, Max, Min, Ne, Product, Rational as R_, acos, asin, atan, binomial, cbrt, ceiling, conjugate,
                       cosh, factorial, floor, gamma, im, re, root, sinh, tan, tanh)
    k_, t_ = symbols("k t")
    return [x + y, x - y, -x, x * y, x / y, x ** 2, x ** -2, sqrt(x), cbrt(x), x ** R_(3, 2), 1 / (x + 1), (x + 1) / (y - 1),
            (x + 1) * (y - 1), -(x + 1), x - (y + z), 2 * x, R_(1, 2) * x, R_(-3, 4), 3 * x ** 2 * y,
            sin(x), sin(x) ** 2, sin(2 * x), sin(x + y), sin(x) * cos(y), cos(x) ** 2 + sin(x) ** 2, tan(x), asin(x), acos(x),
            atan(x), sinh(x), cosh(x), tanh(x), exp(x), exp(-x ** 2), exp(x + 1), log(x), log(x + 1), log(x) ** 2,
            Abs(x), Abs(x + y), factorial(n), factorial(n + 1), binomial(n, k), floor(x), ceiling(x), conjugate(x), re(x), im(x),
            gamma(x), Max(x, y), Min(x, y),
            Integral(x ** 2, x), Integral(x ** 2, (x, 0, 1)), Integral(sin(x) * exp(x), x), Integral(x * y, (x, 0, 1), (y, 0, 2)),
            Sum(k ** 2, (k, 1, n)), Product(k, (k, 1, n)), Limit(sin(x) / x, x, 0), Limit(1 / x, x, oo),
            Derivative(f(x), x), Derivative(f(x), (x, 2)), Derivative(sin(x) * x, x), Derivative(f(x, y), x, y), f(x), f(x, y),
            f(x + 1) ** 2, g(x) * f(y),
            Eq(x, y), Ne(x, y), x < y, x <= y, x > y, x >= y, Eq(x ** 2 + 1, 0),
            pi, E, I, oo, -oo, pi * x, E ** x, I * x, 2 + 3 * I, exp(I * pi * x), x * E,
            Symbol("alpha") + Symbol("beta"), Symbol("theta") ** 2, Symbol("x_1") + Symbol("x_2"), Symbol("lamda"), Symbol("Omega"),
            Symbol("x_i"), Symbol("alpha_1"), Symbol("xhat"), Symbol("v_max"), Symbol("a_ij"),
            Matrix([[1, 2], [3, 4]]), Matrix([[x, y]]), Matrix([x, y]),
            Float(0.5), Float(1.25) * x, x ** y, x ** (y + 1), (x ** y) ** z, 2 ** x, (x + 1) ** 2, (x * y) ** 2, x / (y * z),
            1 / (x * y), x ** 2 * sin(x), 1 / sin(x), log(x, 2), sqrt(x + 1), sqrt(x) * y, 1 / sqrt(x), root(x, 5),
            (-b + sqrt(b ** 2 - 4 * a * c)) / (2 * a), x ** 2 - 2 * x + 1, -x * y, -x ** 2, -2 * x + 3 * y - z, t_ * k_]


def test_sympy_s_own_latex_reads_back_as_the_expression(reader):
    r"""SymPy writes ``f{\left(x \right)}``, ``\operatorname{asin}{\left(x
    \right)}``, ``\Gamma\left(x\right)`` - and those read as products
    (``f*x``, ``asin*x``), so a formula copied out of SymPy, or out of the
    editor itself, came back as something else.  Each is read as the
    function applied, the product still offered where the text allows it."""
    from sympy import Expr, asin, gamma, latex, simplify
    wrong = []
    for expr in _round_trip_corpus():
        tex = latex(expr)
        res = reader.read(tex)
        if not res["ok"]:
            wrong.append((expr, tex, res["error"]))
            continue
        got = res["expr"]
        same = got == expr or (isinstance(expr, Expr) and isinstance(got, Expr) and simplify(got - expr) == 0)
        if not same:
            wrong.append((expr, tex, res["src"]))
    assert not wrong, wrong
    for tex, want, other in [(r"f{\left(x \right)}", f(x), "f*x"), (r"\operatorname{asin}{\left(x \right)}", asin(x), "asin*x"),
                             (r"\Gamma\left(x\right)", gamma(x), "Gamma*x"), (r"f^{2}{\left(x + 1 \right)}", f(x + 1) ** 2, None)]:
        res = reader.read(tex)
        assert res["ok"] and res["expr"] == want, (tex, res.get("src"), res.get("error"))
        if other:
            assert any(o["src"] == other for amb in res["ambiguities"] for o in amb["options"]), (tex, res["ambiguities"])
    assert reader.read(r"f{\left(x,y \right)}")["expr"] == f(x, y)


def test_a_long_row_of_bare_functions_is_read_by_the_convention(reader):
    r"""Seven logarithms in a row (``\ln x \ln y ... \ln u``) have more
    readings than were weighed, and the conventional one - a product of
    logarithms - was neither the first reading nor among the choices.  It
    always is now; only what is shown of a part's readings is capped."""
    from sympy_editor_latex.parser import MAX_ALTERNATIVES
    names = "x y z a b c u v w".split()
    for count in range(5, 10):
        tex = " ".join(r"\ln " + v for v in names[:count])
        res = reader.read(tex)
        want = Mul(*[log(Symbol(v)) for v in names[:count]])
        assert res["ok"] and res["expr"] == want, (count, res.get("src"))
        assert any(o["src"] == str(want) for amb in res["ambiguities"] for o in amb["options"]), count
        for amb in res["ambiguities"]:
            assert len(amb["options"]) <= MAX_ALTERNATIVES
            assert amb["options"][amb["choice"]]["src"] == str(want)            # the one read is the one marked


def test_readings_in_two_threads_do_not_mix():
    """One reader serves every document, and the widget and the server read
    on threads of their own: what one reading kept on the shared reader (its
    choices, its points) was read by the other midway, and each came back
    with pieces of the other's answer."""
    import json
    import threading
    shared = LatexReader()
    shared.warm()
    texts = [r"\sin x \cos y + \frac{d^2 y}{dx^2}", r"1 + 2 + 3 + 4 + \ln a \ln b + \frac{q d}{dt}"]

    def answer(text):
        res = dict(shared.read(text))
        res.pop("expr")
        return json.dumps(res, sort_keys=True)

    alone = {t: answer(t) for t in texts}
    wrong = []

    def loop(text):
        for _ in range(15):
            try:
                if answer(text) != alone[text]:
                    wrong.append(text)
            except Exception as exc:  # noqa: BLE001
                wrong.append(repr(exc))

    was = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)
    try:
        threads = [threading.Thread(target=loop, args=(t,)) for t in texts]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    finally:
        sys.setswitchinterval(was)
    assert wrong == []


def test_a_reading_is_bounded_in_length_and_in_time(reader, monkeypatch):
    """A reading is asked for at every pause in the typing, the editor waiting
    for it: a run of three hundred letters took eighteen seconds, a formula of
    77 characters and eleven ambiguous parts seven.  The text has a length
    limit, the reading a time limit - each with a message that says so - and
    the choices are worked out within bounds."""
    import time
    from sympy_editor_latex import parser
    res = reader.read("x" * (parser.MAX_LENGTH + 1))
    assert res["ok"] is False and "too long" in res["error"] and str(parser.MAX_LENGTH) in res["error"]
    monkeypatch.setattr(parser, "MAX_SECONDS", 0.3)
    start = time.time()
    res = reader.read("x" * 300)
    assert res["ok"] is False and "too long to read" in res["error"]
    assert time.time() - start < 5
    monkeypatch.undo()
    tex = r"\sin a \cos b + \cos a \sin b - \sin c \cos d - \cos c \sin d + \tan x \tan y"
    start = time.process_time()
    res = reader.read(tex)
    assert res["ok"] and len(res["ambiguities"]) <= parser.MAX_POINTS
    assert time.process_time() - start < 3


def test_nothing_a_page_sends_makes_the_reading_raise(reader):
    r"""A reading answers, whatever it is sent: ``choices`` of 1e400 raised an
    OverflowError out of the reader, a number too large to write out a
    ValueError, and a text that was not one was read as its ``str``
    (``None`` became N*n*o*e)."""
    key = "_expression_mul@0-13"
    for bad in (1e400, float("nan"), float("inf"), "1", -1, 99, None, [1], 1.5):
        res = reader.read(r"\sin x \cos y", choices={key: bad})
        assert res["ok"] and res["expr"] == sin(x) * cos(y), (bad, res)
        taken = res["choices"][key]
        assert isinstance(taken, int) and 0 <= taken < 2, (bad, taken)
    for choices, constants in (("abc", [1]), ({1: 1}, {"pi": "no"}), ([], None)):
        assert reader.read(r"\sin x \cos y", choices=choices, constants=constants)["ok"]
    # a derivative's choice out of range: the convention, and the choice echoed is the one taken
    res = reader.read(r"\frac{d^2 y}{dx^2}", choices={"derivative@0-18": 5})
    assert res["expr"] == Derivative(y, (x, 2)) and res["choices"]["derivative@0-18"] == 0
    assert reader.read(r"2^{10000} \cdot 2^{10000}")["ok"]              # a product of two large numbers: kept as a product
    for tex in (r"2^{-20000}", r"x \frac{d}{dx}"):
        res = reader.read(tex)
        assert res["ok"] is False and res["error"].startswith("This LaTeX could not be read"), (tex, res)
    for latex in (None, 5, ["a"]):
        assert reader.read(latex)["ok"] is False
    doc = Document(x, addons=[ADDON])
    for payload in ({}, {"latex": None}, {"latex": 5}):
        snap = doc.handle(dict(payload, action="addon", addon="latex", method="read"))
        assert snap["query"]["result"]["ok"] is False, payload


def test_names_read_from_latex_can_be_typed_back_in_the_source_line():
    r"""``x_{1}``, ``a_{ij}``, ``\hat{v}`` were named as they are written -
    ``Symbol("x_{1}")`` - which the editor's source line cannot read back: with
    one such name in the formula, no edit of the text went through.  They are
    named as SymPy names them (``x_1``, ``a_ij``, ``vhat``), which print the
    same; a document that holds an old spelling still has it found."""
    doc = Document(x + 1, addons=[ADDON])
    snap = doc.handle({"action": "addon", "addon": "latex", "method": "insert",
                       "latex": r"x_1^2 + \hat{v} + a_{ij} + \alpha_{i} + x'", "path": "/"})
    assert not snap.get("error")
    assert {s.name for s in doc.expr.free_symbols} == {"x_1", "vhat", "a_ij", "alpha_i", "xprime"}
    src = doc.snapshot()["src"]
    again = doc.handle({"action": "replace", "path": "/", "src": src + " + 2"})
    assert not again.get("error"), again.get("error")
    from sympy import latex as to_latex
    assert to_latex(Symbol("x_1")) == "x_{1}" and to_latex(Symbol("vhat")) == r"\hat{v}"
    # a document made before: its x_{1} is the symbol a reading of x_{1} finds
    old = Symbol("x_{1}", positive=True)
    before = Document(old + 1, addons=[ADDON])
    for tex in (r"x_{1}", r"x_1"):
        got = ADDON.read(before, {"latex": tex})
        assert got["ok"] and got["expr"] == old, (tex, got.get("src"))


def test_the_lesser_ambiguities_are_offered_and_mismatched_environments_refused(reader):
    r"""``a/bc`` offered no other reading, ``f'(x)`` was the product f'*x
    and ``\operatorname{sinc}(x)`` could not be read as the function; and
    ``\begin{pmatrix} ... \end{bmatrix}`` was taken for a matrix."""
    from sympy import sinc
    res = reader.read("a/bc")
    assert res["expr"] == a / (b * c)
    assert any(o["src"] == str(a * c / b) for amb in res["ambiguities"] for o in amb["options"])
    res = reader.read("f'(x)")
    assert res["src"] == "fprime(x)" and any(o["src"] == "fprime*x" for amb in res["ambiguities"] for o in amb["options"])
    res = reader.read(r"\operatorname{sinc}(x)")
    assert res["expr"] == sinc(x) and any(o["src"] == "sinc*x" for amb in res["ambiguities"] for o in amb["options"])
    res = reader.read(r"\begin{pmatrix} a \end{bmatrix}")
    assert res["ok"] is False and "closed by" in res["error"]
    assert reader.read(r"\begin{pmatrix} a \end{pmatrix}")["expr"] == Matrix([[a]])


def test_keep_and_undo_wear_the_colours_of_the_change():
    """Keep is the green of what came, Undo the red of what went - the
    editor's own diff colours, so they follow the theme - and the red is
    the weaker of the two: its border and its words, no fill."""
    import re
    from pathlib import Path
    import sympy_editor_latex
    css = (Path(sympy_editor_latex.__file__).parent / "static" / "latex.css").read_text(encoding="utf-8")
    rule = lambda sel: re.search(re.escape(sel) + r"\s*\{([^}]*)\}", css).group(1)
    # under the row's own selector: `.ltx-applied-ask button` sets the plain
    # look, and a bare `.ltx-keep` would lose to it
    keep, back = rule(".ltx-applied-ask .ltx-keep"), rule(".ltx-applied-ask .ltx-back")
    assert "background: rgba(var(--se-added-rgb)" in keep and "color: rgb(var(--se-added-rgb))" in keep
    assert "color: rgb(var(--se-removed-rgb))" in back and "border-color: rgba(var(--se-removed-rgb)" in back
    assert "background" not in back


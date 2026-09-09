"""The LaTeX reader: readings by convention, ambiguities as choices,
constants as switches, and the document methods."""
import sys
from pathlib import Path

import pytest
from sympy import (Derivative, E, Eq, EulerGamma, Function, I, Integral, Limit, Matrix, Rational, Sum, Symbol, cos, exp, log, oo,
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
    (r"\vec{v} \cdot \hat{x}", Symbol(r"\vec{v}") * Symbol(r"\hat{x}")),
    (r"\bar x + \mathbf{A}", Symbol(r"\bar{x}") + Symbol(r"\mathbf{A}")),
    (r"\text{foo} + \operatorname{bar}", Symbol("foo") + Symbol("bar")),
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
    bad = doc.handle({"action": "addon", "addon": "latex", "method": "insert", "latex": r"\frac{x}", "path": "/"})
    assert "could not be read" in bad["query"]["error"] and doc.expr == E ** (Symbol("i") * Symbol("pi"))
    assert ADDON.client_options()["constants"][0]["name"] == "pi"
    assert "static/grammar/latex.lark" in ADDON.python_sources()                             # the grammar travels to Pyodide pages

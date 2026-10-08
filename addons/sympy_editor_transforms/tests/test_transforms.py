import sys
from pathlib import Path

import pytest
from sympy import (S, Eq, Heaviside, KroneckerDelta, LaplaceTransform, Rational, Sum, Symbol, cos, exp, expand_func, gamma,
                   oo, pi, simplify, sin, sqrt, symbols)
from sympy.integrals.transforms import FourierTransform

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_transforms import ADDON, TRANSFORMS, inverse_z_transform, transform, unevaluated, words, z_transform  # noqa: E402

t, s, x, k, n, z, r = symbols("t s x k n z r")
a = Symbol("a", positive=True)
b = Symbol("b")
w = Symbol("omega", positive=True)


def _call(doc, method, **payload):
    return doc.handle(dict({"action": "addon", "addon": "transforms", "method": method}, **payload))


# ---- the z-transform: a table, both ways ----

Z_TABLE = [
    # f(n),                 F(z),                                          R in |z| > R
    (1, z / (z - 1), 1),
    (b ** n, z / (z - b), abs(b)),
    (Rational(1, 2) ** n, z / (z - Rational(1, 2)), Rational(1, 2)),
    (n, z / (z - 1) ** 2, 1),
    (n ** 2, z * (z + 1) / (z - 1) ** 3, 1),
    (n * b ** n, b * z / (z - b) ** 2, abs(b)),
    (exp(-n), z / (z - exp(-1)), exp(-1)),
    (2 ** (n + 1), 2 * z / (z - 2), 2),
    (sin(w * n), z * sin(w) / (z ** 2 - 2 * z * cos(w) + 1), 1),
    (cos(w * n), z * (z - cos(w)) / (z ** 2 - 2 * z * cos(w) + 1), 1),
    (sin(n) / 2 ** n, (z / 2) * sin(1) / (z ** 2 - z * cos(1) + Rational(1, 4)), Rational(1, 2)),
    (KroneckerDelta(n, 0), 1, 0),
    (KroneckerDelta(n, 2), z ** -2, 0),
]


@pytest.mark.parametrize("f, F, R", Z_TABLE)
def test_the_z_transform_of_the_table(f, F, R):
    got, radius, _nonzero = z_transform(f, n, z)
    assert simplify(got - F) == 0
    assert simplify(radius - R) == 0


@pytest.mark.parametrize("f, F, R", Z_TABLE)
def test_the_inverse_z_transform_of_the_table(f, F, R):
    got = inverse_z_transform(F, z, n)
    for m in range(6):    # the sequence, term by term
        assert simplify(expand_func(got.subs(n, m)) - expand_func(S(f).subs(n, m))) == 0, (m, got)


def test_the_inverse_z_transform_by_partial_fractions():
    # two simple poles, a double pole, a pole at 0 with the others
    assert simplify(inverse_z_transform(z ** 2 / ((z - 1) * (z - Rational(1, 2))), z, n) - (2 - Rational(1, 2) ** n)) == 0
    # a double pole and a simple one: the sequence is the power series of F in 1/z
    F = z / ((z - 2) ** 2 * (z - 3))
    got = inverse_z_transform(F, z, n)
    u = Symbol("u")
    series = F.subs(z, 1 / u).series(u, 0, 7).removeO()
    for m in range(7):
        assert simplify(got.subs(n, m) - series.coeff(u, m)) == 0
    # z / (z**2 + 1): poles at +-i, the sequence sin(pi n / 2)
    got = inverse_z_transform(z / (z ** 2 + 1), z, n)
    assert [simplify(got.subs(n, m)) for m in range(6)] == [0, 1, 0, -1, 0, 1]
    # (z + 1) / z**2: a finite sequence of deltas
    got = inverse_z_transform((z + 1) / z ** 2, z, n)
    assert [got.subs(n, m) for m in range(4)] == [0, 1, 1, 0]


def test_the_inverse_z_transform_refuses_in_words():
    with pytest.raises(ValueError, match="not a rational function"):
        inverse_z_transform(exp(1 / z), z, n)
    with pytest.raises(ValueError, match="causal"):
        inverse_z_transform(z ** 2 / (z - 1), z, n)


def test_the_z_transform_falls_back_on_summation():
    F, R, _ = z_transform(1 / gamma(n + 1), n, z)    # 1/n!: not in the table
    assert simplify(F - exp(1 / z)) == 0 and R == 0


# ---- SymPy's transforms, with their conditions in words ----

def test_laplace_with_its_abscissa():
    res = transform("laplace", exp(-a * t), t, s)
    assert res["result"] == 1 / (a + s)
    assert res["conditions"] == ["converges for Re(s) > -a"]
    res = transform("laplace", sin(t), t, s)
    assert res["result"] == 1 / (s ** 2 + 1) and res["conditions"] == ["converges for Re(s) > 0"]


def test_inverse_laplace_says_it_is_causal():
    res = transform("inverse_laplace", 1 / (s + a), s, t)
    assert res["result"] == exp(-a * t) * Heaviside(t)
    assert "t > 0" in res["conditions"][0]


def test_fourier_sine_cosine_mellin_hankel():
    res = transform("fourier", exp(-x ** 2), x, k)
    assert res["result"] == sqrt(pi) * exp(-pi ** 2 * k ** 2) and "2πi" in res["convention"]
    assert transform("inverse_fourier", exp(-k ** 2), k, x)["result"] == sqrt(pi) * exp(-pi ** 2 * x ** 2)
    res = transform("sine", exp(-x), x, k)
    assert simplify(res["result"] - sqrt(2) * k / (sqrt(pi) * (k ** 2 + 1))) == 0
    assert res["conditions"] == ["provided k is real and positive"]
    assert simplify(transform("cosine", exp(-x), x, k)["result"] - sqrt(2) / (sqrt(pi) * (k ** 2 + 1))) == 0
    res = transform("mellin", exp(-x), x, s)
    assert res["result"] == gamma(s) and res["conditions"] == ["converges for Re(s) > 0"]
    assert transform("inverse_mellin", gamma(s), s, x, (0, oo))["result"] == exp(-x)
    res = transform("hankel", 1 / r, r, k, 0)
    assert res["result"] == 1 / k and res["conditions"][0] == "of order ν = 0"
    assert transform("inverse_hankel", 1 / k, k, r, 0)["result"] == 1 / r


def test_failures_are_said_in_words():
    with pytest.raises(ValueError, match="could not find the Laplace transform"):
        transform("laplace", exp(t ** 2), t, s)
    with pytest.raises(ValueError, match="already occurs"):
        transform("laplace", exp(-s * t), t, s)
    with pytest.raises(ValueError, match="No transform"):
        transform("nope", t, t, s)


def test_words():
    assert words(Eq(abs(x), 1)) == "|x| = 1"
    assert words((x <= 1) & (x > 0)) in ("x ≤ 1 and x > 0", "x > 0 and x ≤ 1")


def test_unevaluated_forms():
    assert unevaluated("laplace", exp(-t), t, s) == LaplaceTransform(exp(-t), t, s)
    assert unevaluated("fourier", exp(-x ** 2), x, k) == FourierTransform(exp(-x ** 2), x, k)
    assert unevaluated("z", n, n, z) == Sum(n * z ** (-n), (n, 0, oo))
    assert unevaluated("inverse_z", z / (z - 1), z, n) is None
    for key in TRANSFORMS:      # every one SymPy has an object for builds
        var = Symbol(TRANSFORMS[key]["vars"][0])
        unevaluated(key, exp(-var), var, Symbol(TRANSFORMS[key]["new"]))


# ---- in a document: the panel's methods and the ops ----

def test_compute_is_a_query_apply_a_step():
    doc = Document(exp(-a * t) + sin(t), addons=[ADDON])
    res = _call(doc, "compute", path="/", transform="laplace", var="t", new="s")["query"]["result"]
    assert res["src"] == "1/(s**2 + 1) + 1/(a + s)" and res["conditions"] == ["converges for Re(s) > 0"]
    assert "\\frac" in res["latex"] and not doc.can_undo
    snap = _call(doc, "apply", path="/", transform="laplace", var="t", new="s")
    assert doc.expr == 1 / (s ** 2 + 1) + 1 / (a + s)
    assert snap["note"] == "Laplace: converges for Re(s) > 0"
    assert doc.history_labels()["actions"][-1] == "Transforms: Laplace transform, t → s"
    doc.undo()
    assert doc.expr == exp(-a * t) + sin(t)


def test_apply_replaces_the_selection_only():
    doc = Document(Eq(Symbol("y"), exp(-2 * t)), addons=[ADDON])
    _call(doc, "apply", path="/1", transform="laplace", var="t", new="s")
    assert doc.expr == Eq(Symbol("y"), 1 / (s + 2))


def test_apply_unevaluated_and_the_session_reopens():
    doc = Document(exp(-t), addons=[ADDON])
    _call(doc, "apply", path="/", transform="laplace", var="t", new="s", lazy=True)
    assert doc.expr == LaplaceTransform(exp(-t), t, s)
    assert doc.history_labels()["actions"][-1].endswith("(unevaluated)")
    snap = doc.snapshot()
    assert "\\mathcal{L}" in snap["latex"]
    state = doc.export()
    again = Document(0, addons=[ADDON], history=state["history"], index=state["index"])
    assert again.expr == LaplaceTransform(exp(-t), t, s)
    doc.apply("/", "doit")
    assert doc.expr == 1 / (s + 1)


def test_apply_unevaluated_inverse_z_says_it_computed():
    doc = Document(z / (z - 1), addons=[ADDON])
    snap = _call(doc, "apply", path="/", transform="inverse_z", var="z", new="n", lazy=True)
    assert doc.expr == 1 and "no unevaluated form" in snap["note"]


def test_a_range_is_transformed():
    doc = Document(x + exp(-t) + t, addons=[ADDON])
    # the arguments of the sum holding t and exp(-t)
    idx = [i for i, arg in enumerate(doc.expr.args) if arg.has(t)]
    _call(doc, "apply", path="/", children=idx, transform="laplace", var="t", new="s")
    assert doc.expr == x + 1 / (s + 1) + 1 / s ** 2


def test_errors_come_back_to_the_panel():
    doc = Document(exp(t ** 2), addons=[ADDON])
    res = _call(doc, "compute", path="/", transform="laplace", var="t", new="s")
    assert "could not find the Laplace transform" in res["query"]["error"]
    res = _call(doc, "compute", path="/", transform="laplace", var="t", new="2*s")
    assert "must be a name" in res["query"]["error"]


def test_hankel_and_inverse_mellin_take_their_extra_value():
    doc = Document(1 / r, addons=[ADDON])
    res = _call(doc, "compute", path="/", transform="hankel", var="r", new="k", extra="0")["query"]["result"]
    assert res["src"] == "1/k"
    doc = Document(gamma(s), addons=[ADDON])
    res = _call(doc, "compute", path="/", transform="inverse_mellin", var="s", new="x", extra="(0, oo)")["query"]["result"]
    assert res["src"] == "exp(-x)"


def test_the_ops_ask_for_the_variables():
    doc = Document(exp(-2 * t), addons=[ADDON])
    ops = {o["name"]: o for o in doc.snapshot()["ops"]}
    for name in ("laplace", "inverse_laplace", "fourier", "inverse_fourier", "z", "inverse_z"):
        op = ops["transforms_" + name]
        assert op["label"].endswith("…")
        assert [p["kind"] for p in op["params"]] == ["symbol", "text"]
        assert not op["params"][0]["optional"] and op["params"][1]["optional"]
    assert ops["transforms_laplace"]["lazy"] and not ops["transforms_inverse_z"]["lazy"]
    snap = doc.handle({"action": "apply", "path": "/", "op": "transforms_laplace", "args": ["t", ""]})
    assert doc.expr == 1 / (s + 2) and snap["note"] == "Laplace: converges for Re(s) > -2"
    doc.handle({"action": "apply", "path": "/", "op": "transforms_inverse_laplace", "args": ["s", "u"]})
    assert doc.expr == exp(-2 * Symbol("u")) * Heaviside(Symbol("u"))
    doc.undo()
    doc.handle({"action": "apply", "path": "/", "op": "transforms_inverse_laplace", "args": ["s", ""], "lazy": True})
    assert doc.expr.func.__name__ == "InverseLaplaceTransform"


def test_the_z_ops():
    doc = Document(Rational(1, 2) ** n, addons=[ADDON])
    snap = doc.handle({"action": "apply", "path": "/", "op": "transforms_z", "args": ["n", ""]})
    assert simplify(doc.expr - 2 * z / (2 * z - 1)) == 0 and "|z| > 1/2" in snap["note"]
    doc.handle({"action": "apply", "path": "/", "op": "transforms_inverse_z", "args": ["z", ""]})
    assert simplify(doc.expr - Rational(1, 2) ** n) == 0
    doc.handle({"action": "apply", "path": "/", "op": "transforms_z", "args": ["n", ""], "lazy": True})
    assert isinstance(doc.expr, Sum)


def test_the_panel_ships_with_the_package():
    files = ADDON.python_sources()
    assert any(p.endswith("ztransform.py") for p in files) and any(p.endswith("transforms.js") for p in files)
    opts = ADDON.client_options()
    assert [t["key"] for t in opts["transforms"]][:2] == ["laplace", "inverse_laplace"]

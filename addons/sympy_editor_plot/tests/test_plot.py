"""The plot add-on: samples of the selection, gaps where it is not real."""
import sys
from pathlib import Path

from sympy import Eq, sin, sqrt, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_plot import ADDON, sample  # noqa: E402

x, y, a = symbols("x y a")


def _samples(doc, **payload):
    snap = doc.handle(dict(payload, action="addon", addon="plot", method="samples"))
    assert not snap["error"], snap["error"]
    assert snap["query"]["addon"] == "plot"
    return snap["query"]["result"]


def test_samples_of_the_whole_expression():
    doc = Document(sin(x) / x, addons=[ADDON])
    res = _samples(doc, path="/", span=[-6, 6], n=13)
    assert res["var"] == "x" and res["needs"] == []
    assert len(res["x"]) == 13 and len(res["curves"]) == 1
    ys = res["curves"][0]["y"]
    assert ys[0] is not None and abs(ys[0] - sin(-6) / -6) < 1e-9
    assert ys[6] is None                     # x = 0: 0/0, a gap, not a crash
    assert doc.can_undo is False             # a query changes nothing


def test_gaps_where_the_value_is_not_real():
    ys = sample(sqrt(x), x, (-1, 1), 5)
    assert ys[0] is None and ys[1] is None and ys[2] == 0 and abs(ys[4] - 1) < 1e-12


def test_other_symbols_need_values_then_get_them():
    doc = Document(y * sin(x), addons=[ADDON])
    res = _samples(doc, path="/", span=[0, 1], n=3)
    assert res["needs"] == ["y"] and "curves" in res and res["curves"] == []
    res = _samples(doc, path="/", span=[0, 1], n=3, values={"y": 2})
    assert res["needs"] == [] and abs(res["curves"][0]["y"][2] - 2 * sin(1)) < 1e-9
    res = _samples(doc, path="/", span=[0, 1], n=3, var="y", values={"x": 1})
    assert res["var"] == "y"


def test_a_selection_and_an_equation():
    doc = Document(Eq(sin(x), x**2), addons=[ADDON])
    res = _samples(doc, path="/", span=[0, 1], n=3)
    assert [c["label"] for c in res["curves"]] == ["lhs", "rhs"]
    res = _samples(doc, path="/1", span=[0, 2], n=3)
    assert res["src"] == "x**2" and res["curves"][0]["y"] == [0.0, 1.0, 4.0]


def test_free_symbols_are_reported_before_substitution():
    """The panel keeps a row per symbol besides the axis; a symbol given a
    value must stay in ``free`` (it used to vanish once substituted, and
    the panel dropped its slider the moment it got a value)."""
    doc = Document(a * sin(x), addons=[ADDON])
    res = _samples(doc, path="/", span=[0, 1], n=3, var="x", values={"a": 2})
    assert res["free"] == ["a", "x"] and res["needs"] == [] and res["var"] == "x"
    assert abs(res["curves"][0]["y"][2] - 2 * sin(1)) < 1e-9
    # a value for the axis variable itself is ignored, not substituted
    res = _samples(doc, path="/", span=[0, 1], n=3, var="x", values={"a": 2, "x": 7})
    assert res["needs"] == [] and len(res["curves"][0]["y"]) == 3


def test_no_value_is_guessed():
    doc = Document(a * sin(x), addons=[ADDON])
    res = _samples(doc, path="/", span=[0, 1], n=3)
    assert res["var"] == "a" and res["needs"] == ["x"] and res["curves"] == []


def test_a_piece_that_cannot_be_sampled_says_so():
    from sympy import Integral, oo, exp
    doc = Document(Integral(exp(-x ** 2), (x, -oo, y)) + sin(y), addons=[ADDON])
    snap = doc.handle({"action": "addon", "addon": "plot", "method": "samples", "path": "/", "span": [0, 1], "n": 3})
    assert snap["error"] is None                                     # not the editor's error
    assert "cannot be plotted as it stands" in snap["query"]["error"]
    assert "PrintMethodNotImplementedError" in snap["query"]["error"]


def _error(doc, **payload):
    snap = doc.handle(dict(payload, action="addon", addon="plot", method="samples"))
    return snap["query"].get("error") or ""


def test_the_number_of_points_is_clamped_once_for_both_axes():
    doc = Document(sin(x), addons=[ADDON])
    for n, want in [(1, 2), (6000, 5000), (10 ** 9, 5000)]:
        res = _samples(doc, path="/", span=[0, 1], n=n)
        assert len(res["x"]) == want and len(res["curves"][0]["y"]) == want
    assert "span" in _error(doc, path="/", span=[1, 1])
    assert "span" in _error(doc, path="/", span=["a", 2])


def test_a_function_with_no_numbers_behind_it_is_said_not_drawn_as_gaps():
    from sympy import besselj, factorial, zeta
    for expr in (besselj(0, x), zeta(x), factorial(x)):
        assert "cannot be plotted" in _error(Document(expr, addons=[ADDON]), path="/", span=[1, 3], n=5)
    # a curve with no real value anywhere is gaps, not an error
    res = _samples(Document(sqrt(-1 - x ** 2), addons=[ADDON]), path="/", span=[0, 1], n=5)
    assert res["curves"][0]["y"] == [None] * 5


def test_a_value_is_a_number_read_in_the_documents_names():
    doc = Document(y * sin(x), addons=[ADDON])
    assert "must be a number" in _error(doc, path="/", var="x", values={"y": "z"}, span=[0, 1], n=3)
    res = _samples(doc, path="/", var="x", values={"y": "pi/2"}, span=[0, 1], n=3)
    assert abs(res["curves"][0]["y"][2] - 3.141592653589793 / 2 * 0.8414709848078965) < 1e-9

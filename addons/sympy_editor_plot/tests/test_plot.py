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

import sys
from pathlib import Path

import pytest
from sympy import Matrix, Symbol, cos, log, sin, sqrt, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_assumptions import ADDON, facts, given  # noqa: E402

x, y = symbols("x y")


def _call(doc, method, **payload):
    return doc.handle(dict({"action": "addon", "addon": "assumptions", "method": method}, **payload))


def _row(res, name):
    return next(r for r in res["rows"] if r["name"] == name)


def test_facts_of_the_whole_formula_and_of_a_selection():
    doc = Document(x**2 + 1, addons=[ADDON])
    res = _call(doc, "facts", path="/")["query"]["result"]
    assert res["applicable"] and res["src"] == "x**2 + 1"
    assert [r["name"] for r in res["rows"]][:4] == ["real", "complex", "imaginary", "positive"]
    assert _row(res, "positive")["value"] is None           # x may be complex
    assert _row(res, "commutative")["value"] is True and _row(res, "commutative")["source"] == "assumptions"
    # the constant term, selected
    one = next(p for p, n in doc.snapshot()["nodes"].items() if n["src"] == "1")
    res = _call(doc, "facts", path=one)["query"]["result"]
    assert _row(res, "positive")["value"] is True and _row(res, "odd")["value"] is True
    assert _row(res, "prime")["value"] is False
    assert not doc.can_undo                                   # a query


def test_an_unknown_is_explained_with_what_would_decide_it():
    res = facts(x**2 + 1)
    pos = _row(res, "positive")
    assert pos["why"].startswith("SymPy cannot tell whether x**2 + 1 is positive")
    assert "depends on the value of x" in pos["why"] and "nothing is assumed" in pos["why"]
    assert {"names": ["x"], "assumption": "positive", "value": True} in pos["would"]
    assert "It would be True if x was assumed positive." in pos["why"]
    # nothing to assume: said so
    res = facts(sin(1) + cos(1))
    unk = [r for r in res["rows"] if r["value"] is None]
    assert unk and "no symbol" in unk[0]["why"]
    # two symbols together
    both = _row(facts(x + y), "integer")
    assert both["would"] == [] or all(w["value"] is not None for w in both["would"])


def test_ask_answers_where_the_old_assumptions_cannot():
    res = facts(sin(x)**2 + cos(x)**2)
    fin = _row(res, "finite")
    assert fin["value"] is True and fin["source"] == "ask"
    # too large for ask: the old assumptions alone
    big = sum(sin(i * x) for i in range(1, 30))
    res = facts(big)
    assert res["asked"] is False and all(r["source"] != "ask" for r in res["rows"])


def test_not_a_number():
    res = facts(Matrix([[x, 1], [0, y]]))
    assert res["applicable"] is False and "not a number" in res["reason"]


def test_symbols_ride_with_every_snapshot():
    xp = Symbol("x", positive=True)
    doc = Document(xp + y, addons=[ADDON])
    rows = {r["name"]: r for r in doc.snapshot()["assumptions"]["symbols"]}
    assert rows["x"]["given"] == {"positive": True}
    assert rows["x"]["known"]["real"] is True and rows["x"]["known"]["negative"] is False
    assert rows["y"]["given"] == {} and rows["y"]["known"] == {}


def test_assume_retypes_everywhere_and_undo_brings_it_back():
    doc = Document(sqrt(x**2) + x, addons=[ADDON])
    snap = _call(doc, "assume", name="x", assumption="positive", value=True)
    assert snap["error"] is None
    assert str(doc.expr) == "2*x"                            # sqrt(x**2) is x now
    (sym,) = doc.expr.free_symbols
    assert given(sym) == {"positive": True}
    assert doc.history_labels()["actions"][-1] == "Assumptions: x positive"
    hint = snap["assumptions"]["hint"]
    assert hint["rewrote"] and hint["before"] == "x + sqrt(x**2)"
    rows = {r["name"]: r for r in snap["assumptions"]["symbols"]}
    assert rows["x"]["given"] == {"positive": True}
    doc.undo()
    assert doc.expr == sqrt(x**2) + x
    assert "hint" not in doc.snapshot()["assumptions"]       # the hint was about the other step


def test_assume_false_and_forget():
    doc = Document(x + 1, addons=[ADDON])
    _call(doc, "assume", name="x", assumption="integer", value=False)
    (sym,) = doc.expr.free_symbols
    assert given(sym) == {"integer": False}
    _call(doc, "assume", name="x", assumption="integer", value=None)
    (sym,) = doc.expr.free_symbols
    assert given(sym) == {} and sym == x
    assert doc.history_labels()["actions"][-1] == "Assumptions: forget x integer"


def test_a_contradicting_assumption_is_dropped_and_said_so():
    xn = Symbol("x", negative=True, integer=True)
    doc = Document(xn**2, addons=[ADDON])
    snap = _call(doc, "assume", name="x", assumption="positive", value=True)
    (sym,) = doc.expr.free_symbols
    assert given(sym) == {"positive": True, "integer": True}
    assert snap["assumptions"]["hint"]["dropped"] == ["negative"]


def test_simplification_that_became_possible_and_applying_it():
    doc = Document(log(x) + log(y), addons=[ADDON])
    snap = _call(doc, "assume", name="y", assumption="integer", value=True)
    assert "hint" not in snap["assumptions"]                 # nothing new to simplify
    # log(x*y) = log(x) + log(y) whatever y is, once x > 0
    snap = _call(doc, "assume", name="x", assumption="positive", value=True)
    hint = snap["assumptions"]["hint"]
    assert hint["simplified"] == "log(x*y)" and hint["was"] == "log(x) + log(y)"
    _call(doc, "simplify")
    assert str(doc.expr) == "log(x*y)"
    assert doc.history_labels()["actions"][-1] == "Assumptions: simplify"
    res = _call(doc, "simplify")["query"]
    assert "no simplification waiting" in res["error"]


def test_refusals():
    doc = Document(x + 1, addons=[ADDON])
    assert "No symbol named 'z'" in _call(doc, "assume", name="z", assumption="positive", value=True)["query"]["error"]
    assert "not an assumption" in _call(doc, "assume", name="x", assumption="banana", value=True)["query"]["error"]
    # two symbols called x: refused by name
    doc = Document(x + Symbol("x", positive=True), addons=[ADDON])
    rows = doc.snapshot()["assumptions"]["symbols"]
    assert all(r.get("clash") for r in rows)
    assert "2 different symbols" in _call(doc, "assume", name="x", assumption="real", value=True)["query"]["error"]
    with pytest.raises(Exception):
        ADDON.handle(doc, "nothing", {})


def test_a_range_is_asked_about_as_a_whole():
    doc = Document(x + y + 1, addons=[ADDON])
    xp = Symbol("x", positive=True)
    yp = Symbol("y", positive=True)
    doc = Document(xp + yp + sin(x), addons=[ADDON])
    snap = doc.snapshot()
    path_x = next(p for p, n in snap["nodes"].items() if n["src"] == "x" and p.count("/") == 1)
    path_y = next(p for p, n in snap["nodes"].items() if n["src"] == "y" and p.count("/") == 1)
    idx = sorted(int(p.strip("/")) for p in (path_x, path_y))
    res = _call(doc, "facts", path="/", children=idx)["query"]["result"]
    assert res["src"] == "x + y" and _row(res, "positive")["value"] is True

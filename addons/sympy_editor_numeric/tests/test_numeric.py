"""The numeric add-on: values of the selection, exact forms, reasons, tables."""
import sys
from pathlib import Path

import pytest
from sympy import (
    Eq, Function, I, Integral, Matrix, Symbol, asin, exp, gamma, log, oo, sin, sqrt, symbols, tan,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_numeric import ADDON, NumericAddon, evaluate_at  # noqa: E402

x, y, a = symbols("x y a")


def _ask(doc, method, **payload):
    snap = doc.handle(dict(payload, action="addon", addon="numeric", method=method))
    assert not snap["error"], snap["error"]
    q = snap["query"]
    assert q["addon"] == "numeric"
    if "error" in q:
        raise ValueError(q["error"])
    return q["result"]


def _value(expr, digits=15, guess=False, **values):
    doc = Document(expr, addons=[ADDON])
    return _ask(doc, "evaluate", path="/", values=values, digits=digits, guess=guess)


def test_a_value_with_its_exact_form_and_nothing_changes():
    doc = Document(sin(x) + 1, addons=[ADDON])
    res = _ask(doc, "evaluate", path="/1", values={"x": "pi/3"}, digits=15)    # the selected piece
    assert res["src"] == "sin(x)" and res["free"] == ["x"] and res["needs"] == []
    assert res["result"] == {"kind": "real", "text": "0.866025403784439"}
    assert res["exact"]["src"] == "sqrt(3)/2" and res["exact"]["guessed"] is False and "sqrt" in res["exact"]["latex"]
    assert res["values"]["x"].startswith("1.047")             # what pi/3 was read as
    assert doc.can_undo is False                                # a query


@pytest.mark.parametrize("digits", [15, 30, 50, 100])
def test_the_precision_is_the_digits_asked_for(digits):
    res = _value(sqrt(2), digits=digits)
    text = res["result"]["text"]
    assert text.startswith("1.41421356237309504"[:min(digits, 17) - 3])
    assert len(text.replace(".", "").rstrip("0")) <= digits and len(text.replace(".", "")) >= digits - 2


def test_every_value_is_needed_and_none_is_guessed():
    res = _value(x + y, x="1")
    assert res["needs"] == ["y"] and res["result"] is None
    res = _value(x + y, x="1", y="1e-3")
    assert res["result"]["text"] == "1.001"


def test_values_are_read_like_typed_text_and_must_be_numbers():
    assert _value(x, x="sqrt(2)")["result"]["text"].startswith("1.414213562")
    with pytest.raises(ValueError, match="names z"):
        _value(x, x="z")
    with pytest.raises(ValueError, match="cannot be read"):
        _value(x, x="1 +")
    # an emptied field is no value
    assert _value(x, x="  ")["needs"] == ["x"]


def test_complex_values_read_a_plus_b_i():
    res = _value(x + y, x="1", y="2 + I")
    assert res["result"] == {"kind": "complex", "text": "3.0 + 1.0 i"} and res["exact"]["src"] == "3 + I"
    assert _value(x * I, x="-2")["result"]["text"] == "-2.0 i"
    assert evaluate_at(x - I, {x: 1}, 15)["text"] == "1.0 - 1.0 i"


def test_a_complex_value_from_real_inputs_says_where_it_left_the_reals():
    res = _value(sqrt(x), x="-2")
    assert res["result"]["kind"] == "complex" and "not real" in res["result"]["reason"]
    res = _value(asin(x) + 1, x="2")
    assert "outside the real domain of asin" in res["result"]["reason"]


@pytest.mark.parametrize("expr, value, kind, says", [
    (1 / x, "0", "undefined", "division by zero: 1/x at x = 0"),
    (sin(1 / x), "0", "undefined", "division by zero: 1/x"),
    (log(x), "0", "undefined", "singularity of log, outside its domain"),
    (tan(x), "pi/2", "undefined", "pi/2 is a singularity of tan"),
    (gamma(x), "-1", "undefined", "singularity of gamma"),
    ((x - 1) / (x ** 2 - 1), "1", "undefined", "division by zero"),
])
def test_no_value_says_why(expr, value, kind, says):
    res = _value(expr, x=value)["result"]
    assert res["kind"] == kind and says in res["reason"], res


def test_infinite_and_symbolic_values():
    res = evaluate_at(x + oo, {x: 1}, 15)
    assert res["kind"] == "infinite" and res["text"] == "∞"
    f = Function("f")
    res = _value(f(x), x="1")["result"]
    assert res["kind"] == "symbolic" and "undefined function" in res["reason"]


def test_integrals_relations_and_matrices():
    res = _value(Integral(exp(-y ** 2), (y, 0, x)), x="1")
    assert res["result"]["text"].startswith("0.7468241328")
    assert res["exact"] is None                         # not the decimal written as a fraction
    res = _value(Eq(x ** 2, 4), x="2")["result"]
    assert res["kind"] == "relation" and res["truth"] is True and "==" in res["text"]
    res = _value(Matrix([[x, 1 / x]]), x="2")["result"]
    assert res == {"kind": "matrix", "text": "[[2.0, 0.5]]"}


def test_guessing_an_exact_form_is_asked_for_and_marked():
    assert _value(x, x="0.785398163397448")["exact"] is None
    res = _value(x, guess=True, x="0.785398163397448")
    assert res["exact"] and res["exact"]["guessed"] is True and res["exact"]["src"] == "pi/4"


def test_a_range_is_evaluated_like_its_parent_would_send_it():
    doc = Document(x + y + sin(x), addons=[ADDON])
    res = _ask(doc, "evaluate", path="/", children=[0, 1], values={"x": "1", "y": "2"})
    assert res["result"]["text"] == "3.0"


def test_symbols_a_number_cannot_stand_for_are_refused():
    from sympy import MatrixSymbol
    A = MatrixSymbol("A", 2, 2)
    with pytest.raises(ValueError, match="matrix expression"):
        _value(A * 2)
    with pytest.raises(ValueError, match="two different symbols called x"):
        _value(x + Symbol("x", real=True), x="1")


def test_a_table_over_a_range():
    doc = Document(1 / x + y, addons=[ADDON])
    res = _ask(doc, "table", path="/", var="x", values={"y": "1"}, start="-1", stop="1", step="0.5")
    assert res["var"] == "x" and [r["x"] for r in res["rows"]] == ["-1", "-0.5", "0", "0.5", "1"]
    assert [r["value"] for r in res["rows"]][:2] == ["0", "-1.0"]
    zero = res["rows"][2]
    assert zero["kind"] == "undefined" and "division by zero" in zero["note"]
    assert res["capped"] is False and res["stopped"] is None and res["asked"] == 5


def test_a_table_over_a_list_keeps_the_exact_points():
    doc = Document(sin(x), addons=[ADDON])
    res = _ask(doc, "table", path="/", var="x", list="0, pi/6, pi/2")
    assert [r["x"] for r in res["rows"]] == ["0", "0.523598775598299", "1.5707963267949"]
    assert res["rows"][1]["exact"] == "pi/6" and res["rows"][1]["value"] == "0.5"


def test_a_table_needs_the_other_values_and_a_sound_range():
    doc = Document(a * x, addons=[ADDON])
    res = _ask(doc, "table", path="/", var="x", start="0", stop="1", step="1")
    assert res["needs"] == ["a"] and res["rows"] == []
    with pytest.raises(ValueError, match="goes the other way"):
        _ask(doc, "table", path="/", var="x", values={"a": "1"}, start="1", stop="0", step="1")
    with pytest.raises(ValueError, match="cannot be zero"):
        _ask(doc, "table", path="/", var="x", values={"a": "1"}, start="0", stop="1", step="0")
    with pytest.raises(ValueError, match="finite real"):
        _ask(doc, "table", path="/", var="x", values={"a": "1"}, start="I", stop="1", step="1")
    with pytest.raises(ValueError, match="no free symbol"):
        _ask(Document(sin(1) + x - x, addons=[ADDON]), "table", path="/", start="0", stop="1", step="1")


def test_a_table_is_capped_and_time_boxed():
    doc = Document(x ** 2, addons=[ADDON])
    res = _ask(doc, "table", path="/", var="x", start="0", stop="100000", step="1")
    assert res["capped"] is True and len(res["rows"]) == ADDON.max_rows and res["asked"] == 100001
    slow = NumericAddon(table_seconds=0.0)
    doc = Document(x ** 2, addons=[slow])
    res = _ask(doc, "table", path="/", var="x", start="0", stop="10", step="1")
    assert res["stopped"] == "time" and len(res["rows"]) < 11


def test_the_panel_ships_with_the_page():
    from sympy_editor.html import build_config
    doc = Document(x, addons=["sympy_editor_numeric"], available=[])
    cfg = build_config(doc, backend="pyodide")
    assert cfg["document"]["addons"] == ["sympy_editor_numeric"]
    assert {"__init__.py", "static/numeric.js", "static/numeric.css"} <= set(cfg["packages"]["sympy_editor_numeric"])
    assert "registerAddon(\"numeric\"" in ADDON.js and ADDON.client_options()["digits"] == [15, 30, 50, 100]

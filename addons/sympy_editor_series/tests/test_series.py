import sys
import threading
import time
from pathlib import Path

import pytest
from sympy import Integer, O, Rational, exp, gamma, log, oo, sin, sqrt, symbols, tan

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_series import ADDON, GaveUp, SeriesAddon, expand_node, run_limited  # noqa: E402

x, y, a = symbols("x y a")


def ask(doc, **payload):
    res = doc.handle({"action": "addon", "addon": "series", "method": "expand", **payload})["query"]
    if res.get("error"):
        raise ValueError(res["error"])
    return res["result"]


def test_taylor_series_with_its_o_term_and_without():
    doc = Document(sin(x), addons=[ADDON])
    res = ask(doc, n=6)
    assert res["label"] == "Taylor" and res["text"] == "x - x**3/6 + x**5/120 + O(x**6)"
    assert res["text_plain"] == "x - x**3/6 + x**5/120"                  # read in rising powers
    assert res["has_o"] and not res["dir_matters"]
    assert [(t["k"], t["coeff"]) for t in res["terms"]] == [("1", "1"), ("3", "-1/6"), ("5", "1/120")]
    assert not doc.can_undo                                               # a query changes nothing


def test_the_point_is_any_expression_and_the_coefficients_are_of_x_minus_a():
    doc = Document(sin(x), addons=[ADDON])
    res = ask(doc, point="1", n=3)
    assert res["text_plain"] == "sin(1) + (x - 1)*cos(1) - (x - 1)**2*sin(1)/2"
    assert [t["k"] for t in res["terms"]] == ["0", "1", "2"]
    assert res["terms"][2]["coeff"] == "-sin(1)/2"
    res = ask(Document(exp(x), addons=[ADDON]), point="a", n=2)          # a symbolic point
    assert res["text"] == "exp(a) + (-a + x)*exp(a) + O((-a + x)**2, (x, a))"
    assert res["check"] is None and res["check_error"] is None           # no number to sample near a


def test_laurent_puiseux_and_logarithms_are_named():
    assert ask(Document(1 / sin(x), addons=[ADDON]), n=4)["label"] == "Laurent"
    res = ask(Document(sqrt(x + x**2), addons=[ADDON]), n=3)
    assert res["label"] == "Puiseux" and [t["k"] for t in res["terms"]] == ["1/2", "3/2", "5/2"]
    assert ask(Document(log(x) + x, addons=[ADDON]), n=3)["label"] == "with logarithms"


def test_asymptotic_at_infinity_in_falling_powers():
    doc = Document(1 / (x + 1), addons=[ADDON])
    res = ask(doc, point="oo", n=4)
    assert res["label"] == "asymptotic" and res["infinite"]
    assert res["text_plain"] == "1/x - 1/x**2 + x**(-3)"
    assert res["check"]["at"] == "10" and float(res["check"]["error"]) < 1e-3
    res = ask(doc, kind="asymptotic", point="0", n=3)                     # asymptotic means infinity
    assert res["point"] == "oo" and "infinity" in res["note"]
    assert ask(Document(x / (x + 1), addons=[ADDON]), point="-oo", n=3)["point"] == "-oo"


def test_the_direction_is_reported_where_it_matters():
    assert not ask(Document(sin(x), addons=[ADDON]), n=3)["dir_matters"]
    doc = Document(sqrt(x**2), addons=[ADDON])
    res = ask(doc, n=3)
    assert res["dir_matters"] and res["text"] == "x"
    assert ask(doc, n=3, dir="-")["text"] == "-x"
    both = ask(doc, n=3, dir="both")
    assert both["other"]["text"] == "-x"


def test_the_leading_term():
    res = ask(Document(sin(x) / x**3, addons=[ADDON]), kind="leading")
    assert res["text"] == "x**(-2)" and res["coeff"] == "1" and res["exponent"] == "-2"
    res = ask(Document(sin(x), addons=[ADDON]), kind="leading", point="pi")
    assert res["text"] == "pi - x" and res["exponent"] == "1"
    res = ask(Document(1 / (x**2 + 1), addons=[ADDON]), kind="leading", point="oo")
    assert res["text"] == "x**(-2)" and res["exponent"] == "-2"


def test_the_truncation_error_at_a_sample_point():
    doc = Document(exp(x), addons=[ADDON])
    res = ask(doc, n=3)
    assert res["check"]["at"] == "1/10"
    assert res["check"]["error"] == pytest.approx(float(exp(Rational(1, 10)) - Rational(1, 10) - Rational(1, 200) - 1))
    assert res["check"]["o_size"] == "0.001"
    res = ask(doc, n=3, at="1/2")
    assert res["check"]["at"] == "1/2" and res["check"]["error"] > 0.02
    res = ask(Document(sin(a * x), addons=[ADDON]), n=3)
    assert "numbers for a" in res["check_error"]


def test_failures_are_said_in_words():
    with pytest.raises(ValueError, match="singularity"):
        ask(Document(gamma(x), addons=[ADDON]), point="oo")
    res = ask(Document(exp(1 / x), addons=[ADDON]), n=3)
    assert res["label"] == "no expansion" and "essential singularity" in res["note"]
    assert ask(Document(exp(x), addons=[ADDON]), point="oo")["label"] == "no expansion"
    res = ask(Document(sqrt(x**2) + exp(x), addons=[ADDON]), point="oo")    # what SymPy leaves as it is
    assert res["label"] == "incomplete" and "SymPy left exp(x) as it is" in res["note"]
    with pytest.raises(ValueError, match="constant"):
        ask(Document(sin(Integer(1)) + 2, addons=[ADDON]))
    with pytest.raises(ValueError, match="cannot contain x"):
        ask(Document(sin(x), addons=[ADDON]), point="x + 1")


def test_a_series_that_does_not_end_is_given_up_on():
    slow = SeriesAddon(time_limit=0.5)
    doc = Document(exp(exp(exp(x))) / x**3 + sin(tan(x))**20, addons=[slow])
    began = time.monotonic()
    with pytest.raises(ValueError, match="gave up"):
        ask(doc, n=20)
    assert time.monotonic() - began < 5


def test_run_limited_stops_the_computation():
    def spin():
        while True:
            pass
    with pytest.raises(GaveUp):
        run_limited(spin, 0.2)
    for _ in range(50):
        if not any(t.name == "sympy-editor-series" for t in threading.enumerate()):
            break
        time.sleep(0.05)
    assert not any(t.name == "sympy-editor-series" for t in threading.enumerate())
    with pytest.raises(ZeroDivisionError):
        run_limited(lambda: 1 / 0, 1)
    assert run_limited(lambda: 42, 1) == 42 and run_limited(lambda: 7, None) == 7


def test_a_selection_and_a_range_are_expanded():
    doc = Document(y + sin(x) * exp(x), addons=[ADDON])
    paths = {str(doc.get(p)): p for p in doc.snapshot()["nodes"]}
    res = ask(doc, path=paths["sin(x)"], n=4)
    assert res["src"] == "sin(x)" and res["free"] == ["x"]
    res = ask(doc, n=2, var="y")                                            # the whole formula, in y
    assert res["var"] == "y"
    assert expand_node(sin(x) * exp(x), x, Integer(0), 3)["plain"] == x**2 + x


def test_insert_is_one_undoable_step():
    doc = Document(y + sin(x), addons=[ADDON])
    path = next(p for p in doc.snapshot()["nodes"] if doc.get(p) == sin(x))
    payload = {"path": path, "n": 4, "kind": "series", "point": "0"}
    ask(doc, **payload)
    doc.handle({"action": "addon", "addon": "series", "method": "insert", "with_o": True, **payload})
    assert doc.expr == y + x - x**3 / 6 + O(x**4)
    assert doc.history_labels()["actions"][-1] == "Series: x → 0 (order 4)"
    doc.undo()
    assert doc.expr == y + sin(x)
    doc.handle({"action": "addon", "addon": "series", "method": "insert", "with_o": False, **payload})
    assert doc.expr == y + x - x**3 / 6
    assert doc.history_labels()["actions"][-1] == "Series: x → 0 (order 4, without O)"


def test_the_expansion_prints_in_the_formula():
    """An O term in the formula is selectable like any node: the annotated
    printer reaches it, and the document reads it back from a session."""
    doc = Document(sin(x), addons=[ADDON])
    doc.handle({"action": "addon", "addon": "series", "method": "insert", "with_o": True, "n": 4})
    snap = doc.snapshot()
    assert "O" in snap["latex_plain"]
    state = doc.export()
    again = Document(None, history=state["history"], index=state["index"], addons=[ADDON])
    assert again.expr == x - x**3 / 6 + O(x**4)


def test_the_addon_is_found_as_a_folder():
    from sympy_editor.addons import scan_addons
    found = scan_addons(Path(__file__).resolve().parents[2])
    assert found["series"]["module"] == "sympy_editor_series"


def test_without_threads_the_computation_simply_runs(monkeypatch):
    """Pyodide cannot start a thread: the series runs as it is there."""
    def refuse(self):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Thread, "start", refuse)
    assert run_limited(lambda: 42, 0.1) == 42
    assert ask(Document(sin(x), addons=[ADDON]), n=4)["text"] == "x - x**3/6 + O(x**4)"

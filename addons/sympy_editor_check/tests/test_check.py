import sys
import time
from pathlib import Path

import pytest
from sympy import (Eq, Integer, Lt, Matrix, MatrixSymbol, Or, cos, exp, log, sin, sqrt, symbols)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_check import ADDON, CheckAddon, classify  # noqa: E402
from sympy_editor_check.compare import attempt, compare, numeric_check  # noqa: E402

x, y = symbols("x y")
p = symbols("p", positive=True)


def _status(a, b):
    return compare(a, b)["status"]


@pytest.mark.parametrize("a, b", [
    ((x + 1) ** 2, x ** 2 + 2 * x + 1),
    (sin(x) ** 2 + cos(x) ** 2, Integer(1)),
    (sqrt(p ** 2), p),
    (exp(x) ** 2, exp(2 * x)),
    (Matrix([[x, 1], [0, x]]) ** 2, Matrix([[x ** 2, 2 * x], [0, x ** 2]])),
    (Eq(2 * x + 2, 4), Eq(x + 1, 2)),
    (Eq(x ** 2, 4), Or(Eq(x, 2), Eq(x, -2))),
    (Eq(x + y, 3), Eq(y, 3 - x)),
    (Lt(2 * x, 4), Lt(x, 2)),
    (Eq(x ** 2 - 1, 0), Eq((x - 1) * (x + 1), 0)),
])
def test_equal_steps(a, b):
    assert _status(a, b) == "equal"


def test_a_difference_names_the_point_and_the_two_values():
    res = compare((x + 1) ** 2, x ** 2 + x + 1)
    assert res["status"] == "different"
    (name, value), = res["point"].items()
    v = float(value)
    assert name == "x" and v != 0
    assert float(res["values"][0]) == pytest.approx((v + 1) ** 2, rel=1e-4)
    assert float(res["values"][1]) == pytest.approx(v ** 2 + v + 1, rel=1e-4)
    assert "Not the same" in res["text"] and "x = " in res["text"]


def test_assumptions_choose_the_points():
    assert _status(sqrt(x ** 2), x) == "different"             # x may be negative
    assert _status(sqrt(p ** 2), p) == "equal"


def test_a_lost_solution_is_found():
    res = compare(Eq(x ** 2, 4), Eq(x, 2))
    assert res["status"] == "different" and res["point"] == {"x": "-2"}


def test_an_inequality_multiplied_by_a_negative_number_must_turn_round():
    assert _status(Lt(-2 * x, 4), Lt(x, -2)) == "different"
    assert _status(Lt(-2 * x, 4), Lt(-2, x)) == "equal"


def test_equations_in_several_unknowns():
    assert _status(Eq(x + y, 3), Eq(y, 3 + x)) == "different"


def test_matrix_expressions_are_sampled_with_random_matrices():
    A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
    assert _status((A * B).T, B.T * A.T) == "equal"
    assert _status((A * B).T, A.T * B.T) == "different"
    assert compare(Matrix([[1, 2]]), Matrix([[1], [2]]))["status"] == "different"     # shapes


def test_an_equation_and_an_expression_are_not_compared():
    res = compare(Eq(x, 1), x - 1)
    assert res["status"] == "unknown" and "not compared" in res["text"]


def test_numbers_are_compared_as_numbers():
    assert _status(Integer(2), Integer(3)) == "different"
    assert numeric_check(log(x * y), log(x) + log(y))["status"] == "different"


def test_attempt_stops_a_computation_that_runs_too_long():
    def forever():
        def step(n):
            return n + 1
        n = 0
        while True:
            n = step(n)
    t = time.monotonic()
    assert attempt(forever, 0.2) == (None, "time")
    assert time.monotonic() - t < 2
    assert attempt(lambda: 1 / 0, 1) == (None, "error")
    assert attempt(lambda: 42, 1) == (42, None)
    old = sys.gettrace()

    def mine(frame, event, arg):
        return None
    sys.settrace(mine)
    try:
        attempt(forever, 0.05)
        assert sys.gettrace() is mine                                # a debugger's or coverage's tracer is put back
    finally:
        sys.settrace(old)


def test_attempt_lets_the_editors_interrupt_through():
    from sympy_editor.document import Interrupted

    def interrupted():
        raise Interrupted("stop")
    with pytest.raises(Interrupted):
        attempt(interrupted, 1)


def test_a_hard_comparison_is_time_boxed():
    a = sum(sin(k * x) ** 7 * cos(k * x) ** 5 for k in range(1, 7))
    b = a + sin(x) ** 2 + cos(x) ** 2 - 1
    t = time.monotonic()
    res = compare(a, b, seconds=1.0)
    assert time.monotonic() - t < 3.5
    assert res["status"] in ("equal", "unknown")


def test_labels_say_which_steps_are_transformations():
    assert classify("Transform: Differentiate…")
    assert classify("Transform: Integrate…")
    assert classify("Transform: Solve for…")
    assert classify("Transform: Substitute…")
    assert classify("SymPy: diff(x)")
    assert classify("SymPy: .subs(x, 2)")
    assert classify("Unwrap x**2")
    assert classify("Transform: Simplify") is None
    assert classify("Transform: Expand (unevaluated)") is None
    assert classify("SymPy: factor") is None
    assert classify("SymPy: .rewrite(exp)") is None
    assert classify("Edit: x → y") is None
    assert classify(None) is None


def _rows(doc):
    res = doc.handle({"action": "addon", "addon": "check", "method": "check", "budget": 30})["query"]["result"]
    assert not res["pending"]
    return res


def test_the_history_is_checked_step_by_step():
    doc = Document((x + 1) ** 2, addons=[CheckAddon()])
    send = doc.handle                                              # as the front end does: the history gets labels
    send({"action": "apply", "path": "/", "op": "expand"})                                # ✓
    send({"action": "replace", "path": "/", "src": "x**2 + x + 1"})                       # ✗: the maths went wrong here
    send({"action": "apply", "path": "/", "op": "differentiate", "args": ["x"]})          # →
    send({"action": "replace", "path": "/", "src": "1 + 2*x"})                            # ✓ (identical)
    res = _rows(doc)
    assert [r["status"] for r in res["steps"]] == ["start", "equal", "different", "transformation", "equal"]
    assert [r["symbol"] for r in res["steps"]] == ["•", "✓", "✗", "→", "✓"]
    assert res["first_error"] == 2
    assert res["index"] == 4
    assert "Differentiate" in res["steps"][3]["text"]
    assert res["steps"][2]["point"]
    assert res["counts"] == {"equal": 2, "different": 1, "transformation": 1}


def test_a_check_is_answered_as_a_query_and_changes_nothing():
    doc = Document(x + x, addons=[CheckAddon()])
    doc.replace("/", "2*x")
    before = doc.expr
    snap = doc.handle({"action": "addon", "addon": "check", "method": "check"})
    assert "result" in snap["query"] and doc.expr == before and doc.history_labels()["index"] == 1


def test_goto_is_the_editors_and_the_check_follows_the_index():
    doc = Document(x, addons=[CheckAddon()])
    doc.replace("/", "x + 0")
    doc.replace("/", "2*x")
    doc.handle({"action": "goto", "index": 1})
    assert _rows(doc)["index"] == 1


def test_steps_being_built_and_an_empty_start_are_not_errors():
    from sympy_editor.printer import Placeholder
    doc = Document(Integer(0), addons=[CheckAddon()])
    doc.handle({"action": "set", "src": "(x + 1)**2"})
    doc.set(Placeholder("_1") + x)
    doc.set(y + x)
    statuses = [r["status"] for r in _rows(doc)["steps"]]
    assert statuses == ["start", "start", "incomplete", "incomplete"]


def test_a_request_never_runs_much_past_its_budget():
    addon = CheckAddon()
    doc = Document(x, addons=[addon])
    for k in range(2, 12):
        doc.replace("/", f"x + {k} - {k}")                          # cheap, but each is a pair to compare
        doc.replace("/", f"sin(x)**2 + cos(x)**2 + x - 1 + {0}")
    res = doc.handle({"action": "addon", "addon": "check", "method": "check", "budget": 0})["query"]["result"]
    assert res["pending"] and any(r["status"] == "pending" for r in res["steps"])
    for _ in range(100):
        res = doc.handle({"action": "addon", "addon": "check", "method": "check"})["query"]["result"]
        if not res["pending"]:
            break
    assert not res["pending"]


def test_verdicts_are_kept_and_reach_the_history_steps():
    addon = CheckAddon()
    doc = Document((x + 1) ** 2, addons=[addon])
    doc.replace("/", "x**2 + 1")
    steps = doc.history_labels()["steps"]
    assert "check" not in steps[1]                                 # not computed by the history view
    _rows(doc)
    steps = doc.history_labels()["steps"]
    assert steps[1]["check"]["status"] == "different" and steps[1]["check"]["symbol"] == "✗"
    assert steps[0]["check"]["status"] == "start"
    t = time.monotonic()
    _rows(doc)                                                     # from the cache
    assert time.monotonic() - t < 0.5


def test_the_snapshot_carries_only_the_history_size():
    doc = Document(x, addons=[ADDON])
    doc.replace("/", "2*x")
    assert doc.snapshot()["check"] == {"n": 2, "index": 1}


def test_unknown_method_is_refused():
    doc = Document(x, addons=[ADDON])
    snap = doc.handle({"action": "addon", "addon": "check", "method": "nope"})
    assert snap["query"]["error"]

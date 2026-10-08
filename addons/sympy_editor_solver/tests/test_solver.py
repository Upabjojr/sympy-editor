import sys
import time
from pathlib import Path

import pytest
from sympy import And, Dummy, Eq, FiniteSet, ImageSet, Integers, Symbol, Tuple, exp, sin, sqrt, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_solver import ADDON  # noqa: E402
from sympy_editor_solver import solver as solver_module  # noqa: E402
from sympy_editor_solver.solver import TimeUp, read_problem, time_boxed  # noqa: E402

x, y, z, a, n = symbols("x y z a n")


def call(doc, method, **payload):
    """A method as the panel calls it: the query's result, or raises with
    its error; a change answers with the new snapshot."""
    snap = doc.handle({"action": "addon", "addon": "solver", "method": method, **payload})
    if "query" in snap:
        if snap["query"].get("error"):
            raise ValueError(snap["query"]["error"])
        return snap["query"]["result"]
    assert not snap.get("error"), snap.get("error")
    return snap


def test_the_selection_reads_as_an_equation_an_inequality_an_expression_or_a_system():
    doc = Document(Eq(x**2, 4), addons=[ADDON])
    p = call(doc, "problem", path="/")
    assert p["ok"] and p["kind"] == "equation" and p["defaults"] == ["x"] and p["latex"] == "x^{2} = 4"
    assert call(Document(x**2 > 4, addons=[ADDON]), "problem", path="/")["kind"] == "inequality"
    p = call(Document(x**2 + a, addons=[ADDON]), "problem", path="/")
    assert p["kind"] == "expression" and "= 0" in p["words"] and p["latex"].endswith("= 0")
    assert [s["name"] for s in p["symbols"]] == ["a", "x"] and p["defaults"] == ["x"]      # x before a
    p = call(Document(And(Eq(x + y, 3), Eq(x - y, 1)), addons=[ADDON]), "problem", path="/")
    assert p["kind"] == "system" and p["count"] == 2 and p["defaults"] == ["x", "y"] and "cases" in p["latex"]
    p = call(Document(FiniteSet(Eq(x + y, 3), x - y), addons=[ADDON]), "problem", path="/")
    assert p["kind"] == "system" and "expression" in p["words"]


def test_what_cannot_be_solved_says_why():
    for expr, words in [(Eq(x, 1) | Eq(y, 2), "or"), (Integers, "not an equation"), (sqrt(2) + 1, "no free symbol")]:
        p = call(Document(expr, addons=[ADDON]), "problem", path="/")
        assert not p["ok"] and words in p["reason"], (expr, p)
    x_real = Symbol("x", real=True)
    p = call(Document(x + x_real, addons=[ADDON]), "problem", path="/")
    assert not p["ok"] and "same name" in p["reason"]


def test_one_unknown_is_solved_by_solveset_and_each_solution_has_its_line():
    doc = Document(Eq(x**2, 4), addons=[ADDON])
    res = call(doc, "solve", path="/")
    assert res["method"] == "solveset" and res["summary"] == "2 solutions." and res["domain"] == "ℂ"
    assert [i["latex"] for i in res["items"]] == ["x = -2", "x = 2"] and all(i["check"] for i in res["items"])
    pos = call(doc, "solve", path="/", domain="positive")
    assert [i["latex"] for i in pos["items"]] == ["x = 2"]
    none = call(Document(Eq(x**2, -4), addons=[ADDON]), "solve", path="/", domain="real")
    assert none["items"] == [] and none["summary"] == "No solution in ℝ."


def test_infinite_sets_are_shown_as_such_with_words():
    res = call(Document(sin(x) - 1, addons=[ADDON]), "solve", path="/")
    assert res["summary"] == "Infinitely many solutions."
    (item,) = res["items"]
    assert item["kind"] == "set" and "every integer n" in item["words"] and item["check"]
    assert "_n" not in item["src"] and "n \\in \\mathbb{Z}" in item["latex"]                 # no Dummy left
    res = call(Document(x**2 > 4, addons=[ADDON]), "solve", path="/")
    assert "solved over ℝ" in res["notes"][0] and len(res["items"]) == 2
    assert all("interval" in i["words"] and not i["check"] for i in res["items"])
    res = call(Document(exp(x) - x, addons=[ADDON]), "solve", path="/", domain="real")
    assert "could not solve" in res["summary"] and "condition" in res["items"][0]["words"]


def test_systems_take_linsolve_nonlinsolve_and_the_domain_filters():
    doc = Document(And(Eq(x + y, 3), Eq(x - y, 1)), addons=[ADDON])
    res = call(doc, "solve", path="/")
    assert res["method"] == "linsolve" and [i["latex"] for i in res["items"]] == ["x = 2,\\quad y = 1"]
    free = call(Document(Eq(x + y, 1), addons=[ADDON]), "solve", path="/", unknowns=["x", "y"])
    assert free["items"][0]["latex"] == "x = 1 - y" and "y is free" in free["items"][0]["words"]
    circle = Document(And(Eq(x**2 + y**2, 1), Eq(x, y)), addons=[ADDON])
    res = call(circle, "solve", path="/")
    assert res["method"] == "nonlinsolve" and len(res["items"]) == 2
    res = call(circle, "solve", path="/", domain="positive")
    assert len(res["items"]) == 1 and "left out" in res["notes"][0]
    res = call(Document(And(x > y, Eq(x, 1)), addons=[ADDON]), "solve", path="/", unknowns=["x", "y"])
    assert res["failed"] and "inequalities in several unknowns" in res["message"]


def test_a_range_of_an_and_is_the_system():
    doc = Document(And(Eq(x + y, 3), Eq(x - y, 1), Eq(z, 5)), addons=[ADDON])
    order = [str(arg) for arg in doc.expr.args]
    picked = [order.index("Eq(x + y, 3)"), order.index("Eq(x - y, 1)")]
    p = call(doc, "problem", path="/", children=picked)
    assert p["count"] == 2 and [s["name"] for s in p["symbols"]] == ["x", "y"]
    res = call(doc, "solve", path="/", children=picked)
    call(doc, "insert", token=res["token"], index=0)
    assert doc.expr == And(Eq(x, 2), Eq(y, 1), Eq(z, 5))


def test_substitute_back_shows_the_substituted_equation_and_its_verdict():
    doc = Document(Eq(x**2, 4), addons=[ADDON])
    res = call(doc, "solve", path="/")
    chk = call(doc, "check", token=res["token"], index=0)
    assert chk["rows"] == [{"before": "\\left(-2\\right)^{2} = 4", "after": "\\text{True}", "verdict": "holds"}]
    assert chk["words"] == "It holds."
    doc = Document(sin(x) - 1, addons=[ADDON])
    res = call(doc, "solve", path="/")
    chk = call(doc, "check", token=res["token"], index=0)
    assert chk["rows"][0]["verdict"] == "holds" and chk["words"] == "It holds for every integer n."
    doc = Document(x**5 - x + 1, addons=[ADDON])                       # a CRootOf: decided numerically
    res = call(doc, "solve", path="/", domain="real")
    assert call(doc, "check", token=res["token"], index=0)["rows"][0]["verdict"] == "holds"


def test_insert_is_an_undoable_step_in_place_of_the_selection():
    doc = Document(Eq(x**2, 4) & Eq(y, 1), addons=[ADDON])
    path = "/" + str([str(arg) for arg in doc.expr.args].index("Eq(x**2, 4)"))
    res = call(doc, "solve", path=path)
    call(doc, "insert", token=res["token"], index=1, text="Eq(x, 2)")
    assert doc.expr == And(Eq(x, 2), Eq(y, 1))
    assert doc.history_labels()["actions"][-1] == "Solve: a solution Eq(x, 2)"
    doc.undo()
    assert doc.expr == Eq(x**2, 4) & Eq(y, 1)
    with pytest.raises(ValueError, match="solve again"):                # the token went with the insertion
        call(doc, "insert", token=res["token"], index=0)
    doc = Document(sin(x) - 1, addons=[ADDON])
    res = call(doc, "solve", path="/")
    call(doc, "insert", token=res["token"], whole=True)
    assert isinstance(doc.expr, ImageSet) and not doc.expr.atoms(Dummy)
    assert doc.history_labels()["actions"][-1].startswith("Solve: the solution set")
    saved = doc.export()
    again = Document(saved["history"][0], history=saved["history"], index=saved["index"], format=saved["format"],
                     addons=[ADDON])                                     # the session opens again
    assert again.expr == doc.expr


def test_insert_refuses_a_formula_changed_since():
    doc = Document(Eq(x**2, 4), addons=[ADDON])
    res = call(doc, "solve", path="/")
    doc.replace("/0", "x**3")
    with pytest.raises(ValueError, match="changed since"):
        call(doc, "insert", token=res["token"], index=0)


def test_solving_is_time_boxed(monkeypatch):
    def forever(*args, **kwargs):
        while True:
            sum(abs(i) for i in range(50))
    start = time.monotonic()
    with pytest.raises(TimeUp):
        time_boxed(forever, 0.2)
    assert time.monotonic() - start < 2 and sys.getprofile() is None
    monkeypatch.setattr(solver_module, "solveset", forever)
    doc = Document(Eq(x**2, 4), addons=[ADDON])
    res = call(doc, "solve", path="/", timeout=0.3)
    assert res["timed_out"] and "gave up" in res["message"]
    assert doc.expr == Eq(x**2, 4) and not doc.can_undo


def test_the_problem_reader_and_the_pyodide_sources():
    p = read_problem(Tuple(Eq(x, 1), y - 2))
    assert p.system and [str(r) for r in p.as_relations()] == ["Eq(x, 1)", "Eq(y - 2, 0)"]
    sources = ADDON.python_sources()
    assert any(k.endswith("solver.py") for k in sources) and any(k.endswith("solver.js") for k in sources)
    assert "Solve" in ADDON.js and ADDON.client_options()["timeout"] > 0


def test_a_set_where_it_cannot_stand_is_refused_with_a_way_out():
    doc = Document(And(Eq(x**2, 4), Eq(y, 1)), addons=[ADDON])
    path = "/" + str([str(arg) for arg in doc.expr.args].index("Eq(x**2, 4)"))
    res = call(doc, "solve", path=path)
    with pytest.raises(ValueError, match="insert one solution instead"):
        call(doc, "insert", token=res["token"], whole=True)               # {-2, 2} inside an "and"
    assert doc.expr == And(Eq(x**2, 4), Eq(y, 1)) and not doc.can_undo
    res = call(Document(And(Eq(x + y, 1), Eq(x + y, 2)), addons=[ADDON]), "solve", path="/")
    assert res["items"] == [] and res["summary"] == "No solution in ℂ."

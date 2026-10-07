"""The steps add-on's Python: the three engines, and the two methods
through a Document (a query, and a change that is a step of the history)."""
import json
import sys
from pathlib import Path

import pytest
from sympy import Derivative, Eq, Function, I, Integral, Or, asin, cos, diff, exp, log, simplify, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor.server import load_session  # noqa: E402
from sympy_editor_steps import ADDON  # noqa: E402
from sympy_editor_steps.engine import explain  # noqa: E402

x, y = symbols("x y")
f = Function("f")


def texts(expl):
    return [st.text for st in expl.steps]


def _same(a, b):
    return simplify(a - b) == 0


# -- integrals ------------------------------------------------------------------

def test_integration_by_parts_step_by_step():
    expl = explain(Integral(x * sin(x), x))
    assert expl.task == "integrate" and expl.message is None
    assert texts(expl)[0].startswith("Integration by parts")
    assert "u = x" in expl.steps[0].detail
    # every applicable step is the same value, the integrals left standing until done
    assert expl.steps[0].expr.has(Integral)
    last = [st for st in expl.steps if st.applicable][-1].expr
    assert not last.has(Integral) and _same(diff(last, x), x * sin(x))
    assert expl.steps[-1].text == "Add the constant of integration" and not expl.steps[-1].applicable
    assert expl.steps[-1].latex().endswith("+ C")


def test_a_substitution_names_its_variable_and_puts_x_back():
    expl = explain(Integral(sin(x) ** 3, x))
    t = texts(expl)
    assert "Substitution" in t and "Substitute back" in t
    sub = expl.steps[t.index("Substitution")]
    u = symbols("u")
    assert sub.expr == Integral(u**2 - 1, u)                 # a plain symbol, not a Dummy: something one can apply
    assert "u = \\cos" in sub.detail and "du = " in sub.detail
    final = expl.steps[t.index("Substitute back")].expr
    assert _same(diff(final, x), sin(x) ** 3)


def test_the_substitution_variable_avoids_names_in_use():
    u = symbols("u")
    expl = explain(Integral(u * x * exp(x**2), x))
    names = {str(s) for st in expl.steps if st.expr is not None for s in st.expr.free_symbols}
    assert "v" in names                                       # u is the formula's: the new one is v


def test_a_definite_integral_shows_the_antiderivative_and_applies_the_value():
    expl = explain(Integral(x * exp(x**2), (x, 0, 1)))
    assert expl.steps[0].text == "First find an antiderivative"
    assert [st.applicable for st in expl.steps[:-1]] == [False] * (len(expl.steps) - 1)
    assert expl.steps[-1].applicable and _same(expl.steps[-1].expr, (exp(1) - 1) / 2)
    assert "\\right]_{0}^{1}" in expl.steps[-1].latex()


def test_an_integral_without_rules_says_so():
    expl = explain(Integral(x**x, x))
    assert "no rule" in expl.message
    assert not [st for st in expl.steps if st.applicable]


def test_an_integral_with_no_steps_but_a_result_gives_the_result():
    expl = explain(Integral(exp(x**2) * x**2 / (1 + x), x))
    assert expl.message                                       # whatever happens, said in words


def test_double_integrals_are_refused_in_words():
    expl = explain(Integral(x * y, x, y))
    assert expl.message.startswith("Only single integrals") and expl.steps == []


# -- derivatives ----------------------------------------------------------------

@pytest.mark.parametrize("g, rule", [
    (x**2 * sin(x), "Product rule"),
    (sin(x) / (x + 1), "Quotient rule"),
    (sin(x**2), "Chain rule"),
    (x**5, "Power rule"),
    (exp(3 * x) + 5, "Sum rule"),
    (3 * cos(x), "Constant multiple rule"),
    (2**x, "Exponential rule"),
    (x**x, "Logarithmic differentiation"),
    (log(x), "Known derivative of log"),
])
def test_each_derivative_rule_is_named_and_the_result_is_right(g, rule):
    expl = explain(Derivative(g, x))
    assert expl.task == "differentiate" and expl.message is None
    assert any(t.startswith(rule) for t in texts(expl)), texts(expl)
    assert not expl.result.has(Derivative) and _same(expl.result, diff(g, x))
    for st in expl.steps:                                    # every step is a value of the derivative
        assert _same(st.expr.doit(), diff(g, x))


def test_one_rule_per_step():
    expl = explain(Derivative(x**2 * sin(x), x))
    assert texts(expl)[:3] == ["Product rule: (fg)′ = f′g + fg′", "Known derivative of sin",
                               "Power rule: (xⁿ)′ = n·xⁿ⁻¹"]
    assert expl.steps[0].expr == x**2 * Derivative(sin(x), x) + sin(x) * Derivative(x**2, x)


def test_higher_derivatives_differentiate_again():
    expl = explain(Derivative(sin(x), (x, 2)))
    assert "Differentiate again, with respect to x" in texts(expl)
    assert expl.steps[0].expr == Derivative(cos(x), x)        # still the second derivative's value
    assert expl.result == -sin(x)


def test_an_undefined_function_keeps_its_derivative():
    expl = explain(Derivative(x * f(x), x))
    assert expl.result == x * Derivative(f(x), x) + f(x)


# -- equations ------------------------------------------------------------------

def test_a_linear_equation():
    expl = explain(Eq(2 * x + 3, 7))
    assert expl.task == "solve"
    assert [st.expr for st in expl.steps] == [Eq(2 * x, 4), Eq(x, 2)]
    assert texts(expl) == ["Collect the terms with x on the left and the rest on the right", "Divide both sides by 2"]


def test_a_linear_equation_with_brackets_is_expanded_first():
    expl = explain(Eq(x * (x + 2) - x**2, 4))
    assert texts(expl)[0] == "Expand both sides"
    assert expl.result == Eq(x, 2)


def test_a_quadratic_equation_by_the_formula():
    expl = explain(Eq(x**2 - 5 * x + 6, 0))
    t = texts(expl)
    assert t[:3] == ["Read off the coefficients of a·x² + b·x + c = 0",
                     "The discriminant Δ = b² − 4ac", "The quadratic formula"]
    assert [st.applicable for st in expl.steps] == [False, False, False, True]
    assert "\\Delta = b^{2} - 4ac = 1" in expl.steps[1].latex()
    assert expl.result == Or(Eq(x, 2), Eq(x, 3))


def test_a_quadratic_without_real_solutions_says_so():
    expl = explain(Eq(x**2 + 2 * x + 5, 0))
    assert "no real solution" in expl.steps[-1].text
    assert expl.result == Or(Eq(x, -1 + 2 * I), Eq(x, -1 - 2 * I))


def test_quadratics_with_a_missing_coefficient():
    assert texts(explain(Eq(x**2 + 3 * x, 0)))[0] == "Factor out x"
    assert explain(Eq(x**2 + 3 * x, 0)).result == Or(Eq(x, 0), Eq(x, -3))
    expl = explain(Eq(2 * x**2, 8))
    assert texts(expl)[0] == "Isolate x²" and expl.result == Or(Eq(x, 2), Eq(x, -2))


def test_what_is_not_solved_is_said_in_words():
    assert "degree 3" in explain(Eq(x**3, 1)).message
    assert "denominator" in explain(Eq(1 / x, 2)).message
    several = explain(Eq(x * y, 2))
    assert "choose the variable" in several.message and several.vars == [x, y]
    assert explain(Eq(x * y, 2), var="y").result == Eq(y, 2 / x)
    assert "inequalities" in explain(x < 2).message


def test_a_plain_expression_offers_what_can_be_done():
    expl = explain(x**2 + y)
    assert expl.steps == [] and expl.offers == ["differentiate", "integrate", "solve"]
    assert explain(x**2 - 4, "solve").result == Or(Eq(x, -2), Eq(x, 2))
    assert explain(x**2 * y, "differentiate", "y").result == x**2
    assert _same(diff(explain(x * cos(x), "integrate").result, x), x * cos(x))


# -- through the document -------------------------------------------------------

def _steps(doc, **payload):
    res = doc.handle(dict({"action": "addon", "addon": "steps", "method": "steps"}, **payload))["query"]
    assert "error" not in res, res
    json.dumps(res)                                           # it travels as JSON
    return res["result"]


def test_the_query_names_the_steps_and_changes_nothing():
    doc = Document(1 + Integral(x * sin(x), x), addons=[ADDON])
    path = next(p for p, n in doc.snapshot()["nodes"].items() if n["type"] == "Integral")
    res = _steps(doc, path=path)
    assert res["task"] == "integrate" and res["src"] == "Integral(x*sin(x), x)"
    assert res["start"].startswith("\\int")
    assert res["steps"][0]["text"].startswith("Integration by parts") and res["steps"][0]["applicable"]
    assert not doc.can_undo


def test_applying_a_step_is_a_step_of_the_history():
    doc = Document(1 + Integral(x * sin(x), x), addons=[ADDON])
    path = next(p for p, n in doc.snapshot()["nodes"].items() if n["type"] == "Integral")
    res = _steps(doc, path=path)
    last = max(i for i, st in enumerate(res["steps"]) if st["applicable"])
    doc.handle({"action": "addon", "addon": "steps", "method": "apply", "path": path, "index": last,
                "src": res["src"], "label": res["steps"][last]["text"]})
    assert doc.expr == 1 - x * cos(x) + sin(x)
    assert doc.history_labels()["actions"][-1] == "Steps: Known integral: ∫ cos x dx = sin x"
    again = load_session(doc, doc.export())                  # a session with it opens again
    assert again.expr == doc.expr
    doc.undo()
    assert doc.expr == 1 + Integral(x * sin(x), x)


def test_applying_an_intermediate_step_keeps_the_integrals_to_do():
    doc = Document(Integral(sin(x) ** 3, x), addons=[ADDON])
    res = _steps(doc, path="/")
    i = [st["text"] for st in res["steps"]].index("Substitution")
    doc.handle({"action": "addon", "addon": "steps", "method": "apply", "path": "/", "index": i, "src": res["src"],
                "label": res["steps"][i]["text"]})
    u = symbols("u")
    assert doc.expr == Integral(u**2 - 1, u)
    assert doc.history_labels()["actions"][-1] == "Steps: Substitution"


def test_solving_an_expression_it_is_the_whole_of():
    doc = Document(x**2 - 4, addons=[ADDON])
    res = _steps(doc, path="/", task="solve")
    assert res["src"] == "x**2 - 4" and res["steps"][-1]["applicable"]
    doc.handle({"action": "addon", "addon": "steps", "method": "apply", "path": "/", "task": "solve",
                "index": len(res["steps"]) - 1, "src": res["src"], "label": res["steps"][-1]["text"]})
    assert doc.expr == Or(Eq(x, -2), Eq(x, 2)) and doc.can_undo


def test_solving_a_piece_of_a_sum_shows_the_steps_but_puts_no_equation_in_the_sum():
    doc = Document(x**2 - 4 + y, addons=[ADDON])
    kids = [i for i, a in enumerate(doc.expr.args) if a != y]       # the range x**2 - 4
    res = _steps(doc, path="/", children=kids, task="solve", var="x")
    assert res["src"] == "x**2 - 4" and res["steps"]
    assert not any(st["applicable"] for st in res["steps"])          # an equation inside a sum is nonsense
    refused = doc.handle({"action": "addon", "addon": "steps", "method": "apply", "path": "/", "children": kids,
                          "task": "solve", "var": "x", "index": len(res["steps"]) - 1, "src": res["src"]})
    assert "error" in refused["query"] and not doc.can_undo


def test_apply_refuses_a_changed_target_and_a_remark():
    doc = Document(Eq(x**2 - 5 * x + 6, 0), addons=[ADDON])
    res = _steps(doc, path="/")
    stale = doc.handle({"action": "addon", "addon": "steps", "method": "apply", "path": "/", "index": 3, "src": "x"})
    assert "has changed" in stale["query"]["error"]
    remark = doc.handle({"action": "addon", "addon": "steps", "method": "apply", "path": "/", "index": 0,
                         "src": res["src"]})
    assert "nothing to apply" in remark["query"]["error"]
    assert doc.expr == Eq(x**2 - 5 * x + 6, 0) and not doc.can_undo
    bad = doc.handle({"action": "addon", "addon": "steps", "method": "steps", "path": "/", "task": "nonsense"})
    assert "No task" in bad["query"]["error"]


def test_the_package_travels_to_a_pyodide_page():
    sources = ADDON.python_sources()
    assert {"__init__.py", "engine.py", "static/steps.js", "static/steps.css"} <= set(sources)
    assert "registerAddon(\"steps\"" in ADDON.js and ADDON.requires == ()
    # nothing but SymPy, the standard library and the editor's add-on contract
    import ast
    for name in ("__init__.py", "engine.py"):
        for node in ast.walk(ast.parse(sources[name])):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module.split(".")[0] in {"__future__", "sympy", "sympy_editor", "dataclasses", "re",
                                                     "typing", "pathlib"}, node.module
            if isinstance(node, ast.Import):
                assert all(a.name.split(".")[0] in {"re", "dataclasses"} for a in node.names)


def test_without_rules_the_result_of_integrate_is_given_without_steps():
    expl = explain(Integral(asin(x) ** 2, x))
    assert "no steps" in expl.message
    assert [st.text for st in expl.steps] == ["SymPy's integrate (no steps to show)"]
    assert _same(diff(expl.result, x), asin(x) ** 2)

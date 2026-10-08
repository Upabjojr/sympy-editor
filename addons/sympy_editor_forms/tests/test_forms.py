import sys
import time
from pathlib import Path

import pytest
from sympy import Add, Integer, Matrix, Symbol, cos, exp, log, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sympy_editor_forms as forms  # noqa: E402
from sympy_editor import Document  # noqa: E402
from sympy_editor_forms import ADDON, TimedOut, form_key, jobs_for, run_job, time_box  # noqa: E402

x, y = symbols("x y")
EXPR = (x**2 - 1) / (x - 1) + sin(x)**2 + cos(x)**2


def call(doc, method, **payload):
    snap = doc.handle(dict(payload, action="addon", addon="forms", method=method))
    q = snap.get("query")
    if q is None:
        return snap
    if q.get("error"):
        raise ValueError(q["error"])
    return q["result"]


def test_plan_lists_every_function_and_the_variables():
    doc = Document(EXPR + y, addons=[ADDON])
    res = call(doc, "plan", path="/")
    ids = [j["id"] for j in res["jobs"]]
    for name in ("simplify", "expand", "factor", "cancel", "together", "trigsimp", "expand_trig", "fu", "powsimp",
                 "powdenest", "radsimp", "ratsimp", "logcombine", "expand_log", "combsimp", "gammasimp", "nsimplify"):
        assert name in ids
    assert {"apart:x", "apart:y", "collect:x", "collect:y", "rewrite:exp", "rewrite:sin"} <= set(ids)
    assert res["current"]["src"] == str(EXPR + y) and res["current"]["ops"] > 0
    assert res["current"]["key"] == form_key(EXPR + y)
    assert not doc.can_undo                                    # a query changes nothing


def test_run_answers_a_form_unchanged_or_not_applicable():
    doc = Document(EXPR, addons=[ADDON])
    res = call(doc, "run", path="/", job="simplify")
    assert res["status"] == "ok" and res["src"] == "x + 2" and res["ops"] == 1 and res["length"] == 5
    assert res["latex"] == "x + 2" and res["key"] == form_key(x + 2) and res["label"] == "simplify"
    assert call(doc, "run", path="/", job="expand_trig")["status"] == "same"
    bad = call(doc, "run", path="/", job="apart:x")            # cos(x) is no polynomial generator
    assert bad["status"] == "error" and "PolynomialError" in bad["error"]
    assert call(doc, "run", path="/", job="nonsense")["status"] == "error"
    # equal forms have equal keys, which is how the panel groups them
    assert call(doc, "run", path="/", job="trigsimp")["key"] == res["key"]
    assert call(doc, "run", path="/", job="cancel")["key"] == call(doc, "run", path="/", job="ratsimp")["key"]
    assert not doc.can_undo


def test_a_selection_and_a_range_are_rewritten_alone():
    doc = Document(log(x * y) + exp(x) * exp(y), addons=[ADDON])
    tree = doc.snapshot()["nodes"]
    path = next(p for p, n in tree.items() if n["src"] == "log(x*y)")
    assert call(doc, "run", path=path, job="expand_log")["status"] == "same"          # not without force
    res = call(doc, "run", path=path, job="expand_log", force=True)
    assert res["src"] == "log(x) + log(y)" and res["label"] == "expand_log(force)"
    call(doc, "apply", path=path, job="expand_log", force=True, key=res["key"])
    assert doc.expr == log(x) + log(y) + exp(x) * exp(y)
    assert doc.history_labels()["actions"][-1] == "Forms: expand_log(force)"
    doc.undo()
    assert doc.expr == log(x * y) + exp(x) * exp(y)
    # a range: two terms of a sum
    a, b, c = symbols("a b c")
    doc = Document(a * b + a * c + sin(x), addons=[ADDON])
    root = doc.snapshot()["nodes"]["/"]
    assert root["type"] == "Add"
    idx = [i for i, t in enumerate(doc.expr.args) if t.has(a)]
    res = call(doc, "run", path="/", children=idx, job="factor")
    assert res["src"] == "a*(b + c)"
    call(doc, "apply", path="/", children=idx, job="factor", key=res["key"])
    assert doc.expr == a * (b + c) + sin(x)


def test_apply_computes_again_when_nothing_was_kept_and_refuses_a_stale_key():
    doc = Document(EXPR, addons=[ADDON])
    call(doc, "apply", path="/", job="factor")                 # never run: computed now
    assert doc.expr == x + sin(x)**2 + cos(x)**2 + 1
    with pytest.raises(ValueError, match="another form"):
        call(doc, "apply", path="/", job="trigsimp", key="0" * 16)
    assert doc.expr == x + sin(x)**2 + cos(x)**2 + 1
    with pytest.raises(ValueError, match="as it is"):
        call(doc, "apply", path="/", job="expand_trig")
    with pytest.raises(ValueError, match="no method"):
        call(doc, "nothing")


def test_the_cache_follows_the_expression():
    """A form computed for one expression is never put into another: the
    cache is keyed by the expression, the path and force."""
    doc = Document(EXPR, addons=[ADDON])
    res = call(doc, "run", path="/", job="simplify")
    doc.replace("/", sin(x)**2 + cos(x)**2)                    # another formula
    call(doc, "apply", path="/", job="simplify")              # recomputed for this one
    assert doc.expr == Integer(1)
    assert res["key"] != form_key(Integer(1))


def test_the_time_box_stops_a_function_that_runs_too_long(monkeypatch):
    def forever(e, f):
        def step(n):
            return n + 1
        n = 0
        while True:
            try:
                n = step(n)
            except Exception:               # SymPy catches Exception: the timeout is not one
                pass
    monkeypatch.setattr(forms, "BASE_JOBS", forms.BASE_JOBS + [("slow", "slow", forever)])
    doc = Document(EXPR, addons=[ADDON])
    began = time.monotonic()
    res = call(doc, "run", path="/", job="slow", timeout=0.3)
    assert res["status"] == "timeout" and res["seconds"] == 0.3
    assert time.monotonic() - began < 2
    # and the trace function is gone: code runs at full speed afterwards
    with time_box(5):
        pass
    with pytest.raises(TimedOut):
        with time_box(0.05):
            while True:
                abs(1)
                len("x")
                (lambda: None)()
    with pytest.raises(ValueError, match="too long"):
        call(doc, "apply", path="/", job="slow", timeout=0.2)
    assert doc.expr == EXPR


def test_the_previous_trace_function_is_put_back():
    seen = []

    def tracer(frame, event, arg):
        seen.append(event)
        return None
    sys.settrace(tracer)
    try:
        with time_box(1):
            pass
        assert sys.gettrace() is tracer
    finally:
        sys.settrace(None)


def test_timeouts_are_clamped():
    assert forms._timeout("abc") == forms.DEFAULT_TIMEOUT
    assert forms._timeout(0) == forms.MIN_TIMEOUT
    assert forms._timeout(1e9) == forms.MAX_TIMEOUT
    assert forms._timeout(float("nan")) == forms.DEFAULT_TIMEOUT


def test_per_variable_jobs_and_rewrites():
    assert str(run_job(1 / (x**2 - 1), "apart:x")) == "-1/(2*(x + 1)) + 1/(2*(x - 1))"
    assert run_job(x * y + x, "collect:x") == x * (y + 1)
    assert run_job(sin(x), "rewrite:exp").has(exp)
    with pytest.raises(ValueError):
        run_job(sin(x), "apart:z")
    with pytest.raises(ValueError):
        run_job(sin(x), "rewrite:__import__")
    many = Add(*symbols("a:h"))
    assert sum(j["id"].startswith("apart:") for j in jobs_for(many)) == forms.MAX_VARIABLES


def test_a_matrix_and_an_equation_are_explored_too():
    doc = Document(Matrix([[sin(x)**2 + cos(x)**2, x]]), addons=[ADDON])
    assert call(doc, "run", path="/", job="simplify")["src"] == "Matrix([[1, x]])"
    from sympy import Eq
    doc = Document(Eq(y, (x**2 - 1) / (x - 1)), addons=[ADDON])
    assert call(doc, "run", path="/", job="simplify")["status"] == "ok"


def test_the_addon_is_found_and_ships_its_front_end():
    doc = Document(x, addons=["sympy_editor_forms"])
    assert "forms" in doc.snapshot()["addons"]
    files = ADDON.python_sources()
    assert {"__init__.py", "static/forms.js", "static/forms.css"} <= set(files)
    client = ADDON.client()
    assert 'registerAddon("forms"' in client["js"] and client["options"]["timeout"] == forms.DEFAULT_TIMEOUT
    assert ADDON.describe("run", {}) is None
    assert ADDON.describe("apply", {"job": "apart:x"}) == "Forms: apart(x)"
    assert forms.FormsAddon(timeout=100).timeout == forms.MAX_TIMEOUT


def test_the_bound_symbol_has_no_jobs_of_its_own():
    from sympy import Sum
    k, n = symbols("k n")
    ids = [j["id"] for j in jobs_for(Sum(k, (k, 1, n)))]
    assert "apart:n" in ids and "apart:k" not in ids
    assert all(isinstance(v, Symbol) for v in forms._variables(Sum(k, (k, 1, n))))

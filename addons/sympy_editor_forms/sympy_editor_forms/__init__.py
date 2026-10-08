"""sympy-editor add-on "forms": the selection rewritten every way SymPy knows.

A simplification explorer.  For the selected node (a range, or the whole
expression with nothing selected) the panel runs SymPy's rewriting functions
one after another - ``simplify``, ``expand``, ``factor``, ``apart`` and
``collect`` per variable, ``trigsimp``, ``fu``, ``rewrite(exp)``... - and
shows each *different* form it gets in a card, with its operation count
(``count_ops``) and its length; a card applies its form to the formula, an
undoable step labelled with the function that made it.

Three methods, all through the editor's one add-on message:

* ``plan`` (query): ``{path, children?}`` -> the node as it is (``latex``,
  ``ops``, ``length``, ``key``) and the list of jobs to run on it
  (``[{id, label}]``: ``"apart:x"`` for ``apart(x)``...);
* ``run`` (query): ``{path, children?, job, force, timeout}`` -> one job's
  outcome: ``status`` ``"ok"`` (a new form: ``latex``, ``src``, ``ops``,
  ``length``, ``key``), ``"same"`` (the node unchanged), ``"timeout"`` or
  ``"error"``;
* ``apply`` (change): ``{path, children?, job, force, key}`` -> the form the
  job gave put in place of the node, labelled "Forms: <function>".

Each job runs under a *time box* (:func:`time_box`): SymPy's ``simplify``
can run for minutes, and the editor answers one message at a time, so a
job that hangs would hold every edit behind it.  The box is a trace
function that raises :class:`TimedOut` at the first Python call past the
deadline - it works in any thread and in Pyodide, which has none; work done
inside one C call (a huge integer product) is not interrupted until it
returns.  The panel asks for one job at a time, so the user's own edits get
in between.
"""

from __future__ import annotations

import hashlib
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from sympy import (Basic, Symbol, apart, cancel, collect, combsimp, count_ops, expand, expand_log, expand_trig,
                   factor, gammasimp, latex, logcombine, nsimplify, powdenest, powsimp, radsimp, ratsimp, simplify,
                   srepr, together, trigsimp)
from sympy.simplify.fu import fu

from sympy_editor.addons import Addon

__all__ = ["FormsAddon", "ADDON", "TimedOut", "time_box", "jobs_for", "run_job"]

STATIC = Path(__file__).parent / "static"

#: The seconds a job may take by default, and the bounds of what the panel may ask.
DEFAULT_TIMEOUT = 2.0
MIN_TIMEOUT, MAX_TIMEOUT = 0.1, 30.0
#: ``apart`` and ``collect`` run once per free symbol, for this many at most.
MAX_VARIABLES = 4
#: A form whose text is longer than this is shown by its LaTeX only, and one
#: whose LaTeX is longer still is not typeset at all (a card is not a page).
MAX_LATEX = 6000


class TimedOut(BaseException):
    """A job ran past its time box.  A ``BaseException``, as
    ``KeyboardInterrupt`` is: SymPy catches ``Exception`` here and there to
    try another way, and a timeout caught there would simply carry on."""


@contextmanager
def time_box(seconds: float):
    """Run the body for ``seconds`` at most: past the deadline, the next
    Python function call raises :class:`TimedOut`.  The trace function is
    called on function calls only (it returns None for the lines), and
    looks at the clock once every 64 of them; the thread's previous trace
    function (a debugger, coverage) is put back afterwards."""
    deadline = time.monotonic() + max(0.0, float(seconds))
    count = [0]

    def trace(frame, event, arg):
        count[0] += 1
        if count[0] & 63 == 0 and time.monotonic() > deadline:
            count[0] = 63                       # past it: every call from now on checks
            raise TimedOut()
        return None

    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)


def _variables(node: Basic) -> List[Symbol]:
    """The node's plain symbols, by name, the first :data:`MAX_VARIABLES`."""
    syms = [s for s in getattr(node, "free_symbols", ()) if isinstance(s, Symbol)]
    return sorted(syms, key=lambda s: (str(s), s.sort_key()))[:MAX_VARIABLES]


#: The functions run on every node: (id, label, function of (expr, force)).
#: The ``force`` variants assume what the function would otherwise check
#: (the symbols positive: ``log(x*y) = log(x) + log(y)``).
BASE_JOBS: List[Tuple[str, str, Callable[[Basic, bool], Basic]]] = [
    ("simplify", "simplify", lambda e, f: simplify(e)),
    ("expand", "expand", lambda e, f: expand(e)),
    ("factor", "factor", lambda e, f: factor(e)),
    ("cancel", "cancel", lambda e, f: cancel(e)),
    ("together", "together", lambda e, f: together(e)),
    ("trigsimp", "trigsimp", lambda e, f: trigsimp(e)),
    ("expand_trig", "expand_trig", lambda e, f: expand_trig(e)),
    ("fu", "fu", lambda e, f: fu(e)),
    ("powsimp", "powsimp", lambda e, f: powsimp(e, force=f)),
    ("powdenest", "powdenest", lambda e, f: powdenest(e, force=f)),
    ("radsimp", "radsimp", lambda e, f: radsimp(e)),
    ("ratsimp", "ratsimp", lambda e, f: ratsimp(e)),
    ("logcombine", "logcombine", lambda e, f: logcombine(e, force=f)),
    ("expand_log", "expand_log", lambda e, f: expand_log(e, force=f)),
    ("combsimp", "combsimp", lambda e, f: combsimp(e)),
    ("gammasimp", "gammasimp", lambda e, f: gammasimp(e)),
    ("nsimplify", "nsimplify", lambda e, f: nsimplify(e)),
]
#: Which jobs take ``force``: their label says so when it is on.
FORCED = {"powsimp", "powdenest", "logcombine", "expand_log"}
#: Per variable: ``apart(e, x)``, ``collect(e, x)``.
VARIABLE_JOBS: Dict[str, Callable[[Basic, Symbol], Basic]] = {
    "apart": lambda e, v: apart(e, v),
    "collect": lambda e, v: collect(e, v),
}
#: ``e.rewrite(target)``, kept only where the form changes.
REWRITE_TARGETS = ("exp", "sin", "cos", "tan", "cot", "sinh", "cosh", "tanh", "sqrt", "log", "Pow",
                   "gamma", "factorial", "binomial", "Piecewise", "Heaviside")


def _rewrite_target(name: str):
    import sympy
    return getattr(sympy, name)


def jobs_for(node: Basic) -> List[Dict[str, str]]:
    """The jobs the panel runs on ``node``, in the order it runs them."""
    out = [{"id": jid, "label": label} for jid, label, _ in BASE_JOBS]
    for v in _variables(node):
        for name in VARIABLE_JOBS:
            out.append({"id": f"{name}:{v}", "label": f"{name}({v})"})
    out.extend({"id": f"rewrite:{t}", "label": f"rewrite({t})"} for t in REWRITE_TARGETS)
    return out


def job_label(job: str, force: bool = False) -> str:
    """What the history and the cards call ``job``: ``apart(x)``,
    ``rewrite(exp)``, ``logcombine(force)``."""
    if ":" in job:
        name, arg = job.split(":", 1)
        return f"{name}({arg})"
    return f"{job}(force)" if force and job in FORCED else job


def run_job(node: Basic, job: str, force: bool = False) -> Basic:
    """``job`` applied to ``node`` - without a time box."""
    if ":" in job:
        name, arg = job.split(":", 1)
        if name == "rewrite":
            if arg not in REWRITE_TARGETS:
                raise ValueError(f"No rewrite target {arg!r}")
            return node.rewrite(_rewrite_target(arg))
        if name not in VARIABLE_JOBS:
            raise ValueError(f"No job {job!r}")
        var = next((v for v in _variables(node) if str(v) == arg), None)
        if var is None:
            raise ValueError(f"{arg} is not a variable of {node}")
        return VARIABLE_JOBS[name](node, var)
    for jid, _, func in BASE_JOBS:
        if jid == job:
            return func(node, bool(force))
    raise ValueError(f"No job {job!r}")


def form_key(expr: Basic) -> str:
    """A short name for a form: equal forms - structurally, as SymPy's
    ``==`` compares - have equal keys."""
    return hashlib.sha1(srepr(expr).encode("utf-8")).hexdigest()[:16]


def _ops(expr: Basic) -> Optional[int]:
    try:
        return int(count_ops(expr))
    except Exception:
        return None


def describe_form(expr: Basic, printer_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A form as a card shows it."""
    src = str(expr)
    try:
        tex = latex(expr, **(printer_settings or {}))
    except Exception:
        tex = latex(expr)
    return {"src": src, "latex": tex if len(tex) <= MAX_LATEX else None, "ops": _ops(expr), "length": len(src),
            "key": form_key(expr)}


def _timeout(value) -> float:
    try:
        t = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if t != t:
        return DEFAULT_TIMEOUT
    return min(MAX_TIMEOUT, max(MIN_TIMEOUT, t))


class FormsAddon(Addon):
    name = "forms"
    label = "Forms"
    js = (STATIC / "forms.js").read_text(encoding="utf-8")
    css = (STATIC / "forms.css").read_text(encoding="utf-8")

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = _timeout(timeout)

    def client_options(self) -> Dict[str, Any]:
        return {"timeout": self.timeout}

    # -- the target --------------------------------------------------------------

    @staticmethod
    def _target(doc, payload: Dict[str, Any]) -> Tuple[str, Optional[List[int]], Basic]:
        path = payload.get("path") or "/"
        children = payload.get("children")
        if children is not None:
            children = [int(i) for i in children]
            node = doc._extract_range(doc.expr, doc._path(path), children)
        else:
            node = doc.get(path)
        return path, children, node

    def _cache(self, doc, path, children, force) -> Dict[str, Basic]:
        """The forms computed for this node of this expression, by job: what
        ``apply`` puts in place without computing it again.  Forgotten as
        soon as the panel asks about another node or the expression changes."""
        state = doc.addon_state.setdefault(self.name, {})
        context = (doc.expr, path, tuple(children) if children is not None else None, bool(force))
        if state.get("context") != context:
            state["context"] = context
            state["results"] = {}
        return state["results"]

    # -- the methods ---------------------------------------------------------------

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "plan":
            _, _, node = self._target(doc, payload)
            current = describe_form(node, doc.printer_settings)
            return {"current": current, "jobs": jobs_for(node)}
        if method == "run":
            return self._run(doc, payload)
        if method == "apply":
            return self._apply(doc, payload)
        raise ValueError(f"Forms has no method {method!r}")

    def _run(self, doc, payload: Dict[str, Any]) -> Dict[str, Any]:
        path, children, node = self._target(doc, payload)
        job = str(payload.get("job") or "")
        force = bool(payload.get("force"))
        timeout = _timeout(payload.get("timeout", self.timeout))
        answer: Dict[str, Any] = {"job": job, "label": job_label(job, force)}
        began = time.monotonic()
        try:
            with time_box(timeout):
                result = run_job(node, job, force)
                same = result == node
                form = None if same else describe_form(result, doc.printer_settings)
        except TimedOut:
            answer.update(status="timeout", seconds=timeout)
            return answer
        except Exception as exc:
            answer.update(status="error", error=f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}")
            return answer
        answer["ms"] = int((time.monotonic() - began) * 1000)
        if same:
            answer["status"] = "same"
            return answer
        self._cache(doc, path, children, force)[job] = result
        answer.update(form, status="ok")
        return answer

    def _apply(self, doc, payload: Dict[str, Any]):
        path, children, node = self._target(doc, payload)
        job = str(payload.get("job") or "")
        force = bool(payload.get("force"))
        result = self._cache(doc, path, children, force).get(job)
        if result is None:
            try:
                with time_box(_timeout(payload.get("timeout", self.timeout))):
                    result = run_job(node, job, force)
            except TimedOut:
                raise ValueError(f"{job_label(job, force)} took too long this time") from None
        key = payload.get("key")
        if key and form_key(result) != key:
            raise ValueError(f"{job_label(job, force)} gives another form now: explore again")
        if result == node:
            raise ValueError(f"{job_label(job, force)} leaves it as it is")
        doc.replace(path, result, children=children)
        return None

    def describe(self, method: str, payload: Dict[str, Any]) -> Optional[str]:
        if method == "apply":
            return "Forms: " + job_label(str(payload.get("job") or ""), bool(payload.get("force")))
        return None


ADDON = FormsAddon()

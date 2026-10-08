"""sympy-editor add-on: step-by-step solutions for the selection.

Two methods, both through the editor's one add-on message:

* ``steps`` - a query: ``{"path", "children", "task", "var"}`` answers with
  the steps for the node at ``path`` (a view path, or a range of its
  ``children``): ``task`` "auto" picks by the node (an integral is worked
  out, a derivative too, an equation solved), or "integrate",
  "differentiate", "solve" for any expression.  Each step has its rule in
  words, the expression it leaves as LaTeX and whether it can be applied.
* ``apply`` - a change: the same target and task plus ``index`` and ``src``
  (the target as the panel saw it) put step ``index``'s expression in place
  of the target, one step of the history labelled with the rule.  The steps
  are worked out again rather than sent back as text: nothing typed or
  saved is parsed, and a target that changed meanwhile is refused.

The explanations themselves are :mod:`sympy_editor_steps.engine`: SymPy and
the standard library only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from sympy import Basic, latex
from sympy.logic.boolalg import Boolean

from sympy_editor.addons import Addon

from .engine import TASKS, Explanation, explain

__all__ = ["StepsAddon", "ADDON", "explain", "Explanation"]

STATIC = Path(__file__).parent / "static"

TITLES = {"integrate": "Integrate", "differentiate": "Differentiate", "solve": "Solve", "auto": "Steps"}


def _target(doc, payload: Dict[str, Any]) -> Basic:
    path = payload.get("path") or "/"
    children = payload.get("children")
    if children is not None:
        return doc._extract_range(doc.expr, doc._path(path), children)
    return doc.get(path)


def _task(payload: Dict[str, Any]) -> str:
    task = str(payload.get("task") or "auto")
    if task not in TASKS:
        raise ValueError(f"No task {task!r}: one of {', '.join(TASKS)}")
    return task


def _whole(payload: Dict[str, Any]) -> bool:
    return (payload.get("path") or "/") == "/" and payload.get("children") is None


def _var(payload: Dict[str, Any]) -> Optional[str]:
    var = payload.get("var")
    return str(var) if var not in (None, "") else None


def _applicable(step, node: Basic, whole: bool) -> bool:
    """Whether a step's result may take the target's place: not a remark,
    and not an equation or a set of solutions where the target is a piece of
    something larger (solving ``x**2 - 4`` selected in a sum would put
    ``x = 2 | x = -2`` in the sum)."""
    if not step.applicable:
        return False
    if whole or isinstance(node, Boolean):
        return True
    return not isinstance(step.expr, Boolean)


def as_json(expl: Explanation, node: Basic, whole: bool = True) -> Dict[str, Any]:
    """An explanation as the panel reads it.  ``whole``: the target is the
    whole formula."""
    return {
        "task": expl.task,
        "title": TITLES.get(expl.task, expl.task),
        "src": str(node),
        "start": latex(expl.start) if expl.start is not None else None,
        "var": str(expl.var) if expl.var is not None else None,
        "vars": [str(v) for v in expl.vars],
        "offers": list(expl.offers),
        "message": expl.message,
        "steps": [{"text": st.text, "latex": st.latex(), "detail": st.detail, "applicable": _applicable(st, node, whole)}
                  for st in expl.steps],
    }


class StepsAddon(Addon):
    name = "steps"
    label = "Steps"
    js = (STATIC / "steps.js").read_text(encoding="utf-8")
    css = (STATIC / "steps.css").read_text(encoding="utf-8")

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "steps":
            node = _target(doc, payload)
            return as_json(explain(node, _task(payload), _var(payload)), node, _whole(payload))
        if method == "apply":
            node = _target(doc, payload)
            seen = payload.get("src")
            if seen is not None and str(seen) != str(node):
                raise ValueError("The formula has changed since these steps were worked out: they are shown again")
            expl = explain(node, _task(payload), _var(payload))
            try:
                index = int(payload.get("index"))
            except (TypeError, ValueError):
                raise ValueError(f"No step {payload.get('index')!r}") from None
            if not 0 <= index < len(expl.steps):
                raise ValueError(f"No step {index}: there are {len(expl.steps)}")
            step = expl.steps[index]
            if step.applicable and not _applicable(step, node, _whole(payload)):
                raise ValueError(f"Step {index + 1} is an equation: it can take the place of the whole formula "
                                 "or of an equation, not of a piece of an expression")
            if not step.applicable:
                raise ValueError(f"Step {index + 1} ({step.text}) is a remark, with nothing to apply")
            doc.replace(payload.get("path") or "/", step.expr, children=payload.get("children"))
            return None
        raise ValueError(f"The steps add-on has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]) -> Optional[str]:
        if method == "apply":
            text = str(payload.get("label") or "").strip()
            if len(text) > 60:
                text = text[:59] + "…"
            return "Steps: " + (text or "a step applied")
        return None


ADDON = StepsAddon()

"""sympy-editor add-on: solve the selected equation, inequality or system.

Four methods, all through ``{"action": "addon", "addon": "solver",
"method": ..., "path", "children"}`` (the selection, a range included):

* ``problem`` (a query): how the selection reads - an equation, an
  inequality, an expression meaning ``= 0``, a system - its free symbols and
  the unknowns ticked to begin with;
* ``solve`` (a query): the solutions for the ``unknowns`` ticked over the
  ``domain`` chosen, within ``timeout`` seconds;
* ``check`` (a query): one solution substituted back into every part;
* ``insert`` (a change): one solution, or the solution set, in place of the
  solved selection - a step of the history, undone like any other.

The last solution is kept per document (``doc.addon_state["solver"]``, not
saved with a session): ``check`` and ``insert`` name a solution by its index
in it, so no SymPy text travels back from the page to be read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from sympy import latex

from sympy_editor.addons import Addon

from .solver import (DOMAINS, Problem, TimeUp, check_item, default_unknowns, read_problem, solve_problem,
                     time_boxed)

__all__ = ["SolverAddon", "ADDON", "DEFAULT_TIMEOUT"]

STATIC = Path(__file__).parent / "static"

#: Seconds SymPy is given to solve before the panel says it gave up.
DEFAULT_TIMEOUT = 8.0
#: ... and the most a panel may ask for.
MAX_TIMEOUT = 60.0
#: Seconds for checking one solution (``simplify`` may take its time too).
CHECK_TIMEOUT = 5.0


class SolverAddon(Addon):
    name = "solver"
    label = "Solve"
    js = (STATIC / "solver.js").read_text(encoding="utf-8")
    css = (STATIC / "solver.css").read_text(encoding="utf-8")

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        self.timeout = float(timeout)

    def client_options(self) -> Dict[str, Any]:
        return {"timeout": self.timeout, "domains": [{"key": k, "sign": v[1], "words": v[2]} for k, v in DOMAINS.items()]}

    # -- the selection ----------------------------------------------------------

    @staticmethod
    def _target(doc, payload):
        path = payload.get("path") or "/"
        children = payload.get("children")
        if children is not None:
            children = [int(i) for i in children]
            node = doc._extract_range(doc.expr, doc._path(path), children)
        else:
            node = doc.get(path)
        return path, children, node

    def _problem(self, doc, payload):
        path, children, node = self._target(doc, payload)
        return path, children, read_problem(node)

    @staticmethod
    def _unknowns(problem: Problem, names):
        by_name = {s.name: s for s in problem.symbols}
        missing = [str(n) for n in names if str(n) not in by_name]
        if missing:
            raise ValueError(f"{', '.join(missing)} is not a free symbol of the selection")
        return [s for s in problem.symbols if s.name in {str(n) for n in names}]

    def _timeout(self, payload) -> float:
        try:
            t = float(payload.get("timeout") or self.timeout)
        except (TypeError, ValueError):
            t = self.timeout
        return max(0.1, min(t, MAX_TIMEOUT))

    def _state(self, doc) -> Dict[str, Any]:
        return doc.addon_state.setdefault(self.name, {})

    def _last(self, doc, payload) -> Dict[str, Any]:
        last = self._state(doc).get("last")
        if not last or last["token"] != payload.get("token"):
            raise ValueError("These solutions are not the last ones found: solve again")
        return last

    # -- the methods --------------------------------------------------------------

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "problem":
            try:
                _path, _children, problem = self._problem(doc, payload)
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
            return {"ok": True, "kind": problem.kind, "words": problem.words(), "latex": problem.latex(),
                    "src": str(problem.node), "count": len(problem.parts),
                    "symbols": [{"name": s.name, "latex": latex(s)} for s in problem.symbols],
                    "defaults": [s.name for s in default_unknowns(problem)]}

        if method == "solve":
            path, children, problem = self._problem(doc, payload)
            names = payload.get("unknowns")
            unknowns = self._unknowns(problem, names) if names else default_unknowns(problem)
            domain = str(payload.get("domain") or "complex")
            seconds = self._timeout(payload)
            taken = {s.name for s in doc.expr.free_symbols if hasattr(s, "name")}
            try:
                solution = time_boxed(lambda: solve_problem(problem, unknowns, domain, taken), seconds)
            except TimeUp:
                return {"timed_out": True, "seconds": seconds,
                        "message": f"SymPy had not finished after {seconds:g} s, so the solver gave up. "
                                   "Try fewer unknowns, another domain, or simplify the equation first."}
            except (NotImplementedError, ValueError, TypeError) as exc:
                return {"failed": True, "message": f"SymPy cannot solve this: {exc}"}
            state = self._state(doc)
            token = int(state.get("token", 0)) + 1
            state["token"] = token
            state["last"] = {"token": token, "expr": doc.expr, "path": path, "children": children,
                             "problem": problem, "solution": solution}
            out = solution.as_json()
            out["token"] = token
            return out

        if method == "check":
            last = self._last(doc, payload)
            item = self._item(last, payload)
            seconds = min(CHECK_TIMEOUT, self._timeout(payload))
            try:
                rows = time_boxed(lambda: check_item(last["problem"], item), seconds)
            except TimeUp:
                return {"timed_out": True, "message": f"Checking took longer than {seconds:g} s: gave up."}
            verdicts = {r["verdict"] for r in rows}
            if verdicts == {"holds"}:
                words = "It holds" + (f" {item.check_words}" if item.check_words else "") + "."
            elif "fails" in verdicts:
                words = "It does not hold."
            else:
                words = "SymPy cannot decide: what is left is shown."
            return {"index": item_index(payload), "rows": rows, "words": words}

        if method == "insert":
            last = self._last(doc, payload)
            if doc.expr != last["expr"]:
                raise ValueError("The formula has changed since it was solved: solve again before inserting")
            solution = last["solution"]
            if payload.get("whole"):
                # one solution and nothing else: its equations, which can
                # stand where the equation stood (in an "and", say), as a
                # set cannot
                points = [i for i in solution.items if i.kind == "point"]
                new = points[0].insert if len(points) == 1 == len(solution.items) else solution.whole
            else:
                new = self._item(last, payload).insert
            try:
                doc.replace(last["path"], new, children=last["children"])
            except Exception as exc:
                raise ValueError(f"{new} cannot stand in place of what was solved ({exc}): "
                                 "insert one solution instead, or solve the whole formula") from None
            self._state(doc).pop("last", None)
            return None

        raise ValueError(f"The solver has no method {method!r}")

    @staticmethod
    def _item(last, payload):
        items = last["solution"].items
        i = item_index(payload)
        if not 0 <= i < len(items):
            raise ValueError(f"There is no solution number {i + 1}")
        return items[i]

    def describe(self, method: str, payload: Dict[str, Any]) -> Optional[str]:
        if method != "insert":
            return None
        text = str(payload.get("text") or "")[:60]
        what = "the solution set" if payload.get("whole") else "a solution"
        return f"Solve: {what}" + (f" {text}" if text else "")


def item_index(payload) -> int:
    try:
        return int(payload.get("index"))
    except (TypeError, ValueError):
        raise ValueError("Which solution? (index)") from None


ADDON = SolverAddon()

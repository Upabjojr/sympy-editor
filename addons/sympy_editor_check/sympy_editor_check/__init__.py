"""sympy-editor add-on "check": is each step of the history the same
mathematics as the one before it?

For every pair of consecutive steps the panel shows ✓ (equal), ✗ (not equal,
with the point where the two differ), ? (could not decide, said in words) or
→ (a transformation that is not meant to keep the value - differentiating,
substituting, solving -, named by the history's own label).  The first ✗ is
where the maths went wrong.  See :mod:`sympy_editor_check.compare` for how
two steps are compared, and README.md.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sympy import Basic, Integer, srepr

from sympy_editor.addons import Addon

from .compare import attempt, compare

__all__ = ["CheckAddon", "ADDON", "classify", "compare"]

STATIC = Path(__file__).parent / "static"

#: Labels of built-in transformations (``Transform: <label>``) that are not
#: meant to keep the value: the lower-case start of the label.
TRANSFORM_OPS = (
    "differentiate", "integrate", "solve for", "substitute", "negate", "derive by array", "transpose",
    "adjoint", "inverse", "trace", "determinant", "conjugate", "rank", "contract axes", "diagonal over axes",
    "permute axes", "reshape", "as array", "as matrix",
)

#: SymPy functions and methods (``SymPy: name(...)`` / ``SymPy: .name(...)``)
#: that rewrite without changing the value: those steps are checked.  Any
#: other call (``diff``, ``subs``, ``sin``...) is a transformation.
EQUIVALENT_CALLS = frozenset("""
    simplify expand factor cancel together apart collect trigsimp expand_trig powsimp powdenest expand_log
    logcombine radsimp ratsimp nsimplify doit evalf n N rewrite expand_mul expand_power_base expand_power_exp
    expand_multinomial expand_complex expand_func combsimp gammasimp sqrtdenest factor_terms signsimp
    exptrigsimp hyperexpand besselsimp as_explicit reversed canonical simplify_sides expand_sides
    piecewise_fold refine fu nfloat
""".split())

#: Editor actions (the start of their history label) that change the
#: structure on purpose: never an equality to check.
STRUCTURAL = ("Unwrap ", "Isolate ", "Wrap", "Matrix: ", "Retype ", "Declare ")

SYMBOLS = {"equal": "✓", "different": "✗", "unknown": "?", "transformation": "→", "incomplete": "…",
           "start": "•", "pending": "⋯"}


def classify(label: Optional[str]) -> Optional[str]:
    """``None`` when the step labelled ``label`` should be checked against the
    one before, else the reason it is a deliberate transformation."""
    if not label:
        return None
    text = str(label)
    if text.startswith("Transform: "):
        name = text[len("Transform: "):].replace(" (unevaluated)", "").strip().lower()
        if any(name.startswith(word) for word in TRANSFORM_OPS):
            return text
        return None
    if text.startswith("SymPy: "):
        m = re.match(r"SymPy: \s*\.?\s*([A-Za-z_]\w*)", text)
        if m is None or m.group(1) not in EQUIVALENT_CALLS:
            return text
        return None
    if text.startswith(STRUCTURAL):
        return text
    return None


def _has_placeholder(expr: Basic) -> bool:
    try:
        from sympy_editor.printer import Placeholder
    except Exception:            # pragma: no cover - an editor without templates
        return False
    try:
        return any(isinstance(s, Placeholder) for s in expr.free_symbols)
    except Exception:
        return False


def _short(text: str, n: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _history(doc) -> Tuple[List[Basic], List[Optional[str]], int]:
    """The document's steps, their labels and the current index.  Read from
    the document's own lists (read only: ``history_labels()`` would print
    every step, and ``export()`` write it out, for nothing)."""
    steps = list(getattr(doc, "_history", []) or [])
    labels = list(getattr(doc, "_labels", []) or [])
    labels = [None] * (len(steps) - len(labels)) + labels[-len(steps):] if steps else []
    index = int(getattr(doc, "_index", len(steps) - 1))
    return steps, labels, index


class CheckAddon(Addon):
    name = "check"
    label = "Check my work"
    requires = ()

    js = (STATIC / "check.js").read_text(encoding="utf-8")
    css = (STATIC / "check.css").read_text(encoding="utf-8")

    #: Seconds for one comparison, and for one request (the panel asks again
    #: while steps are left: the editor is never held for long).
    pair_seconds = 2.0
    call_seconds = 1.0
    cache_size = 2000

    def __init__(self) -> None:
        self._cache: "OrderedDict[Tuple[str, str], Dict[str, Any]]" = OrderedDict()

    # -- one pair ----------------------------------------------------------

    def _key(self, a: Basic, b: Basic) -> Optional[Tuple[str, str]]:
        try:
            return srepr(a), srepr(b)
        except Exception:
            return None

    def _cached(self, a: Basic, b: Basic) -> Optional[Dict[str, Any]]:
        key = self._key(a, b)
        if key is None:
            return None
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
        return hit

    def _store(self, a: Basic, b: Basic, verdict: Dict[str, Any]) -> None:
        key = self._key(a, b)
        if key is None:
            return
        self._cache[key] = verdict
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)

    def _without_compare(self, steps: List[Basic], labels: List[Optional[str]], i: int) -> Optional[Dict[str, Any]]:
        """The verdict for step ``i`` when it needs no comparison."""
        if i == 0:
            return {"status": "start", "text": "The first step."}
        prev = steps[i - 1]
        if i == 1 and prev == Integer(0) and labels[i] and str(labels[i]).startswith("Type the whole expression"):
            return {"status": "start", "text": "The expression typed into an empty session."}
        reason = classify(labels[i])
        if reason:
            return {"status": "transformation", "text": f"Transformation: {reason}."}
        if _has_placeholder(prev) or _has_placeholder(steps[i]):
            return {"status": "incomplete", "text": "Being built: a step with empty slots is not compared."}
        return None

    def verdict(self, steps: List[Basic], labels: List[Optional[str]], i: int, compute: bool = True,
                seconds: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """The verdict for step ``i`` against step ``i - 1``: from the cache,
        computed when ``compute``, else None."""
        quick = self._without_compare(steps, labels, i)
        if quick is not None:
            return quick
        a, b = steps[i - 1], steps[i]
        hit = self._cached(a, b)
        if hit is not None or not compute:
            return hit
        limit = seconds or self.pair_seconds
        res, why = attempt(lambda: compare(a, b, limit), limit * 1.5)
        if res is None:
            res = {"status": "unknown", "how": "none",
                   "text": "Could not decide: " + ("the comparison ran out of time." if why == "time"
                                                   else "SymPy raised an error comparing the steps.")}
        self._store(a, b, res)
        return res

    # -- the add-on --------------------------------------------------------

    def check(self, doc, budget: Optional[float] = None) -> Dict[str, Any]:
        steps, labels, index = _history(doc)
        end = time.monotonic() + (self.call_seconds if budget is None else float(budget))
        rows = []
        pending = False
        for i, expr in enumerate(steps):
            v = self.verdict(steps, labels, i, compute=time.monotonic() < end)
            if v is None:
                pending = True
                v = {"status": "pending", "text": "Not checked yet."}
            row = {"index": i, "label": labels[i] or ("start" if i == 0 else "edit"), "src": _short(_safe_str(expr))}
            row.update(v)
            row["symbol"] = SYMBOLS.get(row["status"], "?")
            rows.append(row)
        first = next((r["index"] for r in rows if r["status"] == "different"), None)
        counts: Dict[str, int] = {}
        for r in rows[1:]:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"steps": rows, "index": index, "first_error": first, "pending": pending, "counts": counts}

    def contribute(self, doc, snap: Dict[str, Any], expr: Basic) -> None:
        steps, _, index = _history(doc)
        snap["check"] = {"n": len(steps), "index": index}

    def contribute_step(self, doc, step: Dict[str, Any], expr: Basic) -> None:
        """The verdict of each step, when it is known already (the history
        view never waits for a comparison)."""
        steps, labels, _ = _history(doc)
        i = next((k for k, e in enumerate(steps) if e is expr), None)
        if i is None:
            return
        v = self.verdict(steps, labels, i, compute=False)
        if v is not None:
            step["check"] = {"status": v["status"], "symbol": SYMBOLS.get(v["status"], "?"), "text": v.get("text", "")}

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "check":
            return self.check(doc, payload.get("budget"))
        if method == "forget":
            self._cache.clear()
            return self.check(doc, payload.get("budget"))
        raise ValueError(f"Check my work has no method {method!r}")


def _safe_str(expr: Basic) -> str:
    try:
        return str(expr)
    except Exception:
        return type(expr).__name__


ADDON = CheckAddon()

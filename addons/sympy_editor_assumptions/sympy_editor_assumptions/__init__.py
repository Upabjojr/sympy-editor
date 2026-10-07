"""sympy-editor add-on: what SymPy knows about the selection.

A query, ``facts``, answers for the node at a view path (or a range of its
arguments) with the main predicates - real, positive, integer, ... - each
True, False or unknown.  The old assumptions (``expr.is_real``) answer
first; where they cannot tell, ``sympy.ask(Q.real(expr))`` is tried on an
expression small enough for it.  An unknown is explained in words, with the
assumption on a symbol that would decide it when there is one.

The symbols of the formula travel in every snapshot (``snap["assumptions"]``)
with what was assumed of each.  The method ``assume`` switches one
assumption of one symbol on, off (assumed false) or back to nothing, through
the document's own :meth:`~sympy_editor.document.Document.retype` - every
occurrence changes, the change is a step of the history, undo gives the old
symbol back.  After the change the add-on compares ``simplify`` before and
after: what became possible is shown as a hint, and the method ``simplify``
applies it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sympy import Basic, Dummy, Expr, Q, Symbol, ask, latex, preorder_traversal, simplify
from sympy.core.facts import InconsistentAssumptions

from sympy_editor.addons import Addon

__all__ = ["AssumptionsAddon", "ADDON", "facts", "PREDICATES", "EDITABLE"]

STATIC = Path(__file__).parent / "static"

#: The predicates of the table, in its order, with how a sentence says them.
PREDICATES: Tuple[Tuple[str, str], ...] = (
    ("real", "real"),
    ("complex", "a complex number"),
    ("imaginary", "purely imaginary"),
    ("positive", "positive"),
    ("negative", "negative"),
    ("nonnegative", "nonnegative"),
    ("zero", "zero"),
    ("nonzero", "nonzero"),
    ("integer", "an integer"),
    ("rational", "rational"),
    ("irrational", "irrational"),
    ("even", "even"),
    ("odd", "odd"),
    ("prime", "a prime"),
    ("finite", "finite"),
    ("infinite", "infinite"),
    ("algebraic", "algebraic"),
    ("transcendental", "transcendental"),
    ("commutative", "commutative"),
)
PHRASE = dict(PREDICATES)

#: The assumptions a symbol's row offers as switches.
EDITABLE: Tuple[str, ...] = ("real", "positive", "negative", "nonnegative", "nonzero",
                             "integer", "rational", "even", "odd", "prime", "finite")

#: ``ask`` is a SAT solver: on a large expression it takes seconds per
#: predicate, and nineteen of them are asked.  Beyond this many nodes only
#: the old assumptions answer.
ASK_MAX_NODES = 40
#: ``simplify`` before and after a switch, for the hint: beyond this many
#: nodes it is not tried (it can take minutes, and Pyodide cannot be
#: interrupted without losing the document's history).
SIMPLIFY_MAX_NODES = 150
#: Symbols tried one by one when looking for the assumption that would
#: decide an unknown.
SUGGEST_MAX_SYMBOLS = 4


def _size(expr: Basic, limit: int) -> int:
    """The number of nodes of ``expr``, counted up to ``limit + 1``."""
    n = 0
    for _ in preorder_traversal(expr):
        n += 1
        if n > limit:
            break
    return n


def _plain_symbols(expr: Basic) -> List[Symbol]:
    """The free symbols a user can assume something about, by name: plain
    ``Symbol``s (no dummies, no empty slots, no matrix symbols)."""
    out = [s for s in getattr(expr, "free_symbols", ()) if isinstance(s, Symbol)
           and not isinstance(s, Dummy) and not type(s).__name__ == "Placeholder"]
    return sorted(out, key=lambda s: (s.name, str(s.assumptions0)))


def given(sym: Symbol) -> Dict[str, bool]:
    """What was assumed of ``sym`` when it was made (not what follows from
    it): ``{"positive": True}`` for ``Symbol("x", positive=True)``."""
    orig = getattr(sym, "_assumptions_orig", None)
    if orig is None:          # an older SymPy: the whole fact base
        orig = sym.assumptions0
    return {str(k): bool(v) for k, v in orig.items() if v is not None
            and not (k == "commutative" and v)}


def _words(names: List[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _describe_given(syms: List[Symbol]) -> str:
    parts = []
    for s in syms:
        g = given(s)
        if g:
            parts.append(s.name + " " + ", ".join(k if v else "not " + k for k, v in sorted(g.items())))
    return "; ".join(parts)


def _old(node: Expr, pred: str) -> Optional[bool]:
    try:
        v = getattr(node, "is_" + pred)
    except Exception:
        return None
    return v if v in (True, False) else None


def _with(sym: Symbol, pred: str, value: bool = True) -> Optional[Symbol]:
    """``sym`` with ``pred`` assumed, or None when that contradicts what it
    has (or ``pred`` is no assumption a symbol can carry)."""
    flags = given(sym)
    flags[pred] = value
    try:
        return Symbol(sym.name, **flags)
    except (InconsistentAssumptions, ValueError, TypeError, KeyError):
        return None


def _would_decide(node: Expr, pred: str, syms: List[Symbol]) -> List[Dict[str, Any]]:
    """Which assumption ``pred`` on which of ``syms`` would decide
    ``node.is_<pred>``: each symbol alone first, then all of them."""
    def decided(group: List[Symbol]) -> Optional[bool]:
        rep = {}
        for s in group:
            new = _with(s, pred)
            if new is None:
                return None
            rep[s] = new
        try:
            return _old(node.xreplace(rep), pred)
        except Exception:
            return None

    out: List[Dict[str, Any]] = []
    for s in syms[:SUGGEST_MAX_SYMBOLS]:
        v = decided([s])
        if v is not None:
            out.append({"names": [s.name], "assumption": pred, "value": v})
    if not out and len(syms) > 1:
        # all the symbols together, only when none alone is enough
        v = decided(list(syms))
        if v is not None:
            out.append({"names": [s.name for s in syms], "assumption": pred, "value": v})
    return out[:2]


def _why(node: Expr, src: str, pred: str, syms: List[Symbol], would: List[Dict[str, Any]]) -> str:
    """An unknown, said in words."""
    head = f"SymPy cannot tell whether {src} is {PHRASE[pred]}"
    if not syms:
        text = (head + ": its rules do not decide it for this expression, and there is no symbol "
                "in it to assume anything about.")
    else:
        names = [s.name for s in syms]
        assumed = _describe_given(syms)
        what = f"what is assumed ({assumed})" if assumed else "nothing is assumed about " + ("it" if len(names) == 1 else "them")
        text = (f"{head}: that depends on the value of {_words(names)}, and "
                + (f"{what} does not decide it." if assumed else f"{what}."))
    for w in would:
        text += (f" It would be {w['value']} if {_words(w['names'])} "
                 f"{'were' if len(w['names']) > 1 else 'was'} assumed {w['assumption']}.")
    return text


def facts(node: Basic, use_ask: bool = True) -> Dict[str, Any]:
    """The table for ``node``: ``{"applicable", "rows": [{"name", "value",
    "source", "why", "would"}], "symbols": [...]}``."""
    src = str(node)
    short = src if len(src) <= 60 else src[:57] + "..."
    out: Dict[str, Any] = {"src": src}
    try:
        out["latex"] = latex(node)
    except Exception:
        out["latex"] = None
    if not isinstance(node, Expr) or getattr(node, "is_Matrix", False) or getattr(node, "is_MatrixExpr", False) \
            or not getattr(node, "is_scalar", True):
        out.update(applicable=False, rows=[],
                   reason=f"{short} is a {type(node).__name__}, not a number: the predicates speak of numbers. "
                          "Select a part of it that is one.")
        return out
    syms = _plain_symbols(node)
    small = use_ask and _size(node, ASK_MAX_NODES) <= ASK_MAX_NODES
    rows = []
    for pred, _phrase in PREDICATES:
        value = _old(node, pred)
        source = "assumptions" if value is not None else None
        if value is None and small:
            try:
                res = ask(getattr(Q, pred)(node))
            except Exception:
                res = None
            if res in (True, False):
                value, source = bool(res), "ask"
        row: Dict[str, Any] = {"name": pred, "value": value, "source": source}
        if value is None:
            would = _would_decide(node, pred, syms)
            row["would"] = would
            row["why"] = _why(node, short, pred, syms, would)
        rows.append(row)
    out.update(applicable=True, rows=rows, symbols=[s.name for s in syms], asked=small)
    return out


def symbol_rows(expr: Basic) -> List[Dict[str, Any]]:
    """The free symbols of ``expr`` with what was assumed of each (``given``)
    and what follows (``known``: the editable predicates SymPy decides)."""
    syms = _plain_symbols(expr)
    counts: Dict[str, int] = {}
    for s in syms:
        counts[s.name] = counts.get(s.name, 0) + 1
    out = []
    for s in syms:
        known = {}
        for pred in EDITABLE:
            v = _old(s, pred)
            if v is not None:
                known[pred] = v
        row: Dict[str, Any] = {"name": s.name, "given": given(s), "known": known}
        if counts[s.name] > 1:
            row["clash"] = True
        out.append(row)
    return out


class AssumptionsAddon(Addon):
    name = "assumptions"
    label = "Assumptions"
    requires = ()

    js = (STATIC / "assumptions.js").read_text(encoding="utf-8")
    css = (STATIC / "assumptions.css").read_text(encoding="utf-8")

    def client_options(self) -> Dict[str, Any]:
        return {"predicates": [p for p, _ in PREDICATES], "editable": list(EDITABLE)}

    # -- data ---------------------------------------------------------------

    def contribute(self, doc, snap: Dict[str, Any], expr: Basic) -> None:
        data: Dict[str, Any] = {"symbols": symbol_rows(expr)}
        hint = doc.addon_state.get(self.name, {}).get("hint")
        if hint and hint.get("expr") is not None and hint["expr"] == expr:
            data["hint"] = {k: v for k, v in hint.items() if k != "expr"}
        snap["assumptions"] = data

    # -- methods ------------------------------------------------------------

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "facts":
            path = payload.get("path") or "/"
            children = payload.get("children")
            if children is not None:
                node = doc._extract_range(doc.expr, doc._path(path), children)
            else:
                node = doc.get(path)
            return facts(node, use_ask=payload.get("ask", True) is not False)
        if method == "assume":
            return self._assume(doc, payload)
        if method == "simplify":
            state = doc.addon_state.setdefault(self.name, {})
            hint = state.get("hint")
            if not hint or hint.get("expr") != doc.expr or "simplified" not in hint:
                raise ValueError("There is no simplification waiting: switch an assumption first.")
            new = state.pop("simplified_expr")
            state.pop("hint", None)
            return new
        raise ValueError(f"The assumptions add-on has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]):
        if method == "assume":
            name, pred, value = payload.get("name"), payload.get("assumption"), payload.get("value")
            if value is None:
                return f"Assumptions: forget {name} {pred}"
            return f"Assumptions: {name} {'' if value else 'not '}{pred}"
        if method == "simplify":
            return "Assumptions: simplify"
        return None

    def _assume(self, doc, payload: Dict[str, Any]):
        name = str(payload.get("name") or "")
        pred = str(payload.get("assumption") or "")
        value = payload.get("value")
        if pred not in PHRASE:
            raise ValueError(f"{pred!r} is not an assumption the panel knows")
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"An assumption is true, false or none, not {value!r}")
        before = doc.expr
        matches = [s for s in _plain_symbols(before) if s.name == name]
        if not matches:
            raise ValueError(f"No symbol named {name!r} in the formula")
        if len(matches) > 1:
            raise ValueError(f"There are {len(matches)} different symbols named {name} in the formula "
                             "(with different assumptions): give one of them another name first")
        old = matches[0]
        flags = given(old)
        flags.pop(pred, None)
        dropped: List[str] = []
        if value is not None:
            # The new assumption wins: what contradicts it is dropped (x was
            # negative, now it is positive), the rest kept.
            kept = {pred: value}
            for k, v in sorted(flags.items()):
                try:
                    Symbol(name, **dict(kept, **{k: v}))
                    kept[k] = v
                except (InconsistentAssumptions, ValueError, TypeError):
                    dropped.append(k if v else "not " + k)
            flags = kept
        try:
            new_sym = Symbol(name, **flags)
        except (InconsistentAssumptions, ValueError, TypeError) as exc:
            raise ValueError(f"{name} cannot be assumed so: {exc}") from None
        if new_sym == old:
            return None
        simple = _size(before, SIMPLIFY_MAX_NODES) <= SIMPLIFY_MAX_NODES
        try:
            simp_before = simplify(before).xreplace({old: new_sym}) if simple else None
        except Exception:
            simp_before = None
        doc.retype(name, "Symbol", assumptions=flags)
        after = doc.expr
        state = doc.addon_state.setdefault(self.name, {})
        state.pop("hint", None)
        state.pop("simplified_expr", None)
        hint: Dict[str, Any] = {"name": name, "assumption": pred, "value": value, "dropped": dropped}
        if str(after) != str(before):
            # SymPy evaluated on its own: sqrt(x**2) is x for a positive x
            hint.update(rewrote=True, before=str(before), before_latex=latex(before))
        if simple and simp_before is not None:
            try:
                simp_after = simplify(after)
            except Exception:
                simp_after = None
            if simp_after is not None and simp_after != after and simp_after != simp_before:
                hint.update(simplified=str(simp_after), simplified_latex=latex(simp_after),
                            was=str(simp_before))
                state["simplified_expr"] = simp_after
        if hint.get("rewrote") or hint.get("simplified") or dropped:
            hint["expr"] = after
            state["hint"] = hint
        return None


ADDON = AssumptionsAddon()

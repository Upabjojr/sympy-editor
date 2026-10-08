"""sympy-editor add-on: the selection evaluated numerically.

Two queries, neither of which changes the expression:

``{"action": "addon", "addon": "numeric", "method": "evaluate", "path",
"children", "values", "digits", "guess"}`` answers with the value of the
node at ``path`` (a view path, the editor's own; ``children`` for a range)
with every free symbol replaced by its value in ``values`` (by name, each
the text the user typed, read as the document reads what is typed), to
``digits`` significant digits, and with an exact form when SymPy has one
(``guess``: ask ``nsimplify`` when it has not).

``"method": "table"`` gives one row per value of the symbol ``var`` - from
``start`` to ``stop`` by ``step``, or the values listed in ``list`` - the
others fixed by ``values``.  At most :data:`MAX_ROWS` rows, and the rows
stop coming after :data:`TABLE_SECONDS`.

A value that is not a finite number says why: a division by zero, an
argument outside a function's domain, an indeterminate form - found by
evaluating the pieces of the node from the inside out and naming the first
that goes wrong.  The numbers come from SymPy's ``evalf`` (mpmath): no
numpy, nothing compiled, so the same code runs in the kernel, the server,
the apps and Pyodide.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mpmath.libmp import to_str
from sympy import (
    Basic, Derivative, E, Expr, Float, Integral, Limit, N, Product, Rational, S, Subs, Sum, Symbol, Tuple as STuple,
    floor, latex, nsimplify, pi, sympify,
)
from sympy.core.function import AppliedUndef, Function
from sympy.core.relational import Relational
from sympy.logic.boolalg import BooleanAtom
from sympy.matrices import MatrixBase

from sympy_editor.addons import Addon

__all__ = ["NumericAddon", "ADDON", "evaluate_at", "MAX_ROWS", "TABLE_SECONDS"]

STATIC = Path(__file__).parent / "static"

#: The precisions the panel offers (``evalf`` digits); any whole number from
#: 2 to :data:`MAX_DIGITS` is taken from Python.
DIGITS = (15, 30, 50, 100)
MAX_DIGITS = 1000
#: The most rows a table has: more is a longer answer, not a better table.
MAX_ROWS = 500
#: How long a table may take: the rows evaluated by then are the answer.
TABLE_SECONDS = 4.0
#: An exact form longer than this is no help beside the number.
EXACT_CHARS = 80
#: Rows of one table that get the reason they have no value (finding it
#: evaluates every piece of the node).
REASONS_PER_TABLE = 25
#: Pieces of a node looked at to find why it has no value.
WHY_NODES = 400
#: A guessed form with a number this big in it is only the decimal written
#: as a fraction.
GUESS_LIMIT = 10 ** 6
#: Nodes whose arguments include a bound variable: evaluated as a whole.
_BOUND = (Integral, Sum, Product, Derivative, Limit, Subs)


def _digits(value) -> int:
    try:
        n = int(value if value is not None else 15)
    except (TypeError, ValueError):
        raise ValueError(f"The precision must be a whole number of digits, not {value!r}") from None
    return max(2, min(n, MAX_DIGITS))


def number_text(v: Basic, digits: int) -> str:
    """A real number as text: ``digits`` significant digits, trailing zeros
    gone, ``∞`` for an infinity."""
    if v is S.Infinity:
        return "∞"
    if v is S.NegativeInfinity:
        return "-∞"
    if v.is_Integer:
        if abs(int(v)).bit_length() <= 3.33 * digits:
            return str(v)
        v = Float(v, digits)
    if not v.is_Float:
        v = Float(v, digits)
    return to_str(v._mpf_, digits)


def complex_text(re: Basic, im: Basic, digits: int) -> str:
    """``a + b i``, ``b i`` when the real part is zero."""
    if im.is_zero:
        return number_text(re, digits)
    i_part = number_text(abs(im), digits) + " i"
    if re.is_zero:
        return ("-" if im.is_negative else "") + i_part
    return number_text(re, digits) + (" - " if im.is_negative else " + ") + i_part


def _short(v: Basic) -> str:
    """A value in a sentence: exact when short, else 6 digits."""
    s = str(v)
    if len(s) <= 20:
        return s
    try:
        return complex_text(*N(v, 6).as_real_imag(), 6)
    except Exception:
        return s[:20] + "…"


def _numeric(node: Basic, vals: Dict[Symbol, Basic], digits: int) -> Tuple[Optional[Basic], Optional[Exception]]:
    """``node`` at ``vals`` as a number, or the exception that stopped it.

    The exact substitution first, evaluated afterwards: SymPy then knows
    that ``tan(pi/2)`` and ``gamma(-1)`` have no value, where a numeric
    substitution lands next to the pole and answers 1e23, and ``evalf``
    refines the exact result to any precision.  ``evalf(subs=)`` (mpmath
    with the values put in as numbers) when that fails or leaves the
    expression unevaluated."""
    err: Optional[Exception] = None
    try:
        exact = node.subs(vals) if vals else node
        v = exact.evalf(digits, chop=True)
        if not (isinstance(v, Basic) and v.free_symbols & set(vals)):
            return v, None
    except Exception as exc:
        err = exc
    try:
        return node.evalf(digits, subs=vals, chop=True), None
    except Exception as exc:
        return None, err or exc


def _subs(e: Basic, vals: Dict[Symbol, Basic]) -> Basic:
    try:
        return e.subs(vals)
    except Exception:
        return e


def _real_inputs(vals: Dict[Symbol, Basic]) -> bool:
    return all(v.is_extended_real for v in vals.values())


def _at(node: Basic, vals: Dict[Symbol, Basic]) -> str:
    here = [f"{s} = {_short(vals[s])}" for s in sorted(node.free_symbols, key=str) if s in vals]
    return (" at " + ", ".join(here)) if here else ""


def _func_name(node: Basic) -> str:
    if node.is_Pow and node.exp == S.Half:
        return "sqrt"
    return getattr(node.func, "__name__", str(node.func))


def _is_bad(v: Optional[Basic], want_real: bool) -> bool:
    if v is None or not isinstance(v, Expr):
        return v is None
    if v.has(S.ComplexInfinity, S.NaN, S.Infinity, S.NegativeInfinity):
        return True
    if not v.is_number:
        return False                         # a symbolic piece: not what went wrong
    if want_real:
        _, im = v.as_real_imag()
        return not im.is_zero
    return False


def why(node: Basic, vals: Dict[Symbol, Basic]) -> Optional[str]:
    """Why ``node`` has no finite (real, for real inputs) value at ``vals``:
    the innermost piece that goes wrong while its own arguments are fine,
    and what went wrong with it.  None when no piece can be blamed."""
    want_real = _real_inputs(vals)
    budget = [WHY_NODES]
    found: List[Tuple[Basic, Optional[Basic], Optional[Exception]]] = []

    def visit(s: Basic) -> bool:
        if found or budget[0] <= 0 or not isinstance(s, Expr):
            return False
        budget[0] -= 1
        if not isinstance(s, _BOUND):
            for arg in s.args:
                if visit(arg):
                    return True
            if found:
                return True
        if s.is_Atom and not s.free_symbols:
            return s in (S.ComplexInfinity, S.NaN)
        v, exc = _numeric(s, vals, 15)
        if _is_bad(v, want_real):
            found.append((s, v, exc))
            return True
        return False

    visit(node)
    if not found:
        return None
    s, v, exc = found[0]
    at = _at(s, vals)
    name = _func_name(s)
    args = ", ".join(_short(_subs(a, vals)) for a in s.args[:3]) if s.args else ""
    negative_power = s.is_Pow and s.exp.is_extended_negative
    if v is None:
        return f"{s} cannot be evaluated{at} ({type(exc).__name__}: {str(exc).splitlines()[0][:100] if str(exc) else ''})"
    if v.has(S.NaN):
        return f"{s} is an indeterminate form{at} (0/0, ∞ - ∞, 0·∞ or the like): undefined"
    if v.has(S.ComplexInfinity, S.Infinity, S.NegativeInfinity):
        if negative_power or s.is_Mul:
            return f"division by zero: {s}{at}"
        if isinstance(s, Function):
            return f"{s} is infinite{at}: {args} is a singularity of {name}, outside its domain"
        if s.is_Pow:
            return f"{s} is infinite{at}"
        return f"{s} is not finite{at}"
    # a complex value from real inputs
    if s.is_Pow:
        return f"{s} is not real{at}: a negative number to a fractional power ({name})"
    if isinstance(s, Function):
        return f"{s} is not real{at}: {args} is outside the real domain of {name}"
    return f"{s} is not real{at}"


def evaluate_at(node: Basic, vals: Dict[Symbol, Basic], digits: int = 15, reason: bool = True) -> Dict[str, Any]:
    """The value of ``node`` at ``vals`` as the panel shows it: ``kind``
    (real, complex, infinite, undefined, symbolic, boolean, relation,
    matrix), ``text``, and ``reason`` when the value is not a finite number
    (or not a real one, from real inputs)."""
    vals = {k: sympify(v) for k, v in vals.items()}
    if isinstance(node, MatrixBase):
        rows, bad = [], None
        for i in range(node.rows):
            row = []
            for j in range(node.cols):
                r = evaluate_at(node[i, j], vals, digits, reason=reason and bad is None)
                if r["kind"] not in ("real", "complex") and bad is None:
                    bad = r.get("reason") or f"entry ({i}, {j}) is {r['text']}"
                row.append(r["text"])
            rows.append("[" + ", ".join(row) + "]")
        out = {"kind": "matrix", "text": "[" + ", ".join(rows) + "]"}
        if bad:
            out["reason"] = bad
        return out
    if isinstance(node, Relational):
        lhs = evaluate_at(node.lhs, vals, digits, reason)
        rhs = evaluate_at(node.rhs, vals, digits, reason)
        out = {"kind": "relation", "text": f"{lhs['text']} {node.rel_op} {rhs['text']}"}
        if lhs["kind"] in ("real", "complex") and rhs["kind"] in ("real", "complex"):
            try:
                truth = node.subs(vals)
                if isinstance(truth, BooleanAtom):
                    out["truth"] = bool(truth)
            except Exception:
                pass
        why_not = lhs.get("reason") or rhs.get("reason")
        if why_not:
            out["reason"] = why_not
        return out
    if not isinstance(node, Expr):
        try:
            v = node.subs(vals)
        except Exception as exc:
            return {"kind": "undefined", "text": "undefined", "reason": f"{node} cannot be evaluated ({exc})"}
        if isinstance(v, BooleanAtom):
            return {"kind": "boolean", "text": str(bool(v))}
        return {"kind": "symbolic", "text": str(v)[:200], "reason": f"{type(node).__name__} has no numeric value"}

    v, exc = _numeric(node, vals, digits)
    if v is None:
        out = {"kind": "undefined", "text": "undefined"}
        if reason:
            out["reason"] = why(node, vals) or f"{type(exc).__name__}: {str(exc).splitlines()[0][:120] if str(exc) else ''}"
        return out
    if v.has(S.NaN) or v is S.ComplexInfinity or (v.is_number and v.has(S.ComplexInfinity)):
        out = {"kind": "undefined", "text": "undefined" if v.has(S.NaN) else "complex ∞"}
        if reason:
            out["reason"] = why(node, vals) or ("division by zero" if v is S.ComplexInfinity else "undefined")
        return out
    if not v.is_number:
        undef = sorted({str(f.func) for f in v.atoms(AppliedUndef)})
        if undef:
            because = f"{', '.join(undef)} {'is an undefined function' if len(undef) == 1 else 'are undefined functions'}: it has no values"
        elif v.free_symbols:
            because = f"it still depends on {', '.join(sorted(map(str, v.free_symbols)))}"
        else:
            because = "SymPy cannot evaluate it numerically"
        return {"kind": "symbolic", "text": str(v)[:200], "reason": because}
    re, im = v.as_real_imag()
    if re.has(S.NaN) or im.has(S.NaN):
        out = {"kind": "undefined", "text": "undefined"}
        if reason:
            out["reason"] = why(node, vals) or "undefined"
        return out
    if not (re.is_finite and im.is_finite):
        out = {"kind": "infinite", "text": complex_text(re, im, digits) if not im.is_zero else number_text(re, digits)}
        if reason:
            out["reason"] = why(node, vals) or "the value is infinite"
        return out
    out = {"kind": "real" if im.is_zero else "complex", "text": complex_text(re, im, digits)}
    if not im.is_zero and reason and _real_inputs(vals):
        because = why(node, vals)
        if because:
            out["reason"] = because
    return out


def exact_form(node: Basic, vals: Dict[Symbol, Basic], shown: str, digits: int,
               guess: bool) -> Optional[Dict[str, Any]]:
    """An exact form of the value, when SymPy finds a short one: the exact
    substitution itself (``sin(pi/3)`` is ``sqrt(3)/2``), or with ``guess``
    what ``nsimplify`` recognises in the number (checked to the digits
    shown).  None when there is nothing to add to the number."""
    if not isinstance(node, Expr) or isinstance(node, MatrixBase):
        return None
    try:
        e = node.subs(vals)
    except Exception:
        e = None
    if (e is not None and isinstance(e, Expr) and e.is_number and not e.has(Float)
            and not e.has(*_BOUND) and not e.has(S.ComplexInfinity, S.NaN) and not e.atoms(Function)
            and not (e.is_Integer and abs(int(e)).bit_length() > 256)):
        s = str(e)
        if len(s) <= EXACT_CHARS and s != shown:
            return {"src": s, "latex": latex(e), "guessed": False}
        if s == shown:
            return None
    if not guess:
        return None
    try:
        v = N(node, digits, subs=vals)
        if not v.is_number or not v.is_finite:
            return None
        cand = nsimplify(v, [pi, E])
        if cand.has(Float) or len(str(cand)) > EXACT_CHARS:
            return None
        if any(abs(n.p) >= GUESS_LIMIT or n.q >= GUESS_LIMIT for n in cand.atoms(Rational)):
            return None                      # 746824132812427/10**15 is the decimal again, not a form of it
        err = abs(N(cand - v, digits + 5))
        if not err <= Float(10, digits) ** (2 - digits) * max(1, abs(v)):
            return None
    except Exception:
        return None
    s = str(cand)
    return None if s == shown else {"src": s, "latex": latex(cand), "guessed": True}


class NumericAddon(Addon):
    name = "numeric"
    label = "Values"
    js = (STATIC / "numeric.js").read_text(encoding="utf-8")
    css = (STATIC / "numeric.css").read_text(encoding="utf-8")

    def __init__(self, max_rows: int = MAX_ROWS, table_seconds: float = TABLE_SECONDS):
        self.max_rows = int(max_rows)
        self.table_seconds = float(table_seconds)

    def client_options(self) -> Dict[str, Any]:
        return {"digits": list(DIGITS), "maxRows": self.max_rows}

    # ---- reading what the panel sends ----

    def _target(self, doc, payload) -> Basic:
        path = payload.get("path") or "/"
        children = payload.get("children")
        if children is not None:
            return doc._extract_range(doc.expr, doc._path(path), children)
        return doc.get(path)

    @staticmethod
    def _free(node: Basic) -> List[Symbol]:
        """The node's free symbols, by name; refused when a number cannot
        stand for one, or two share a name (the panel knows names)."""
        if getattr(node, "is_Matrix", False) and not isinstance(node, MatrixBase):
            raise ValueError(f"{node} is a matrix expression: it has no numeric value until its entries are explicit")
        free = sorted(node.free_symbols, key=str)
        odd = [str(s) for s in free if not isinstance(s, Symbol)]
        if odd:
            raise ValueError(f"{node} depends on {', '.join(odd)}, which no number can stand for")
        by_name = {str(s): s for s in free}
        if len(by_name) < len(free):
            twice = sorted({str(s) for s in free if by_name[str(s)] != s})
            raise ValueError(f"{node} has two different symbols called {', '.join(twice)} (the same name, other "
                             "assumptions): the panel tells symbols apart by name, so one of them needs another name")
        return free

    @staticmethod
    def read_value(doc, name: str, text) -> Basic:
        """The number typed for ``name``: read as the document reads typed
        text (``pi/3``, ``1e-3``, ``sqrt(2)``, ``2 + I``)."""
        text = str(text).strip()
        if not text:
            raise ValueError(f"{name} has no value")
        try:
            parsed = doc.parse(text)
        except Exception:
            raise ValueError(f"The value of {name} must be a number: {text} cannot be read as one") from None
        if not isinstance(parsed, Expr):
            raise ValueError(f"The value of {name} must be a number: {text} is not one")
        if parsed.free_symbols:
            names = ", ".join(sorted(str(s) for s in parsed.free_symbols))
            raise ValueError(f"The value of {name} must be a number: {text} names {names}")
        if not parsed.is_number or parsed.has(S.NaN, S.ComplexInfinity):
            raise ValueError(f"The value of {name} must be a number: {text} is not one")
        return parsed

    def _values(self, doc, free: List[Symbol], payload, skip=None):
        by_name = {str(s): s for s in free}
        vals, read = {}, {}
        for name, text in (payload.get("values") or {}).items():
            sym = by_name.get(str(name))
            if sym is None or sym == skip or not str(text).strip():
                continue
            vals[sym] = self.read_value(doc, str(name), text)
            read[str(name)] = _short(vals[sym]) if vals[sym].is_Number else complex_text(*N(vals[sym], 15).as_real_imag(), 15)
        needs = [str(s) for s in free if s != skip and s not in vals]
        return vals, read, needs

    # ---- the methods ----

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "evaluate":
            return self.evaluate(doc, payload)
        if method == "table":
            return self.table(doc, payload)
        raise ValueError(f"The values panel has no method {method!r}")

    def evaluate(self, doc, payload: Dict[str, Any]) -> Dict[str, Any]:
        node = self._target(doc, payload)
        free = self._free(node)
        digits = _digits(payload.get("digits"))
        vals, read, needs = self._values(doc, free, payload)
        answer: Dict[str, Any] = {"src": str(node), "free": [str(s) for s in free], "needs": needs,
                                  "values": read, "digits": digits, "result": None, "exact": None}
        if needs:
            return answer                            # the panel asks for the missing values
        began = time.monotonic()
        result = evaluate_at(node, vals, digits)
        answer["result"] = result
        if result["kind"] in ("real", "complex") and time.monotonic() - began < 2.0:
            answer["exact"] = exact_form(node, vals, result["text"], digits, bool(payload.get("guess")))
        return answer

    def _points(self, doc, payload, var: str) -> Tuple[List[Basic], int]:
        """The values the varying symbol takes, at most ``max_rows`` of them,
        and how many were asked for."""
        listed = str(payload.get("list") or "").strip()
        if listed:
            try:
                parsed = doc.parse("(" + listed.strip("[]() ") + ",)")
            except Exception:
                raise ValueError(f"The values of {var} must be numbers separated by commas: {listed} is not") from None
            items = list(parsed) if isinstance(parsed, (STuple, tuple, list)) else [parsed]
            points = []
            for item in items:
                if not isinstance(item, Expr) or item.free_symbols or not item.is_number or item.has(S.NaN, S.ComplexInfinity):
                    raise ValueError(f"The values of {var} must be numbers: {item} is not one")
                points.append(item)
            return points[:self.max_rows], len(points)
        ends = {}
        for key in ("start", "stop", "step"):
            text = str(payload.get(key) if payload.get(key) is not None else "").strip()
            label = {"start": "from", "stop": "to", "step": "step"}[key]
            if not text:
                raise ValueError(f"The table needs a value for {label}")
            try:
                v = self.read_value(doc, label, text)
            except ValueError as exc:
                raise ValueError(str(exc).replace("The value of", "The table's")) from None
            if not (v.is_extended_real and v.is_finite):
                raise ValueError(f"The table's {label} must be a finite real number: {text} is not one")
            ends[key] = v
        start, stop, step = ends["start"], ends["stop"], ends["step"]
        if step.is_zero:
            raise ValueError("The table's step cannot be zero")
        count = int(floor(N((stop - start) / step, 30) + Rational(1, 10 ** 9))) + 1
        if count < 1:
            raise ValueError(f"From {start} to {stop} a step of {step} goes the other way: make it {-step}")
        return [start + k * step for k in range(min(count, self.max_rows))], count

    def table(self, doc, payload: Dict[str, Any]) -> Dict[str, Any]:
        node = self._target(doc, payload)
        free = self._free(node)
        if isinstance(node, MatrixBase):
            raise ValueError(f"{node} is a matrix: a table has one value per row - select one entry")
        digits = _digits(payload.get("digits"))
        by_name = {str(s): s for s in free}
        var_name = str(payload.get("var") or "")
        var = by_name.get(var_name) or (free[0] if free else None)
        vals, read, needs = self._values(doc, free, payload, skip=var)
        answer: Dict[str, Any] = {"src": str(node), "free": [str(s) for s in free], "var": str(var) if var else None,
                                  "needs": needs, "values": read, "digits": digits, "rows": [],
                                  "asked": 0, "capped": False, "stopped": None, "maxRows": self.max_rows}
        if var is None:
            raise ValueError(f"{node} has no free symbol to vary: its value does not change")
        if needs:
            return answer
        points, asked = self._points(doc, payload, str(var))
        answer["asked"] = asked
        answer["capped"] = asked > len(points)
        deadline = time.monotonic() + self.table_seconds
        reasons = REASONS_PER_TABLE
        for p in points:
            if time.monotonic() > deadline:
                answer["stopped"] = "time"
                break
            at = dict(vals)
            at[var] = p
            r = evaluate_at(node, at, digits, reason=reasons > 0)
            if "reason" in r:
                reasons -= 1
            x_text = complex_text(*N(p, 15).as_real_imag(), 15) if not p.is_Integer else str(p)
            if x_text.endswith(".0"):
                x_text = x_text[:-2]               # 1, not 1.0, beside -1 and 0
            row = {"x": x_text, "value": r["text"], "kind": r["kind"]}
            if not p.is_Float and str(p) != x_text and len(str(p)) <= 40:
                row["exact"] = str(p)
            if r.get("reason"):
                row["note"] = r["reason"]
            answer["rows"].append(row)
        return answer


ADDON = NumericAddon()

"""sympy-editor add-on: series expansions of the selection.

The panel asks one query, ``expand`` - ``{"action": "addon", "addon":
"series", "method": "expand", "path", ["children",] "var", "point", "n",
"dir", "kind", ["at"]}`` - and Python answers with the expansion of the node
at ``path`` (a view path, the editor's own; a range with ``children``):

* ``kind="series"``: ``node.series(var, point, n, dir)``.  What SymPy gives
  is named after its exponents - *Taylor* (whole powers from 0 up),
  *Laurent* (negative whole powers too), *Puiseux* (fractional powers),
  *with logarithms* - and the point may be any expression, ``oo`` and
  ``-oo`` included.
* ``kind="asymptotic"``: the same at ``oo`` or ``-oo`` (a finite point is
  taken as ``oo``), in falling powers of the variable.
* ``kind="leading"``: the leading term (``as_leading_term`` after moving the
  point to 0 from the chosen side) and its coefficient and exponent, as
  ``leadterm`` gives them.

The answer carries the expansion with its O term and without it (LaTeX and
text), the coefficients ``a_k`` of ``(x - a)**k`` (of ``x**k`` at infinity),
whether the two directions give different expansions (the panel shows the
direction only then), and - when a sample point ``at`` is given or can be
chosen - the function, the truncated expansion and the difference there.

``insert`` puts the expansion (``with_o``: with or without its O term) in
place of the node, as one undoable step.

Every computation runs under a time limit (:data:`TIME_LIMIT` seconds; a
series can run for ever): in a thread that is stopped when it overruns, and
the answer says SymPy gave up.  Where Python has no threads (Pyodide) the
computation runs as it is, and the editor's Interrupt is the way out.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from sympy import (Add, Basic, Dummy, Expr, Function, Integer, Order, Symbol, default_sort_key, expand, latex,
                   log, nan, oo, zoo)
from sympy.core.function import PoleError

from sympy_editor.addons import Addon

__all__ = ["SeriesAddon", "ADDON", "expand_node", "run_limited", "GaveUp", "TIME_LIMIT", "MAX_ORDER"]

STATIC = Path(__file__).parent / "static"

#: Seconds a computation may take before the add-on gives up on it.
TIME_LIMIT = 8.0
#: The highest order the panel offers (and Python accepts).
MAX_ORDER = 20
#: The most coefficients listed: a Puiseux series with logarithms can have many.
MAX_TERMS = 40

KINDS = ("series", "asymptotic", "leading")
DIRECTIONS = ("+", "-", "both")


class GaveUp(Exception):
    """A computation ran past its time limit and was stopped."""


class _Stop(BaseException):
    """Raised asynchronously in a computation's thread to stop it.  A
    ``BaseException``, so that SymPy's ``except Exception`` cannot swallow it."""


def _stop_thread(thread: threading.Thread) -> None:
    try:
        import ctypes
        ident = thread.ident
        if ident is None:
            return
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), ctypes.py_object(_Stop))
        if res > 1:                                  # more than one thread: take it back
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), None)
    except Exception:                                # no ctypes (Pyodide), another interpreter: leave it
        pass


def run_limited(fn: Callable[[], Any], seconds: Optional[float] = TIME_LIMIT) -> Any:
    """``fn()``, given at most ``seconds``: past that it is stopped and
    :class:`GaveUp` raised.  It runs in a thread of its own, stopped with an
    asynchronous exception (SymPy is pure Python, so the exception arrives at
    its next bytecode).  Without threads - Pyodide - it simply runs."""
    if not seconds or seconds <= 0:
        return fn()
    box: Dict[str, Any] = {}

    def work():
        try:
            box["value"] = fn()
        except _Stop:
            box["stopped"] = True
        except BaseException as exc:                 # handed to the caller
            box["error"] = exc

    thread = threading.Thread(target=work, name="sympy-editor-series", daemon=True)
    try:
        thread.start()
    except Exception:                                # no threads here
        return fn()
    try:
        thread.join(seconds)
    except BaseException:                            # the editor's own Interrupt reached the caller
        _stop_thread(thread)
        raise
    if thread.is_alive():
        _stop_thread(thread)
        raise GaveUp(f"no answer within {seconds:g} s")
    if "error" in box:
        raise box["error"]
    if box.get("stopped"):
        raise GaveUp("stopped")
    return box["value"]


# -- the mathematics -----------------------------------------------------------

def _is_infinite(point: Basic) -> bool:
    return point is oo or point is -oo or point == oo or point == -oo


def _basis(var: Symbol, point: Basic) -> Expr:
    """What the coefficients multiply powers of: ``x - a``, or ``x`` at 0
    and at infinity."""
    if _is_infinite(point) or point == 0:
        return var
    return var - point


def coefficients(plain: Expr, var: Symbol, point: Basic) -> Tuple[List[Tuple[Basic, Basic]], bool]:
    """The terms of a truncated expansion as ``[(k, a_k)]``, in rising ``k``:
    ``plain`` = sum of ``a_k * (x - a)**k`` (``x**k`` at 0 and at infinity).
    The second value says whether some coefficient still depends on the
    variable - a logarithm: SymPy's series of ``log(x)`` keep it whole."""
    t = Dummy("t")
    if _is_infinite(point) or point == 0:
        q, back = plain.subs(var, t), var
    else:
        q, back = plain.subs(var, point + t), var - point
    q = expand(q)
    grouped: Dict[Basic, Basic] = {}
    logs = False
    for term in Add.make_args(q):
        if term == 0:
            continue
        c, e = term.as_coeff_exponent(t)
        if c.has(t):
            logs = True
        grouped[e] = grouped.get(e, Integer(0)) + c
    out = []
    for e in sorted(grouped, key=default_sort_key):
        c = grouped[e].subs(t, back)
        if c != 0:
            out.append((e, c))
    try:
        out.sort(key=lambda kc: float(kc[0]))
    except (TypeError, ValueError):
        pass                                          # a symbolic exponent: the sort key's order
    return out, logs


def kind_label(plain: Expr, var: Symbol, point: Basic) -> str:
    """What sort of expansion SymPy gave, by its exponents."""
    if _is_infinite(point):
        return "asymptotic"
    terms, logs = coefficients(plain, var, point)
    if logs:
        return "with logarithms"
    exps = [e for e, _ in terms]
    if all(getattr(e, "is_Integer", False) for e in exps):
        return "Laurent" if any(e < 0 for e in exps) else "Taylor"
    if all(getattr(e, "is_Rational", False) for e in exps):
        return "Puiseux"
    return "generalized"


def leftovers(result: Expr, var: Symbol) -> List[Basic]:
    """The functions of the variable SymPy left in an expansion as they are -
    ``exp(x)`` in what it gives for ``x + exp(x)`` at infinity: there was no
    expansion in powers it could find for them (an essential singularity).
    Logarithms are not counted: a series may carry them."""
    if result.has(Order):
        result = result.removeO()
    return sorted((f for f in result.atoms(Function) if f.has(var) and not isinstance(f, log)), key=default_sort_key)


def _series(node: Expr, var: Symbol, point: Basic, n: int, direction: str) -> Expr:
    if _is_infinite(point):
        return node.series(var, point, n)
    return node.series(var, point, n, dir=direction)


def _leading(node: Expr, var: Symbol, point: Basic, direction: str) -> Tuple[Expr, Basic, Basic]:
    """The leading term at ``point`` from ``direction``, and its coefficient
    and exponent in the basis (``x - a``, ``a - x`` from below, ``x`` at
    infinity)."""
    t = Dummy("t", positive=True)
    if point == oo:
        sub, back = 1 / t, 1 / var
    elif point == -oo:
        sub, back = -1 / t, -1 / var
    elif direction == "-":
        sub, back = point - t, point - var
    else:
        sub, back = point + t, var - point
    g = node.subs(var, sub)
    lt = g.as_leading_term(t)
    c, e = lt.as_coeff_exponent(t)
    if _is_infinite(point):
        e = -e                                         # (1/x)**k is x**-k
    return lt.subs(t, back), c.subs(t, back), e


def _same(a: Expr, b: Expr) -> bool:
    if a == b:
        return True
    try:
        return a.getO() == b.getO() and expand(a.removeO() - b.removeO()) == 0
    except Exception:
        return False


def _point_words(var: Symbol, point: Basic, direction: str = "+") -> str:
    side = "" if _is_infinite(point) or direction not in ("+", "-") else f" (from {'above' if direction == '+' else 'below'})"
    return f"{var} = {point}{side}" if not _is_infinite(point) else f"{var} → {point}"


def _say(exc: BaseException, node: Expr, var: Symbol, point: Basic, direction: str) -> str:
    """A failure of SymPy's, in words."""
    where = _point_words(var, point, direction)
    reason = str(exc).split("\n")[0].strip()
    if isinstance(exc, GaveUp):
        return (f"SymPy gave up: no expansion of {node} at {where} came within the time limit ({reason}). "
                "Try a lower order, another point, or simplify the expression first.")
    if isinstance(exc, PoleError):
        return (f"SymPy cannot expand {node} at {where}: the point is a singularity of a kind it does not expand "
                f"(an essential singularity, or a branch it cannot follow){': ' + reason if reason else ''}.")
    if isinstance(exc, NotImplementedError):
        return f"SymPy has no expansion of {node} at {where}{': ' + reason if reason else ''}."
    if isinstance(exc, ZeroDivisionError):
        return f"SymPy cannot expand {node} at {where}: it divides by zero there."
    return f"SymPy could not expand {node} at {where}: {type(exc).__name__}{': ' + reason if reason else ''}."


def expand_node(node: Expr, var: Symbol, point: Basic = Integer(0), n: int = 6, direction: str = "+",
                kind: str = "series", limit: Optional[float] = TIME_LIMIT) -> Dict[str, Any]:
    """The expansion of ``node``, as SymPy objects: ``{"expr"`` (with its O
    term, when there is one), ``"plain"`` (without), ``"label"``,
    ``"dir_matters"``, ``"other"`` (the other side's, when it differs and
    both were asked for), ``"note"``, and for the leading term
    ``"coeff"``/``"exponent"``}``.  Raises ValueError with SymPy's failure in
    words."""
    if kind not in KINDS:
        raise ValueError(f"No kind of expansion {kind!r}: one of {', '.join(KINDS)}")
    if direction not in DIRECTIONS:
        raise ValueError(f"No direction {direction!r}: one of {', '.join(DIRECTIONS)}")
    note = None
    if kind == "asymptotic" and not _is_infinite(point):
        note = f"An asymptotic expansion is at infinity: taken at {var} → ∞, not {point}."
        point = oo
    finite = not _is_infinite(point)
    first = "-" if direction == "-" else "+"

    def one(side):
        if kind == "leading":
            return _leading(node, var, point, side)
        return _series(node, var, point, n, side)

    def work():
        main = one(first)
        other = None
        if finite:
            try:
                other = one("+" if first == "-" else "-")
            except Exception as exc:                  # one side expands and the other does not
                other = exc
        return main, other

    try:
        main, other = run_limited(work, limit)
    except (Exception, GaveUp) as exc:
        raise ValueError(_say(exc, node, var, point, first)) from None

    out: Dict[str, Any] = {"point": point, "note": note, "other": None, "dir_matters": False}
    if kind == "leading":
        lt, c, e = main
        out.update(expr=lt, plain=lt, label="leading term", coeff=c, exponent=e)
        if other is not None and not isinstance(other, Exception):
            out["dir_matters"] = not _same(lt, other[0])
            if direction == "both" and out["dir_matters"]:
                out["other"] = other[0]
        elif isinstance(other, Exception):
            out["dir_matters"] = True
        return out
    plain = main.removeO()
    out.update(expr=main, plain=plain, label=kind_label(plain, var, point))
    if isinstance(other, Exception):
        out["dir_matters"] = True
        if direction == "both":
            out["note"] = f"From {'below' if first == '+' else 'above'}: {_say(other, node, var, point, '-' if first == '+' else '+')}"
    elif other is not None:
        out["dir_matters"] = not _same(main, other)
        if direction == "both" and out["dir_matters"]:
            out["other"] = other
    left = leftovers(main, var)
    if left:
        powers = f"{_basis(var, point)}" if finite else f"1/{var}"
        where = _point_words(var, point, first)
        if main == node:
            out["note"] = (f"SymPy gives {node} back as it is: it found no expansion in powers of {powers} at "
                           f"{where} - an essential singularity there (as exp(1/x) has at 0) has none.")
            out["label"] = "no expansion"
        else:
            out["note"] = (f"SymPy left {', '.join(str(f) for f in left)} as it is: no expansion in powers of "
                           f"{powers} at {where} - an essential singularity there has none.")
            out["label"] = "incomplete"
    return out


def ordered(terms: List[Tuple[Basic, Basic]], var: Symbol, point: Basic, printer: Callable[[Basic], str],
            plus: str = " + ", minus: str = " - ") -> str:
    """The truncated expansion written term by term in the order it is read:
    rising powers of ``x - a``, falling powers of ``x`` at infinity (SymPy's
    own order puts the highest power first once the O term is gone)."""
    basis = _basis(var, point)
    seq = list(terms)
    if _is_infinite(point):
        seq.reverse()
    out = ""
    for k, c in seq:
        text = printer(c * basis ** k)
        if not out:
            out = text
        elif text.startswith("-"):
            out += minus + text[1:].lstrip()
        else:
            out += plus + text
    return out or printer(Integer(0))


def _fmt(value) -> str:
    try:
        f = complex(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(f.imag) <= 1e-14 * max(1.0, abs(f.real)):
        return f"{f.real:.10g}"
    return f"{f.real:.8g}{'+' if f.imag >= 0 else '-'}{abs(f.imag):.8g}i"


def default_sample(point: Basic, direction: str = "+") -> Optional[Basic]:
    """A point near the expansion point to measure the truncation at: a tenth
    away (on the chosen side), ``±10`` at infinity; None for a point that is
    not a number."""
    if point == oo:
        return Integer(10)
    if point == -oo:
        return Integer(-10)
    if not point.is_number:
        return None
    step = Integer(1) / 10
    return point - step if direction == "-" else point + step


def truncation_check(node: Expr, result: Dict[str, Any], var: Symbol, at: Basic,
                     limit: Optional[float] = TIME_LIMIT) -> Dict[str, Any]:
    """The function and its truncated expansion at ``at``, and how far apart
    they are - against the size of the O term there."""
    others = sorted(str(s) for s in node.free_symbols if s != var)
    if others:
        raise ValueError(f"The error needs numbers for {', '.join(others)}: only {var} is given one (at {var} = {at}).")
    if at.free_symbols:
        raise ValueError(f"The sample point must be a number, not {at}.")

    def work():
        exact = node.subs(var, at).evalf(15)
        approx = result["plain"].subs(var, at).evalf(15)
        expr = result["expr"]
        order = expr.getO() if hasattr(expr, "getO") else None
        size = abs(order.expr.subs(var, at)).evalf(15) if order is not None else None
        return exact, approx, size

    try:
        exact, approx, size = run_limited(work, limit)
    except GaveUp:
        raise ValueError(f"Evaluating at {var} = {at} took too long: SymPy gave up.") from None
    if not (exact.is_number and approx.is_number) or exact.has(zoo, nan, oo, -oo) or approx.has(zoo, nan, oo, -oo):
        raise ValueError(f"No finite value at {var} = {at}: {exact} against {approx}.")
    err = abs(complex(exact) - complex(approx))
    rel = err / abs(complex(exact)) if complex(exact) != 0 else None
    return {"at": str(at), "at_latex": latex(at), "exact": _fmt(exact), "approx": _fmt(approx),
            "error": float(err), "error_text": f"{err:.3g}", "relative": None if rel is None else float(rel),
            "relative_text": None if rel is None else f"{rel:.3g}",
            "o_size": None if size is None else _fmt(size)}


# -- the add-on ------------------------------------------------------------------

def _clamp_order(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise ValueError(f"The order must be a whole number, not {n!r}") from None
    return max(1, min(n, MAX_ORDER))


class SeriesAddon(Addon):
    name = "series"
    label = "Series"
    js = (STATIC / "series.js").read_text(encoding="utf-8")
    css = (STATIC / "series.css").read_text(encoding="utf-8")

    def __init__(self, time_limit: float = TIME_LIMIT, order: int = 6):
        self.time_limit = time_limit
        self.order = _clamp_order(order)

    def client_options(self) -> Dict[str, Any]:
        return {"order": self.order, "maxOrder": MAX_ORDER, "timeLimit": self.time_limit}

    # -- reading the request ---------------------------------------------------

    def _node(self, doc, payload):
        path = payload.get("path") or "/"
        children = payload.get("children")
        node = doc._extract_range(doc.expr, doc._path(path), children) if children is not None else doc.get(path)
        if getattr(node, "is_Matrix", False) or not isinstance(node, Expr):
            raise ValueError(f"{node} is not an expression with a value: expand a piece of it (an entry, a side)")
        return path, children, node

    def _var(self, node, name):
        free = sorted(node.free_symbols, key=str)
        if not free:
            raise ValueError(f"{node} has no variable to expand in: it is a constant")
        if name:
            for s in free:
                if str(s) == str(name):
                    return s, free
        for s in free:
            if str(s) == "x":
                return s, free
        return free[0], free

    def _point(self, doc, text, var):
        text = str(text if text is not None else "0").strip() or "0"
        text = text.replace("∞", "oo").replace("−", "-")
        try:
            point = doc.parse(text)
        except Exception:
            raise ValueError(f"The point {text!r} cannot be read as an expression") from None
        if not isinstance(point, Expr) or point.has(zoo, nan):
            raise ValueError(f"The point must be a value, an expression, oo or -oo - not {text}")
        if point.has(var):
            raise ValueError(f"The point cannot contain {var}, the variable itself")
        return point

    def _request(self, doc, payload):
        path, children, node = self._node(doc, payload)
        var, free = self._var(node, payload.get("var"))
        point = self._point(doc, payload.get("point"), var)
        n = _clamp_order(payload.get("n") or self.order)
        direction = str(payload.get("dir") or "+")
        if direction == "+-":
            direction = "both"
        kind = str(payload.get("kind") or "series")
        return path, children, node, var, free, point, n, direction, kind

    def _compute(self, doc, payload):
        """The expansion asked for, from the document's cache when the same
        question about the same formula was answered already (the panel asks,
        then Insert asks again)."""
        path, children, node, var, free, point, n, direction, kind = self._request(doc, payload)
        key = (doc.expr, path, tuple(children) if children is not None else None, var, point, n, direction, kind)
        state = doc.addon_state.setdefault(self.name, {})
        cached = state.get("last")
        if cached and cached[0] == key:
            return node, var, free, n, direction, kind, cached[1]
        result = expand_node(node, var, point, n, direction, kind, self.time_limit)
        state["last"] = (key, result)
        return node, var, free, n, direction, kind, result

    # -- methods ---------------------------------------------------------------

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "expand":
            return self._expand(doc, payload)
        if method == "insert":
            path, children, _ = self._node(doc, payload)
            node, var, free, n, direction, kind, result = self._compute(doc, payload)
            value = result["expr"] if payload.get("with_o", True) else result["plain"]
            doc.replace(path, value, children=children)
            return None
        raise ValueError(f"The series panel has no method {method!r}")

    def _expand(self, doc, payload):
        node, var, free, n, direction, kind, result = self._compute(doc, payload)
        point = result["point"]
        terms, logs = coefficients(result["plain"], var, point)
        basis = _basis(var, point)
        answer: Dict[str, Any] = {
            "src": str(node), "var": str(var), "free": [str(s) for s in free],
            "point": str(point), "point_latex": latex(point), "infinite": _is_infinite(point),
            "n": n, "dir": direction, "kind": kind, "label": result["label"],
            "latex": latex(result["expr"]), "text": str(result["expr"]),
            "latex_plain": latex(result["plain"]) if logs else ordered(terms, var, point, latex),
            "text_plain": str(result["plain"]) if logs else ordered(terms, var, point, str),
            "has_o": result["expr"].has(Order), "dir_matters": bool(result["dir_matters"]),
            "note": result["note"], "basis_latex": latex(basis), "logs": logs,
            "terms": [{"k": str(k), "k_latex": latex(k), "coeff": str(c), "coeff_latex": latex(c)}
                      for k, c in terms[:MAX_TERMS]],
            "more_terms": max(0, len(terms) - MAX_TERMS),
            "other": None, "check": None, "check_error": None,
        }
        if result.get("other") is not None:
            other = result["other"]
            answer["other"] = {"latex": latex(other), "text": str(other)}
        if kind == "leading":
            answer["coeff"] = str(result["coeff"])
            answer["coeff_latex"] = latex(result["coeff"])
            answer["exponent"] = str(result["exponent"])
        at_text = payload.get("at")
        at = None
        if at_text is not None and str(at_text).strip():
            try:
                at = doc.parse(str(at_text).strip().replace("∞", "oo").replace("−", "-"))
            except Exception:
                answer["check_error"] = f"The sample point {at_text!r} cannot be read"
        else:
            at = default_sample(point, "-" if direction == "-" else "+")
        if at is not None and answer["check_error"] is None:
            try:
                answer["check"] = truncation_check(node, result, var, at, self.time_limit)
            except ValueError as exc:
                answer["check_error"] = str(exc)
        return answer

    def describe(self, method: str, payload: Dict[str, Any]):
        if method != "insert":
            return None
        kind = payload.get("kind") or "series"
        var = payload.get("var") or "x"
        point = payload.get("point") or "0"
        what = "leading term" if kind == "leading" else f"order {payload.get('n') or self.order}"
        if kind != "leading" and not payload.get("with_o", True):
            what += ", without O"
        return f"Series: {var} → {point} ({what})"


ADDON = SeriesAddon()

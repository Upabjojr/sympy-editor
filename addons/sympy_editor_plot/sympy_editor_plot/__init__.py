"""sympy-editor add-on: the graph of the selection, drawn in the browser.

The Python side samples: ``{"action": "addon", "addon": "plot", "method":
"samples", "path", "var", "span", "n", "values"}`` answers with the points
of the node at ``path`` (a view path, the editor's own) as a function of
``var`` over ``span``, every other free symbol replaced by its value in
``values``.  The browser draws the points - with Plotly.js when its CDN is
reachable, as an SVG polyline otherwise; SymPy's plotting module is not
used.  Nothing here changes the expression: every method is a query.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sympy import Basic, Expr, Symbol, lambdify, preorder_traversal
from sympy.core.function import AppliedUndef
from sympy.core.relational import Relational

from sympy_editor.addons import Addon

__all__ = ["PlotAddon", "ADDON", "sample"]

STATIC = Path(__file__).parent / "static"

#: The most points a curve is sampled at: more would only be a longer answer.
MAX_SAMPLES = 5000
#: What a sample raises when the function cannot be evaluated as numbers at
#: all (``besselj`` unknown to ``math``, ``factorial`` of a float), rather
#: than having no real value at a point.
_CANNOT = (NameError, TypeError, AttributeError)


def _count(n) -> int:
    """How many points: between 2 and :data:`MAX_SAMPLES`."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise ValueError(f"The number of points must be a whole number, not {n!r}") from None
    return max(2, min(n, MAX_SAMPLES))


def _span(span):
    """``span`` as two different finite floats, in order."""
    try:
        a, b = float(span[0]), float(span[1])
    except (TypeError, ValueError, IndexError, KeyError):
        raise ValueError(f"The span must be two numbers, not {span!r}") from None
    if not (math.isfinite(a) and math.isfinite(b)) or a == b:
        raise ValueError(f"The span must be two different finite numbers, not {span!r}")
    if not math.isfinite(b - a):
        # -1e308 to 1e308: the width is no number, and neither were the points
        raise ValueError(f"The span is too wide to sample: {span!r}")
    return (a, b) if a < b else (b, a)


_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def numeric_form(expr: Expr, var: Symbol) -> Tuple[Expr, Symbol]:
    """``expr`` and ``var`` with every name replaced by one made here.

    ``lambdify`` writes Python and runs it, and the names in the expression
    go into that text as they are: those of undefined functions and of
    bound symbols (the index of a sum).  A name is any string - a formula
    opened from a file may hold ``Function("(lambda: ...)()")`` - so none
    reaches the text: an undefined function has no value and is refused,
    every symbol, bound ones included, is replaced by ``v0``, ``v1``..., and
    anything else carrying a name that is not a plain one is refused."""
    if expr.atoms(AppliedUndef):
        raise ValueError("an undefined function has no values")
    renamed = {}
    for i, sym in enumerate(sorted(expr.atoms(Symbol) | {var}, key=lambda s: (str(s), s.class_key(), id(s)))):
        try:
            renamed[sym] = Symbol(f"v{i}", **getattr(sym, "_assumptions_orig", sym.assumptions0))
        except Exception:
            renamed[sym] = Symbol(f"v{i}")
    out = expr.xreplace(renamed)
    for node in preorder_traversal(out):
        name = getattr(node, "name", None)
        if isinstance(name, str) and not _PLAIN_NAME.match(name):
            raise ValueError(f"{type(node).__name__} {name!r} has no values")
        if isinstance(node, Basic) and not _PLAIN_NAME.match(type(node).__name__):
            raise ValueError(f"{type(node).__name__!r} has no values")
    return out, renamed[var]

#: Plotly.js (MIT), pinned; override with ``PlotAddon(plotly_js=...)``.  An
#: offline bundle (mobile/build_www.py) vendors it and its page loads the
#: copy instead (the option ``localAssets``).
PLOTLY_VERSION = "2.35.2"
PLOTLY_JS = f"https://cdn.jsdelivr.net/npm/plotly.js-dist-min@{PLOTLY_VERSION}/plotly.min.js"


def _real(value) -> Optional[float]:
    """A sample as a float, or None where the curve has a gap (a complex
    value, an infinity, an error)."""
    try:
        if isinstance(value, complex):
            if abs(value.imag) > 1e-9 * max(1.0, abs(value.real)):
                return None
            value = value.real
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def sample(expr: Expr, var: Symbol, span=(-6.0, 6.0), n: int = 400) -> List[Optional[float]]:
    """``expr`` at ``n`` points of ``span``: floats, None where it is not a
    real number.  numpy when it is installed (one vectorised call), plain
    ``math`` otherwise (Pyodide pages without numpy, and thin installs)."""
    a, b = _span(span)
    n = _count(n)
    xs = [a + (b - a) * i / (n - 1) for i in range(n)]
    expr, var = numeric_form(expr, var)            # no name of the formula's is written into code
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        try:
            f = lambdify(var, expr, "numpy")
            with np.errstate(all="ignore"):
                ys = np.asarray(f(np.asarray(xs)), dtype=complex) + np.zeros(n, dtype=complex)   # a constant broadcasts
            return [_real(complex(v)) for v in ys]
        except Exception:
            pass                                    # fall back to point by point
    f = lambdify(var, expr, "math")
    out: List[Optional[float]] = []
    first, evaluated = None, False
    for xv in xs:
        try:
            out.append(_real(f(xv)))
            evaluated = True
        except _CANNOT as exc:
            out.append(None)
            first = first or exc
        except Exception:                           # no value there (a domain error): a gap
            out.append(None)
            evaluated = True
    if first is not None and not evaluated:
        # not one point could be evaluated, for want of the function rather
        # than of a real value: a curve of gaps would say nothing
        raise first
    return out


def _value(doc, name, value) -> Tuple[Basic, float]:
    """The value given to the symbol ``name``: as SymPy has it (``pi/2``
    stays exact on its way into the expression) and as a float.  It is read
    as the document reads what is typed, and must be a real number: one
    naming a symbol would leave a curve of gaps, and so would ``I`` or
    ``oo``."""
    text = str(value).strip()
    try:
        parsed = doc.parse(text)
    except Exception:
        raise ValueError(f"The value of {name} must be a number: {text or 'nothing'} cannot be read as one") from None
    if getattr(parsed, "free_symbols", None):
        names = ", ".join(sorted(str(s) for s in parsed.free_symbols))
        raise ValueError(f"The value of {name} must be a number: {text} names {names}")
    try:
        number = float(parsed)
    except Exception:                               # I, a list, a relation
        number = math.nan
    if not math.isfinite(number):
        raise ValueError(f"The value of {name} must be a real number: {text} is not one")
    return parsed, number


class PlotAddon(Addon):
    name = "plot"
    label = "Plot"
    js = (STATIC / "plot.js").read_text(encoding="utf-8")
    css = (STATIC / "plot.css").read_text(encoding="utf-8")

    def __init__(self, plotly_js: str = PLOTLY_JS, samples: int = 400, span=(-6.0, 6.0)):
        self.plotly_js = plotly_js
        self.samples = samples
        self.span = (float(span[0]), float(span[1]))

    def client_options(self) -> Dict[str, Any]:
        return {"plotlyJs": self.plotly_js, "samples": self.samples, "span": list(self.span)}

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method != "samples":
            raise ValueError(f"The plot has no method {method!r}")
        path = payload.get("path") or "/"
        children = payload.get("children")
        node = doc._extract_range(doc.expr, doc._path(path), children) if children is not None else doc.get(path)
        # The node's own free symbols, before any value goes in: the panel
        # keeps a row per symbol besides the axis, whether it has a value yet
        # or not.  Values are matched by name (the sliders know names; the
        # expression may carry assumptions on its symbols).
        free = sorted(node.free_symbols, key=str)
        if getattr(node, "is_Matrix", False):
            # A + A*A.T of a matrix symbol A was sampled with A as the axis,
            # a number in place of the matrix
            raise ValueError(f"{node} is a matrix expression: it has no curve")
        odd = [str(s) for s in free if not isinstance(s, Symbol)]
        if odd:
            raise ValueError(f"{node} depends on {', '.join(odd)}, which no number can stand for: it has no curve")
        by_name = {str(s): s for s in free}
        if len(by_name) < len(free):
            # x and an x that is real, say: two symbols, one name.  The panel
            # knows names - one of the two went on the axis and the other
            # was asked a value for, under a name no row could give it.
            twice = sorted({str(s) for s in free if by_name[str(s)] != s})
            raise ValueError(f"{node} has two different symbols called {', '.join(twice)} - the same name, other "
                             "assumptions: the plot tells symbols apart by name, so one of them needs another name")
        var_name = payload.get("var")
        var = by_name.get(str(var_name)) if var_name else None
        if var is None:
            var = free[0] if free else Symbol("x")
        values, read = {}, {}
        for name, value in (payload.get("values") or {}).items():
            if str(name) in by_name and by_name[str(name)] != var:
                values[by_name[str(name)]], read[str(name)] = _value(doc, name, value)
        others = [str(s) for s in free if s != var and s not in values]
        span = _span(payload.get("span") or self.span)
        n = _count(payload.get("n") or self.samples)
        # "values": the number each value was read as (pi/2 is 1.57...), for
        # the panel to put its slider there
        answer: Dict[str, Any] = {"var": str(var), "free": [str(s) for s in free], "needs": others, "values": read,
                                  "span": [float(span[0]), float(span[1])], "src": str(node), "curves": []}
        if others:
            return answer                            # the panel says which values are missing
        node = node.subs(values)
        sides = [("lhs", node.lhs), ("rhs", node.rhs)] if isinstance(node, Relational) else [("", node)]
        xs = [span[0] + (span[1] - span[0]) * i / (n - 1) for i in range(n)]
        answer["x"] = xs
        for label, side in sides:
            if not isinstance(side, Expr):
                raise ValueError(f"{side} is not something with a value to plot")
            try:
                ys = sample(side, var, span, n)
            except Exception as exc:
                # an unevaluated Integral, a Sum, an undefined function: SymPy
                # cannot turn it into numbers, and says so in printer terms
                reason = str(exc).split("\n")[0][:120]
                raise ValueError(f"{side} cannot be plotted as it stands ({type(exc).__name__}: {reason}); "
                                 "evaluate it first, or select a piece that has a value") from None
            answer["curves"].append({"label": label or str(side), "y": ys})
        return answer


ADDON = PlotAddon()

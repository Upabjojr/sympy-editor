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
from pathlib import Path
from typing import Any, Dict, List, Optional

from sympy import Basic, Expr, Symbol, lambdify
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
    return (a, b) if a < b else (b, a)

#: Plotly.js (MIT), pinned; override with ``PlotAddon(plotly_js=...)`` or
#: a vendored copy for an offline bundle.
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
        by_name = {str(s): s for s in free}
        var_name = payload.get("var")
        var = by_name.get(str(var_name)) if var_name else None
        if var is None:
            var = free[0] if free else Symbol("x")
        values = {}
        for name, value in (payload.get("values") or {}).items():
            if str(name) in by_name and by_name[str(name)] != var:
                # read as the document reads what is typed; a value must be a
                # number - one naming a symbol would leave a curve of gaps
                parsed = doc.parse(str(value))
                if getattr(parsed, "free_symbols", None):
                    names = ", ".join(sorted(str(s) for s in parsed.free_symbols))
                    raise ValueError(f"The value of {name} must be a number: {value} names {names}")
                values[by_name[str(name)]] = parsed
        others = [str(s) for s in free if s != var and s not in values]
        span = _span(payload.get("span") or self.span)
        n = _count(payload.get("n") or self.samples)
        answer: Dict[str, Any] = {"var": str(var), "free": [str(s) for s in free], "needs": others,
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

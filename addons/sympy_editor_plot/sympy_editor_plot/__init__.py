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

from sympy import Basic, Expr, Symbol, lambdify, sympify
from sympy.core.relational import Relational

from sympy_editor.addons import Addon

__all__ = ["PlotAddon", "ADDON", "sample"]

STATIC = Path(__file__).parent / "static"

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
    a, b = float(span[0]), float(span[1])
    n = max(2, min(int(n), 5000))
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
    for xv in xs:
        try:
            out.append(_real(f(xv)))
        except Exception:
            out.append(None)
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
        values = {}
        for name, value in (payload.get("values") or {}).items():
            values[Symbol(str(name))] = sympify(value)
        # Substitute by name: the sliders know names, the expression may
        # carry assumptions on its symbols.
        by_name = {str(s): s for s in node.free_symbols}
        node = node.subs({by_name[str(s)]: v for s, v in values.items() if str(s) in by_name})
        free = sorted(node.free_symbols, key=str)
        var_name = payload.get("var")
        var = next((s for s in free if str(s) == var_name), None) or (free[0] if free else Symbol("x"))
        others = [str(s) for s in free if s != var]
        span = payload.get("span") or self.span
        n = int(payload.get("n") or self.samples)
        answer: Dict[str, Any] = {"var": str(var), "free": [str(s) for s in free], "needs": others,
                                  "span": [float(span[0]), float(span[1])], "src": str(node), "curves": []}
        if others:
            return answer                            # the panel makes sliders and asks again
        sides = [("lhs", node.lhs), ("rhs", node.rhs)] if isinstance(node, Relational) else [("", node)]
        xs = [float(span[0]) + (float(span[1]) - float(span[0])) * i / (n - 1) for i in range(max(2, n))]
        answer["x"] = xs
        for label, side in sides:
            if not isinstance(side, Expr):
                raise ValueError(f"{side} is not something with a value to plot")
            answer["curves"].append({"label": label or str(side), "y": sample(side, var, span, n)})
        return answer


ADDON = PlotAddon()

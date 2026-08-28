"""Generate a self-contained demo page (examples/demo.html) and, optionally,
serve the same expression with the local-server backend.

    python examples/demo.py          # writes demo.html (Pyodide-backed)
    python examples/demo.py --serve  # opens the editor in the browser
"""

import sys
from pathlib import Path

from sympy import Function, Integral, Matrix, Sum, oo, sin, symbols, exp, sqrt, pi

from sympy_editor import save_html, serve

x, y, n = symbols("x y n")
f = Function("f")

expr = Integral(exp(-(x**2) / 2) / sqrt(2 * pi), (x, -oo, y)) + Sum(f(n) / n**2, (n, 1, oo)) - sin(x) / (x + 1)

if "--serve" in sys.argv:
    print("Edited expression:", serve(expr))
else:
    out = save_html(expr, Path(__file__).with_name("demo.html"), title="sympy-editor demo")
    print("Wrote", out)

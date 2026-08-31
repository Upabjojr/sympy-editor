"""Generate examples/demo_history.html: a derivation shown step by step.

The history viewer is not part of the editor - it takes a list of
expressions and what turned each into the next, whoever computed them.  Here
they come from a plain Python script, not from anybody editing anything:

    python examples/demo_history.py
"""

from pathlib import Path

from sympy import Integral, cos, sin, symbols

from sympy_editor import History, save_history_html

x = symbols("x")

# Integrating x*sin(x) by parts, written out the way one would on paper.
parts = History([
    Integral(x * sin(x), x),
    (x * -cos(x) - Integral(-cos(x), x), "by parts: u = x, dv = sin(x) dx, so uv - ∫v du"),
    (-x * cos(x) + Integral(cos(x), x), "the constant comes out of the integral"),
    (-x * cos(x) + sin(x), "∫cos(x) dx = sin(x)"),
], title="∫ x sin(x) dx, by parts")

# Nothing says the steps have to be written by hand: `History.add` takes
# whatever a computation produces, one step at a time.
#
#     steps = History(title="A derivative, then back")
#     steps.add(expr)
#     steps.add(expr.diff(x), "differentiate")
#     steps.add(integrate(expr.diff(x), x), "integrate it again")

out = Path(__file__).with_name("demo_history.html")
save_history_html(parts, out, title=parts.title)
print("Wrote", out)

# sympy-editor-plot

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): the
graph of the expression - or of the selected piece of it - under the formula,
redrawn at every change.

```python
from sympy_editor import edit
w = edit(sin(x) / x, addons=["plot"])
```

Python samples the function (`lambdify`, with numpy when it is installed and
plain `math` otherwise; a value that is not real becomes a gap in the curve),
and the browser draws the samples with [Plotly.js](https://plotly.com/javascript/)
(MIT) loaded from its CDN - a plain SVG polyline when the CDN cannot be
reached, so the offline bundles still show a curve.  SymPy's own plotting
module is not involved.

- The panel plots the selection when *follow the selection* is on (the default),
  the whole expression otherwise.
- The variable on the axis is the first free symbol; pick another in the menu.
- With more than one free symbol nothing is drawn until the others have a
  value: each gets a field and a slider, the values are substituted on the way
  to the plot and the formula stays symbolic.  No value is guessed.
- Zoom or pan in the picture: the *from*/*to* fields take the range on show,
  *shown* reads it out, and the curve is sampled again over that range.
- An equation plots both sides.

See `addons/README.md` in the sympy-editor repository for how add-ons work.

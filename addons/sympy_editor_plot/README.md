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
  to the plot and the formula stays symbolic.  No value is guessed, and a
  value must be a real number, written any way the editor reads: the field's
  text goes to Python as it was typed and is read in the document's names, so
  `pi/2`, `1/3` and `sqrt(2)` work (the slider goes where the number is),
  while `z`, `2e` and `I` are refused by name.  An emptied field is no value.
- Two different symbols of the same name (`x`, and an `x` that is real) are
  refused: the panel tells symbols apart by name.
- At most 5000 points a curve (and at least 2); the range must be two
  different finite numbers.  A function the numeric libraries cannot evaluate
  at all (`besselj`, `zeta`, `factorial`...) is said so, not drawn as gaps.
- Zoom or pan in the picture: the *from*/*to* fields take the visible range,
  and the curve is sampled again over it.
- An equation plots both sides.
- A folded panel samples nothing; it is drawn afresh when opened.  Switched
  off, the add-on takes its picture and Plotly's listeners with it; Plotly
  itself is loaded once a page.

See `addons/README.md` in the sympy-editor repository for how add-ons work.

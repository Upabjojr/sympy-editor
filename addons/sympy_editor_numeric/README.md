# sympy-editor-numeric

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): the
**Values** panel, which evaluates the selection - or the whole formula -
as a number.  Nothing in it changes the formula: both of its methods are
queries.

```python
from sympy_editor import edit
w = edit(a * sin(x) / x, addons=["numeric"])
```

## Value

- Every free symbol gets a field.  A value is a number or anything that is
  one, read as the editor reads typed text: `pi/3`, `1e-3`, `sqrt(2)`,
  `2 + I`.  No value is guessed; until every symbol has one the panel says
  which are missing.  Values are kept by name, so a piece that uses the
  same symbol finds its value again.
- **digits**: 15, 30, 50 or 100 significant digits, computed by SymPy's
  `evalf` to that precision (Python accepts any whole number up to 1000).
  The values are substituted exactly first and evaluated afterwards, so
  `tan(pi/2)` has no value rather than 1e23, and `evalf(subs=)` (mpmath)
  takes over when that fails or leaves something unevaluated.
- An **exact form** beside the number when SymPy has a short one:
  `sin(pi/3)` gives `sqrt(3)/2`, drawn by KaTeX.  With *guess an exact form*
  ticked, `nsimplify` is asked (with π and e) when SymPy has none; the guess
  is checked to the digits shown and marked `≈`, and one that is only the
  decimal written as a fraction is not offered.
- A complex value reads `a + b i`.  From real inputs, the panel also says
  which piece left the real numbers.
- A value that is not a number **says why**: a division by zero (`1/x` at
  0), a singularity or an argument outside a function's domain (`log(0)`,
  `gamma(-1)`, `tan(pi/2)`), an indeterminate form.  The reason names the
  innermost piece that goes wrong while its own arguments are fine - the
  node is evaluated from the inside out (at most 400 pieces) to find it.
- An equation or inequality shows both sides and whether it holds; an
  explicit matrix, each entry.  A matrix *expression*, an undefined
  function, or two symbols sharing a name (`x` and a real `x`) are said so.

## Table

- One symbol varies (**vary**) from **from** to **to** by **step**, or
  over a list (`0, pi/6, pi/4`); the others keep their fields' values.
  The points are computed exactly (`pi/6` stays `pi/6`, shown as a decimal
  with the exact value in the cell's tooltip).
- At most 500 rows (`NumericAddon(max_rows=...)`), and the rows stop
  coming after 4 seconds (`table_seconds=`): the panel says when either
  limit cut the table.
- **Copy CSV** / **Copy TSV** put it on the clipboard - the host app's own
  (`SympyEditorApp.copyText`) when the page runs in one, the browser's
  otherwise, the way the editor's Copy does - and **Save CSV** saves it
  through the editor's file path (`api.saveFile`).  Rows without a value
  carry the reason in a third column.

## Methods

```
{"action": "addon", "addon": "numeric", "method": "evaluate",
 "path", "children"?, "values": {name: text}, "digits", "guess"}
{"action": "addon", "addon": "numeric", "method": "table",
 "path", "children"?, "var", "values", "digits", "start", "stop", "step" | "list"}
```

No dependency beyond SymPy (mpmath is SymPy's); no numpy, nothing compiled,
nothing fetched - the same code runs in the kernel, the server, the apps and
Pyodide.

## Limits

- A single value is not time-boxed: Python cannot stop a computation from
  the inside portably (no signals off the main thread, none in Pyodide), so
  something huge - `factorial(10**7)` - takes as long as it takes; the
  editor's **Interrupt** stops it, as it stops any long computation.  The
  table checks its time between rows.
- Symbols are told apart by name, as in the plot add-on.

## Tests

```sh
PYTHONPATH=src pytest addons/sympy_editor_numeric      # unit tests and the panel in Chromium (Playwright)
```

See `addons/README.md` in the sympy-editor repository for how add-ons work.

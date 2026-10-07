# sympy-editor-series

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): the
series expansion of the selection, in a panel under the formula.  No
dependency beyond SymPy; plain JavaScript, nothing fetched from the network.

```sh
pip install -e addons/sympy_editor_series
```

```python
from sympy_editor import edit
edit(sin(x) / x, addons=["series"])
```

## What the panel does

It follows the selection (the whole formula when nothing is selected, or
when *follow the selection* is off) and shows its expansion:

| control | meaning |
|---|---|
| kind | **Series** (`series`: what SymPy gives is named *Taylor*, *Laurent*, *Puiseux* or *with logarithms* after its powers), **Asymptotic** (at `oo` / `-oo`, falling powers of the variable), **Leading term** (`as_leading_term`, with its coefficient and exponent as `leadterm` gives them) |
| variable | one of the selection's free symbols (`x` when there is one) |
| point | any expression: `0`, `pi/2`, `a`, `oo`, `-oo` |
| order | 1 to 20, a slider and − / + |
| direction | from above, from below, or both - shown only where the two sides differ (`sqrt(x**2)`, `log(x)` at 0) |
| without O | the truncated expansion instead of the one with its O term |
| error at x = | the function and the truncated expansion at a point near the expansion point (a tenth away, or ±10 at infinity, unless one is typed), their difference and the size of the O term there |
| Insert with O / Insert without O | the expansion in place of the selection: one step of the history, which Undo takes back |
| Show terms | the coefficients a<sub>k</sub> of (x − a)<sup>k</sup> (of x<sup>k</sup> at 0 and at infinity) |

## When there is no answer

A series can run for ever.  Each computation gets `TIME_LIMIT` seconds (8;
`SeriesAddon(time_limit=...)`): it runs in a thread of its own, stopped with
an asynchronous exception when it overruns, and the panel says SymPy gave
up.  A singularity SymPy cannot expand (`gamma(x)` at infinity, the leading
term of `exp(x)/x` there) is reported in words, and so is a function it hands
back unexpanded (`exp(1/x)` at 0) or partly expanded (`exp(x)` left inside a
sum at infinity).

## Python

```python
from sympy_editor_series import expand_node
expand_node(sin(x), x, 0, 6)["expr"]                       # x - x**3/6 + x**5/120 + O(x**6)
```

Messages: `{"action": "addon", "addon": "series", "method": "expand", "path",
["children",] "var", "point", "n", "dir", "kind", ["at"]}` (a query) and
`"insert"` with the same fields plus `with_o` (a change).

## Limitations

- Pyodide has no threads, so in a standalone page the time limit does not
  apply; the editor's Interrupt button (which restarts the worker) is the way
  out of a series that does not end.
- A stopped computation is stopped at its next Python bytecode: a long call
  into C (a huge integer multiplication) finishes first.
- The direction is decided by comparing the two sides' expansions, so asking
  for a finite point costs two expansions.
- Matrices are not expanded as a whole: select an entry.

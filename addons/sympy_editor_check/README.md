# sympy-editor-check

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor):
**Check my work**.  It compares every step of the session's history with the
step before it and says whether the two are the same mathematics - and so
where the maths first went wrong.

| mark | meaning |
|---|---|
| ✓ | equal: proven by SymPy, or found equal at random points when SymPy could not prove it (the line says which) |
| ✗ | not equal: a point where the two steps differ, with each step's value there; the first ✗ is marked |
| ? | could not decide, and why (out of time, different kinds of object...) |
| → | a transformation that is not meant to keep the value: Differentiate, Integrate, Substitute, Solve, `diff`/`subs`/`sin`... called from the function box, Unwrap, Isolate, Wrap, matrix rows and columns, retyping a name |
| … | a step with empty slots (a template being filled in): not compared |

Clicking a step (or Enter on it) goes to it - the editor's own `goto`, as
Undo and Redo walk the history.  The history view and its saved report show
the same marks beside each step that has been checked.

```python
from sympy_editor import edit
edit(expr, addons=["check"])
```

No dependency beyond SymPy and the standard library; nothing goes on the
network.  It runs wherever the editor's Python runs: the Jupyter kernel, the
local server, Pyodide in a standalone page, the apps.

## How two steps are compared

`sympy_editor_check.compare.compare(a, b)`, cheapest first:

1. **Identical** - `a == b`.
2. **Expressions, matrices, arrays**: `a - b` evaluated by SymPy is 0; else a
   **numeric spot check** at random points (each symbol gets a value its
   assumptions allow - a positive number for a positive symbol, an integer
   for an integer one, a nonzero real otherwise; a matrix symbol of a known
   shape gets a random integer matrix), evaluated to 30 digits.  A point where
   the two differ is the answer: ✗ with the point and both values.  Otherwise
   `simplify(a - b) == 0` (after `doit()`, so `Integral(x, x)` equals
   `x**2/2`), then `a.equals(b)`.  Equal at the random points but not proven:
   ✓, saying so.  Shapes that differ are ✗.
3. **Equations and inequalities**: the difference of the sides is the same
   (or negated, for `=`), or a constant multiple that is not 0 (positive, for
   an inequality).  In one unknown the **solution sets** are compared
   (`solveset`, over the reals for inequalities and real symbols, the complex
   numbers otherwise); a point in one set and not the other is the
   counterexample (`x² = 4` then `x = 2`: `x = −2` was lost).  Equations in
   several unknowns are solved for an unknown they share and the roots
   compared at random values of the others; inequalities in several unknowns
   are evaluated at random points.  `Or`/`And`/`Not` of relations in one
   unknown are compared as sets too.
4. An equation against an expression, or objects of other kinds that are not
   identical: ?, in words.

Which steps are transformations is read from the history's labels
(`Document.history_labels()["actions"]`): built-in ops named in
`TRANSFORM_OPS`, any SymPy call not in `EQUIVALENT_CALLS` (`simplify`,
`expand`, `factor`, `rewrite`... are checked), and the structural actions
(Unwrap, Isolate, Wrap, Matrix, Retype, Declare).  Steps made from Python
carry no label and are checked.  The first step of a session typed into an
empty one is a start, not an error.

## Never hanging

SymPy can take forever on a simplification.  Every call into it runs under
`attempt(fn, seconds)`: a trace function (`sys.settrace`, "call" events only)
raises `TimedOut` - a `BaseException`, so SymPy's `except Exception` does not
eat it - at the first Python call past the deadline.  It works in the
server's threads and in Pyodide, where neither `signal.alarm` nor an
abandoned thread is available; the editor's own Interrupt still goes through.
One comparison gets about two seconds; one request about one second's worth
of new pairs, and the panel asks again (once the editor is idle) while steps
are left, so a long history never holds the editor.  Verdicts are cached by
the two steps' `srepr`, so each pair is compared once.

## Python

```python
from sympy_editor import Document
from sympy_editor_check import ADDON

doc = Document(expr, addons=[ADDON])
doc.handle({"action": "addon", "addon": "check", "method": "check"})["query"]["result"]
# {"steps": [{"index", "label", "src", "status", "symbol", "text", "point"?, "values"?}...],
#  "index": current step, "first_error": index or None, "pending": bool, "counts": {...}}
```

Methods: `check` (`budget`: seconds for this request) and `forget` (the cache
emptied, then `check`).  Every snapshot carries `snap["check"] = {"n",
"index"}`, so the panel knows when the history moved; each history step gets
`step["check"]` once its verdict is known.

## Limits

- "Equal" from the spot check is evidence, not proof - the line says so.
  Two steps that differ only on a set of measure zero (a removable
  singularity, `(x**2 - 1)/(x - 1)` against `x + 1`) are reported equal.
- Random points are real; a difference only off the real line is found only
  when SymPy proves or disproves it symbolically.
- Matrix expressions with symbolic shapes, sets, and logic that is not
  relations in one unknown are compared only for identity (?).
- The time limit counts Python calls: a single long computation inside a C
  extension (a huge integer factorisation in gmpy) runs to its end.
- The history is read from the document's own lists (`doc._history`,
  `doc._labels`, `doc._index`), read only: there is no public accessor that
  does not render every step.

## Tests

`pytest addons/sympy_editor_check` - unit tests of the comparisons, the
labels and the time limit (`tests/test_check.py`), and the panel in
Chromium (`tests/test_check_browser.py`, Playwright; skipped without it).

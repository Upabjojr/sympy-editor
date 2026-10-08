# sympy-editor-forms

A [sympy-editor](https://github.com/Upabjojr/sympy-editor) add-on: a
**simplification explorer**.  For the selection - a node, a range of terms,
or the whole formula - it runs SymPy's rewriting functions side by side and
shows every *different* form they give, each in a card:

- `simplify`, `expand`, `factor`, `cancel`, `together`, `trigsimp`,
  `expand_trig`, `fu`, `powsimp`, `powdenest`, `radsimp`, `ratsimp`,
  `logcombine`, `expand_log`, `combsimp`, `gammasimp`, `nsimplify`;
- `apart(e, v)` and `collect(e, v)` for each free symbol `v` (four at most);
- `e.rewrite(t)` for `exp`, `sin`, `cos`, `tan`, `cot`, `sinh`, `cosh`,
  `tanh`, `sqrt`, `log`, `Pow`, `gamma`, `factorial`, `binomial`,
  `Piecewise`, `Heaviside`.

A card shows the form (KaTeX), its operation count (`count_ops`, with the
difference from the selection as it is) and its length.  Forms that are
structurally equal share one card that names every function that gave them;
functions that left the selection unchanged are listed under the cards, and
those that do not apply (`apart` of a non-rational function) are folded
away.  The cards are sorted by the fewest operations, the shortest text, or
in the order they were computed.  **Tapping a card** puts its form in place
of the selection: an undoable step of the history labelled *Forms: factor*.

A **force** switch passes `force=True` to `logcombine`, `expand_log`,
`powsimp` and `powdenest` (the symbols assumed positive).

## Never blocking the editor

`simplify` can run for minutes, and the editor answers one message at a
time.  So the panel asks for **one function per request** and each runs
under a **time box** (2 s by default, 0.5-15 s in the panel): a trace
function raises `TimedOut` - a `BaseException`, which SymPy's own
`except Exception` cannot swallow - at the first Python call past the
deadline.  It needs no thread, so it works the same in the kernel, the local
server, the apps and Pyodide.  A function that runs out of time gets a
*timed out* card.  Between two requests the panel waits for the editor to be
idle and a moment more, so an edit the user makes meanwhile goes first; the
exploration then starts again on the new formula.  A folded panel runs
nothing.

Limitation: work done inside a single C call (a huge integer product, a
`gmpy2` factorisation) is not interrupted until that call returns.

## Use

```sh
pip install -e addons/sympy_editor_forms
```

```python
from sympy_editor import edit
edit(expr, addons=["forms"])
```

The methods, through the editor's one add-on message
(`{"action": "addon", "addon": "forms", "method": ...}`):

| method | kind | payload | answer |
|---|---|---|---|
| `plan` | query | `path`, `children?` | `current` (the node: `latex`, `src`, `ops`, `length`, `key`) and `jobs` (`[{id, label}]`) |
| `run` | query | `path`, `children?`, `job`, `force`, `timeout` | `status`: `ok` (with the form), `same`, `timeout`, `error` |
| `apply` | change | `path`, `children?`, `job`, `force`, `key` | the form put in place; refused if it is no longer the form `key` names |

`FormsAddon(timeout=...)` sets the default time box.  No dependency beyond
SymPy and the editor.

## Tests

```sh
PYTHONPATH=src python -m pytest addons/sympy_editor_forms -q
```

`tests/test_forms.py` covers the methods; `tests/test_forms_browser.py`
drives the panel in Chromium (Playwright; skipped without it or the KaTeX
CDN).

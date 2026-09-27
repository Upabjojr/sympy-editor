# sympy-editor-console

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): a
Python console under the formula, running in the same Python as the editor
and able to read and change the formula.

It has two tabs:

- **Console** works like IPython. Type at `In [n]:` and press
  <kbd>Enter</kbd>: the statements run, and the value of the last line is
  shown as `Out[n]`. Mathematics is typeset; anything else is shown as its
  `repr`.
  - An unfinished block (`for i in range(3):`) asks for the next line instead
    of running, and an empty line ends it.
  - <kbd>Shift</kbd>+<kbd>Enter</kbd> starts a new line, and
    <kbd>Ctrl</kbd>+<kbd>Enter</kbd> runs whatever is there.
  - `_`, `__`, `___`, `_n`, `Out` and `In` are there.
  - `obj?` describes an object and `obj??` shows its source.
  - The line magics `%who`, `%whos`, `%time` and `%reset` work.
  - <kbd>Tab</kbd> completes names.
  - <kbd>↑</kbd>/<kbd>↓</kbd> recall earlier inputs, which are kept between
    visits.
  - `display(obj)` shows a value typeset in the middle of the output.
  - **Use** beside an `Out[n]` puts that value in the formula. It goes over
    the selection, or replaces the whole formula when nothing is selected.
- **Script** runs a whole file, typed in the panel or opened with **Open…**,
  the way `python script.py` runs it: in a namespace of its own, with
  `__name__ == "__main__"`. Afterwards, as with IPython's `%run`, what the
  script defined is available in the console.
  - The script is kept between visits.
  - **Save .py** writes it out through the platform's own save or share
    sheet, or as a download in a browser.

## The formula from Python

SymPy is imported (`from sympy import *`). The formula's own symbols are
available under their names, with their assumptions. Where SymPy has a
function of the same name (`beta`, `gamma`), the formula's symbol wins, as it
does in the editor. A name you assign yourself stays yours.

`editor` is the formula:

```python
editor.expr                          # the whole expression
editor.expr = simplify(editor.expr)  # change it
editor.selection                     # what is selected when the code runs (the whole formula if nothing is)
editor.selection = expand(editor.selection)
editor["/1"]                         # the node at a path; editor["/1"] = y replaces it
editor.paths()                       # every path, with what is there
editor.find(cos(x))                  # the paths where cos(x) is drawn
editor.apply("factor", "/0")         # one of the editor's transformations (editor.ops lists them)
editor.call("diff(x)", "/")          # what the function box does
editor.select("/0")                  # select it in the formula after the run
editor.undo(); editor.redo()
editor.doc                           # the sympy_editor.Document, for anything else
```

Every change is a step of the formula's history, labelled
`Console: <the code>` or `Script: <name>`. The editor's Undo takes it back.

## Where it runs

The console runs in whatever Python holds the document:

| where | the Python |
|---|---|
| Android and iOS apps | the app's own interpreter (Chaquopy, python.org's iOS build). No network, no shell |
| `serve()` | the server's process |
| a standalone page (`save_html(..., addons=["console"])`) | Pyodide, in the page |
| the Jupyter widget | the kernel. It works there too, but a notebook already has a better console |

Each document has its own namespace, so each session of the editor has one.
The editor's **Interrupt** button stops a long computation. In a standalone
page that restarts Python, and the variables go with it.

`input()` has no keyboard to wait on and says so. `!commands` have no shell
to run in.

Running code here is running code on the machine that holds the document,
with the same trust as the editor's own input, which is already parsed by
`parse_expr` (see the main README on the server's token).

No dependency beyond sympy-editor: IPython is not needed. The IPython-like
behaviour is built on the standard library (`ast`, `codeop`, `rlcompleter`).

## Methods

| method | payload | answer |
|---|---|---|
| `run` | `code`, `path`, `children`, `interactive` | `{n, items, out?, changed, select?, next, token}`, or `{incomplete: true}` when `interactive` and the block is unfinished |
| `script` | `code`, `name`, `path`, `children` | `{items, changed, select?, next, token}` |
| `complete` | `code`, `pos` | `{start, word, matches}` |
| `use` | `n`, `path`, `children` | a change: `Out[n]` in the formula |
| `reset` | none | `{next, token}` |

`items` is the output, in order: `{kind: "stdout" \| "stderr" \| "error",
text}` and `{kind: "display", text, latex?}`. When a run changed the formula
(`changed`), the panel asks for a fresh snapshot.

## Tests

```sh
PYTHONPATH=src pytest addons/sympy_editor_console                                # unit tests and the panel on the local server
SYMPY_EDITOR_SLOW_TESTS=1 PYTHONPATH=src pytest addons/sympy_editor_console -k standalone   # the panel in Pyodide
```

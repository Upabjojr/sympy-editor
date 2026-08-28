# sympy-editor

A click-to-edit, WYSIWYG-style editor for [SymPy](https://www.sympy.org)
expressions.  Expressions are rendered as LaTeX (with [KaTeX](https://katex.org))
in HTML; every sub-expression is selectable, and a selection can be replaced by
typing new SymPy syntax or by applying SymPy transformations (`expand`,
`factor`, `simplify`, ...).  The SymPy expression tree — not the LaTeX — is
always the source of truth.

Works as a **Jupyter widget** and as **standalone HTML** (self-contained file, or
a local server).  Pure Python + plain JavaScript: no node.js, no bundler, no
GPL dependencies.

## Install

```bash
pip install sympy-editor            # core: SymPy only
pip install "sympy-editor[jupyter]" # adds anywidget for the notebook widget
```

## Usage

### Jupyter (JupyterLab, Notebook 7, VS Code, Colab...)

```python
from sympy import symbols, sin
from sympy_editor import edit

x, y = symbols("x y")
w = edit(x**2 / y - sin(x))
w            # display the widget and edit in place
w.expr       # the current, edited expression (live)
w.on_change(lambda e: print("now:", e))
```

### Standalone HTML file

```python
from sympy_editor import save_html
save_html(expr, "expr.html")   # open in any browser
```

The file is self-contained: it renders immediately and, on the first edit,
loads [Pyodide](https://pyodide.org) + SymPy from a CDN to run the editing
logic inside the browser.  Use `editable=False` for a view-only page (still
selectable).

### Local server (scripts, plain Python sessions)

```python
from sympy_editor import serve
new_expr = serve(expr)   # opens the browser; returns when you press "Done"
```

### Editing

| Action | Mouse | Keyboard |
| --- | --- | --- |
| Select sub-expression | click | ↓ (enter children), ←/→ (siblings) |
| Select enclosing expression | click again on the same spot, or **↑** | ↑ |
| Reduce to the first sub-expression | | ↓ |
| Select a range of adjacent terms / factors | drag across them (mouse, touch or pen) | Shift+→ / Shift+← grow and shrink the range; ←/→/↓ collapse it, ↑ selects the whole sum/product |
| Replace selection by typing | | just start typing (SymPy syntax) |
| Insert a term / factor / argument | click **between** two terms (or on the operator): a caret appears; type the new term, Enter. A leading `+`/`-` always adds a *term* (even with the caret inside a product), a leading `*` a *factor* | Tab / Shift+Tab put the caret after / before the selection; ←/→ move it; Enter opens an empty field; Esc removes it |
| LaTeX shortcuts in the field | | `\theta` becomes `θ` as you type (Greek letters, `\infty`, `\sin`, `\cdot`, `\le`...); Greek letters are SymPy's names (`θ` is `theta`, `λ` is `lamda`, `∞` is `oo`) |
| Edit selection's existing text | double-click / **Edit** | Enter |
| Apply / cancel an edit | click elsewhere applies | Enter / Esc |
| Remove selection from its parent | **Delete** | Del |
| Apply a transformation to the selection | choose in the dropdown, **Apply** | |
| Undo / redo | ↶ / ↷ | Ctrl+Z / Ctrl+Shift+Z |

Editing happens *inside* the formula: the selected node is swapped for a small
text field at its position, and the formula re-renders when you press Enter.
A selection and an insertion caret never coexist: with a selection, typing
replaces it; with a caret, typing only inserts.  A range (`b + c` inside `a + b + c + d`) is
edited, deleted and transformed like a single node: typing replaces it, Del
removes its terms, *Apply* transforms just those terms.

On phones and tablets: tap to select, drag to select a range, use the toolbar
(*Edit*, *Delete*, *Apply*) instead of keys; the field opens the on-screen
keyboard.  Vertical swipes and pinches still scroll and zoom the page.
Transformations act on the selected sub-expression only (on the whole formula
when nothing is selected).

Typed input is parsed with `sympy.parsing.sympy_parser.parse_expr` in the
context of the expression, so existing symbols keep their assumptions and
undefined functions (and `MatrixSymbol`s / `IndexedBase`s) are reused.  Names
that do not occur in the current expression become plain symbols - unless the
node being replaced is a matrix, in which case they become `MatrixSymbol`s of
its shape (so `C.T` typed over `B` in `A*B` works).  Ancestors are rebuilt
with SymPy's normal automatic evaluation (replacing `y` by `-x` in `x + y`
gives `0`).

A denominator raised to a power (`(x+1)**2` in `x/(x+1)**2`) is selectable as
a whole even though the tree holds `Pow(x + 1, -2)`: editing it replaces the
denominator (typing `y**3` gives `x/y**3`).

The **Symbols** panel under the formula lists every name with what it stands
for (`Symbol` with its assumptions, `MatrixSymbol` with its shape, `Function`,
...) and lets you change it throughout the expression: make `y` a 2×2
`MatrixSymbol`, an explicit `Matrix` of `y[i, j]` entries (symbolic dimensions
such as `n` are fine for a `MatrixSymbol`), or a positive real `Symbol`
(assumptions are a comma-separated list).  Products and powers are rebuilt as
`MatMul`/`MatPow`; a change SymPy cannot represent (a matrix under a
transpose back to a scalar) is refused with its error.  The last row of the
panel **declares a new name before you type it** — so `C` typed into a
scalar context can still be a 3×3 matrix symbol — and from Python the same is
`edit(expr, symbols=[MatrixSymbol("C", 3, 3)])` or
`w.document.declare("C", "MatrixSymbol", 3, 3)`.

The transformation dropdown offers what applies to the selection: the general
ops always, and the ops registered for the selection's *kind* - for a matrix
(`MatrixSymbol` algebra or an explicit matrix): transpose, adjoint, inverse,
trace, determinant, `as_explicit`, conjugate - in a group of their own.

Matrices (dense and sparse), `MatrixSymbol` expressions, block matrices,
determinants/traces and N-dimensional `Array`s are supported: every entry is
selectable and editable, and the container is rebuilt around the edit (see
`examples/demo_matrices.py` and `examples/demo_matrices.ipynb`).

Register your own transformations, for every selection or only for some
kinds (`"matrix"`, `"array"`, `"scalar"`; the mapping from kinds to SymPy
types is `sympy_editor.ops.KINDS`):

```python
from sympy_editor import register_op

@register_op("my_op", label="My op")
def my_op(expr):
    return ...

@register_op("gram", label="Gram matrix", kinds=("matrix",))
def gram(m):
    return m.T * m
```

## How it works

`sympy_editor.AnnotatedLatexPrinter` extends SymPy's `LatexPrinter` so that
every printed sub-expression is wrapped in KaTeX's `\htmlData{path=/1/0}{...}`.
KaTeX turns that into `<span data-path="/1/0">`, so the DOM knows which node
of the expression tree each glyph belongs to.  Editing operations
(`Document.replace/delete/apply/undo/redo`) rebuild the tree and re-render.
See `AGENTS.md` for the architecture and design notes.

## Dependencies and licences

| Component | Licence | How it is used |
| --- | --- | --- |
| SymPy | BSD-3 | required |
| anywidget (+ ipywidgets, traitlets) | MIT / BSD-3 | optional, Jupyter widget |
| KaTeX | MIT | loaded from a CDN by the browser (URL configurable) |
| Pyodide | MPL-2.0 | loaded from a CDN by the browser, standalone HTML only |

sympy-editor itself is BSD-3-Clause.

## Development

```bash
pip install -e ".[jupyter,test]"
pytest                           # Python tests (printer, document, HTML, server, widget)
python examples/demo.py          # writes examples/demo.html
python examples/demo.py --serve  # local-server mode
jupyter lab examples/demo.ipynb  # notebook demo
```

Browser end-to-end tests of the JavaScript front end use
[Playwright](https://playwright.dev/python/) (dev-only, Apache-2.0, never
shipped) and a real headless Chromium:

```bash
pip install playwright && python -m playwright install chromium
pytest tests/test_browser.py                       # needs network for the KaTeX CDN
SYMPY_EDITOR_SLOW_TESTS=1 pytest tests/test_browser.py   # also the Pyodide page
```

They are skipped automatically when Playwright, the browser or the network
are unavailable.  `.github/workflows/ci.yml` runs everything on push.

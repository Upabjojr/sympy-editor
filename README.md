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

The widget runs every edit in **the kernel's SymPy** (no Pyodide involved).
`edit(expr, backend="pyodide")` gives instead the self-contained HTML page
that runs its own SymPy in the browser — useful for notebooks exported with
`nbconvert`, but its edits do not reach the kernel; the default `"auto"`
picks the kernel widget when anywidget is installed and falls back to Pyodide
with a warning otherwise.

### Standalone HTML file

```python
from sympy_editor import save_html
save_html(expr, "expr.html")   # open in any browser
```

The file is self-contained: it renders immediately and loads
[Pyodide](https://pyodide.org) + SymPy from a CDN in the background to run
the editing logic inside the browser; a spinner overlay blocks the editor
until they are ready (`options={"preload": False}` defers that to the first
edit).  Use `editable=False` for a view-only page (still
selectable).

### Local server (scripts, plain Python sessions)

```python
from sympy_editor import serve
new_expr = serve(expr)   # opens the browser; returns when you press "Done"
```

### Editing

| Action | Mouse | Keyboard |
| --- | --- | --- |
| Select sub-expression | click its middle (its left/right edge places a caret before/after it instead; next to a matrix entry or a power's base the caret *extends* it: `+ 1` adds, `y` multiplies) | ↓ (enter children), ←/→ (siblings) |
| Previous / next sibling (or move the caret) | **←** / **→** (toolbar and action bar) | ←/→ |
| Select enclosing expression | click again on the same spot, or **↑** | ↑ |
| Go inside: the sub-expression you came up from, or the first one (on an atom: a caret after it) | **↓** (toolbar or action bar) | ↓ |
| Select a range of adjacent terms / factors | drag across them (mouse, touch or pen) | Shift+→ / Shift+← grow and shrink the range; ←/→/↓ collapse it, ↑ selects the whole sum/product |
| Replace selection by typing | | just start typing (SymPy syntax) |
| Type at a caret | click **between** two terms (or on the operator), or at the edge of an object: a caret appears; what you type is spliced between its neighbours like in a text editor: operators you type are used as written, a missing one means juxtaposition (`cos(t)` after `x` gives `x cos(t)`), `+`/`-` bind at the sum level (`x z` with `+y+` typed between gives `x + y + z`), `, …` adds a function argument | Tab / Shift+Tab put the caret after / before the selection; ←/→ move it; ↑ selects the object next to it; Enter opens an empty field; Esc removes it |
| LaTeX shortcuts in the field | | `\theta` becomes `θ` as you type (Greek letters, `\infty`, `\sin`, `\cdot`, `\le`...); Greek letters are SymPy's names (`θ` is `theta`, `λ` is `lamda`, `∞` is `oo`) |
| Edit selection's existing text | double-click / **Edit** | Enter |
| Apply / cancel an edit | click elsewhere applies | Enter / Esc |
| Remove the selection entirely (on the whole expression: the formula is emptied, type the new one in the source line; Esc brings the old one back) | **Delete** | Del |
| Remove the node but keep its argument (`cos(θ)` → `θ`, `√x` → `x`, `∫f dx` → `f`) | **Unwrap** | Backspace — after ↑ from a term, that term is the one kept |
| Keep only the selection (it becomes the whole expression) | **Isolate** | Ctrl+Shift+I |
| Transform the selection | pick an operation in the **Transform ▾** menu (general) or the type menu ("Matrix ▾"...): it applies at once | |
| Copy / cut / paste a part | **Copy** / **Paste** (toolbar or action bar) | Ctrl+C / Ctrl+X copy the selection's SymPy source; Ctrl+V pastes over a selection or at a caret |
| Apply any SymPy function | the **function box** in the toolbar: type to search SymPy's functions, pick one; a function that needs parameters asks for them (symbol parameters offer the selection's free symbols — `solve` on `sin(x)cos(y)` asks x or y); `diff(x)`, `.T`, `det()` typed in full apply as written | |
| Undo / redo | ↶ / ↷ | Ctrl+Z / Ctrl+Shift+Z |
| Zoom the formula | **−** / **100%** (reset) / **+**, Ctrl+mouse wheel, pinch with two fingers | Ctrl+plus / Ctrl+minus / Ctrl+0 |
| Scroll a formula wider than the view | the scrollbar, the mouse wheel over the formula, or drag its empty space (one finger on a phone) | |

A small action bar appears under whatever is selected — ↑ parent, ↓ inside,
Edit, Unwrap, Delete, Copy — so these actions are one click or one tap away
from the object; the same commands sit in the toolbar and on the keys.

Editing happens *inside* the formula: the selected node is swapped for a small
text field at its position, and the formula re-renders when you press Enter.
A selection and an insertion caret never coexist: with a selection, typing
replaces it; with a caret, typing only inserts.  A range (`b + c` inside `a + b + c + d`) is
edited, deleted and transformed like a single node: typing replaces it, Del
removes its terms, an operation picked in a menu transforms just those terms.

On phones and tablets: tap to select, **tap the selected node again to edit
it**, tap a gap for a caret and tap it again to insert, drag to select a
range; the toolbar (one strip that scrolls sideways on a narrow screen) has ↑
for the parent and a ⌨ button that opens the keyboard for the selection, the
caret or the whole expression; the menus apply an operation as soon as it is
picked.  Two fingers zoom the formula, a drag on its empty space scrolls it
sideways, and vertical swipes still scroll the page.
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

**Names vs. SymPy functions.**  A typed name is resolved in this order: a
symbol declared in the Symbols panel (or passed as `symbols=`), a name already
in the expression, then SymPy's own names (`sin`, `pi`, `E`, `I`, `gamma`,
...), and finally a new plain symbol.  So a variable called `sin` is declared
once in the panel and wins from then on; for a one-off, write it in backticks
(`` `sin`*x ``); `\sin` is always the function.  When a name you typed was
taken as SymPy's function or constant, the status line says so and points at
these two options.

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

The **Transform ▾** menu holds the general ops (simplify, expand, factor,
...) and applies one as soon as it is picked.  Operations specific to the selection's *type* appear in a
separate highlighted **type menu** next to it, labelled with the type
("Matrix ▾", "Integral ▾", "Equation ▾"...), and apply as soon as you pick
one: transpose / inverse / trace / determinant / `as_explicit` for matrices,
evaluate / numeric value / expand or simplify the function inside for
integrals, sums, derivatives and limits, swap sides / move everything to the
left / simplify or expand both sides for equations, `tomatrix` for arrays.

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

### Mobile apps

`mobile/` packages the same editor page as a minimal Android (Kotlin WebView)
and iOS (SwiftUI `WKWebView`) app: `python mobile/build_www.py` produces the
shared, offline-capable bundle, and each platform folder is a few files that
just display it.  See `mobile/README.md`.

### The source line

The SymPy source under the formula is linked to the rendering: select a
piece of it and the corresponding sub-expression is selected in the formula;
select in the formula and the matching source text is highlighted.  The line
is editable and **previews as you type**: whenever the text parses, the
formula above shows it (a text that does not parse marks the line red and
leaves the formula alone); Enter commits it — as one undo step — and Esc
reverts.  That is where whole-expression edits happen: the rendered formula
itself is never replaced by code.

### Long computations

A transformation that takes a while does not freeze the page: after a moment
a spinner overlay names what is being computed, and after a couple of seconds
it offers an **Interrupt** button, which stops the computation and leaves the
expression as it was.  In standalone pages Python runs in a Web Worker and is
restarted on interruption (the undo history of the page is lost then; a
`file://` page in Chromium cannot create the worker and runs Python in the
page instead, without interruption); the local server and the Jupyter widget
interrupt the thread doing the work (`interrupt_thread`), so nothing else is
lost.

### Sessions (mobile app, or `options={"sessions": True}`)

The **Sessions** panel lists your expressions, each with its own undo
history, kept in the browser's storage: **New session** starts one from the
current expression, **Open** switches (the one you leave is saved first),
**Delete** (click twice) removes one.  Available on Pyodide-backed pages.

## How it works

`sympy_editor.AnnotatedLatexPrinter` extends SymPy's `LatexPrinter` so that
every printed sub-expression is wrapped in KaTeX's `\htmlData{path=/1/0}{...}`
(`AnnotatedStrPrinter` does the same for `str()`, and `latex_spans(expr)` /
`annotate_str(expr)` give the character spans of every node in both strings,
keyed by the same paths).
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
python examples/demo.py          # writes examples/demo.html (regenerate after code changes:
python examples/demo_matrices.py #  the pages embed the package; tests/test_examples.py checks they are current)
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
are unavailable.  Graphical edits are tested as user scenarios with the
`Scenario` helper in `tests/test_browser.py` (`scenario(expr).caret_after(path)
.type("+ B*A").enter()` then `.source`), run on both the HTTP backend and a
Pyodide page.  `.github/workflows/ci.yml` runs everything on push.

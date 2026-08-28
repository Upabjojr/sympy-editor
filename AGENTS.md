# AGENTS.md — notes for coding agents and contributors

## What this project is

A WYSIWYG editor for SymPy expressions.  Requirements set by the project owner:

- Render SymPy expressions nicely using LaTeX in HTML.
- Expressions must be **selectable and click-editable** (structural editing of
  sub-expressions, not free-form LaTeX editing).
- **No GPL dependencies** (GPL is incompatible with this project's BSD-3
  licence).  Check the licence of anything you add: runtime, optional or
  vendored, Python or JavaScript.
- Must work both **integrated in Jupyter** and as **standalone HTML**.
- **No npx / node.js package dependencies.**  No bundler, no `package.json`,
  no build step.  JavaScript is plain, hand-written and shipped as static
  files inside the Python package.  (Running `node --check file.js` as a
  local syntax check is fine; it is not a dependency.)
- The project is **shipped through pip** (`pyproject.toml`, setuptools,
  `src/` layout, static assets in `package-data`).

## Architecture

```
src/sympy_editor/
  printer.py    AnnotatedLatexPrinter: LatexPrinter subclass that wraps every
                printed sub-expression in \htmlData{path=/i/j}{...}; path
                helpers (get_at/replace_at/delete_at, format/parse_path).
  document.py   Document: current expression + undo history + edit operations
                (replace/delete/apply/undo/redo), input parsing in the context
                of the expression, JSON `snapshot()` and message `handle()`.
                Single source of truth for all front ends.
  ops.py        Registry of transformations (simplify, expand, ...); register_op.
                KINDS maps kinds (integral, sum, derivative, limit, relational,
                matrix, array, scalar) to SymPy types; node_kinds() lists all
                kinds of a node, most specific first.  An op registered with
                kinds= appears in the front end's *type menu* (labelled with
                the most specific kind) for selections of those kinds; ops
                without kinds are in the general dropdown.
  html.py       Standalone HTML (full page or fragment) with the `pyodide`,
                `http` or `readonly` backend; embeds the core modules for Pyodide.
  server.py     Stdlib http.server backend: serve(expr) -> edited expr.
  widget.py     anywidget widget (optional dependency): kernel-backed editing.
  static/
    editor.js   The whole front end (plain script, no imports/exports).
    editor.css  Styles, scoped under .sympy-editor, theme-aware.
    widget.js   anywidget entry point; widget.py concatenates editor.js + this.
tests/          pytest suite (printer round-trips, document ops, HTML, server).
examples/       demo.py generates demo.html / runs the server.
```

Data flow: Python `Document.snapshot()` → JSON (`latex`, `latex_plain`,
`nodes` {path → {src, type, kind[, reciprocal]}}, `symbols`, `ops`,
`can_undo`...) → `editor.js` renders
`latex` with KaTeX (`trust` enabled for `\htmlData` only) → user acts →
message `{action, path, src|op}` → `Document.handle()` → new snapshot.
The three backends only differ in how the message reaches Python:
anywidget `model.send` + `snapshot` trait, `fetch` POST to `/api`, or a
direct call into Pyodide.  A page with several Pyodide editors loads one
interpreter (cached on `window.__sympyEditorPyodide`, keyed by the Pyodide
index URL) and keeps one `Document` per editor in it; `html.py` also skips
re-running `editor.js` when `window.SympyEditor` already exists.

Two conventions between printer, document and front end:

- **Reciprocal nodes.**  SymPy prints `x/(x+1)**2` by synthesising
  `Pow(x+1, 2)` for the denominator; the printer annotates it with the path of
  the tree's `Pow(x+1, -2)`, `snapshot()` flags the node `reciprocal: true`,
  and the front end sends that flag back with a `replace`, which then stores
  `1/new` at the path.  Deleting or applying an op to the node acts on the real
  `Pow`.
- **Insertion caret.**  `snapshot()` marks nodes whose argument list can
  grow (`insertable`, with `nargs`: Add, Mul, MatMul, function calls, sets...).
  The front end computes the gaps between the rendered arguments of such
  nodes (`Editor._gapsOf`); a click in a gap (or on the operator glyph there,
  which belongs to no argument) shows a caret, and typing sends
  `{"action": "insert", "path", "index", "src"}` (`Document.insert`, parsed
  in the context of the node so a new name in a `MatMul` is a matrix).
  Commutative nodes re-order, so the index only matters for `MatMul` and
  function arguments.
  Messages carry `left`/`right` (argument indices next to the caret) and
  `attach`.  In sums and products `Document.insert` *splices*: the text is
  joined with the neighbour(s) - typed operators as written, juxtaposition
  (`*`) otherwise; `+`/`-` at a junction take the whole half of a product;
  the involved arguments are replaced by the parsed result (which flattens
  into the parent).  Elsewhere one neighbour is combined; `,` inserts an
  argument.
  An edge-click caret remembers its side (`gap.attach`), so the left edge
  of `y` attaches typed text to `y`, the right edge of `x` to `x`; a caret
  on the operator attaches to the left neighbour.
  Clicking within the edge zone (2-5 px, 20 % of the width) of an object
  gives a caret before/after it in the nearest insertable ancestor whose
  argument shares that edge (`_edgeCaretAt`); the middle selects.
  When there is no such ancestor (matrix/array entries, a power's base, a
  function's argument) the caret *extends* the object itself:
  `{"action": "extend", "path", "side", "src"}` -> `Document.extend`,
  which joins the text and the node with the typed operator, or `*` when
  there is none.
- **Ranges.**  Adjacent arguments of a *rangeable* node (`AssocOp`,
  `LatticeOp`: Add, Mul, MatAdd, MatMul, And, Or, Max...; snapshot flag
  `rangeable`) can be selected together: `Editor.range = {parent, anchor,
  focus}` indexes the parent's display-ordered children (`_displayChildren`).
  Messages carry `children: [arg indices]` with `replace`/`delete`/`apply`
  (`printer.extract_range/replace_range/delete_range`); the range's source is
  built in the front end from the children's sources.  Drags use pointer
  events (mouse, touch, pen alike); `touch-action: pan-y pinch-zoom` keeps
  vertical scrolling and pinch-zoom on phones, and `@media (pointer: coarse)`
  enlarges targets.
- **Source line.**  `AnnotatedStrPrinter` (same mixin as the LaTeX printer,
  markers instead of `\htmlData`) gives `snapshot["spans"]`: the character
  span of every node in `str(expr)` (empty if the marked output would not
  match `str(expr)` - explicit matrices are re-aligned on plain widths).  The
  front end's source line is `contenteditable`: a selection in it selects
  the innermost node whose span contains it (only while the line has
  focus); a selection in the rendering wraps its span in a `<mark>` (never
  the document selection, which would move focus into the editable line);
  Enter sends `set`, Esc reverts.  `beginEdit("/")`
  edits there - the rendering is never swapped for a text field.
- **Function box.**  `{"action": "call", "path", "func": "diff(x)"}` →
  `Document.call`: a public callable of `sympy` is called as `f(node, *args)`,
  a `.name`/attribute of the node otherwise; extra args are parsed in the
  document's namespace; lists become `FiniteSet`s.  `{"action":
  "functions"}` returns a snapshot with `functions` (common names first) for
  the box's `<datalist>`, requested once on focus.
- **Loading overlay.**  Backend progress messages go through
  `Editor._report`: texts mentioning loading/waiting show `.se-loading` (a
  blocking spinner overlay, keys and clicks ignored) until the message
  clears; `mount()` shows it during Pyodide preloading.
- **Isolate.**  `{"action": "isolate", "path"[, "children"]}` → `Document.isolate`
  commits the node (or range) as the whole expression; undoable.
- **Unwrap.**  `{"action": "unwrap", "path", "keep"}` → `Document.unwrap`
  replaces a node by one argument (`keep`, default the first; sums/products
  with several terms require it).  The front end passes the child ↑ came
  from (`_cameFrom`) as `keep`; Backspace/Unwrap button.  Delete removes.
- **Layout stability.**  `.sympy-editor` is `display: block` and
  `.se-status` has `flex: 1 1 0; min-width: 0`: the status text must never
  change the container's width nor wrap the toolbar onto a second line,
  either of which moves the formula under the pointer between two clicks.
- **Caret vs selection.**  `Editor.selected` and `Editor.caret` are mutually
  exclusive (`select()` hides the caret, `_showCaret()` clears the
  selection): keys replace a selection, insert at a caret, and never delete
  anything while a caret is shown.
- **LaTeX shortcuts.**  The text field expands complete `\command`s as they
  are typed (`expandCommands`, tables `GREEK`/`COMMANDS` in editor.js) and
  converts between the displayed Greek letters and SymPy's names
  (`toDisplay`/`toSource`: `θ` ↔ `theta`, `λ` ↔ `lamda`, `∞` ↔ `oo`), so
  what is sent to Python is plain ASCII SymPy syntax.
- **Declared names.**  `Document.declared` (name -> object) holds names put in
  scope before they occur in the expression: `Document(expr, symbols=[...])`,
  `declare()` / `undeclare()` and the messages of the same names (fields as
  `retype`, plus `assumptions`).  `namespace()` = names in the expression
  (they win) + declared; `symbol_info()` reports `used`.  Pyodide pages carry
  them as `srepr` strings in `config.document.symbols`.
- **Retyping symbols.**  `{"action": "retype", "name", "type", "rows",
  "cols"}` swaps every occurrence of a name (`xreplace`) for a `Symbol`, a
  `MatrixSymbol` or its `as_explicit()` matrix; since `xreplace` skips the
  constructors' checks, the result is test-printed before it is committed.
  `Document.parse(src, context=node)` reads new names as `MatrixSymbol`s of
  the context's shape when the context is a matrix.

## Backends at a glance

- Jupyter widget (`edit()`, `SympyEditorWidget`): the kernel's SymPy through
  anywidget messages - never Pyodide.
- Standalone HTML (`to_html`, `save_html`, `display_html`,
  `edit(backend="pyodide")`): Pyodide in the browser, preloaded behind an
  overlay.
- `serve()`: the stdlib HTTP server backed by the running Python.

## Key design decisions

- **Tree paths, not LaTeX positions.**  Paths are `args` indices
  (`"/"` = root, `"/1/0"` = `expr.args[1].args[0]`).  Editing rebuilds
  ancestors with `node.func(*args)`, so SymPy auto-evaluation applies.
- **Locating nodes while printing.**  SymPy's printer does not print the tree
  verbatim (`x - y` prints a negated term; `x/y**2` synthesises `Pow(y, 2)`;
  matrix/limit containers are traversed directly).  `AnnotatedLatexPrinter`
  keeps a stack of frames (real nodes being printed) and, on every `_print`,
  searches the innermost frame's sub-tree (BFS, `max_depth=3`) for an
  unclaimed structurally-equal node.  `Tuple` and `ExprCondPair` are
  "transparent": their children are searched first and do not add depth
  (this is what keeps `Sum(x**y, (y, 1, oo))`'s index from being confused
  with the exponent).  Synthesised objects print unannotated, but their
  children still get located.  `_print_Add` is overridden so a negated term
  is annotated with the original term's path, sign included.
- **Containers and rebuild rules** live in `printer.rebuild`: `func(*args)`
  by default; `BlockMatrix` is rebuilt from rows; `MatMul`/`MatAdd` get
  `doit(deep=False)` so `A*A` becomes `A**2` like operator syntax would.
  Matrices/N-dim arrays work because their entries sit in a transparent
  `Tuple` arg.  `Document.namespace()` puts `Symbol`, `MatrixSymbol`,
  `IndexedBase` and undefined functions in scope for typed input.
- **Annotation must be transparent:** `strip_annotations(annotated) ==
  sympy.latex(expr)` is enforced by tests for every expression in the suite.
  Keep it that way when touching the printer.
- **KaTeX specifics:** `\htmlData` needs `trust`; the `enclosing` span it
  produces is treated as a partial group by KaTeX, so operator spacing is
  preserved.  Hit-testing uses `document.elementsFromPoint` and picks the
  deepest `data-path`, because KaTeX stacks empty `vlist` struts over glyphs
  in fractions/scripts (a plain `target.closest()` selects the wrong node).
- **User input is parsed by `parse_expr`** (evaluates Python).  This is the
  same trust level as running the notebook / script, but the HTTP backend
  therefore requires a per-server random token header so that other web
  pages cannot POST to it (cross-site requests fail the CORS preflight).
- **Editing is in place, no dialogs.**  `Editor.beginEdit` stashes the
  children of the selected node's `<span data-path>` and inserts an `<input
  class="se-inline">` there (auto-sized in `ch`); Enter/blur commit, Esc
  restores the stashed rendering.  Typing while a node is selected starts an
  edit with the typed text.  Ops (`apply`) act on the selected path only.
- **Same JS everywhere.**  `editor.js` must stay free of `import`/`export`
  and top-level `const`/`let` (it is inlined into classic `<script>` tags,
  possibly several times per page, and concatenated into an ES module for
  anywidget).  Use `var SympyEditor = (function () { ... })();`.
- Pyodide-backed pages embed the *source* of `printer.py`, `ops.py`,
  `document.py`; those three modules must import nothing but SymPy, the
  standard library and each other.

## Mobile apps (`mobile/`)

Everything about phone packaging lives in `mobile/` and is *not* part of the
pip package.  The rule is minimal wrapping and maximal sharing:

- `mobile/build_www.py` builds `mobile/www/`: the very same page
  `sympy_editor.to_html()` produces for the desktop, with KaTeX and the part of
  Pyodide SymPy needs vendored under `www/vendor/` (about 30 MB) so the app
  works offline.  Test it in a desktop browser with
  `python -m http.server -d mobile/www` (it must be served, not opened as a
  file: WebAssembly and fetch need an origin).
- `mobile/android/`: a Gradle/Kotlin project whose only activity is a WebView
  serving the bundle through `WebViewAssetLoader` (an https origin, with MIME
  types fixed for .wasm/.whl).  No node toolchain; Android Studio (or a local
  Gradle) builds it.
- `mobile/ios/`: a SwiftUI app with a `WKWebView` and a `WKURLSchemeHandler`
  that serves the bundle from the app bundle (`app://www/...`); the Xcode
  project is generated from `project.yml` with XcodeGen (or created by hand,
  see `mobile/README.md`).
- Keep platform code to loading the bundle; every feature belongs in
  `editor.js`/Python so that desktop, Android and iOS stay identical.
  `tests/test_mobile.py` builds the bundle and, with
  `SYMPY_EDITOR_SLOW_TESTS=1`, edits in it with all external requests blocked.

## Conventions

- Python ≥ 3.9, no type-checking tooling enforced; keep type hints and
  docstrings.
- Tests (`pytest`, run by `.github/workflows/ci.yml`):
  - `tests/test_printer.py` — annotation transparency (`strip_annotations`
    == `sympy.latex`) and path correctness for a corpus of expressions;
    add any expression that ever mis-mapped to `EXPRS`.
  - `tests/test_document.py`, `test_html.py`, `test_server.py`,
    `test_widget.py` — Python behaviour; widget tests skip without anywidget.
  - `tests/test_browser.py` — the JavaScript front end driven by Playwright
    in headless Chromium against the HTTP backend (selection, in-place
    editing, ops, undo, errors, Done, read-only page; Pyodide page with
    `SYMPY_EDITOR_SLOW_TESTS=1`).  Skipped without Playwright/Chromium/network.
    Playwright is a dev-only tool, not a project dependency.  Use
    `_click(page, path)` (force click) for glyphs: KaTeX struts intercept
    Playwright's actionability check.
  - `tests/test_examples.py` fails when a generated `examples/*.html` embeds
    outdated JS/Python: regenerate the pages after code changes.
  - Graphical edits: use the `Scenario` helper in `tests/test_browser.py`
    (click/select/caret_after/caret_between/drag/type/enter, then `.source`);
    the `scenario` fixture runs each such test on the HTTP backend and, with
    `SYMPY_EDITOR_SLOW_TESTS=1`, on a Pyodide page.
  - Any change to editor.js should come with a browser test; there is no
    JavaScript unit-test runner by design (no node.js toolchain).
- Front-end options live in `DEFAULTS` in editor.js and are passed through
  `options=` from Python; CDN URLs are in `html.default_urls()` and can be
  overridden (`urls=`) for offline/vendored use.
- Pinned CDN versions: KaTeX `KATEX_VERSION`, Pyodide `PYODIDE_VERSION`
  (in html.py).  Bump deliberately and re-test in a browser.
- Manual browser check: `python examples/demo.py --serve` (server backend)
  and `python examples/demo.py` then open `examples/demo.html` (Pyodide).

## Known limitations / ideas

- Sub-expressions synthesised by the printer (e.g. the `y**2` denominator of
  `x/y**2`, coefficients split off by `Mul`) have no annotation; their
  children do.  Selecting them requires walking up to the parent.
- Parentheses added by the printer are outside the annotated span.
- Identical sub-expressions are disambiguated by print order, which is
  correct for SymPy's printer today but heuristic; a wrong mapping would
  show up as a click selecting an equal-looking node elsewhere.
- Possible extensions: MathJax renderer, drag-and-drop of terms, richer
  input (implicit multiplication is available via `parser="implicit"`),
  vendoring KaTeX for offline use behind an option.

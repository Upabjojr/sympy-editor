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
                An op may declare `params` (label, kind, optional, default) -
                values asked for before it runs, in the shape the function form
                already uses: the array tools (permute axes, contract,
                diagonal) want the axes.  `Document.apply(..., args=[...])`
                parses them in the expression's namespace and passes them after
                the expression.
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
`nodes` {path → {src, type, kind[, parts]}}, `symbols`, `ops`,
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

- **The view tree.**  Paths address what is *shown*, not the SymPy tree:
  wherever the printer shows something else than a node's arguments, the
  node has *virtual parts* in place of them (`printer.view_parts(node,
  settings)`, a pure function of the value and the printer settings):
  `n`/`d` for anything shown as a fraction (a `Rational` - `|p|` and `q`,
  the sign is printed in front -, a `Mul` with negative powers or a rational
  coefficient, a `Pow` with a negative exponent; the split is the printer's,
  `fraction(node, exact=True)`), `neg` for a `Mul`/`MatMul` shown after a
  leading minus (the negated product, whose own parts or arguments are what
  follows the sign).  So `1/n` is `/n` = `1`, `/d` = `n`; `1/(2e)` is
  `/n` = `1`, `/d` = `2*E` with `/d/0`, `/d/1`; `x - 2y` has `/1` = `-2y`,
  `/1/neg` = `2y`, `/1/neg/0` = `2`.  `get_at` reads parts, `replace_at`
  rebuilds the node around a new part (`new/denom`, `numer/new`, `-new`,
  the sign kept for a `Rational`), `delete_at` leaves `1` for a removed
  numerator/denominator and removes the signed product for `neg`.  The
  printers search a frame's virtual parts before its real arguments (the
  real ones stay reachable for printers that do print them: `str` shows
  `exp(-1)/2` for `1/(2e)`).  `snapshot()` lists a node's `parts` and marks
  it neither `insertable` nor `rangeable` (its parts may be); `unwrap` takes a
  part name as `keep`.  `AnnotatedLatexPrinter._print_Pow` routes a power
  shown as `1/…` through `_print_Mul` (as SymPy 1.14 does; 1.15 prints the
  `1` literally) so the numerator gets its part, and `_patch_number_separator`
  makes SymPy 1.15's "two numbers side by side" check (`2 \cdot 3`) see
  through the annotation wrappers.  `_print_Add` prints a negative term as
  `- (negated term)` inside the term's span, and the str printer strips the
  sign inside nested wrappers (`_strip_minus`).
- **The empty view.**  Deleting the whole expression (`editSource("")`,
  also an empty new session) puts `.se-empty` on the view and opens a field
  in it (`beginEmptyInput`, `.se-inline-empty`, `this.emptyField`); a click
  or a typed character on the empty view opens it too.  Typing mirrors the
  text into the source line and schedules the usual preview; `_render`
  re-appends the field (focus and caret restored) after KaTeX replaced the
  view's content, so the preview shows above it.  Enter sends `set`, Esc
  ends it and `revertSource()` brings the expression back; a committed
  state or `revertSource` ends it (`_endEmptyInput`); blur applies a
  non-empty field, checked in a timeout so a re-render's blur does not.
- **Insertion caret.**  `snapshot()` marks nodes whose argument list can
  grow (`insertable`, with `nargs`: Add, Mul, MatMul, function calls, sets...).
  The front end computes the gaps between the rendered arguments of such
  nodes (`Editor._gapsOf`); a click in a gap shows a caret, and typing sends
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
  of `y` attaches typed text to `y`, the right edge of `x` to `x`.
  An operator typed at either end of the text takes the neighbour on that
  side whichever one the caret is attached to ("*y*" between the factors
  of `x*z` gives `x*y*z`); with one neighbour a far-side operator is dropped.
  `_caretPositions` walks the formula in reading order for ←/→; it always
  includes the two ends of the whole formula (a matrix root has no argument
  gaps, yet the caret must be able to stand outside it), and coinciding
  positions merge only when they overlap vertically (rows of a matrix can
  align in x without being one place).
  Clicking within the edge zone (2-5 px, 20 % of the width) of an object
  gives a caret before/after it in the nearest insertable ancestor whose
  argument shares that edge (`_edgeCaretAt`); the middle selects.
  When there is no such ancestor (matrix/array entries, a power's base, a
  function's argument) the caret *extends* the object itself:
  `{"action": "extend", "path", "side", "src"}` -> `Document.extend`,
  which joins the text and the node with the typed operator, or `*` when
  there is none.
- **Operators are selectable.**  `Editor._operatorAt` finds the operator
  glyph under a click (`OPERATOR_GLYPHS`: `+ − ⋅ × = < > ≤ ≥ ∧ ∨`; the `−`
  shown before a negative term counts, it is the sum's operator there) and
  `selectJunction` selects it - `Editor.junction`, exclusive with selection,
  caret and range.  Typing another operator (or the `.se-opbar` palette;
  Del/Backspace mean "none": juxtaposition, a product) sends
  `{"action": "operator", "path", "left", "right", "op"}` ->
  `Document.operator`: in a sum `*`/`/`/`^` bind the two terms and `-`
  negates the right one, in a product `+`/`-` split it at the operator, a
  relation or connective (`= < > & |`) needs the two arguments to be the
  whole node; honours the unevaluated toggle (`lazy`).  A lone operator
  typed at a caret between two arguments is routed to `Document.operator`
  by `Document.insert`.  The node the junction belonged to is selected
  after the change.
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
  the box's dropdown (`_filterFn`), requested once on focus, with
  `signatures` for the common names; `{"action": "signature", "name"}`
  gives `function_signature()` for others (params with kind symbol/text,
  optional, defaults; `PARAM_HINTS` overrides for `*args` functions).  A
  picked function with a required parameter opens the form (`_showFnForm`:
  symbol params are a select over the node's `free` symbols), otherwise it
  is applied at once.
- **Applying a callable node.**  `Document.call` accepts `"(2)"` (and
  `".__call__(2)"`): the node is the function - a `Lambda` evaluated at a
  point.  `type_methods` puts a `{"name": "__call__", "label": "( ) apply"}`
  entry first for a class that defines `__call__` in sympy (only `Lambda`
  does), `function_signature` lets that one dunder through and asks for the
  arguments, and the menu shows `entry.label` when there is one.
- **Methods menu.**  `type_methods(cls)`: the public methods and
  properties a class from sympy itself defines (`is_*` queries, dunders
  and `METHOD_SKIP` plumbing left out), cached per class.  Every snapshot
  carries `methods`: the lists of the view-tree types it introduces, once
  per document (`Document._methods_sent`) - piggybacked so that no extra
  request ever races an edit (`Editor.send` drops messages while busy).
  The front end accumulates them (`_methodsCache` by type name) and
  `_fillMethods` (from `_fillOps`) fills the `.se-methods` select for the
  selection - the root when nothing is selected.  Picking an entry goes
  through the function-box flow: `_pickFn("." + name)` - signature,
  parameter form when required, then `call` (the per-type signature is
  never reused across types).  `{"action": "methods", "path"}` returns a
  snapshot with the target's list included regardless.
- **Loading overlay.**  Backend progress messages go through
  `Editor._report`: texts mentioning loading/waiting show `.se-loading` (a
  blocking spinner overlay, keys and clicks ignored) until the message
  clears; `mount()` shows it during Pyodide preloading.
- **Preview.**  `{"action": "preview", "src"}` → `Document.preview`: a
  snapshot of the parsed source flagged `preview`, nothing committed (an
  unparsable source gives the current snapshot with `error` and the flag).
  The source line sends it, debounced (`previewDelay`), on every change
  (`_previewSource`); `setState` renders a preview without touching the
  line, keeps `Editor.committed` (the last non-preview snapshot) for Esc /
  `revertSource`, and `commitSource` compares against it.  Deleting the
  whole expression empties the line and hides the formula (`.se-empty`)
  until something is typed.
- **Change animation.**  `_captureRendering` (before a re-render of a
  committed change) keeps a clone of the old rendering and every node's
  box; `_animateChange` diffs old and new nodes with `diffNodes`, which
  aligns the two trees (`buildTree`) from the root down: in corresponding
  containers, children with the same `src` are kept with everything inside
  them (any order: SymPy reorders terms), remaining children that are
  containers of the same `type` are paired in order and aligned in turn,
  the rest goes / comes as a whole - so unwrapping `cos(x)**2` in
  `sin(x)**2 + cos(x)**2` colours `cos(x)**2` (exponent included) and
  `2x` typed into `3x` colours the `2` and the `3`.  `diffNodes` also
  returns `map` (old path → new path for kept and aligned nodes), which
  `selectionAfter` uses in `setState` to move the selection with a change:
  to what replaced the selected node (the top-most new node under the
  nearest surviving ancestor; their common holder when several), to the
  node itself where it was kept (a term SymPy moved is followed), else to
  that ancestor - never to whatever now sits at the old path.  Kept nodes get `.se-kept` /
  `.se-diff-kept` / `.rep-kept` (normal colour inside a coloured parent),
  the rest is removed / added - and animates two ghosts (`.se-ghost`, no
  `data-path`, `pointer-events: none`) over the real rendering, which is
  `.se-changing` (opacity 0, still hit-testable) meanwhile: the old
  ghost's top-most removed parts (`.se-removed`, red) move to the new node
  at the same path and fade, the new ghost's top-most added parts
  (`.se-added`, green) fade in and its top-most kept parts slide from their
  old boxes (FLIP).  The real rendering keeps `.se-added` until a
  pointerdown on the view (`_clearChangeMarks`).  Previews do not animate,
  but the committed rendering the first preview replaces is kept
  (`_committedCapture`) and the commit animates from it; unchanged
  re-renders (a reverted preview included) and `prefers-reduced-motion`
  do not animate.
- **Unevaluated results.**  `apply` and `call` messages carry `lazy: true`
  when the toolbar's "unevaluated" toggle (`.se-lazy-box`, `Editor.lazy()`,
  option `unevaluated`) is on.  An `Op` has an optional `lazy` callable
  (`register_op(..., lazy=Determinant)`; `snapshot()["ops"][i]["lazy"]` says
  whether there is one) used instead of `func`; `Document.call` uses
  `LAZY_FORMS` (name → constructor: `diff` → `Derivative`, `det` →
  `Determinant`, `subs` → `Subs`...), calls a SymPy class or a constructor
  in disguise (`UNEVALUATED_CONSTRUCTORS`: `cbrt`, `root`...) under
  `sympy.evaluate(False)`, and for anything else applies as usual with
  `last_note` "… has no unevaluated form: applied" (shown in the status
  line).  `_describe` appends " (unevaluated)" to the history label.
- **History labels and the report.**  `Document.handle` describes each
  message (`_describe`: "Transform: Simplify", "SymPy: diff(x)", "Edit: a →
  b"...) and `_commit` records it per step (`_labels`, exported/restored
  with sessions, `history_labels()["actions"]`; edits made from Python
  have None).  `Editor.buildReport()` asks for an export and writes a
  self-contained page: `katex.renderToString` per step (data-path
  attributes turned into `rep-added` / `rep-removed` classes with
  `diffNodes`), the KaTeX stylesheet with its woff2 fonts inlined as data
  URIs (`_katexCssInline`, cached on `window`), `REPORT_CSS`, no script.
  `showHistory()` shows that page in an overlay (`.se-history-view`: a
  header with the save buttons and an `iframe[srcdoc]`; the steps carry
  `data-index`, a click calls `gotoStep`; Esc closes) and `exportReport()`
  / `exportPython()` save it through `_exportFile(name, mime, text)`:
  `window.SympyEditorApp.shareFile` (the Android app's
  `@JavascriptInterface`: Downloads via MediaStore on API 29+, then a
  share sheet through the FileProvider; `shareHtml` is the older
  HTML-only entry), else the Web Share API with a File, else a blob
  download.  The Python script comes from `Document.python_script(title)`
  (action `script`): SymPy's `PythonPrinter` per step (`Rational(1, 2)`,
  `Integer(3)`), declarations via `srepr` for symbols (assumptions kept),
  `MatrixSymbol(name, rows, cols)`, `Function(name)`, `IndexedBase(name)`.
- **Long computations.**  `Editor.send` shows the spinner overlay after
  `workingAfter` ms and the Interrupt button after `interruptAfter` ms when
  the backend has `interrupt()` (and `canInterrupt()` allows).  Backends:
  the HTTP server takes `{"action": "interrupt"}` on another connection and
  raises `Interrupted` in the thread holding the lock
  (`interrupt_thread`, `PyThreadState_SetAsyncExc`); the widget runs each
  message on a thread and interrupts it the same way (`wait()` joins it in
  tests); the Pyodide backend runs Python in a Web Worker (built from an
  inline script via a Blob URL; `pyodideInPage` is the fallback when the
  worker cannot be created, e.g. Chromium on `file://`) and interrupts by
  terminating it - the next request starts a new worker and re-creates the
  documents from their last snapshot (`rt.docs`), the undo history being
  lost.  `Interrupted` is an `Exception`, so `handle` reports it like any
  error with the document unchanged.
- **Sessions and history.**  `Document(history=[srepr...], index=...,
  symbols=...)` / `Document.export()` (`{"action": "export"}` adds it to a
  snapshot, with `history` = `history_labels()`: the `str` of every step and
  the index) carry a document's state; `{"action": "goto", "index"}` →
  `Document.goto` moves within the history.  With `options.sessions` the
  editor keeps a list of sessions in `localStorage` (`SESSIONS_KEY`), saves
  the current one after each committed change (debounced `_saveSession`)
  and switches with `backend.openDocument(state)` (Pyodide: a new document
  id in the shared runtime).  All of it lives in a lateral drawer
  (`.se-drawer`, `position: fixed`, the ☰ toolbar button, Esc / backdrop /
  ✕ close it), not in the widget's own layout; the history is a sub-tab
  (`.se-subtabs`, `showDrawerTab("history")` toggles `.se-drawer-pane`)
  nested in the current session's card, so the hierarchy session ⊃ history
  is visible.  `history_labels()` also carries `steps` (annotated LaTeX +
  node table per step, cached per expression in `Document`), which
  `_renderHistory` renders lazily into the rows as diffs - `diffNodes`, the
  same rule as the change animation; the rendered `data-path` attributes
  become `data-hpath` so they never collide with the formula's.  A session
  row is a tap target; `openSession` moves `store.current` only once the
  document has opened.  "New session…" opens a chooser
  (`_showSessionPicker`): empty (the default - a placeholder `0` behind
  the empty state, flagged `empty` until a first real expression is
  committed), a copy of the current expression, or an example
  (`sympy_editor.examples.EXAMPLES`, carried as `cfg.examples` →
  `options.examples` when `sessions` is on).  The mobile bundle turns it on.
- **Wrap.**  `{"action": "wrap", "path", "func"[, "args", "children"]}` →
  `Document.wrap` puts the node (or range) inside a function - the inverse of
  unwrap: `cos`, `sqrt`, `Integral` with `args="x"` (or `func="Integral(x)"`).
  An unknown name becomes an undefined `Function`, so `f` gives `f(x)`.  It
  builds rather than computes: when the node would be evaluated away
  (`sqrt` of `4`, `cos` of `0`) the application is rebuilt under
  `sympy.evaluate(False)`, so the editor shows what was asked for; `call`
  is the one that computes.
- **Isolate.**  `{"action": "isolate", "path"[, "children"]}` → `Document.isolate`
  commits the node (or range) as the whole expression; undoable.
- **Unwrap.**  `{"action": "unwrap", "path", "keep"}` → `Document.unwrap`
  replaces a node by one argument (`keep`, default the natural one - the
  function body, the base, the first term; sums/products with several terms
  and fractions require it).  When more than one argument (or virtual part)
  could stand on its own, `snapshot()` lists them as the node's
  `keep_choices` (`[{key, src}]`, the `keep` values `unwrap` accepts):
  `x**2` gives the base and the exponent, a sum its terms, a fraction `n`
  and `d`; `cos(x)` gives none, and an integral's limits are a `Tuple`, which
  cannot be kept.  The front end then opens the `.se-keep` chooser instead of
  deciding (`_askKeep`): the child ↑ came from (`_cameFrom`) is the focused
  button, so ↑, Backspace, Enter keeps it and any other argument can be picked
  instead; Escape cancels, ←/→ move between the choices.  A node with a single
  candidate is unwrapped straight away.  Backspace/Unwrap button.  Delete
  removes.
- **Matrices and arrays.**  The "array" kind covers explicit `NDimArray`s *and*
  symbolic ones: `_ArrayExpr` (an `ArraySymbol`) and `_CodegenArrayAbstract`
  (`PermuteDims`, `ArrayContraction`, `ArrayDiagonal`, `Reshape`) have no public
  base class in common and are both `Expr`, so without them in `KINDS` an array
  symbol would be a "scalar" and get none of its own tools.
  Conversions keep components implicit when they are: `to_array` turns a
  `MatrixSymbol` into an `ArraySymbol` of the same name and shape (an explicit
  matrix into an explicit `Array`, another matrix expression through
  `convert_matrix_to_array`), and `tomatrix` inverts it - a rank other than 2
  says so instead of raising from inside SymPy.  `array_as_explicit` writes an
  array symbol out as its entries.
  The array's own tools - `permutedims`, `contraction` (`tensorcontraction`),
  `diagonal` (`tensordiagonal`), `reshape`, `derive_by_array` - are the plain
  SymPy functions, which dispatch to the symbolic forms on their own, so each
  works on both kinds of array; they ask for their axes/shape/variables through
  the op `params` mechanism.  Axes and shapes are read as plain integers
  (`_array_indices`), so `(x, 1)` is refused with a clear message.  `reshape` is
  registered for matrices as well (reshaped to a rank other than 2 a matrix
  becomes an array); `derive_by_array` is registered *without* kinds - it is in
  the general Transform menu, since an expression, a matrix and an array can all
  be differentiated by `[x, y]` - and a kind-specific copy would put a type menu
  on every scalar, which the editor deliberately does not have.
- **Change tint.**  One box per changed region: `markBoxes(root, cls, boxCls)`
  gives the box class to the marks with no marked ancestor (report:
  `rep-box`, drawer steps: `se-diff-box`; the editor uses `addedTop` -
  the outermost new paths - for `se-added-box`).  The box is
  `display: inline-block`, because a background on an inline element paints
  its *line box*: a tall fraction or matrix kept a band across the middle
  and poked out above and below, and a tint per level made a patchwork of
  overlapping rectangles.  The box is the node's whole visual extent and
  moves nothing else (measured in
  `test_change_tint_covers_the_whole_changed_area`); the tint is opaque, so
  nothing can stack darker.
- **Editing area.**  `.se-view` is a canvas of its own: `--se-surface` (a
  hair lighter than the panel, darker in dark mode) inside `--se-rule`, a
  faint 1px border.  The change tint mixes into `--se-surface`, not
  `--se-bg`, so it matches the ground it sits on.
- **Sessions.**  A session's `name` follows its formula until the user types
  one (`_renameSession`), which sets `title: true` on the stored row;
  `_storeSession` then leaves the name alone.  Emptying the field clears
  `title` and hands the session back to its formula.  While a rename is open
  it owns the row - the field plus ✓ and ✕, with the pencil, Open and Delete
  gone - and `_fillSessions` returns early if a `input.se-session-name` is in
  the list: a snapshot arriving in the background used to rebuild the rows
  and take the field away mid-typing.  `done()` removes the input before
  asking for the refresh, or that guard would keep the row in edit mode.
- **Containers take contents, not arguments.**  `Matrix(x)` is an error,
  `Matrix([[x]])` is the 1x1 matrix, so `_over_contents(fn, target, args)`
  retries `fn([[node]])` then `fn([node])` when the plain call fails.  Both
  `Document.call` (which is where the function box goes - the only path a
  user has) and `Document.wrap` go through it; fixing one and not the other
  leaves the bug exactly where it is felt.
- **The `?` guide is part of the tool.**  `HELP_HTML` is what a user reads to
  find out what the editor can do: a feature that is not in it does not
  exist for them.  Update it in the same commit as the feature -
  `test_help_button_shows_the_guide` checks a phrase from each area.
- **Tool blocks and columns.**  Every tool lives in a `.se-block`
  (`data-block`: session, zoom, sessions, nav, edit, clip, apply) built by
  the `block()` helper, and a block never breaks apart.  Under 44rem the
  blocks spread across each line (`justify-content: space-between`); from
  44rem `.se-tools` becomes a three-column grid and `:nth-child(3n+1/2/0)`
  put each block at the start, the centre or the end of its column.  The
  sessions button is its own block at the right end because the drawer
  slides in from the right: wide it is the third block, so it lands in the
  right-hand column; narrow, `order` moves it up to the first line and a
  `.se-linebreak` span ends that line so nothing can sit to its right.  That
  span is why the column rules use `:nth-of-type` - it takes no grid cell but
  `:nth-child` would count it and shift every block after it one column.  `test_the_tools_are_laid_out_in_columns` and
  `test_the_tools_stay_in_blocks_on_a_narrow_screen` measure the edges.
- **The slideshow.**  `PLAYER_JS` is plain ES5 in a string, inlined in every
  report: the slides are built from the `section.step` and `.transition`
  elements already on the page, one slide per step with the `.transition`
  that produced it grouped in front of it (`slide-on`, plus `slide-top` /
  `slide-bottom` on the group's ends so it centres as one), shown under
  `body.slides`.  A change and its result belong on the screen together -
  never one slide after the other.  It has to
  keep working offline in a saved file years from now - no dependency, no
  build step, and the report stays self-contained (the tests check there is
  no `<script src>` and no `<link>`).  A host drives it from its own fixed
  strip: the report exposes `window.sympyHistoryPlayer`
  (play/pause/toggle/prev/next/exit/state/subscribe), `playerControls(frame)`
  builds the buttons for `.se-history-head`, and `body.hosted` hides the
  report's own bar - the controls must not scroll away with the steps.  The
  saved file keeps its bar, since it has no host.
- **The history strip.**  `.se-history-head` wraps, so its controls come in
  `.se-head-group` spans - playing, the size, saving, closing - and a group
  is `inline-flex` with `flex: 0 0 auto`, which wraps as a whole.  Put a new
  control in a group, never loose in the strip: a `- 100% +` broken across
  two lines is not a control.  The report's own bar does the same with
  `.player .group`.  Nothing in the strip may move when the player's state
  changes: `.se-play` holds the width of the wider of Play/Pause, the
  counter and the zoom level are tabular with a `min-width`, and "Show all"
  toggles `visibility` (a `se-on` class), never `hidden` - which used to
  shove every button beside it the moment Play was pressed.
  `test_nothing_in_the_history_strip_moves` compares the geometry of every
  control before, during and after playing.  A saved report's own bar has
  the same rule, and one trap of its own: under `body.slides` the body is a
  flex column, where `main`'s `margin: 0 auto` stops it stretching and lets
  it shrink to the slide on show - which walked the bar left and right as
  the formulas changed width.  `body.slides main` carries `width: 100%` for
  that reason; keep it.  The same goes for opening the
  view: `playerControls(frame, total)` takes the number of steps up front,
  builds the controls in their final shape (the counter already reads
  `1 / n`) merely disabled, and returns nothing at all below two steps -
  they used to appear only when the iframe answered, which rearranged the
  strip a moment after it was drawn.  Note an iframe fires `load` once for
  its empty document before `srcdoc` is set; that one finds no player and
  must leave the controls alone.
- **Walking versus playing.**  The player's `prev`/`next` go through
  `stepBy`, which plays a slide under `body.slides` and otherwise `locate`s
  the step in the listing - scrolling to it and outlining it with
  `.slide-here`.  `exit()` locates the step it was showing instead of
  clearing, so "Show all" lands where the slideshow was.  Keep the two
  arrows useful in both modes.
- **The history's zoom.**  `--se-report-zoom` on the report's root scales
  `.step` and `.transition`; the slideshow's own size multiplies it
  (`calc(1.35em * var(--se-report-zoom, 1))`), so do not give `.slide-on` a
  plain font-size again - that pinned the slideshow and the buttons stopped
  working there.  The player owns the value (`setZoom`, `zoomIn`/`zoomOut`
  on `window.sympyHistoryPlayer`) and drives it from the strip's buttons,
  Ctrl+wheel and a two-finger pinch inside the report.  `.se-history-page`
  is `resize: vertical` so an embedded viewer can be dragged taller.
- **The history viewer is not the editor.**  `buildHistoryReport(hist,
  opts)`, `renderMarked`, `katexCssInline` and `saveFile` are free functions
  in editor.js, and `SympyEditor.mountHistory(host, cfg)` shows a history
  with no editor and no backend behind it: `cfg.history` is
  `{steps: [{latex, nodes}], labels, actions, index}` - what both
  `Document.history_labels()` and `History.payload()` produce.  `Editor`
  keeps thin wrappers (`buildReport` fetches the session's history and
  delegates).  On the Python side `render_step(expr, printer_settings)` in
  document.py builds one step, `history.History` holds a list of them, and
  `html.to_history_html` / `save_history_html` / `display_history` wrap the
  viewer in a page or a fragment.  Keep the payload shape identical on both
  sides: one viewer serves the editor's sessions and any list of
  expressions.
- **The two views are one document.**  Formula and source line follow each
  other both ways, and all four directions must keep working:
  selection -> `<mark>` (`_markSource`), mark -> selection
  (`_onSourceSelection`), text caret -> formula caret (`_caretNear` +
  `_showCaret`), formula caret -> `.se-source-caret` (`_markSource` again).
  `_sourceOffsetOf(gap)` is the one place that maps a caret position to a
  character offset - through `state.spans` and the paths of the elements
  beside the gap - and `_caretNear` is its inverse by nearest offset.  A
  negated term's source span covers its minus (`AnnotatedStrPrinter.
  _print_Add` puts the sign inside the term's marker, as the LaTeX printer
  does), so selecting `- sin(x)` marks the same text in both; a plus stays
  outside, since there it is the operator between two terms.
- **The source line's selection.**  Selecting text there selects in the
  formula (`_onSourceSelection`, `selectionchange`).  Two things used to
  break it and both are easy to reintroduce: `contenteditable` must be
  `"true"`, not `"plaintext-only"` (a drag over a plaintext-only line
  selects nothing in Chromium, though a double-click does), and
  `_placeActions` must do nothing while the source has the focus - the
  floating bar appearing under the pointer takes the drag apart.  A test
  drags over `sin(x)` and checks both the text and the formula.
- **Touch sizes.**  The `any-pointer: coarse` rule lists every control that
  grows for a finger.  A control added later and left out of that list stays
  12px beside its 14px neighbours - which is exactly what happened to the
  type and Methods menus.  Add new controls to it.
- **Printers that reach past `_print`.**  Annotation happens in
  `_AnnotatingMixin._print`, so a `_print_*` that formats a child without
  going through `self._print` leaves that node out of the view tree and
  ↑/↓ skip it.  `LatexPrinter._print_Determinant` did exactly that
  (`_print_matrix_contents`), so `AnnotatedLatexPrinter` overrides it and
  prints the matrix with `mat_delim` emptied - the bars are the
  determinant's.  Watch for the same shape in any printer added later.
- **What "changed" means in a diff.**  `diffNodes` aligns two node tables;
  `folded(tree, nodes, p) = nargs - children.length` is how many arguments
  the printer draws inside the node itself instead of giving them a place of
  their own (sqrt(x) is `Pow(x, 1/2)` with the exponent inside the radical;
  the leading minus of a product is the same).  When that count changes the
  node draws itself differently, so `align` marks the node even though its
  arguments still line up - the radical goes red while the radicand stays
  black through `rep-kept` / `se-diff-kept`.  Do not make `align` keep a node
  unconditionally again: that is exactly the bug where a vanished sqrt sign
  was drawn as unchanged.
- **Full screen.**  `.se-view` lives on a `.se-stage` (`position:
  relative`) beside `.se-fullbtn`, not inside it: within the view a wide
  formula would scroll the button out of sight.  `Editor.setFullscreen`
  toggles `.se-full` on the root - `position: fixed`, a column flex box, the
  stage and the view taking the leftover height, the toolbar, the source line
  and the Symbols panel all hidden (the corner button and the floating action
  bar are what is left), the formula centred and drawn at 1.9em - and Esc
  leaves it when nothing is selected (the last `Escape` branch of `_onKey`).
  The panel is only the fallback: `_browserFullscreen` asks for the real
  thing through the Fullscreen API (a user gesture is needed, and an iframe
  without `allowfullscreen` refuses - hence the fallback) and follows
  `fullscreenchange` so leaving through the browser leaves here too, while
  `_nativeFullscreen` calls `window.SympyEditorApp.setFullscreen(on)`, which
  the Android app answers by hiding the system bars
  (`WindowInsetsControllerCompat`, `MainActivity.ReportBridge`).  Android
  ignores `hide()` from an unfocused window and undoes it when focus comes
  back, so the activity remembers `wantsFullscreen` and applies it again in
  `onWindowFocusChanged`.  The button is 44x44 on a coarse pointer - a
  target, not a glyph - and re-measures the overlay boxes after the size
  change.
- **Touch keyboards.**  `noAutoCaps` (applied by `h()` to every text input,
  and by hand to the contenteditable source line) turns off
  `autocapitalize`, `autocorrect`, `autocomplete` and `spellcheck`: what is
  typed is code, so a phone keyboard must not come up shifted (`x`, not
  `X`).  Anything new that takes typed expressions goes through it.
- **Button surface.**  The tools are raised, not flat: a barely-there
  vertical gradient (`--se-btn`), a lit top edge and a soft drop shadow
  (`--se-btn-edge`, `--se-btn-shadow`), hover lifting it with an accent
  border, `:active` flipping the gradient and moving the shadow inside
  (`--se-btn-down`, half a pixel down), `:focus-visible` an accent ring,
  and disabled buttons flat with no shadow.  Fields (the function box)
  are sunken instead (`--se-field` plus an inner shadow), so a control
  looks like what it does.  Dark mode redefines the same tokens; the
  transitions are dropped under `prefers-reduced-motion`.  Keep the
  paddings as they are - the toolbar-row and arrow tests measure them.
- **Icons.**  The four navigation arrows are `arrowSvg(dir)`: one drawing
  rotated, sized to the text line box (`.se-icon`), so they match each
  other and the buttons beside them on every platform.  As text glyphs they
  came from whichever installed font had them - the horizontal pair twice
  as wide as the vertical one, often another weight and baseline.
- **Backends.**  `Editor` only needs `{send(msg, report) -> snapshot}`, plus
  the optional `warmup`, `openDocument`, `interrupt`/`canInterrupt`.
  `http` (the local server), `pyodide` (a worker in the page), `readonly`,
  and `native`: the *host application* runs Python.  The native backend
  hands JSON to `window.SympyEditorPy` (injected by the host) with a
  request id and is answered through `window.__sympyEditorNative(id, ok,
  payload)`; `build_config(backend="native")` carries only the `srepr` and
  the Document keyword arguments, since the sources and the runtime belong
  to the host.  The Android app is such a host: Chaquopy packages CPython
  and SymPy in the APK, `MainActivity.PythonBridge` runs
  `sympy_editor_app.py` (new_doc/handle/close/version) on a single Python
  thread and posts the answer back on the UI thread.  `mobile/build.py`
  copies `src/sympy_editor` into the app's Python source set, so the app is
  never built against a stale copy, and `build_www.py --native` leaves
  Pyodide out of the bundle (~1 MB instead of ~24 MB).  A debug build turns
  on WebView debugging: `adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>`
  and Playwright's `connect_over_cdp` then drive the app on the device.
- **Help view.**  The toolbar's "?" (`showHelp`/`closeHelp`) overlays
  `HELP_HTML` - the whole gesture/key/tool guide, static content in
  `.se-help-body` (multi-column via `column-width`), dressed as the
  history view (`.se-history-view se-help-view`).  Esc closes it from
  anywhere (`_onKey` checks `helpView` first - the toolbar click refocuses
  the formula view, whose handler would swallow Esc otherwise); opening
  the history view closes it.  Keep HELP_HTML in step with README's table
  when gestures change.
- **Layout stability.**  `.sympy-editor` is `display: block` and
  `.se-status` has its own full line under the tool rows (`flex: 0 0 100%;
  min-width: 0; min-height: 1.3em`): the status text must never change the
  container's width nor move the tools, either of which moves the formula
  under the pointer between two clicks.  The tools sit in `.se-tools` in three logical rows -
  session/timeline + zoom, selection navigation + edits + clipboard, and
  the transform menus + function box - forced by `.se-break` spans
  (`flex-basis: 100%`), with `.se-sep` rules between the blocks of a row;
  each row still wraps onto more lines when narrow, and the status line sits
  under them at every width; `.se-actions` wraps too (`max-width: calc(100% - 8px)`), so no
  button is ever off-screen.
- **Zoom and sideways scrolling.**  `Editor.setZoom(zoom, anchorX)` sets the
  CSS variable `--se-zoom` on `.se-view` (`font-size: calc(base *
  var(--se-zoom))`), keeps the content under `anchorX` in place, drops the
  gap cache and the caret and redraws the selection; sources: the −/100%/+
  buttons, Ctrl+wheel, Ctrl+plus/minus/0 and a two-pointer pinch
  (`_pointers`/`_pinch`; a non-passive `touchstart` listener prevents the
  browser's own pinch when two fingers land, so `touch-action: pan-y` can
  stay for one-finger page scrolling).  `rememberZoom` (option; on in the
  mobile bundle) keeps it in `localStorage`.  A formula wider than the view
  (`overflow-x: auto`) scrolls with a plain wheel over it (the event reaches
  the page again at the ends) and by dragging its empty space
  (`_pan`; a drag that starts on a glyph still selects a range).
- **Caret vs selection.**  `Editor.selected` and `Editor.caret` are mutually
  exclusive (`select()` hides the caret, `_showCaret()` clears the
  selection): keys replace a selection, insert at a caret, and never delete
  anything while a caret is shown.  ←/→ at a caret (`_moveCaret`) step
  through `_caretPositions()`: every caret position of the formula in
  reading order (`_readingChildren`: left to right on a line, higher lines
  first), built by a depth-first walk - the gaps of insertable nodes,
  extend carets before/after the arguments of the others - with coinciding
  positions merged (a gap beats an extend caret, an inner extend caret an
  outer one, as an edge click does).  ↑ at a caret selects the object it is
  attached to (`_selectBesideCaret`); ↓ does nothing and its button is
  disabled.  ←/→ with nothing selected put a caret at the first/last
  position (`_caretAtEnd`); the ←/→ buttons are disabled when the move
  would do nothing (`_caretIndex`, `_sidewaysTarget`).  Hover styles are
  under `@media (hover: hover)`: on a touch screen `:hover` sticks to the
  last tapped button and reads as "active".
- **LaTeX shortcuts.**  The text field expands complete `\command`s as they
  are typed (`expandCommands`, tables `GREEK`/`COMMANDS` in editor.js) and
  converts between the displayed Greek letters and SymPy's names
  (`toDisplay`/`toSource`: `θ` ↔ `theta`, `λ` ↔ `lamda`, `∞` ↔ `oo`), so
  what is sent to Python is plain ASCII SymPy syntax.
- **Name resolution.**  `Document.parse` uses `parse_expr(local_dict=namespace())`:
  declared/used names win, then SymPy's globals, then new symbols.
  `` `name` `` (backticks) forces a Symbol for that parse; `_collision_note`
  reports names that were taken as SymPy globals (`snapshot["note"]`, shown
  in the status line).
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
  Highlight boxes, caret gaps and edge zones come from `Editor._visualRect`:
  the union of the boxes of what is drawn inside the annotated span -
  struts and `vlist` spacers count for nothing, and a clipping element
  (`.hide-tail`, `.stretchy`, anything with `overflow` other than
  `visible`) counts with its own box only: KaTeX draws roots and stretchy
  symbols with a 400em-wide SVG that its wrapper clips, and following the
  SVG once put a √'s box 5,000 px to the right.
- **User input is parsed by `parse_expr`** (evaluates Python).  This is the
  same trust level as running the notebook / script, but the HTTP backend
  therefore requires a per-server random token header so that other web
  pages cannot POST to it (cross-site requests fail the CORS preflight), and
  a server bound to a loopback address answers only requests whose `Host`
  is a loopback name (`EditorServer.accepts_host`): a DNS-rebinding page
  could otherwise fetch the editor page, token included.
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

## Web app (`webapp/`)

`webapp/build.py` builds `webapp/dist/`: `mobile/build_www.build(...)` with a
`head` (manifest link, theme/mobile meta tags, the service-worker
registration; `to_html(head=...)` → `render_page`) plus
`manifest.webmanifest`, icons (an SVG, and PNGs written without any imaging
library) and `sw.js`, which precaches every file of the bundle under a cache
named by a hash of the bundle (cache-first for same-origin requests; a new
build replaces the old cache).  Not part of the Android bundle: a service
worker cannot fetch through `WebViewAssetLoader`.  `tests/test_webapp.py`
builds a `--cdn` copy and checks the worker installs and caches in Chromium;
`.github/workflows/webapp.yml` deploys `dist/` to GitHub Pages.

## Conventions

- Python ≥ 3.9, SymPy ≥ 1.14 (`pyproject.toml`); no type-checking tooling
  enforced; keep type hints and docstrings.  In the browser, Pyodide's own
  sympy package lags behind (1.13.3 in Pyodide 0.28), so the pages load
  the `SYMPY_VERSION` wheel from PyPI (`SYMPY_WHEEL`, `urls["sympyWheel"]`;
  after Pyodide's `mpmath`) and the offline bundles vendor it - keep the
  three in step when bumping.  CI runs
  the suite with the latest SymPy *and* with the oldest admitted one (job
  `oldest-sympy`): only use what exists in both.
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
- Pinned CDN versions: KaTeX `KATEX_VERSION`, Pyodide `PYODIDE_VERSION`,
  SymPy in the browser `SYMPY_VERSION`/`SYMPY_WHEEL` (in html.py).  Bump
  deliberately and re-test in a browser (`SYMPY_EDITOR_SLOW_TESTS=1`).
- Manual browser check: `python examples/demo.py --serve` (server backend)
  and `python examples/demo.py` then open `examples/demo.html` (Pyodide).

## Known limitations / ideas

- The view tree covers fractions, signs and rationals; other synthesised
  pieces (the literal `1` of `1/√x`, the `1` of a long fraction split as
  `\frac{1}{d} · numer`) have no annotation, and a numerator that is a
  product has no span of its own (its factors do).
- Parentheses added by the printer are outside the annotated span.
- Identical sub-expressions are disambiguated by print order, which is
  correct for SymPy's printer today but heuristic; a wrong mapping would
  show up as a click selecting an equal-looking node elsewhere.
- Possible extensions: MathJax renderer, drag-and-drop of terms, richer
  input (implicit multiplication is available via `parser="implicit"`),
  vendoring KaTeX for offline use behind an option.

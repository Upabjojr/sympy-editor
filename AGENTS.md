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
`nodes` {path → {src, type}}, `ops`, `can_undo`...) → `editor.js` renders
`latex` with KaTeX (`trust` enabled for `\htmlData` only) → user acts →
message `{action, path, src|op}` → `Document.handle()` → new snapshot.
The three backends only differ in how the message reaches Python:
anywidget `model.send` + `snapshot` trait, `fetch` POST to `/api`, or a
direct call into Pyodide.

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

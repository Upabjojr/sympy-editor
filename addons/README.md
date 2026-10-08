# Add-ons

This folder holds what is *not* the editor: packages that plug into it.  The
editor (`src/sympy_editor`) knows the contract - `sympy_editor.addons.Addon` -
and nothing about any particular add-on; each add-on is a pip package of its
own, with its own dependencies, licence check, tests and release cadence.
Nothing here is installed with `sympy-editor`.

```
addons/
  README.md                 this document: the architecture and the contract
  template/                 an add-on to copy: every hook once, in 60 lines of Python and 40 of JavaScript
  sympy_editor_tree/        the expression tree as an editable graph      (no dependency)
  sympy_editor_plot/        the graph of the selection, drawn by Plotly.js (numpy optional)
  sympy_editor_matching/    rewrite rules matched many-to-one              (sympy-matching)
  sympy_editor_latex/       LaTeX in: a first reading, every ambiguity a choice, constants as switches (lark)
  sympy_editor_console/     a Python console and script runner, with `editor` for the formula (no dependency)
  sympy_editor_assumptions/ what SymPy knows about the selection; the symbols' assumptions as switches (no dependency)
  sympy_editor_forms/       the selection rewritten by every simplification function, one card per form (no dependency)
  sympy_editor_numeric/     the selection as a number: any precision, exact forms, why there is none, tables (no dependency)
  sympy_editor_series/      series expansions of the selection: Taylor, Laurent, Puiseux, asymptotic, leading term (no dependency)
  sympy_editor_solver/      solve the selected equation, inequality or system; check, insert (no dependency)
  sympy_editor_units/       physical units: typed names, dimensions checked term by term, conversions (no dependency)
  sympy_editor_handwriting/ writing on the formula by hand, read by math-ocr's stroke model (onnxruntime; not in Pyodide)
  sympy_editor_export/      the selection as LaTeX, MathML, Python, C, Fortran, JS, Octave, Julia, Rust or a function (no dependency)
  sympy_editor_check/       "Check my work": is each step of the history equivalent to the one before (no dependency)
  sympy_editor_transforms/  Laplace, Fourier, Mellin, Hankel and z-transforms of the selection (no dependency)
  sympy_editor_steps/       step-by-step solutions: integrals, derivatives, equations of degree one or two (no dependency)
  sympy_editor_linalg/      a linear algebra workbench for the selected matrix: spectrum, Jordan form, LU/QR/Cholesky, row reduction step by step (no dependency)
  sympy_editor_feynman/     path integrals of QED expanded into Feynman diagrams, each drawn (no dependency)
  demo.py                   a page with the first five, to try them in a browser
```

All seven are **drafts**: they work end to end (each has tests, and the
editor's browser test drives a panel), but their interfaces are the first
version of an idea, not a promise.  They live in this repository for
convenience only: an add-on is an **external project** - any package, in
any repository, by anyone - and the editor learns of it through a Python
entry point, the way pytest learns of its plugins.

## Starting the editor with add-ons

1. **Install the add-on** - it is a normal package.  For the drafts, from
   the checkout:

   ```sh
   pip install -e addons/sympy_editor_tree
   pip install -e addons/sympy_editor_plot        # numpy is optional: pip install -e "addons/sympy_editor_plot[fast]"
   pip install -e addons/sympy_editor_matching    # pulls sympy-matching
   pip install -e addons/sympy_editor_latex       # pulls lark (pure Python)
   ```

   A published add-on is `pip install sympy-editor-whatever`.  The editor
   itself is unchanged by any of this: `installed_addons()` lists what it
   can find (`{'matching': 'sympy_editor_matching:ADDON', 'plot': ..., 'tree': ...}`).

2. **Name it when starting the editor.**  Every entry point takes `addons=`,
   because all of them build a `Document`:

   ```python
   from sympy_editor import edit, save_html, serve, Document, to_html

   edit(expr, addons=["tree", "plot", "matching"])     # the Jupyter widget (or the Pyodide fallback)
   save_html(expr, "page.html", addons=["tree"])       # a self-contained page; the add-on's package goes with it
   serve(expr, addons=["matching"])                    # the local HTTP server
   Document(expr, addons=["plot"])                     # from Python, no browser
   ```

   Names are entry-point names.  Without installing, a module name works
   when the package is importable (`addons=["sympy_editor_tree"]`,
   `"my_pkg.addons:PLOT"` for an object under another name), and so does the
   object itself (`addons=[sympy_editor_tree.ADDON]`).  Several editors may
   share one add-on object; state is kept per document.

3. **Or try the page**: `python addons/demo.py` writes `addons/demo.html`
   with the drafts (no install needed, it reads them from the
   checkout), `python addons/demo.py --serve` runs them on the local server.

4. **Switch them while editing.**  The **Add-ons** section at the top of the **≡** drawer lists
   what the document can run - what it started with plus what `available=`
   named, and by default every installed add-on - with a check box each.
   Ticking one mounts its panel and tools on the spot, unticking takes them
   down; the same from Python is `doc.enable("plot")` / `doc.disable("plot")`,
   or the message `{"action": "addons", "enable": [...], "disable": [...]}`.
   A switch is not a step of the history, and an add-on's per-document state
   (a rule set) waits for it to be switched on again.  A self-contained page
   carries the packages of every add-on it may switch on
   (`save_html(expr, ..., addons=["tree"], available=["plot"])`).

There is no configuration file and no build: an add-on is on for the
documents that have it on, and off elsewhere.

## Writing an add-on of your own

Copy `addons/template/` to a repository of yours - it is a complete package:
`pyproject.toml` with the entry point, `__init__.py` with one op, one query,
one change and one contribution, `static/panel.js` with a panel and a
toolbar button, a test.  Rename it (its README says where), `pip install -e .`,
and `edit(expr, addons=["yours"])` finds it.  The contract is
`sympy_editor.addons.Addon` (documented in the module); `API_VERSION` says
which version of it this editor speaks, and an add-on that sets
`api_version` higher is refused with a message.  Nothing in an add-on is
imported by `sympy_editor`, and nothing of `sympy_editor`'s internals is
needed beyond the documented helpers (`make_op`, the path helpers in
`printer`, the document's `get`/`replace`/`parse`).

## What an add-on can do

Three kinds of extension were asked for, and the contract has a place for each:

| need | where it lands |
|---|---|
| **new nodes** in the expression tree, from another library | `Addon.kinds`, `namespace()`, `make_symbol()`, `rebuilders`, `latex_printers` |
| **a different interface**: HTML/JavaScript beside the formula | `Addon.js` / `Addon.css`, `SympyEditor.registerAddon`, the panel and toolbar hooks |
| **custom widgets**: something that computes and shows | `contribute()` (data in every snapshot), `handle()` (methods), `api.call()` |
| **the history**: something drawn under every step | `contribute_step()` (data per step), `historyStep` / `historyStepHtml` / `historyCss` (the drawer's list and the report) |

An add-on may use one of these or all of them.  A menu of transformations is an
add-on with nothing but `ops`; a widget under the formula is one with nothing
but `js`.

## How it fits the editor

The editor already has one shape for everything: a `Document` in Python is the
only source of truth, the front end sends it JSON messages
(`{"action": ..., "path": ...}`), every message is answered with a *snapshot*
(the LaTeX, the node table, the ops...), and the three backends - the Jupyter
kernel, Pyodide in the page, the local HTTP server - differ only in how the
message travels.  Add-ons keep that shape.  They do not get a second channel:

```
  Python                                              browser
  ──────                                              ───────
  Document(expr, addons=[tree, plot])                 SympyEditor.mount(host, cfg)
    ├─ ops table += addon.ops                           ├─ loadAddons(cfg.addons)   (css once, js once)
    ├─ namespace() += addon.namespace()                 └─ new Editor(...)
    ├─ snapshot():  addon.contribute(doc, snap, expr)        └─ _mountAddons(): def.mount(api) per add-on
    ├─ handle({"action": "addon", "addon", "method", ...})        ├─ element  → a box under the formula
    │    └─ addon.handle(doc, method, payload)                     ├─ tools    → a block in the toolbar
    │         dict  → snap["query"] (nothing changed)              ├─ onState(snap), onSelect(path, range)
    │         Basic → committed as the whole expression            └─ api.call(method, payload) → Promise
    │         None  → whatever doc.replace(...) did
    └─ handle({"action": "addons", "enable": [...], "disable": [...]})
         └─ enable()/disable(): kinds, ops, methods on or off    _syncAddons(snap): mount / unmount to match
            snap["addons_available"] → the Add-ons switches (≡)
```

* **One message.**  `{"action": "addon", "addon": name, "method": m, ...}` goes
  through `Document.handle` like every other action, so it runs under the same
  lock, is interruptible the same way, is answered with a snapshot like the
  rest, and works unchanged on the kernel widget, the HTTP server and Pyodide.
* **Queries and changes are told apart by what the method returns.**  A `dict`
  is a query: it travels back under `snap["query"]` and the front end does
  *not* treat the answer as a new state (`api.call` resolves with the dict).
  A SymPy object is committed as the new expression, with the label
  `describe()` gives, so it lands in the undo history and the History view
  like any edit.  `None` means "I edited through the document myself"
  (`doc.replace(path, ...)`, which commits).
* **Data rides with the snapshot.**  `contribute(doc, snap, expr)` adds to
  every snapshot - the tree add-on puts the argument tree there - so a panel
  never has to ask after an edit and no request of its own can race one.
  Keep it small: it goes with every answer, previews included.
* **The front end is a plain script**, run once per page with `SympyEditor`
  in scope, that calls `SympyEditor.registerAddon(name, def)`.  No module
  system, no bundler, no `package.json`: the same rules as `editor.js`.  A
  library it needs is loaded from a CDN with `api.loadScript(url)` (as KaTeX
  is), or vendored by whoever builds an offline bundle.
* **Nodes from elsewhere print themselves.**  The annotated printer is a
  `LatexPrinter` subclass, so a class with a `_latex(self, printer)` method
  that formats its children through `printer._print(child)` gets every child
  annotated and selectable, for free.  `latex_printers` is for classes one
  cannot edit (the matching add-on draws sympy-matching's `WildSymbol`
  underlined).  Editing inside such a node rebuilds it with
  `node.func(*args)`; a class whose constructor takes something else
  registers a rebuilder.  `namespace()` puts the constructors in scope for
  typed input and - under the class names `srepr` writes - for sessions read
  back; `make_symbol(name)` decides what a *new* name typed by the user is
  (a wildcard when it ends in `_`, say).
* **Kinds give a node its own menu.**  `kinds = {"rule": (RewriteRule,)}` is
  added to `ops.KINDS` ahead of "scalar" when the add-on is activated, and an
  op with `kinds=("rule",)` then appears in the type menu for selected rules.
  Ops are built with `make_op` (an `Op` that is not registered globally) and
  listed in `Addon.ops`; a document takes them beside the built-in ones.  An
  op with `context=True` receives the document too (`func(expr, doc=doc)`) -
  for state kept per document, such as a rule set.
* **Per-document state** lives in `doc.addon_state[name]`, a dict the document
  keeps for each add-on; an `Addon` instance may serve many documents.
  `export_state(doc)` / `restore_state(doc, data)` carry it with a session
  (`Document.export()["addon_state"]`), as JSON the add-on parses back; in
  Jupyter `w.addon_state` is the same dict, live.  What should outlive a
  session - a library of rule sets - the add-on keeps from its panel
  through `api.keep`, its editor's keeper (the app's storage, the server's
  or the kernel's store; the browser's only on a standalone page).

### The `api` a panel receives

```js
api.name, api.options        // the add-on's name, and Addon.client_options() from Python
api.state()                  // the last snapshot; api.node(path) one entry of its node table
api.selected(), api.range()  // the selection (a view path) and the range, as the editor holds them
api.rangeIndices()           // the range's argument indices, as `children` in the editor's messages (null without one)
api.select(path)             // select in the formula
api.call(method, payload[, {quiet: true}])   // → Promise: the query's result, or the new snapshot for a change;
                             // quiet: no "Working…" overlay over the editor, the focus left alone - for a
                             // question the panel shows its own progress for (the LaTeX box reads as one types)
                             // without it the overlay still waits `backgroundAfter` (2.5 s), not the editor's
                             // 0.4 s: an add-on's work does not block the editor unless it hangs.  A query's
                             // answer carries the result only, not a snapshot of the formula
api.send(msg)                // any editor message ({action: "apply", ...})
api.status(text), api.error(text)
api.h(tag, attrs, children)  // the editor's element helper; api.katex(); api.loadScript(url) - once per URL and page
api.keep.read(name)          // → Promise of the text kept under `name` (or null); api.keep.write(name, text):
                             // this editor's keeper - the app's storage, the server's or the kernel's store,
                             // the browser's only on a standalone page.  Not SympyEditor.keep, which asks
                             // the editor made last: on a page with several it may be another backend
api.openFile(accept)         // → Promise of {name, text} the user picked (null for none): the host app's picker,
                             // a file input in a browser
api.saveFile(name, mime, text)   // offer text as a file, as the editor saves its own: the host app, the kernel,
                             // the share sheet, or a download
api.editor                   // the Editor itself, for what the above does not cover
```

`onSelect(path, range)` is called when the selection really changes - another
node, range, operator or caret - and once after each new state; never for the
same selection drawn again (a relayout, a zoom, the "Working…" overlay going
away).  A panel that asks Python about the selection may still remember what
it asked for last and ask nothing for the same target: on a phone a query
outlasts the overlay's 0.4 s, and asking again on every redraw made each
answer bring the next question, for ever.

`def.mount(api)` returns `{element, title, help, onState(snap), onSelect(path,
range), onZoom(zoom), onBack(), commands: {cmd: fn}, destroy(), historyStep(step, i, prev),
historyStepHtml(step, i, prev), historyCss, historyTools(target)}`, all optional (`help` is HTML for
the guide behind the panel's "?", shown as the editor's own guide is - write
one: a feature that is not in it does not exist for the user; the `history*`
hooks draw something under every step of the history - an element in the
drawer's list, static markup plus CSS in the self-contained report, from what
the Python side's `contribute_step(doc, step, expr)` put in the step - the
tree add-on draws each step's tree; `historyTools` returns buttons for the
history's strip and the drawer's list - expand or collapse every tree; `onBack`
answers the system's Back - Android's button, through `SympyEditor.back()` -
by closing what the add-on has open and returning true, or false when it has
nothing open: the LaTeX field closes, the handwriting pen goes down); `def.tools` is a list of
`{cmd, label, title, run(api)}` toolbar buttons, which the editor puts in a
block of their own (`data-block="addon:<name>"`) and disables while it is busy.
The panel goes in a collapsible box under the source line
(`.se-addon.se-addon-<name>`), so an add-on's CSS is scoped there.

## An add-on is a folder

Whatever else it is (a pip distribution with an entry point, a checkout of a
repository, a copy inside an app), an add-on is a **folder with a manifest
beside its package**:

```
sympy_editor_tree/            the folder: a checkout of the add-on's repository
  addon.json                  {"name": "tree", "label": ..., "module": "sympy_editor_tree",
                               "version": ..., "requires": [...], "description": ...}
  sympy_editor_tree/          the Python package the manifest names
    __init__.py               ADDON = TreeAddon()
    static/tree.js, tree.css
  pyproject.toml, README.md, tests/   (not needed at run time)
```

The manifest's optional `"bundle": false` marks a folder the apps must not
ship (the template is one: an example to copy).
An optional `"experimental": true` marks an add-on not yet checked: the
Add-ons window shows an *Experimental* badge on its card (it is on by
default like any other).

`sympy_editor.addons.scan_addons(directory)` reads every such folder under a
directory, puts the folder on `sys.path` and returns the manifests by name;
`installed()` lists them beside the entry points when the directory is in
`SYMPY_EDITOR_ADDONS` (`os.pathsep`-separated) or was passed to
`register_addons_folder()`.  A document's default catalogue is `installed()`,
so an add-on folder that is found is a click away in the Add-ons menu.

This is the layout the apps bundle, and the layout a repository cloned later
will have: **adding an add-on from GitHub will be cloning it into that
directory** - nothing else has to learn about it.  Not implemented yet (no
cloning, no updating, no version pinning); the folder format and the scan
are the part that must not change for it.

## Where the Python of an add-on runs

| backend | the add-on's Python |
|---|---|
| Jupyter widget (`edit(expr, addons=[...])`) | the kernel: whatever is installed (`pip install`, or a folder named in `SYMPY_EDITOR_ADDONS`); the Add-ons menu lists it all, `w.addon_state` is the live state |
| `serve()` | the same process |
| standalone HTML (Pyodide) | the add-on's package is written into the page beside the editor's modules (`cfg["packages"]`, from `Addon.python_sources()`), and what it `requires` is `micropip`-installed first (`cfg["micropip"]`).  The tree and plot add-ons need nothing; matching needs `sympy-matching`, pure Python since 0.0.4. |
| the mobile apps | `mobile/build.py` stages every add-on folder of `addons/` beside the app's Python (`addons/<folder>/`, manifest and package, no tests), one folder each; `sympy_editor_app.py` registers that directory at start, so every document lists them and the page switches them on and off; the app's pip step installs what the manifests `require` (Chaquopy's `pip { install(...) }` on Android - a test keeps it in step with the manifests -, `app_packages` on iOS).  The page has `rememberAddons` on: every add-on is on until the user switches it off, a switch holds for the whole app - every session, not the one open - and what is switched off is kept in the app's own storage between launches (`SympyEditorApp.keepWrite`), as is what an add-on keeps through `api.keep`. |

`Document(addons=[...])` accepts `Addon` objects, entry-point names (an
installed add-on registers under the `sympy_editor.addons` group: `tree`,
`plot`, `matching`) or module names (`sympy_editor_tree`), which is how a
Pyodide page names them again.

## The contract in one example

```python
from sympy_editor import Addon, make_op

class MyAddon(Addon):
    name = "mine"
    label = "My panel"
    ops = [make_op("twice", lambda e: 2 * e, label="Twice")]
    js = 'SympyEditor.registerAddon("mine", { mount: function (api) { ... } });'
    css = ".se-addon-mine .thing { ... }"

    def contribute(self, doc, snap, expr):
        snap["mine"] = {"terms": len(expr.args)}

    def handle(self, doc, method, payload):
        if method == "count":
            return {"n": len(doc.expr.args)}          # a query
        if method == "double":
            return 2 * doc.expr                       # a change
        raise ValueError(f"no method {method}")

ADDON = MyAddon()
```

```python
from sympy_editor import edit, save_html
edit(expr, addons=[ADDON])                  # Jupyter
save_html(expr, "page.html", addons=[ADDON])   # a Pyodide page: the add-on's package goes with it
```

To ship it, make it a package with an entry point (`addons/template/`
already has one):

```toml
[project.entry-points."sympy_editor.addons"]
mine = "my_package:ADDON"
```

Tests: `tests/test_addons.py` in the editor exercises the contract with a
small in-place add-on, and `tests/test_browser.py` drives a panel and the
Add-ons menu in Chromium.  **Every add-on has tests of its own** in its
`tests/`: unit tests of its Python (`test_<name>.py`) and browser tests of
its panel (`test_<name>_browser.py`, Playwright, skipped without Chromium).
A fix to an add-on comes with a test in that add-on's suite - the bug it
fixes, reproduced - not in the editor's.  `pytest addons/` runs them all,
`addons/tests/test_demo_page.py` included, which refuses a stale
`demo.html`.

## The drafts

**`sympy_editor_tree`** - *new interface + custom widget*.  `contribute` puts
the real argument tree in the snapshot (`snap["tree"]`, capped at 400 nodes),
and `tree.js` lays it out (each subtree as wide as its children, the parent
centred; no library) in SVG; every step of the history carries its tree too
(`contribute_step`), drawn under the step in the drawer's list and in the
report, the nodes the previous step did not have in green - how the tree
evolved.  In the panel, click selects the same piece in the formula
(argument paths and view paths agree except under fractions, where the
nearest ancestor is selected), double-click edits a leaf's value or an inner
node's head, a drag with a mouse or a pen drops a subtree under another node
(a finger scrolls the panel and never drags), `Delete` removes, the
panel's fields add an argument or wrap.  Every edit is a method (`set_head`,
`replace`, `delete`, `insert`, `wrap`, `move`) made with the editor's own path
helpers on the real `args`, so SymPy's evaluation applies and undo works.

**`sympy_editor_plot`** - *custom widget with a JavaScript library*.  One
query, `samples`: Python evaluates the node at a view path (with the sliders'
values substituted, the first free symbol on the axis, an equation as two
curves) with `lambdify` - numpy when present, `math` otherwise, a non-real
value a gap - and `plot.js` draws with Plotly.js from the CDN (from the bundle's own copy
in the apps and the web app: the page option `localAssets`), or an SVG
polyline when the CDN is out of reach.  It follows the selection, so
selecting the numerator plots the numerator.  SymPy's plotting module is not
used.

**`sympy_editor_matching`** - *new nodes + all of the above*.  A
`RewriteRule(pattern, replacement, condition)` node (kind "rule", shown as
`p → r  if c`, sides selectable), wildcards typed as `a_` / `_a_`
(`make_symbol`) and drawn underlined (`latex_printers`), a rule set per
document compiled into **one** many-to-one matcher by sympy-matching's
`build_replacer` and recompiled only when it changes, queries for the rules
matching the selection with their bindings, and *Rewrite* / *Rewrite all*
both as buttons and as ops in the Transform menu (`context=True`: they read
the document's rules).

**`sympy_editor_console`** - *a Python console beside the formula*.  Two
tabs: a console that behaves as IPython (`In [n]` / `Out[n]` typeset, `_`,
`obj?`, `%who`, `%time`, Tab completion, an unfinished block asking for the
next line) and a script editor that runs a whole file as `python file.py`
does, then leaves its names in the console (IPython's `%run`).  Both run in
the document's Python - the app's own interpreter, the server's process,
Pyodide - with SymPy imported, the formula's symbols in scope, and `editor`
to read and change the formula (`editor.expr`, `editor.selection`,
`editor["/1"]`, `editor.apply(...)`), each change a step of the history.
Nothing but the standard library: no IPython.  A run that changed the formula
answers as a query with `changed: true`, and the panel then asks for a fresh
snapshot - an add-on method answers either a query or a change, and a run is
both (its output and a new formula).

**`sympy_editor_assumptions`** - *a custom widget that edits through the
document*.  A query, `facts`, gives the main predicates of the selection
(the old assumptions first, `ask(Q.*)` where they cannot tell, on small
expressions) with every unknown explained in words and the assumption on a
symbol that would decide it; `contribute` puts the free symbols with what
was assumed of each in every snapshot; `assume` switches one assumption of
one symbol through `Document.retype` - every occurrence, one step of the
history - and keeps a hint for that step when SymPy rewrote the formula by
itself or `simplify` can now do more, which `simplify` applies.

**`sympy_editor_export`** - *a custom widget, no node of its own*.  One
query, `export`: the selection (a node, a range, or the whole formula)
written by SymPy's printers - LaTeX, MathML, Python (math, NumPy, mpmath or
SymPy source), C, Fortran, JavaScript, Octave/MATLAB, Julia, Rust - or as a
whole function by `codegen`, with the free symbols as arguments.  Printers
run with `strict=False`, so what a language lacks is in their own *Not
supported* comment; a refusal comes back as words.  Each file has Copy (the
host's clipboard, as the editor's Copy) and Save (`api.saveFile`).

**`sympy_editor_forms`** - *a simplification explorer*.  The selection (a
range, the whole formula) rewritten by each of SymPy's rewriting functions -
`simplify`, `expand`, `factor`, `apart`/`collect` per variable, `trigsimp`,
`fu`, `logcombine`/`expand_log` with an optional `force`, `rewrite(exp)`... -
one card per *different* form (equal forms grouped, the functions that gave
each listed), with its `count_ops` and its length, sorted by either.  The
panel asks for one function at a time (`run`, a query), each under a time box
(a trace function raising at the first Python call past the deadline: it works
in Pyodide, which has no threads), with a pause between them so the user's own
edits go first; a card's `apply` is an undoable step, "Forms: factor".

**`sympy_editor_numeric`** - *a custom widget that only asks*.  The Values
panel evaluates the selection: a field per free symbol (read as typed text
is, so `pi/3` or `2 + I`; no value is guessed), the precision in `evalf`
digits, an exact form beside the number when SymPy has a short one (and
`nsimplify`'s guess when asked, marked as one), `a + b i` for a complex
value.  A value that is no number says why - the innermost piece that goes
wrong is found by evaluating the node from the inside out: a division by
zero, an argument outside a function's domain, an indeterminate form.  A
table mode varies one symbol over a range or a list (at most 500 rows,
stopped after a few seconds) and copies as CSV or TSV through the host
app's clipboard when there is one.  Two queries, `evaluate` and `table`;
mpmath through SymPy, nothing else.

**`sympy_editor_series`** - *a panel that computes and inserts*.  One query,
`expand`: the selection's `series` in a variable about any point (`oo` and
`-oo` included), at an order from 1 to 20, from either side, named after its
powers (Taylor, Laurent, Puiseux, with logarithms, asymptotic), or its
leading term; with the coefficients, and the truncation error at a sample
point.  `insert` replaces the selection with the expansion, with or without
its O term, as one step.  Every computation is time-boxed in a thread that
is stopped when it overruns, and SymPy's failures are said in words.

**`sympy_editor_transforms`** - *ops + a panel that computes*.  Laplace,
Fourier, sine, cosine, Mellin and Hankel transforms and their inverses are
SymPy's, asked for their conditions; the one-sided z-transform (a table of
geometric, trigonometric and polynomial-times-geometric terms, `summation`
otherwise) and its inverse for rational functions (partial fractions) are
the add-on's own.  The panel's *Compute* is a query that shows the result
and where it holds in words ("converges for Re(s) > -2"); *Apply* replaces
the selection, unevaluated (`LaplaceTransform(f, t, s)`) when the editor's
toggle is on.  Six ops put the same in the Transform menu, asking for the
variables through the op `params`.

**`sympy_editor_solver`** - *a panel that computes and edits*.  It reads the
selection (a range of an `And`'s equations included) as an equation, an
inequality, an expression `= 0` or a system, offers its free symbols as
unknowns and a domain, and solves with `solveset`, `linsolve`,
`nonlinsolve` or `solve` as fits, inside a time limit kept by the profiler
hook (no thread: it works in Pyodide too).  Each solution can be substituted
back and checked, or inserted in place of what was solved - a step of the
history.  The last solution is kept per document and named by a token, so
nothing SymPy has to be read back from the page.

**`sympy_editor_steps`** - *a custom widget that explains*.  One query,
`steps`, works out the selection - an integral through `manualintegrate`'s
rule tree, flattened one rule at a time; a derivative with sum, product,
quotient, power and chain rules applied to one `d/dx` hole per step; an
equation of degree one or two - and the panel lists the steps with KaTeX.
`apply` puts a step's result in place of the selection, a step of the
history labelled with the rule; it works the steps out again rather than
reading them back.  What it cannot explain it says in words.

**`sympy_editor_linalg`** - *a custom widget that computes*.  For the
explicit matrix around the selection: rank, determinant, trace,
characteristic polynomial, eigenvalues with both multiplicities and their
eigenvectors, diagonalizability and the Jordan form; LU, QR, Cholesky and a
Gauss-Jordan elimination recorded one elementary row operation at a time
(checked against `Matrix.rref()`) on demand.  Every result has *Insert*,
which puts it in place of the matrix as a step of the history.  Every
computation runs under a time budget on a thread of its own and is stopped
past it, the panel saying so in words (Pyodide has no threads: the editor's
Interrupt is the limit there).

**`sympy_editor_units`** - *new nodes from SymPy itself + a panel*.  The
units and constants of `sympy.physics.units` become names in the formula
through `namespace()` - the units' own names (what `srepr` writes, so a
session reads back) and their long aliases always, the one-letter
abbreviations (`m`, `s`, `N`) only with the document's *short unit names*
switch, since they are variables far more often.  The panel shows the
selection's dimension and checks every sum, relation, exponent and function
argument under it - the terms that disagree are listed and outlined in the
formula - and converts (`convert_to`, SI base units, `quantity_simplify`),
each a step of the history.  A unit is a SymPy *atom* whose arguments are the
Symbols of its name: the document does not count those as the formula's names
(`_names_in`), or the next `meter` typed was a plain symbol.

## Open questions

These are the decisions this PR leaves open on purpose:

1. **Sessions** - settled: `Addon.export_state(doc)` / `restore_state(doc,
   data)` carry an add-on's state under `export()["addon_state"]`, so a
   session switch keeps a rule set; `Document(addon_state=...)` gives it back
   when the add-on is on.  Persistence beyond a session is the add-on's:
   the rules panel mirrors its library to its editor's keeper (`api.keep`),
   and in Jupyter the state is also Python (`w.addon_state`).
2. **Global registries.**  Kinds are per document (`doc.kinds`), so a
   switched-off add-on leaves no classification behind; rebuilders and printer
   methods for foreign classes are still process wide, which only shows when
   such a class occurs in a document that has the add-on off.
3. **Ordering of ops.**  An add-on's ops come after the built-in ones and an
   add-on cannot remove or rename one; `Document(ops=...)` still can.
4. **Mobile bundles** - settled: the apps bundle the add-on folders (see
   *Where the Python of an add-on runs*); what is not done is adding one
   at run time from a repository, which the folder format is shaped for.
5. **Keyboard.**  Add-ons get no hook into the editor's key handling; a panel
   handles keys on its own elements (the tree does).
6. **Several editors, one add-on instance** work, since state is per
   document; the front end registry is per page, shared by all editors.

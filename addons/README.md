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
  demo.py                   a page with the three, to try them in a browser
```

All three are **drafts**: they work end to end (each has tests, and the
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
   with the three drafts (no install needed, it reads them from the
   checkout), `python addons/demo.py --serve` runs them on the local server.

4. **Switch them while editing.**  The toolbar's **Add-ons ▾** menu lists
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
            snap["addons_available"] → the Add-ons ▾ menu
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
  keeps for each add-on; an `Addon` instance may serve many documents.  It is
  *not* exported with a session yet (see *Open questions*).

### The `api` a panel receives

```js
api.name, api.options        // the add-on's name, and Addon.client_options() from Python
api.state()                  // the last snapshot; api.node(path) one entry of its node table
api.selected(), api.range()  // the selection (a view path) and the range, as the editor holds them
api.select(path)             // select in the formula
api.call(method, payload)    // → Promise: the query's result, or the new snapshot for a change
api.send(msg)                // any editor message ({action: "apply", ...})
api.status(text), api.error(text)
api.h(tag, attrs, children)  // the editor's element helper; api.katex(); api.loadScript(url)
api.editor                   // the Editor itself, for what the above does not cover
```

`def.mount(api)` returns `{element, title, help, onState(snap), onSelect(path,
range), commands: {cmd: fn}, destroy()}`, all optional (`help` is HTML for the
guide behind the panel's "?", shown as the editor's own guide is - write one:
a feature that is not in it does not exist for the user); `def.tools` is a list of
`{cmd, label, title, run(api)}` toolbar buttons, which the editor puts in a
block of their own (`data-block="addon:<name>"`) and disables while it is busy.
The panel goes in a collapsible box under the source line
(`.se-addon.se-addon-<name>`), so an add-on's CSS is scoped there.

## Where the Python of an add-on runs

| backend | the add-on's Python |
|---|---|
| Jupyter widget (`edit(expr, addons=[...])`) | the kernel: whatever is installed |
| `serve()` | the same process |
| standalone HTML (Pyodide) | the add-on's package is written into the page beside the editor's modules (`cfg["packages"]`, from `Addon.python_sources()`), and what it `requires` is `micropip`-installed first (`cfg["micropip"]`).  The tree and plot add-ons need nothing; matching needs `sympy-matching`, which is pure Python. |
| the mobile apps | not covered by this draft: their bundles carry Python themselves, so an add-on would have to be packaged with the app.  The `native` config already names the add-ons (`document["addons"]`). |

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

## The three drafts

**`sympy_editor_tree`** - *new interface + custom widget*.  `contribute` puts
the real argument tree in the snapshot (`snap["tree"]`, capped at 400 nodes),
and `tree.js` lays it out (each subtree as wide as its children, the parent
centred; no library) in SVG: click selects the same piece in the formula
(argument paths and view paths agree except under fractions, where the
nearest ancestor is selected), double-click edits a leaf's value or an inner
node's head, drag drops a subtree under another node, `Delete` removes, the
panel's fields add an argument or wrap.  Every edit is a method (`set_head`,
`replace`, `delete`, `insert`, `wrap`, `move`) made with the editor's own path
helpers on the real `args`, so SymPy's evaluation applies and undo works.

**`sympy_editor_plot`** - *custom widget with a JavaScript library*.  One
query, `samples`: Python evaluates the node at a view path (with the sliders'
values substituted, the first free symbol on the axis, an equation as two
curves) with `lambdify` - numpy when present, `math` otherwise, a non-real
value a gap - and `plot.js` draws with Plotly.js from the CDN, or an SVG
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

## Open questions

These are the decisions this PR leaves open on purpose:

1. **Sessions.**  `doc.addon_state` is not part of `Document.export()`: a
   rule set does not survive switching sessions in the Pyodide page.  The
   natural fix is an `Addon.export(doc)` / `restore(doc, data)` pair carried
   under `export["addons"]`.
2. **Global registries.**  Kinds are per document (`doc.kinds`), so a
   switched-off add-on leaves no classification behind; rebuilders and printer
   methods for foreign classes are still process wide, which only shows when
   such a class occurs in a document that has the add-on off.
3. **Ordering of ops.**  An add-on's ops come after the built-in ones and an
   add-on cannot remove or rename one; `Document(ops=...)` still can.
4. **Mobile bundles.**  Out of scope here (the apps ship their own Python).
5. **Keyboard.**  Add-ons get no hook into the editor's key handling; a panel
   handles keys on its own elements (the tree does).
6. **Several editors, one add-on instance** work, since state is per
   document; the front end registry is per page, shared by all editors.

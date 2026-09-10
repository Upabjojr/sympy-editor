# Changelog

## 0.1.1 — September 2026

The first release with add-ons, and a great deal of work on how the editor
feels under a finger.

### Add-ons

The editor can be extended now, and four extensions ship with it. An add-on
is an ordinary Python package: it may add node types to the expression tree,
operations, a panel under the formula, tools on the strip, and a front end of
its own. They can be switched on and off while editing, from the top of the
drawer the **≡** button opens, and the apps remember what was left on.

* **Expression tree** — the tree beside the formula, clickable and editable,
  with drag and drop, a collapsible tree per step in the history, and a big
  tree got about with pinch, wheel and drag as the plot's picture is.
* **Plot** — the graph of the selection, sampled by Python and drawn by
  Plotly, with pinch, drag and wheel on both axes.
* **Rewrite rules** — pattern matching through `sympy-matching`, with named
  rule sets kept between sessions.
* **LaTeX** — type or paste LaTeX; every ambiguity is offered as a choice
  rather than guessed.

The web site opens with all four switched on.

### The formula

* Matrices and arrays: rows and columns added and taken away, a grip that
  *reshapes* rather than truncates, and arrows that move as the thing is
  drawn — at any rank.
* Templates: `\int`, `\sum`, `\prod`, `\lim`, `\diff`, `\frac`, `\binom`,
  `\matrix` build the whole construction with empty boxes to fill; **Tab**
  walks them.
* A refused edit says so and the formula flickers red.
* An operator selected: **↓** goes to the caret it stands for, and either side
  of it is a place of its own — which decides what a factor typed there joins.
* Selecting with a finger: a long press starts a range, a drag past the edge
  scrolls and keeps selecting, and the selection no longer blinks out or
  shifts under the finger.
* The full-screen button keeps its corner.
* The apply row is four menus in two groups, and `options={"actions": ...}`
  chooses what the action menus offer.

### Fixed

* `srepr` does not round-trip a `MatAdd` — SymPy prints an `Add`'s terms in
  display order rather than the order it holds them, so a page or an app
  rebuilding an expression got the terms in another order and edits landed on
  the wrong one. `ExactReprPrinter` writes them as they are. (See #17.)
* The web app carries `micropip`, without which a bundled add-on's
  requirements ended the boot; and an add-on's packages can no longer stop the
  editor starting.
* A page that cannot start Python says why instead of showing a spinner for
  ever — including the one case that cannot be made to work: a page opened
  from the file system cannot read the runtime vendored beside it, so it has
  to be served.
* The plot cannot eat the machine: one sampling at a time, a redraw a frame,
  fewer points while it is slow, and it stops following rather than locking up.

### Elsewhere

* Python 3.10 or later: 3.9 is no longer supported (its security support
  ended in October 2025, and the rewrite rules' `sympy-matching` never ran
  on it).
* The Android app is **SymPy Editor**, with a separate debug build
  (`org.sympy.editor.debug`) that can sit beside it.
* `docs/cursor-and-selection.md` writes down what the cursor and the selection
  do, in the page, the server, the Jupyter widget and the apps alike.

## 0.1.0

First release.

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
* The ≡ drawer is there in every editor that edits: the sessions and their
  history when they are on, the add-ons' switches always - in a notebook, a
  served page, a saved page too.  Without sessions and without add-ons the
  strip is as it was.  The Add-ons menu on the strip is gone.
* The apply row is four menus in two groups, and `options={"actions": ...}`
  chooses what the action menus offer.

### Fixed

* Editors on one page share one Python runtime, and it installed only the
  add-ons of the editor that started it: a second editor with other add-ons
  could not start them.  An editor that joins now brings its own.
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
* A selection made while an app is starting is kept.  The snapshots that
  arrive then - the session reopened, the add-ons switched on - dropped the
  range, and the operation picked next went to the whole expression instead
  of the terms selected; a long press whose formula was drawn again under
  the finger selected nothing at all.
* Edit at a caret (and typing there) opens the field on the side the caret is
  on: left of a `+` it opened after the sign, where the other caret is.
* "Computing..." and its Interrupt button stay in the middle of what is on
  screen rather than of the whole editor - on a phone, whose editor with its
  panels is taller than the screen, they sat below the fold - and stay there
  while the page scrolls.
* The apps can interrupt a long computation: the button the page has always
  offered had no way to stop the app's own Python.
* Every "?" is the same button, and an add-on's guide is laid out in the
  columns of the editor's own guide.
* LaTeX: `\sinh` is one command, not `\sin` followed by an `h` - which was
  even the preferred reading - and so for `\cosh`, `\tanh` and every command
  that begins another (`\ge` in `\geq`, `\right` in `\rightarrow`);
  `\coth`, `\sech`, `\csch` and the inverse hyperbolic functions are
  functions now, rather than `cot(h*x)` and the like.  The reading is shown
  once: each ambiguity showed the whole of it again beside its menu.
  "Replace the selected range" replaces the range, not the whole expression.
  The parsers are built as soon as the panel is shown, not at the first
  reading.

### Elsewhere

* Tutorials: `sympy_editor.tutorial` builds a page (or a fragment to embed)
  that plays a JSON script of timed steps on the editor - captions beside what
  they describe, an arrow and a pulsing ring on what is about to be pressed,
  then the press - to be watched or recorded as a video; when it is over, the
  page is the editor.  Only a page built for it plays one.
  `examples/tutorial/` is a tour of the editor built this way: editing in
  the formula itself, the History of the edits, and three add-ons.  The web
  site's front page plays it (without the History), with a button to stop it
  and use the editor; a link followed, or the page scrolled on past the
  editor, stops it too, and a button beside it plays it again from the start.
* Python 3.10 or later: 3.9 is no longer supported (its security support
  ended in October 2025, and the rewrite rules' `sympy-matching` never ran
  on it).
* The Android app is **SymPy Editor**, with a separate debug build
  (`org.sympy.editor.debug`) that can sit beside it.
* A **Mac app**: `python desktop/build.py --run` builds the editor as a macOS
  application and opens it - the same page in a window, editing in the app's
  own CPython, with nothing to install and nothing downloaded at run time.  It
  is the iOS app's shell in a window (the same Swift and Objective-C, a few
  `#if os(macOS)` branches), and its interpreter is the macOS build of the
  release the iOS app pins, which carries the standard library inside
  `Python.framework` - so the app embeds the framework and installs nothing.
  macOS 11 and later, Apple silicon and Intel.  See `desktop/README.md`.
* `docs/cursor-and-selection.md` writes down what the cursor and the selection
  do, in the page, the server, the Jupyter widget and the apps alike.

## 0.1.0

First release.

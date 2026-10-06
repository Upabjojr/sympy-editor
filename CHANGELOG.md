# Changelog

## 0.1.3 — unreleased

* **The keyboard and a screen reader get around.** Help, the history and
  the ≡ drawer are dialogs now: the focus goes in when they open, Tab stays
  inside, and the button that opened them has the focus again when they
  close (it used to stay on the page behind, where Tab wandered while the
  editor ignored keys). The four pickers name their list and the active
  row (`aria-activedescendant`), so ↑/↓ are heard, and the glyph-only
  buttons have names.

* **On a phone the formula comes first.** The tools - seven rows of them at
  a phone's width - stood between the top of the screen and the formula;
  now only the session row (undo, redo, History, ?, Done, the zoom, ≡)
  stays at the top, then the formula, the arrows, the line naming the
  selection and the source line, then the editing tools, where the thumbs
  are, and the add-ons' panels last. A wide screen is laid out as before. And the guide opens with a **Quick
  start**: the five things to know, before the full account.

* **Differentiate, integrate, solve and substitute from the Transform
  menu.** The four things a student looks for first were names to know in
  the function box; they are operations now, **Differentiate…** (by `x`,
  `x, 2`, `x, y`), **Integrate…** (over `x` or `(x, 0, 1)`), **Solve
  for…** (the unknown picked from the selection's symbols; the solutions
  come back as a set, which can be edited on) and **Substitute…**, each
  asking for what it needs, the first three building `Derivative`,
  `Integral` and `Subs` with *keep unevaluated* on. An integral's own menu
  says "Evaluate (doit)" and "Numeric (evalf)" in the general menu's
  words, where it said "Evaluate" and "Numeric value".

* **Nothing typed is lost to a request.** A character typed while Python
  was still answering - an add-on asking after a tap, a Simplify computing
  - was dropped, since the field it would open could not survive the answer
  re-drawing the formula. It is kept now (Backspace takes it back), and the
  field opens with all of it the moment the answer is in - at the caret,
  over the range or over the selection, wherever the answer left them.

* **Type mathematics as it is written.** `2x`, `3(x + 1)`, `sin x`,
  `sin^2 x`, `|x|` for an absolute value, `x = 2` for an equation and `e`
  for Euler's number are read as meant, and a bracket left open at the end
  is closed: every one of them was refused before, `x = 2` with "invalid
  syntax" (an equation could not be typed at all), and a typed `e` was a
  symbol drawn exactly like the number it was not. Multiplication is implicit
  by default now (`Document(parser="implicit")`; names stay whole, `xy` is
  one symbol - `parser="split"` splits them, `parser="strict"` is Python's
  syntax as before). A text that cannot be read is refused in words -
  `Cannot read "x+": something is missing or out of place` - instead of
  `ValueError: Could not parse 'x+': invalid syntax (<string>, line 1)`,
  every error shown has lost the Python class in front of it, and the
  refused text stays in the field, the caret at its end, for the fix.

* **Small things in the way of a first hour.** Backspace on a symbol or a
  number deletes it (the answer used to be "2 has nothing inside to keep",
  with the Unwrap button beside it greyed out). The focus comes back to the
  formula after the "Working…" overlay goes, so the keys after a slow
  Simplify are not lost. The **allow invalid** switch moved from the tool
  strip to *Settings* in the ≡ drawer: it is a setting of the document, not
  a tool for every edit. **Isolate** is now **Extract** - "isolate" reads as
  "solve for" to anyone who does algebra - and the toggle reads **keep
  unevaluated**. The glyph-only buttons (↺ ↻ ? ≡ − +) have names for a
  screen reader. In JupyterLab the editor follows Lab's own light or dark
  theme, where it used to follow the desktop's: a dark Lab on a light
  desktop had light text on white buttons. The guide no longer mentions
  the bar under the selection, gone since 0.1.2.

* **The palette: fractions, roots, integrals, sums and limits as buttons.**
  The **√ ∫ Σ ▾** button beside Paste opens what a
  mathematical formula editor offers as buttons, each drawn the way it will
  look: fraction, power, square root, absolute value, exponential,
  logarithm, factorial, binomial, integral, definite integral, derivative,
  limit, sum, product and a 2 × 2 matrix. With a caret it goes in at the
  caret with its boxes empty; with a selection - or a range of terms - the
  selection becomes its main part (``x`` and ∫ give ``∫ x d□``), built and
  not computed, so ``√4`` stays ``√4``; with nothing selected it takes the
  whole formula, and an empty formula becomes it. The first empty box is
  selected after it, ready to type into. ``Document.wrap`` also takes a
  template with ``$`` where the node goes (``Matrix([[$, _1], [_2, _3]])``).

* **No more endless "Working (plot: samples)…".** On a phone, with the plot
  or the rules panel open, the app could keep asking Python the same thing
  every half second, the overlay blinking for ever: a query there takes
  longer than the 0.4 s after which the editor shows its overlay, taking the
  overlay down redrew the selection, and every redraw told the panels the
  selection had changed - so each answer brought the next question. The
  panels now hear of the selection only when it changes, and ask nothing for
  what they already show.

* **The add-ons load without blocking the editor.** After the formula
  appeared, each panel asking Python what it needed brought up the blocking
  "Working…" overlay on a phone - four times at every start. An add-on's
  question is now answered with its answer alone, not with a copy of the
  whole formula as well (half a second each on a phone, a few hundredths
  now), and add-on work waits two and a half seconds before it blocks
  anything; the formula can be used meanwhile.

* **Tab goes on to the next box.** Tab in a field while the formula has empty
  boxes applies what was typed and selects the next box (Shift+Tab the one
  before): a fraction is filled with ``1`` Tab ``2``. It used to take the
  focus away, leaving the whole construction selected, so the ``2`` replaced
  all of it.

## 0.1.2 — September 2026

* **Handwriting in the iOS app.** The pen reads what is written on an
  iPhone or an iPad as it does on Android, on the device: ONNX Runtime is
  linked into the app (there is no wheel of it for iOS) and runs the stroke
  model for the app's Python. It is pinned to a release with no telemetry
  in it, and the build refuses a library that could reach the network.
  Picking another reader in the panel reads the ink on show again at once,
  instead of waiting for the next stroke.

* **A row under the formula, and no floating bar.** The four arrows moved
  from the tools to a row just beneath the formula, at its left; in a matrix
  the row and column buttons join them, and on a touch screen the keyboard
  button is an icon at the right end of that row, blinking when a selection
  or a caret appears to show that the keyboard can be opened for it. The bar
  that popped up under every selection is gone: Edit, Unwrap, Delete,
  Isolate, Copy and Paste are in the tools, each in one fixed place.

* **Typing in front of a term types onto that term.** With the caret after
  the plus of ``x + 1``, in front of the ``1``, a typed ``r`` joined the
  ``x`` on the other side of the plus and gave ``r*x + 1``; it now gives
  ``x + r``, the caret belonging to the term it is drawn against. Likewise a
  caret outside ``f(x, y)`` - left of the ``f``, right of the ``)`` - now
  multiplies the call instead of typing into its first or last argument.

* **Keep in green, Undo in red.** After a LaTeX or a handwritten reading is
  applied, the two answers wear the colours of the change shown above them:
  Keep the green of what came, Undo the change the red of what went - the
  red weaker, a border and its words, since it is the way back and not a
  danger.

* **Free handwriting has room at the edge.** A stroke written with nothing
  selected that reached the right edge of the formula area had nowhere to
  go on; the area now opens space past it and scrolls there, and grows
  downwards under a stroke by its bottom edge, as it already did when
  writing over a selection. The formula does not move.

* **The pen put away takes its ink.** Switching handwriting off clears the
  strokes that were not applied, instead of leaving them on the formula.

* **A saved file never runs code.** With the plot panel open, a ``.sympy``
  file whose symbols or functions had crafted names ran them as Python when
  the plot sampled the formula; the plot now renames every symbol before
  anything is evaluated and refuses names it cannot. A file holding
  ``factorial(10**8)`` or ``2**(10**9)`` no longer freezes the reader: such
  numbers are kept as written. Pages generated from Python escape an
  element id and a tutorial's options, and an add-on defined in a script no
  longer takes every other ``.py`` beside the script into the page.

* **Sessions shared by several editors.** Two pages of ``serve()``, two
  widgets of a notebook or two windows of the app keep one list of
  sessions, and each save wrote its own copy of it: the sessions the other
  had made were gone. An editor now merges what it changed into the list as
  kept. The last edit before a page is closed reaches ``serve()`` too, a
  reload no longer adds a copy of the session it reopens, and a kept list
  that is damaged no longer switches sessions off for good.

* **Keys go to what is in front.** With the guide, the history or the
  sessions drawer open, typing edited the formula behind it; toolbar buttons
  took no Space or Enter; a character typed with AltGr or Option was
  ignored over a selection; and Enter while an input method composed
  committed the field. ``Lambda`` opened in a field came back as a function
  called ``Lamda``, ``\int`` followed at once by Enter was not expanded, and
  two templates typed in one field shared their empty boxes.

* **Fixes (editor).** A file the editor refuses to open no longer leaves an
  empty session behind, and two files opened at once both open; a method
  picked from the menu while a caret is shown applies to the formula instead
  of being typed into it; the function list arriving no longer closes a field
  being typed in; a refused change of operator keeps the operator selected;
  an add-on whose panel fails is not mounted again at every change;
  ``destroy()`` stops the editor, its timers and its Python; a standalone
  page whose add-ons cannot all be installed installs the others; a script
  asked for twice is loaded once; ``Range``, set operations with symbolic
  bounds and ``BlockDiagMatrix`` are no longer refused as invalid; a history
  longer than ``max_history`` opens on the right step.

* **Fixes (Python).** ``serve()``: *Done* is no longer lost when the page
  has gone first, a malformed or deeply nested request is answered with an
  error, the Host check takes only a well-formed host, and the listening
  socket is closed after *Done*. The widget handles its messages in the
  order they came and passes every ``Document`` option on
  (``allow_invalid`` was dropped); a store with a file it cannot decode, or
  a name with a lone surrogate, answers instead of failing. One malformed
  ``addon.json`` no longer breaks every ``Document()``.

* **Nothing but an expression is committed.** ``[x, 1]`` or ``None`` typed
  as the formula, or an operation answering a list, broke the document; a
  call whose result was text - a saved symbol's ``.name`` - was read back
  as input and could run code from a file. Both are refused now.

* **Edits keep to what was asked.** ``+y`` typed into ``x*z`` gives
  ``x + y*z`` whichever side the caret is on, and ``<=`` typed at a caret
  changes the operator. With *unevaluated* on, a change of operator leaves
  the rest unevaluated (``2*3*4`` → ``2 + 3*4``). Unwrap no longer offers a
  matrix's shape, an invalid node already in the formula no longer blocks
  edits elsewhere, and *Move everything to the left* works on matrix
  equations. A name declared as an explicit ``Matrix`` no longer makes its
  session unopenable, and the Python script rebuilds unevaluated products
  and unions exactly.

* **Interrupt stops only what it was pressed for.** In the Android, iOS and
  Mac apps, an Interrupt that landed as a message ended could leave it
  listed as running, and the next press stopped whatever came after -
  usually the opening of a session, which was then listed as broken.

* **The apps stay offline, and builds ship what they list.** On iOS and the
  Mac the page is not shown if the rules that keep it off the network cannot
  be set up, and a printed report gets the same rules. A build makes
  ``vendor/`` afresh, a download is kept only when it is whole (Pyodide's
  packages checked against its lock, SymPy's wheel against its PyPI digest),
  and ``--cdn`` is refused by every build that makes an app. Files opened
  with the app are read in bounded pieces off the main thread; on the Mac a
  file from the Finder goes to the window in front; a ``file://`` link no
  longer ends the Android app. The web app deletes and reads only its own
  caches. CI's browser jobs fail rather than skip when there is no browser.

* **Add-ons: fixes.** *Plot*: a value typed beside a slider is read as
  written (``pi/2``, ``1/3``), a folded plot asks for nothing, and an older
  answer is no longer drawn over a newer one. *Expression tree*: a finger
  scrolls and never drags a node, the first double-click edits, a field
  left unchanged adds no step, and the fields act on the piece selected.
  *Rewrite rules*: the panel follows the session opened, a range is matched
  and rewritten as a range, each match of a rule has its own result, rules
  that cannot work are refused when typed, and *Rewrite all* stops when the
  expression only grows. *Console*: Use is for this namespace's outputs, a
  script no longer overwrites your names, ``%`` is a magic only where a
  statement begins, two consoles no longer read each other's output, what a
  cell shows has a bound, and completion runs nothing of yours.
  *Handwriting*: a hand resting on the screen no longer breaks the stroke,
  ink stays with the formula when zoomed, the engine chosen is the
  document's own, and a page is no longer told where the model is on the
  machine that built it. *LaTeX*: SymPy's own LaTeX reads back as it was
  (``f{\left(x \right)}``), names come out as SymPy writes them (``x_1``),
  ``\frac{\partial^2 f}{\partial x \partial y}`` is a mixed derivative,
  huge numbers no longer freeze the editor, and a reading is bounded in
  length and time.

* **File is always within reach.** The **≡** drawer - with *Open
  formula…*, *Save formula…* and the history as Python, a web page or on
  paper - used to show its button only when there were sessions or add-ons,
  so a page from a plain ``pip install`` had no way to them. Every editor
  that edits has it now.

* **The last edit survives a closed tab.** A session was saved by asking
  Python for its history, an answer a page being closed or reloaded never
  got: an edit made in the second before was lost. It is now kept at once.

* **Fixes.** In a standalone page and the web app: the add-ons are
  switched on at start without an error (the switch reached a fresh
  document before it knew its add-ons by name, and the formula flickered
  red); after an Interrupt, the "Loading Python runtime…" overlay of the
  restarted Python goes away once it is ready, instead of covering the page;
  the Interrupt button no longer vanishes when the function list arrives
  while a long call is computing; and Python no longer restarts by itself
  right after an Interrupt, covering the page for a quarter of a minute -
  it restarts for the next thing you ask. Everywhere: a session save that
  comes due during a long computation waits for it instead of being lost
  with it when it is interrupted; the *New session* chooser
  stays open while the list is refreshed in the background, and an example
  started as a new session opens as written (the quadratic formula came
  back as ``sqrt(-1*4*a*c + b**2)``).

* **Add-ons are on, for the whole app.** Every add-on starts switched on,
  and switching one off (or on again) holds for every session - opening
  another session no longer brings back the add-ons that one was saved
  with. The choice is remembered between launches, in the app's own
  storage (the server's store under ``serve()``, the browser's on a
  standalone page); an add-on new in an update starts on.

* **A Python console.** A new add-on, *Python console*, runs Python beside
  the formula in the same Python as the editor - the app's own on a phone,
  the server's, Pyodide in a standalone page. Its console works as IPython
  does (``In [n]``/``Out[n]`` typeset, ``_``, ``obj?``, ``%who``, ``%time``,
  a tap on an output copies it into the input) with a completion menu at
  the caret - after a ``.`` it lists the methods and properties of the
  object in memory, and while a name is typed it opens when only a few
  names begin that way, your own first - and its
  script tab runs a whole file at once. The transcript and the script
  are kept between launches, as text: last time's cells come back faded,
  *not run in this Python*, with **Run all again** (no Python object is
  stored). Before the console is first used, the prompt offers
  ``editor.expr``. ``editor`` reads and changes the
  formula - ``editor.selection = expand(editor.selection)`` - each change a
  step of the history.

* **Handwriting among siblings.** Ink written by one factor of a product
  or one term of a sum - a bar and a theta under ``cos(y)`` in
  ``sin(x) cos(y) tan(z)`` - is read with every factor or term as a box of
  its own, and the model says which the ink goes with, or which run of
  them under one longer bar: the page's guess no longer decides it. The
  default model is math-ocr's ``stroke_b_sib2``, trained for it (79 % of
  such readings right, the right pieces 94 % of the time), and better at
  ink around a single piece and at plain handwriting too.

* **Handwriting around a formula.** Ink written around a piece of the formula
  - a bar and an ``x`` under ``sin(x)`` for ``sin(x)/x`` - is read by a new
  model, math-ocr's ``stroke_b_ctx``, trained on ink written around printed
  pieces. It is given the piece's box rather than a triangle drawn in its
  place, and reads such ink right about twice as often as before (71.8 %
  against 39.8 % for short additions; 49 % against 27 % in general), losing
  the piece almost never (0.1 % against 20.6 %). Plain handwriting is read a
  little better too (42.2 % against 38.9 %).

* **Invalid expressions.** An edit SymPy would not build is no longer
  committed behind its back: an operator between a scalar and a matrix, a
  power or a function built unevaluated around a non-square matrix used to
  slip in, and a session holding such a step could not be reopened - the
  formula flickered red and the history never showed. Such sessions open
  now, and a new **allow invalid** switch keeps what SymPy refuses (``A*B``
  of mismatched shapes, ``sin(x, y)``) as a node of its own, drawn in red
  and written ``Invalid(MatMul, A, B)``, until an edit inside it makes it
  valid again.

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

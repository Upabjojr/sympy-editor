# The cursor and the selection

What the editor points at, how it is moved, and what happens when you type.
This is the agreed behaviour: where the four hosts differ, the difference is
written down here and nowhere else.

The rules below hold for the standalone HTML page, the HTTP server, the
Jupyter widget and the mobile apps alike — they run the same `editor.js`
against the same `Document`. Only the *input* differs (a keyboard and a mouse,
or a finger), and only where this file says so.

## 1. The four things that can be pointed at

At any moment the editor is in exactly one of five states. Everything else
follows from which one it is in.

| State | What it looks like | Field |
|---|---|---|
| **Nothing** | no highlight, the status line invites a click | — |
| **A node selected** | the sub-expression under a tinted box | `selected` |
| **A range selected** | several neighbouring arguments under one box | `range` |
| **An operator selected** | the glyph (`+`, `−`, `⋅`, `=`…) boxed, with a small palette | `junction` |
| **A caret** | a thin blinking bar between two things | `caret` |

They are mutually exclusive: selecting a node clears the caret, showing a
caret clears the selection and the operator, and so on. A node selected is the
resting state; a caret is what you get when you point *between* things.

## 2. Pointing with a mouse

| Gesture | Result |
|---|---|
| Click a sub-expression | selects it |
| Click the same spot again | selects its parent, and so on up to the whole expression; from the root, back to the glyph |
| Click an operator glyph | selects the operator (not the terms) |
| Click between two terms | a caret there |
| Click the left or right edge of an object | a caret on that side of it |
| Click a caret again | opens the field to type into, without needing the keyboard |
| Double-click a sub-expression | selects it and opens the field on it |
| Click outside the formula | clears the selection |

## 3. Pointing with a finger

Touch has no hover, no double-click and no keyboard, so three gestures differ.
Everything else is as above.

| Gesture | Result |
|---|---|
| Tap | selects, exactly like a click |
| **Tap the selected node again** | opens the field on it — this replaces double-click, which is ignored on touch |
| **Hold still on a node** (`longPress`, 450 ms by default) | selects it and starts a range: keep the finger down and drag over its neighbours. In the apps the selection is felt too (the platform's haptics), since the finger covers what it selected |
| Drag a held selection past the edge of the view | the formula scrolls itself and the range keeps growing over what comes into sight |
| Two fingers | pan and pinch-zoom; never selects |
| Android's **Back** | what Esc does, one thing per press: closes the help, the history, the drawer, the LaTeX field, the pen, an edit — then lets the selection or the caret go, then leaves full screen; with nothing left, the app goes to the background |

While a drag is drawing a range the selection box is **moved, never
rebuilt**, so it stays painted for the whole gesture, growing into its new
size over 90 ms. Nothing else on the panel may change size during a drag —
the tool strip is left alone until the finger lifts — so the formula does not
shift under the finger.

## 4. The arrow keys

The arrows mean the same thing everywhere: **↑ out, ↓ in, ← → along**.

### From a selected node

| Key | Result |
|---|---|
| ↑ | selects the parent |
| ↓ | selects the first argument — or, on an atom, puts a caret beside it |
| ← / → | the previous / next argument of the same parent |
| Shift + ← / → | grows or shrinks a range from the selection |

### From a selected operator

| Key | Result |
|---|---|
| ↑ | selects the node the operator belongs to |
| ← / → | selects the term on that side of it |
| ↓ | **a caret just to the right of the glyph** |
| `+ - * / ^ =` | changes the operator |
| Del / Backspace | removes it — the two terms then multiply |

A change of operator that is refused leaves the operator selected.

### From a range

| Key | Result |
|---|---|
| ↑ | selects the parent |
| ↓, ← or → | collapses the range back to one argument |
| Esc | drops the range |
| Enter | edits the range as one piece |
| Del / Backspace | removes those arguments |

### From a caret

| Key | Result |
|---|---|
| ← / → | the previous / next caret position in the whole formula |
| ↑ | selects the object the caret sits beside (then its ancestors) |
| ↓ | nothing, except in a grid (see §7) |
| Esc | hides the caret |
| Enter, or any character | opens the field and starts typing there |

## 5. Caret positions, and the two sides of an operator

The caret walks a single ordered list of positions covering the whole formula,
left to right, crossing into and out of nested nodes. `←`/`→` step through
that list; there is no position that the arrows cannot reach.

**An operator drawn between two arguments has a position on either side of
it.** The point before `+` and the point after `+` are two different places on
the screen, so they are two stops, and the arrows visit both. (Where nothing
is drawn between two arguments the two coincide and count as one stop.)

Which side the caret is on decides **which neighbour a bare factor typed there
joins**:

| Formula | Caret | Type `5` | Result |
|---|---|---|---|
| `x + y + z` | left of the first `+` | `5` | `5*x + y + z` |
| `x + y + z` | right of the first `+` | `5` | `x + 5*y + z` |
| `x - y` | right of the `−` | `5` | `x - 5*y` |
| `Eq(x, y)` | right of the `=` | `5` | `Eq(x, 5*y)` |

How the operator is drawn varies — `+` sits between its terms, the `−` of a
negative term is drawn inside that term, and an equation takes no new argument
at all — so ↓ from an operator takes the *nearest real caret position to the
right of the glyph* rather than computing one. That way `←`/`→` step away from
it and back to it like any other position, in every case.

### Typing at a caret splices, like a text editor

A caret between two arguments is a point in the written formula, so what is
typed is spliced in at that point:

* a bare word or number **juxtaposes** with the neighbour whose side the caret
  is on — that is multiplication;
* a leading or trailing `+` or `-` binds at the level of the sum, so `+w` adds
  a term;
* `,` makes a new argument;
* an operator alone, typed between two arguments, changes the operator.

Which side the caret is on is what it shows. A click on the edge of a term
puts it on that term. A caret with no side of its own - a click in the gap,
an arrow key - belongs to the neighbour it is drawn against: in `x + 1` the
gap holds the `+`, and a caret drawn after it, in front of the `1`, is on the
side of the `1`. So `r` typed there gives `x + r` (that is `x + r*1`), and
typed with the caret against the `x`, before the `+`, it gives `r*x + 1`.
Where nothing is drawn between the two (the factors of `x y`) the caret is on
the left one.

A node that draws something of its own around its arguments — the name and
the parentheses of `f(x, y)` — has an inside and an outside. A caret outside
it, left of the `f` or right of the `)`, is beside the call: `r` typed there
gives `r*f(x, y)`. Inside, in front of the `x`, it gives `f(r*x, y)`.

↓ from an operator puts the caret just after it, on its own line only. A
caret attached to the term on its left stands at the end of that term in the
source line (`x| + y`), and the source cursor at `x|` gives that caret. A
resize of the view redraws the caret where it was.

## 6. Ranges

A range is several *neighbouring* arguments of one parent, selected together.
It is started by Shift + ← / → from a node, or by holding a finger on a node
and dragging. It can be edited as one piece (Enter), deleted, or operated on
like a single selection. Dragging over something that is not one of the
parent's own arguments — the operator glyph between two terms, say — leaves
the range as it stands rather than collapsing it.

## 7. Matrices and arrays

Inside a matrix or an array the four arrows move **as the thing is drawn**,
not as the tree is stored: ← → along a row, ↑ ↓ between rows, for a selection
and for a caret alike. An array of any rank works the same way, because the
rule follows the drawing — a rank-3 array is a row of matrices, so → at the
right edge of one block enters the next.

At an edge the ordinary meaning takes over: ↑ in the top row selects the
matrix itself, ← / → step out of it.

In a grid, ←/→ from a cell (or from anything inside one) go to the cell
beside it in the same drawn row; at the row's end they step out of the grid,
never onto the next row.

## 8. Templates and empty slots

`\int`, `\sum`, `\prod`, `\lim`, `\diff`, `\frac`, `\binom` and `\matrix`
typed in a field build the whole construction with faint empty boxes where its
parts go. The first box is selected; **Tab** moves to the next, **Shift+Tab**
back. The boxes are the placeholder symbols `_1`, `_2`… in the source line.

Tab also moves between empty slots when there are any; with none, Tab puts a
caret beside the selection. Tab in an open field applies what was typed and
then selects the next box (Shift+Tab the one before), so a fraction is filled
with `1` Tab `2`; a field left as it was just moves on. When what was typed
holds boxes of its own (a `\frac` typed into a box), its first box comes
first.

The **palette** — the `√ ∫ Σ ▾` button beside Paste —
offers the same constructions as buttons drawn the way they look: fraction,
power, square root, absolute value, exponential, logarithm, factorial,
binomial, integral, definite integral, derivative, limit, sum, product, a
2 × 2 matrix. Where it puts one depends on the state, as for typing:

- **a caret**: the construction goes in at the caret with its boxes empty (a
  new term in a sum, a factor in a product);
- **a selection or a range**: the selection becomes the construction's main
  part — `x` selected and ∫ pressed gives `∫ x d□`; the matrix takes it as
  its first entry;
- **nothing selected**: the whole formula is the main part; an empty formula
  becomes the construction;
- **an operator selected**: the button is greyed out.

The first empty box is selected after it. Arrows walk the palette, Enter or
Space presses, Esc (and Back on Android) close it.

Each template typed gets boxes of its own: `\frac` twice in one field gives
four boxes, not two pairs with the same names, and none takes the name of a
box already in the formula. A command is expanded when the field is
committed too — `\int` followed at once by Enter builds the integral, as a
space after it would have. After a change, the first *new* box is selected,
also when the change was typed over a preview.

## 9. What the hosts share, and where they differ

The editing model — every rule above — is identical in all four. The
differences are only in what the host provides.

| | Standalone HTML page | HTTP server | Jupyter widget | Mobile app |
|---|---|---|---|---|
| Backend | Pyodide, in the page | the Python that served it | the notebook kernel (anywidget) | the app's own CPython |
| Keyboard | yes | yes | yes | on-screen only |
| Every rule in §2, §4–§8 | yes | yes | yes | yes |
| Touch rules (§3) | on a touch screen | on a touch screen | on a touch screen | always |

Nothing in the editor branches on *which* backend is running. Where behaviour
differs it is by **input**, not by host: the touch rules apply wherever the
pointer is coarse, which includes a touchscreen laptop running the Jupyter
widget, and the keyboard rules apply on a tablet with a keyboard attached.

Two pieces of interface follow the pointer rather than the host:

* the **keyboard button** appears only where the pointer is coarse: an icon
  at the right end of the row just under the formula. It opens the field for whatever
  is current: the selection, the caret, or the whole expression. When a
  selection, a range, an operator or a caret appears it blinks for a couple
  of seconds in the accent colour (not under reduced motion, where it only
  takes the colour), to say the keyboard opens there; it does not blink
  again for the same thing drawn again, nor while a field is open.
* the **arrow buttons**, at the left of the row just under the formula, do
  exactly what the arrow keys do, so everything reachable by keyboard is
  reachable by finger. In a matrix the row also holds **+ row**, **+ col**,
  **− row** and **− col**.

No bar pops up under a selection or a caret: every command has one fixed
place - the arrows in that row, Edit, Unwrap, Delete, Isolate, Copy and Paste
on the tool strip. Only the operator palette (under a selected operator) and
the chooser that asks which argument to keep appear at the selection.

## 10. Three rules that hold everywhere

**A command applies to what is pointed at.** With a node selected it applies
to that node; with a range, to those arguments; with a caret and nothing
selected, a function is *added at the caret* rather than applied to the whole
expression; with nothing at all, to the whole expression.

**Keys go to what is in front.** While the guide, the history or the
sessions drawer is open, keys are theirs: Esc closes them and nothing typed
reaches the formula behind. A focused toolbar button or check box takes its
own Space and Enter. A character typed with AltGr (Option on a Mac) is a
character like any other, and Enter while an input method is composing
finishes the composition — it never commits the field.

**A refused edit never changes the selection.** The message appears under the
formula and the formula flickers red for half a second; what was selected
stays selected.

**Scrolling and zooming never take the pointing away.** A formula scrolled -
by a finger, the wheel, the edge arrows, or by the pen bringing its space to
write in into sight - keeps its selection and its caret where they were; the
caret goes only when its place does.

A refused edit changes nothing: the caret or the selection stays, and an
expression refused in the empty view stays in its field to be corrected. The
error line goes away at the next change of selection.

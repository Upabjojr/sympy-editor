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
| **Hold still on a node** (`longPress`, 450 ms by default) | selects it and starts a range: keep the finger down and drag over its neighbours |
| Drag a held selection past the edge of the view | the formula scrolls itself and the range keeps growing over what comes into sight |
| Two fingers | pan and pinch-zoom; never selects |

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

Clicking a gap and typing is different from arrowing to a side of an operator:
a click in a gap inserts a **new argument** there, attached to neither
neighbour.

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

## 8. Templates and empty slots

`\int`, `\sum`, `\prod`, `\lim`, `\diff`, `\frac`, `\binom` and `\matrix`
typed in a field build the whole construction with faint empty boxes where its
parts go. The first box is selected; **Tab** moves to the next, **Shift+Tab**
back. The boxes are the placeholder symbols `_1`, `_2`… in the source line.

Tab also moves between empty slots when there are any; with none, Tab puts a
caret beside the selection.

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

* the **keyboard button** on the tool strip appears only where the pointer is
  coarse. It opens the field for whatever is current: the selection, the
  caret, or the whole expression.
* the **arrow buttons** on the tool strip do exactly what the arrow keys do,
  so everything reachable by keyboard is reachable by finger.

## 10. Two rules that hold everywhere

**A command applies to what is pointed at.** With a node selected it applies
to that node; with a range, to those arguments; with a caret and nothing
selected, a function is *added at the caret* rather than applied to the whole
expression; with nothing at all, to the whole expression.

**A refused edit never changes the selection.** The message appears under the
formula and the formula flickers red for half a second; what was selected
stays selected.

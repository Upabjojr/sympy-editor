# What an edit does

Every operation the editor offers, and what it leaves behind — especially in
the cases where the obvious answer is not the only one. This is the agreed
behaviour: the standalone page, the HTTP server, the Jupyter widget and the
mobile apps all run the same `editor.js` against the same `Document`, so what
is written here holds everywhere, and a change of behaviour is a change to
this file.

Its companion, [`cursor-and-selection.md`](cursor-and-selection.md), says what
the editor *points at* and how the pointing is done. This file says what
happens to the expression once something is pointed at.

The rules live in two modules: `sympy_editor/printer.py` (the tree surgery —
`replace_at`, `delete_at`, `insert_at`, `extract_range`, `replace_range`,
`view_parts`, `rebuild`) and `sympy_editor/document.py` (the operations the
page asks for, and the history they make).

## 1. What every edit has in common

* **An edit is a step.** Each one appends the new expression to the history
  with a label saying what made it (`Transform: Factor`, `Edit: sin(y) →
  cos(y)`), and anything that was redone away is dropped. The history is
  capped at `max_history` (200 by default), oldest first.
* **Ancestors are rebuilt, so SymPy evaluates.** Replacing a node rebuilds
  every node above it with `node.func(*args)`, which means SymPy's automatic
  evaluation applies: replacing `y` by `-x` in `x + y` gives `0`. This is the
  point of editing a *SymPy expression* rather than a string.
* **Nothing is evaluated that was not asked for.** With the **keep unevaluated**
  switch on, operations that would compute a result build it unevaluated
  instead (`2 + 3` stays `2 + 3`).
* **An edit that cannot be read is refused**: the message appears under the
  formula and the expression is untouched. An edit that *reads* but that
  SymPy refuses to build is refused too — unless **allow invalid** (under ≡, *Settings*) is on, and
  then it is kept (§8).
* **Only an expression is committed.** Text that reads as something else
  (`[x, 1]`, `None`), or an operation or a call that answers with one, is
  refused. A result that is text — a symbol's `.name`, `latex(...)` — is
  refused too, never read back as input: in a file opened from elsewhere
  that text could be anything.
* **Where the text is parsed matters.** A string is parsed in the context of
  what it replaces: a new name typed into a matrix slot becomes a
  `MatrixSymbol` of that shape, not a plain `Symbol`.

## 2. Replacing

Replacing the selected node with what is typed is the ordinary edit. Two
cases are worth knowing:

* **Virtual parts.** The view tree shows things the expression does not have
  as arguments: the `1` and the `n` of `1/n` (`/n`, `/d`), the product after
  the minus of `−2x` (`/neg`). They can be selected and replaced; the node is
  rebuilt around the new part.
* **A range** (several neighbouring arguments of a sum or a product) is
  replaced as one: the arguments go and what is typed takes the place of the
  first of them.

## 3. Deleting

Deleting removes the selection from its parent's arguments and rebuilds. The
cases that are not simply "one argument fewer":

| What is deleted | What is left |
| --- | --- |
| an argument of a sum or a product | the node without it (`x + y + z` → `x + z`) |
| a numerator or a denominator | `1` in its place (`a/b` minus `a` → `1/b`) |
| the product after a minus sign | the whole signed product goes |
| **the exponent of a power** | **the base alone: `x²` → `x`, `√x` → `x`** |
| the base of a power | the exponent alone, which is what is left of it |
| the exponent of `eˣ` | `e`, the base it was drawn with |
| the argument of a one-argument function | refused: `sin` is not an expression |
| a symbol or a number, with Backspace | the same as Delete: there is nothing inside it to keep |
| a range | the node without those arguments |
| the whole expression | the view empties, and the new expression is typed in its place |

The power rules are there because a power cannot be built from one side:
before, deleting an exponent raised and the work was lost.

**Unwrap** is the deliberate version of the same idea, and it keeps the
argument you name: `cos(x)` → `x`, `∫f dx` → `f`, `x²` → `x` (the base by
default). A sum, a product of several terms or a fraction has no natural one,
so unwrap asks which to keep.

**Extract** goes the other way: the selection becomes the whole expression and
everything around it is dropped.

## 4. Typing at a cursor

Text typed at a cursor between two arguments is *spliced* in, as in a text
editor, rather than being made an argument of whatever happens to be there:

* an operator typed at the junction is used as written;
* no operator means juxtaposition — a product with the neighbour the cursor
  belongs to, which is the one it is drawn against: after the `+` of
  `x + 1`, in front of the `1`, a typed `r` gives `x + r` (`x + r*1`), and
  before the `+`, against the `x`, it gives `r*x + 1`;
* `+` and `−` bind at the level of the sum: typed into a product they split
  it at the cursor, the halves as drawn (`x*z`, with `+y+` typed between,
  gives `x + y + z`), whichever side the cursor belongs to — `+y` multiplies
  `y` onto the half after it (`x + y*z`), `y+` onto the half before
  (`x*y + z`);
* an operator typed alone — `≤`, `≥`, `≠` (`<=`, `>=`, `!=`) included — changes
  the operator between the two arguments (§5); anything longer, such as `+-`,
  is spliced as text;
* `,` makes a new argument;
* a SymPy object (from an add-on, say) goes in as it is.

At the end of the formula the same rules apply with nothing on the right.

## 5. Changing an operator

Selecting the glyph between two arguments and choosing another one rewrites
the expression around it:

* in a sum, `*`, `/` and `^` bind the two terms (`x + y + z` with `*` at the
  first `+` gives `x*y + z`), and `−` negates the right one;
* in a product, `+` and `−` split it at that point (`x*y*z` at the first
  factor gives `x + y*z`), because a sum binds looser than the product it is
  typed into;
* the `−` shown before a negative term counts as the operator: `x − y` with
  `*` over it gives `x*y`;
* a relation (`=`, `<`, `>`, and `≤`, `≥`, `≠` - sent as `<=`, `>=`, `!=`)
  or a connective (`&`, `|`) needs the two arguments to be the whole
  expression;
* left and right are as drawn, not SymPy's argument order: `x² + x` with
  `/` over the `+` gives `x²/x`, and a product splits between the factors
  drawn on either side;
* deleting the operator leaves juxtaposition, a product;
* asking for the operator that is already there changes nothing;
* with **unevaluated** on, nothing around the change is evaluated either:
  `2*3*4` with `+` after the `2` gives `2 + 3*4`, not `14`.

With the pen (the handwriting add-on), ink written over a selected operator
is read as the operator that takes its place - never as a formula, and
never together with a piece of the formula; Apply changes it as the palette
would.

## 6. Matrices

An explicit matrix is edited by its shape, from any path inside it:
insert or delete the row (column) the selection is in — at the end, and the
last one, for the matrix itself; **resize** to `rows × cols` keeps what fits
from the top-left corner; **reshape** lays the same entries out in another
shape and so accepts only a shape that multiplies to the number of entries
there already are — nothing is added and nothing is lost. New entries are
empty slots (`_1`, `_2`…), and the matrix keeps its class, dense or sparse.
Deleting an entry of a sparse matrix empties the cell (`0`); an empty cell
of a sparse matrix cannot be selected on its own yet (a click selects the
matrix). Rows and columns are whole numbers, and a matrix has at most 10000
entries.

A matrix has nothing to **unwrap**: its arguments are its shape (and a
matrix symbol's its name), which cannot stand in its place, so none is
offered and Unwrap says to select an entry. **Move everything to the left**
of a matrix equation compares with a zero matrix of its shape.

## 7. Typed text and names

A text typed into a field, the source line or the empty view is read as
mathematics is written, before SymPy sees it (`friendly_source`,
`Document.parse`):

| Typed | Read as |
| --- | --- |
| `2x`, `3(x + 1)`, `(x + 1)(x - 1)`, `2 3` | products: multiplication is implicit (`parser="implicit"`, the default) |
| `sin x`, `sin^2 x` | `sin(x)`, `sin(x)**2`: a function applies to what follows it |
| `xy` | one symbol, as SymPy spells it (`parser="split"` makes it `x*y`; `parser="strict"` reads Python's syntax alone: `2x` is refused) |
| `\|x\|`, `\|x - 1\| < 2` | `Abs(x)`: a `\|` with nothing before it (or an operator) opens a pair, the next one closes it; between two operands it is still *or* |
| `x = 2`, `x == 2` | the equation `Eq(x, 2)` - one `=` outside brackets; `<=`, `>=`, `!=` and a keyword argument (`n=5`) are left alone |
| `sin(x`, `f(x, (y` | the brackets left open at the end are closed |
| `e`, `e^x` | Euler's number - the formula draws it as `e`, so a symbol of that name typed over it looked the same and was another thing; a declared or used `e` wins, and the status line says what was read |
| `` `gamma` `` | a variable, when SymPy has a function or a constant of that name |

A text SymPy cannot read is refused in words - `Cannot read "x+": something is
missing or out of place` - never with Python's class in front, and the field
keeps the text for the fix.  A name read as SymPy's own (`E`, `I`, `gamma`)
is noted in the status line, with the error when there is one.

**Declare** puts a name in scope for typed input — a `Symbol` (with
assumptions), a `MatrixSymbol` or explicit `Matrix` of a given shape, or an
undefined `Function`. **Retype** changes what a name stands for *everywhere in
the expression* at once, rebuilding the ancestors: a product of two names
becomes a `MatMul` when both become matrices. The reverse can fail (a matrix
back to a scalar under a transpose), and the error is reported as any other.
A retype is a step: undo gives the name back what it meant.  A declaration is
not a step, and holds in every step. A name declared or retyped as an
explicit `Matrix` is saved as its entries, and opens again as that name.

A field shows Greek names as letters (`theta` as θ) and sends them back as
SymPy's names, and only the names SymPy itself spells that way: `Lambda`,
`varepsilon` and `infty` stay what they are (`Lambda(x, x**2)` used to come
back as a function called `Lamda`), and a Greek name followed by `(` is a
function name, left as typed.

## 8. Expressions SymPy refuses to build

`A*B` with mismatched shapes, `sin(x, y)`, `Inverse` of a matrix that is not
square: the constructor raises. With **allow invalid** off, the edit is
refused and the expression is untouched. With it on, the node is kept as an
`Invalid` holding the head that refused and the arguments it was given —
drawn in red, printed as `Invalid(MatMul, A, B)` (which reads back), and its
arguments are its children, so they can be edited like any others. Every
rebuild tries the head again, so the node becomes the expression SymPy builds
as soon as it builds one.

With **allow invalid** off, only what the edit brings is refused: an invalid
node already in the expression (from a file, or from before the switch was
turned off) stays, the rest of the expression can be edited, and an edit
inside that node must leave it valid.

## 9. What the add-ons put in

An add-on that puts an expression into the formula — LaTeX typed in,
handwriting read — goes through the same door and obeys the same rules: the
reading takes the selection's place, the selected range's place, goes in at
the cursor, or after the whole formula as if typed there (§4). Neither
changes anything until it is applied, and what it did is shown as the history
shows a step, to keep or to take back. To replace the whole expression, select
it first.

## 10. History

Tab walks the empty slots in reading order.  A construction of the palette
put around a selection is *built*, not computed: the selection goes in as it
is, so `4` under √ stays `√4` and `x + y` in a sum's body stays `x + y`
(`Document.wrap`, unevaluated as `wrap` always is); the matrix, which no
function call places, takes the selection as its first entry the same way
(`wrap` with a template, `$` where the node goes — the node itself, never its
text read back).  Put around a range of a sum, the construction replaces the
terms of the range and the other terms stay; one that cannot stand there is
refused like any edit (a matrix added to a scalar).  Undo and redo walk the steps; going to a step in the history view makes it the
current expression (and drops what was after it on the next edit). An edit
made from Python (`doc.set(...)`) is a step like any other. The history and
everything about the session travel together: see
[`file-format.md`](file-format.md) for what a saved formula holds.

---

**When you add or change an editing rule, write it here.** The behaviour in
the code and the behaviour in this file are meant to be the same thing, and
this file is the one a reviewer reads.

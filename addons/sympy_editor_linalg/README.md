# sympy-editor-linalg

A linear algebra workbench for [sympy-editor](https://github.com/Upabjojr/sympy-editor):
a panel under the formula that studies the explicit matrix in the selection.
No dependency beyond the editor and SymPy.

```python
from sympy import Matrix
from sympy_editor import edit
edit(Matrix([[2, 1], [1, 2]]), addons=["linalg"])
```

## What the panel shows

The target is the explicit matrix around the selection: the selected
matrix, the matrix an entry belongs to, or the whole formula when nothing is
selected.  A matrix expression that can be written out (a matrix symbol of a
numeric size, the transpose of a matrix) is written out entry by entry.

* **Properties**: shape, rank, and for a square matrix the determinant, the
  trace and the characteristic polynomial in λ (factored as well when that
  is a different expression).
* **Spectrum**: every eigenvalue with its algebraic multiplicity (as a root
  of the characteristic polynomial) and its geometric one (the number of
  independent eigenvectors), the eigenvectors, whether the matrix is
  diagonalizable, and the Jordan form J with the change of basis P
  (M = P J P⁻¹).  When the matrix is diagonalizable, J and P are built from
  the eigenvectors rather than asking `jordan_form` to compute them again.
* **On demand**: LU (Pᵀ L U when rows had to be exchanged), QR, Cholesky
  (L Lᴴ for a Hermitian matrix; for a symmetric matrix with symbolic
  entries L Lᵀ, valid where it is positive definite; "not applicable"
  otherwise), and **row reduction step by step**.

Every result has an **Insert** button: the result takes the place of the
matrix in the formula as one step of the history, labelled
"Linear algebra: …", so Undo takes it back.  A factorisation is inserted as
the unevaluated product of its factors, which equals the matrix.  A result
computed for a matrix that has changed since is refused.

## Row reduction, step by step

`rref_steps(M)` is Gauss-Jordan elimination written out here, so that every
elementary row operation can be recorded: for each column, the first row at
or below the current one with a non-zero entry becomes the pivot row (a swap,
`R1 ↔ R2`), it is scaled to a leading one (`R1 ← (1/2)·R1`), and the column
is cleared in every other row (`R2 ← R2 − 3·R1`).  Each step carries the
matrix after it.  The final matrix and the pivot columns are checked against
`Matrix.rref()` and the panel says whether they agree.  An entry that cannot
be decided zero or non-zero (a symbol) is taken as non-zero, as SymPy does,
and the step that divides by it says so.

## Time limits

Symbolic linear algebra can run for ever - the eigenvectors of a symbolic
4×4, or the inverse of a matrix of cubic roots.  Each method has a time
budget (4 s for the properties, 8 s for the spectrum and the
decompositions, shared by the computations it makes); the work runs on a
thread of its own, and past the budget that thread is stopped (an exception
raised in it, as the editor's own Interrupt does) and the panel says, in
words, what was not finished, with a button to try for four times longer
(up to 120 s).  Pyodide has no threads: in a standalone page nothing is
limited and the editor's Interrupt button is the way out, which the panel
says.  A result too big to typeset (more than 600 operations) is described
instead of shown; Insert still puts all of it in.

The panel asks Python only when the target changes - another matrix, or a
new formula - so moving from one entry to another of the same matrix asks
nothing, and a folded panel asks nothing at all.

## Methods

All queries but `insert`, through `{"action": "addon", "addon": "linalg", "method": ...}`:

| method | payload | answer |
|---|---|---|
| `analyse` | `path` (view path), `limit` | `path`, `rows`, `cols`, `square`, `items` (rank, det, ...), `problems` |
| `spectrum` | `path`, `limit` | `eigen` (`value`, `alg`, `geo`, `vectors`), `diagonalizable`, `jordan` (`J`, `P`, `product`), `problems` |
| `decompose` | `path`, `kind` (`lu`/`qr`/`cholesky`), `limit` | `factors`, `relation`, `product`, `note`, `problems` |
| `rref` | `path`, `limit` | `steps` (`op`, `oplatex`, `kind`, `assumes`, the matrix), `result`, `pivots`, `check`, `assumed` |
| `insert` | `id` (of any result above), `what` (the history's label) | the new formula, committed |

Each shown result is `{id, label, latex, src}`; the ids are remembered per
document (the last 400).

## Tests

```sh
PYTHONPATH=src python -m pytest addons/sympy_editor_linalg -q
```

`tests/test_linalg.py` covers the Python (the row reduction against
`Matrix.rref()` on random matrices, the decompositions inserted and
multiplied back, the time limit stopping a runaway computation);
`tests/test_linalg_browser.py` drives the panel in Chromium (skipped without
Playwright or the KaTeX CDN).

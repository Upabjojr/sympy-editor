# sympy-editor-solver

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): a
panel under the formula that solves the selected equation, inequality or
system, checks a solution by substituting it back, and puts a solution into
the formula.

```python
from sympy_editor import edit
w = edit(Eq(x**2, 4), addons=["solver"])
```

No dependency beyond SymPy; it runs wherever the editor's Python runs - the
kernel, the local server, Pyodide, the apps.

## What it reads

The selection - the whole formula when nothing is selected:

- an **equation** `a = b`, an **inequality** (`<`, `<=`, `>`, `>=`, `!=`), or
  an **expression** alone, solved as `expression = 0`;
- a **system**: equations joined by `&` (`Eq(x + y, 3) & Eq(x - y, 1)`), a set
  or a tuple of equations, or a *range* of an `And`'s equations (selected
  together with a drag; with a finger a long press, then the drag);
- not an `Or` (select one side), a matrix equation (write its entries as a
  system), or two symbols of one name with different assumptions - each is
  refused with the reason.

The **unknowns** are the selection's free symbols, as check boxes: as many as
there are equations are ticked to begin with, `x, y, z, w, t...` first.  The
**domain** is ℂ, ℝ, the positive reals or ℤ.

## How it solves

- **One unknown**: `solveset` over the domain; in a system every part is
  solved and the sets are intersected, so mixed equations and inequalities
  work.  An inequality is always solved over ℝ (with a note).
- **Several unknowns**: `linsolve` when every equation is linear in them,
  `nonlinsolve` otherwise, `solve(..., dict=True)` when `nonlinsolve` gives
  up.  These take no domain, so the domain *filters* the solutions found (a
  note says how many were left out).  Inequalities in several unknowns are
  refused.
- **Time limit**: 8 seconds by default (`SolverAddon(timeout=...)`, at most 60
  per request), after which the panel says SymPy gave up.  The limit is kept
  by the profiler hook (`sys.setprofile`), checked every 128 calls, so it
  needs no thread or signal and works the same in Pyodide; a long C-level
  computation is only stopped when it returns.  The editor's own Interrupt
  button (after a few seconds) still works.

## What it shows

One line per solution, as KaTeX: `x = 2`, `x = 2, y = 1`.  What is not one
value is shown as SymPy has it, with words: an interval ("every value of x
in an interval"), an `ImageSet` ("infinitely many: one for every integer n" -
its `Dummy` replaced by a plain integer symbol `n`), a `ConditionSet`
("SymPy could not solve this in closed form..."), a `Complement`, an
unevaluated `Intersection`, a free unknown of a linear system ("y is free").

- **Substitute back** shows every equation with the solution substituted (as
  substituted: `(-2)^2 = 4`), and what it simplifies to: *True*, *False*, or
  the residue SymPy cannot decide.  A residue without symbols is also
  evaluated to 30 digits (a `CRootOf`).  A family is checked for its general
  member.  Checking has its own 5-second limit.
- **Insert** puts that solution in place of what was solved - `Eq(x, 2)`, an
  `And` of equations for a system, the set itself for an interval or a
  family.  **Insert the set** puts the whole solution set there (a single
  solution goes in as its equations, so that it can stand inside an `&`).
  Both are steps of the history, labelled "Solve: ...", undone with Undo.  A
  set where it cannot stand (inside an `&`) is refused with the reason.

## The methods

All through `{"action": "addon", "addon": "solver", "method": ...}`:

| method | kind | payload |
|---|---|---|
| `problem` | query | `path`, `children` - how the selection reads, its symbols, the default unknowns |
| `solve` | query | `path`, `children`, `unknowns`, `domain` (`complex`, `real`, `positive`, `integers`), `timeout` |
| `check` | query | `token`, `index` |
| `insert` | change | `token`, `index` or `whole: true`, `text` (only for the history's label) |

The last solution is kept in `doc.addon_state["solver"]` (not saved with a
session) and named by its `token`: `check` and `insert` never read SymPy text
from the page.  `insert` refuses a formula changed since it was solved.

## Tests

```sh
PYTHONPATH=src pytest addons/sympy_editor_solver     # unit tests, and the panel in Chromium (Playwright)
```

## Limitations

- No `diophantine`, `rsolve` or `dsolve`: equations in integers go through
  `solveset` over ℤ, which solves polynomials but leaves much as an
  intersection.
- The domain cannot steer `linsolve`/`nonlinsolve`; it only filters, and a
  parametric solution is kept with the condition stated.
- The time limit cannot stop a single long C call.

See `addons/README.md` in the sympy-editor repository for how add-ons work.

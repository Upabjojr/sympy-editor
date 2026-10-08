# sympy-editor-assumptions

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): what
SymPy knows about the selection, and the symbols' assumptions as switches.

```python
from sympy_editor import edit
edit(sqrt(x**2) + log(x) + log(y), addons=["assumptions"])
```

## The panel

- **The table.**  For the selection - a node, a range of terms, or the whole
  formula when nothing is selected - the main predicates: real, complex,
  imaginary, positive, negative, nonnegative, zero, nonzero, integer,
  rational, irrational, even, odd, prime, finite, infinite, algebraic,
  transcendental, commutative, each *true*, *false* or *unknown*.  SymPy's
  old assumptions (`expr.is_positive`) answer first; where they cannot tell,
  `ask(Q.positive(expr))` is tried on an expression of at most 40 nodes
  (`ASK_MAX_NODES`), and its answers are marked **ask**.  A matrix, a
  relation or another non-number says so instead.
- **Why.**  A tap on a cell gives the reason.  An unknown is said in words -
  *SymPy cannot tell whether x + 1 is positive: that depends on the value of
  x, and nothing is assumed about it.  It would be True if x was assumed
  positive.* - with a button to assume it when one symbol is enough.
- **Symbols.**  Every free symbol of the formula with its assumptions as
  chips: solid = assumed, struck through = assumed not, faint = follows from
  what is assumed.  A tap cycles assumed → assumed not → nothing.  The
  symbol changes *everywhere* in the formula, through the document's own
  `retype` - one step of the history, which Undo walks back.  An assumption
  contradicting the new one is dropped and the panel says which.
- **Hint.**  After a switch the add-on compares `simplify` before and after
  (on at most 150 nodes): when SymPy rewrote the formula by itself
  (`sqrt(x**2)` is `x` once `x` is positive) or a simplification became
  possible (`log(x) + log(y)` → `log(x*y)` once `x` is positive), it is
  shown, and **Apply** commits the simplified form.

## Messages

| method | payload | answer |
|---|---|---|
| `facts` | `path`, optional `children` (a range) | query: `{src, latex, applicable, rows: [{name, value, source, why, would}]}` |
| `assume` | `name`, `assumption`, `value` (`true` / `false` / `null`) | change: the symbol retyped (label "Assumptions: x positive") |
| `simplify` | - | change: the simplification the last hint offered |

Every snapshot carries `snap["assumptions"] = {"symbols": [{name, given,
known[, clash]}], "hint"?}`.

## Limitations

- Two different symbols with the same name (an `x`, and an `x` declared
  positive) cannot be switched here: retyping goes by name.  The panel says
  so.
- `ask` and `simplify` cannot be interrupted in a Pyodide page without losing
  the history, hence the size limits; beyond them only the old assumptions
  answer, and no hint is computed.
- Only plain `Symbol`s get switches: matrix symbols, functions and bound
  variables (a sum's index) do not.
- The suggestions try the same predicate on the symbols (one at a time, then
  all together); an assumption of another kind that would decide it is not
  looked for.

No dependency beyond sympy-editor; nothing touches the network.  Tests:
`PYTHONPATH=src python -m pytest addons/sympy_editor_assumptions`.

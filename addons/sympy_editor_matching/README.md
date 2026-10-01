# sympy-editor-matching

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): rewrite
rules written in SymPy syntax with wildcards, held in a panel under the formula,
matched **all at once** against the selection with
[sympy-matching](https://github.com/Upabjojr/sympy-matching)'s many-to-one
matcher (OmniMatch), and applied where you point.

```python
from sympy_editor import edit
w = edit(sin(x)**2 + cos(x)**2, addons=["matching"])
```

- A name ending in `_` typed anywhere in the editor is a wildcard (`a_`), and
  one wrapped in underscores (`_a_`) an optional wildcard that takes the
  identity of its slot when absent - the conventions of sympy-matching.  In
  the formula a wildcard is underlined: a solid line for one that must be
  there, a dotted line for an optional one.
- An optional wildcard is for a part that may be missing.  `x**m_ -> x**(m_ + 1)/(m_ + 1) if Ne(m_, -1)`
  takes `x**3` to `x**4/4` but not `x`, which is not a power; `x**_m_` reads
  `x` as `x**1` and gives `x**2/2`.  `_c_*x**_n_ -> _c_*x**(_n_ + 1)/(_n_ + 1) if Ne(_n_, -1)`
  works on `5*x**3`, `3*x`, `x**4` and `x` alike (with `c_` and `n_`, on the
  first only), and `_a_*x + _b_ -> -_b_/_a_` gives `-2/3` for `3*x + 2`, `0`
  for `3*x` and `-2` for `x + 2`.  Keep a wildcard required where the rule
  needs the piece: an optional one may always take its identity, so
  `sin(_a_ + b_) -> sin(_a_)*cos(b_) + cos(_a_)*sin(b_)` reads `sin(x + y)` as
  `sin(0 + (x + y))` and changes nothing, while `sin(a_ + b_)` expands it.
  (Every example here is checked by the add-on's tests.)
- The panel holds the rule set: type `sin(a_)**2 -> 1 - cos(a_)**2`, or
  `x**m_ -> x**(m_ + 1)/(m_ + 1) if Ne(m_, -1)` for a guarded rule.  The
  guard is a relation over the wildcards (`a_ > 0`, several joined with `&`
  and `|`).  What could not work is refused when it is typed, with the
  reason: a wildcard of the replacement or the guard that the pattern does
  not have (`x -> x + b_`: nothing binds `b_`), a `Q.…` predicate as a guard
  (a statement, which no match makes true), a SymPy function by itself as a
  side (`beta`; `` `beta` `` in backticks is a symbol of that name).
- Select a piece of the formula: the panel lists the rules that match it,
  with what each wildcard bound, and a button applies the one you pick - a
  rule that matches in several ways (`a_ + b_` takes `x + y` both ways round)
  is listed once for each, with its own result.  Several terms of a sum or
  factors of a product selected together are a selection like any other:
  the panel matches and rewrites those, and `sin(a_)**2 + cos(a_)**2 -> 1`
  takes its two terms out of a longer sum.
  *Rewrite* makes one pass over the selection, outermost first, replacing every
  piece a rule matches and leaving what a rule produced alone (`x -> x**2` on
  `x + sin(x)` gives `x**2 + sin(x**2)`, once - the *ReplaceAll* of term
  rewriting); *Rewrite all* repeats the pass until nothing matches
  (*ReplaceRepeated*; rules that match their own results never settle: after
  50 passes, or as soon as the expression has outgrown 2000 nodes and four
  times its size - rules that feed each other double it at every pass - it
  is refused, with a message, and nothing changes).  A rewrite that would
  change nothing says so and is no step of the history.
- A rule set is saved by itself: type a name in the field and the set joins a
  library of named sets under it, every change saved from then on; load a set
  from the menu, delete the current one.  Typing another name saves a copy
  under it; *Rename* moves the saved set to a new name instead; neither
  writes over another saved set (the name is refused).  *Revert* goes back to the rules as
  they were when the set was named, loaded or restored last, and *Restore*
  brings back what Revert discarded.  The
  library and the current set are kept where the editor keeps its sessions
  (`api.keep`): the app's own storage on Android, iOS and the Mac, the
  server's store under `serve()`, the kernel's in Jupyter, and the browser's
  only on a standalone page - so they are there again after a reload, and a
  set is saved with the editor's sessions too: each session has its rules,
  which the panel shows when the session is opened, the library is one for
  all of them, and a session with no rules of its own starts with the set in
  use last.  A rule is kept as the `srepr` of its parts beside its text
  (`{"text", "pattern", "replacement"[, "condition"]}`), which is read back -
  never run - as the very rule; the text alone, which earlier versions
  kept, is still read.  In Jupyter the same
  state is Python, live: `w.addon_state["matching"]["rules"]` is the list of
  `Rule` objects, `["library"]` the named sets, `["name"]` the current name;
  `MatchingAddon(rules=[...])` starts a document with a set.
- A rule can be changed: the pencil (or a double-click on it) turns it into
  its text form to edit in place (Enter or leaving the field saves, Esc
  leaves the rule as it was), and ↗ opens it in the formula editor as a
  `Rule(...)` node - edit its sides there like any formula, then *Save as
  rule N* puts it back over the same entry.
- A rule is also a node: `Rule(sin(a_)**2, 1 - cos(a_)**2)` typed in the
  editor is shown as `sin²(a) → 1 − cos²(a)`, its sides are selectable and
  editable like anything else, and *Use selection as rule* puts the selected
  rule in the set.  The type menu on a rule offers to swap its sides.

The rules matter to sympy-matching's design in one way: they are compiled into
one matcher when they change, and every query walks that matcher once, so a
rule set of thousands is as quick to ask as one of three.

See `addons/README.md` in the sympy-editor repository for how add-ons work.

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
  identity of its slot when absent - the conventions of sympy-matching.
- The panel holds the rule set: type `sin(a_)**2 -> 1 - cos(a_)**2`, or
  `x**m_ -> x**(m_ + 1)/(m_ + 1) if Ne(m_, -1)` for a guarded rule.
- Select a piece of the formula: the panel lists the rules that match it,
  with what each wildcard bound, and a button applies the one you pick.
  *Rewrite* makes one pass over the selection, outermost first, replacing every
  piece a rule matches and leaving what a rule produced alone (`x -> x**2` on
  `x + sin(x)` gives `x**2 + sin(x**2)`, once - the *ReplaceAll* of term
  rewriting); *Rewrite all* repeats the pass until nothing matches
  (*ReplaceRepeated*; a rule that matches its own result never settles: after
  50 passes it is refused, with a message, and nothing changes).
- A rule set has a name and is kept: type a name and press *Save* and the set
  joins a library of named sets, to load again from the menu or delete.  A
  named set saves itself at every change; *Revert* goes back to the rules as
  they were when the set was saved, loaded or restored last, and *Restore*
  brings back what Revert discarded.  The
  library and the current set are kept in the browser's storage, so they are
  there again after a reload - in a page, in the apps and in JupyterLab
  alike - and a set is saved with the editor's sessions.  In Jupyter the same
  state is Python, live: `w.addon_state["matching"]["rules"]` is the list of
  `Rule` objects, `["library"]` the named sets, `["name"]` the current name;
  `MatchingAddon(rules=[...])` starts a document with a set.
- A rule can be changed: the pencil (or a double-click on it) turns it into
  its text form to edit in place, and ↗ opens it in the formula editor as a
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

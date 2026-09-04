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
  *Rewrite* applies the first rule that matches at the selection or inside it;
  *Rewrite all* repeats until nothing matches.
- A rule is also a node: `Rule(sin(a_)**2, 1 - cos(a_)**2)` typed in the
  editor is shown as `sin²(a) → 1 − cos²(a)`, its sides are selectable and
  editable like anything else, and *Use as rule* puts the selected rule in the
  set.  The type menu on a rule offers to swap its sides.

The rules matter to sympy-matching's design in one way: they are compiled into
one matcher when they change, and every query walks that matcher once, so a
rule set of thousands is as quick to ask as one of three.

See `addons/README.md` in the sympy-editor repository for how add-ons work.

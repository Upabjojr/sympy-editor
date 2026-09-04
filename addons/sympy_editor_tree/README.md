# sympy-editor-tree

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): the
expression as the tree SymPy holds it - `x + y*z` is `Add(x, Mul(y, z))` - drawn
as a graph under the formula, and editable there.

```python
from sympy_editor import edit
w = edit(x + y*z, addons=["tree"])          # or addons=["sympy_editor_tree"]
```

- A click on a node selects the same piece in the formula (and the other way
  round).
- A double-click on a node edits it: a new value for a leaf, a new head for an
  inner node (`Mul` over `Add`'s arguments turns the sum into a product).
- Drag a subtree onto another node to make it that node's last argument.
- `Delete` removes the focused node; the panel's field adds an argument to the
  selected node, and *Wrap* puts it inside a function.

Every change is sent to Python as a method of the add-on
(`{"action": "addon", "addon": "tree", "method": "move", ...}`) and made on the
real `args` tree, so SymPy's evaluation applies as it does for any edit - moving
`y` into `Add(x, ...)` gives `x + y`, and the editor's undo takes it back.

See `addons/README.md` in the sympy-editor repository for how add-ons work.

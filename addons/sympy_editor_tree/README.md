# sympy-editor-tree

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): the
expression as the tree SymPy holds it - `x + y*z` is `Add(x, Mul(y, z))` - drawn
as a graph under the formula, and editable there.

```python
from sympy_editor import edit
w = edit(x + y*z, addons=["tree"])          # or addons=["sympy_editor_tree"]
```

- A click on a node selects the same piece in the formula (a selection in the
  formula marks the node in the tree) and shows a bar of quick actions under
  it: edit, delete, wrap, add an argument, and ⋯ for everything else.
- A double-click on a node edits it: a new value for a leaf, a new head for an
  inner node (`Mul` over `Add`'s arguments turns the sum into a product).
- A right-click on a node - or the **Node ▾** button for the selected one -
  opens its menu: edit, delete, wrap, add an argument, then the editor's own
  *Transform* entries for the node's kind and the *Methods* of its class,
  applied through the editor, so a method with parameters asks for them as
  usual.
- While the add-on is on, every step of the history - the drawer's list and
  the History view, saved web page included - carries the tree of its
  expression in a collapsible box, the nodes the previous step did not have
  in green: how the tree evolved, step by step.  A click on a box's heading
  folds or unfolds it; *Expand trees* / *Collapse trees* do all at once.
- Drag a subtree onto another node to make it that node's last argument.
  While dragging, the node under the pointer lights up green where the drop
  may land and red where it may not: a node its parent needs (the `x` of
  `sin(x)`, the base of a power) cannot be taken out, a leaf takes no
  argument, nothing goes into itself.  Python checks the same before
  changing anything.
- A transformation that is not allowed is refused: the error shows in the
  editor's line and the panel flickers red for half a second.
- `Delete` removes the focused node; the panel's fields add an argument to the
  selected node or wrap it in a function; the *Head* menu changes its head.

Every change is sent to Python as a method of the add-on
(`{"action": "addon", "addon": "tree", "method": "move", ...}`) and made on the
real `args` tree, so SymPy's evaluation applies as it does for any edit - moving
`y` into `Add(x, ...)` gives `x + y`, and the editor's undo takes it back.

See `addons/README.md` in the sympy-editor repository for how add-ons work.

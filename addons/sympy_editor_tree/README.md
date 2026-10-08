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
  it: edit, delete, wrap, add an argument, and ⋯ for everything else.  The
  two trees are not the same: a node the formula has no piece for (the `-1`
  under a fraction) selects the nearest piece around it, and a piece of the
  formula the tree has no node for (the `2` of `x - 2*y`, which is part of a
  `-2` here) marks the node around it - `Mul`, the `-2*y` - which is then
  what the panel's fields act on.
- A double-click on a node edits it: a new value for a leaf, a new head for an
  inner node (`Mul` over `Add`'s arguments turns the sum into a product).
  `Enter` or a click elsewhere applies what was typed, `Esc` gives it up; a
  field left as it was changes nothing and adds no step to the history.  The
  panel tells a double click, or a double tap, by itself - a second click
  soon after the first, where the first was - because the first click may
  move the panel (the formula makes room for its selection's tools) and the
  browser's `dblclick` then lands beside the node, or on another one.
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
- With a mouse or a pen, drag a subtree onto another node to make it that
  node's last argument.  While dragging, the node under the pointer lights up
  green where the drop may land and red where it may not: a node its parent
  needs (the `x` of `sin(x)`, the base of a power) cannot be taken out, a leaf
  takes no argument, nothing goes into itself.  Python checks the same before
  changing anything.  A finger does not drag: it scrolls the tree, from a
  node as from empty space.  A drag the browser cancels (`pointercancel`) is
  given up, never dropped.
- A big tree is got about the way the plot's picture is: two fingers pinch to
  magnify it and push it along, `ctrl` and the wheel - a pinch on a trackpad -
  do the same with a mouse, and one finger, or a drag from empty space, scrolls
  it.  A double-click on empty space puts it back to life size.  Magnified, the
  tree pans inside the panel rather than growing it, and it keeps the
  magnification across edits.
- A transformation that is not allowed is refused: the error shows in the
  editor's line and the panel flickers red for half a second.
- `Delete` removes the focused node; the panel's fields add an argument to the
  selected node or wrap it in a function; the *Head* menu changes its head.

Every change is sent to Python as a method of the add-on
(`{"action": "addon", "addon": "tree", "method": "move", ...}`) and made on the
real `args` tree, so SymPy's evaluation applies as it does for any edit - moving
`y` into `Add(x, ...)` gives `x + y`, and the editor's undo takes it back.
A path (`path`, `from`, `to`) is a list of argument indices, `[1, 0]` for
`expr.args[1].args[0]`, or the same as text (`"1/0"`); none is the root.
Every method reads it the same way and refuses, before changing anything, one
that is not whole numbers from zero up (`-1`, `1.5`, `true`) or that leads to
no node; `index`, where `insert` and `move` take one, is a whole number too,
and one beyond the end means the end.

See `addons/README.md` in the sympy-editor repository for how add-ons work.

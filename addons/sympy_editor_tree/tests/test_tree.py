"""The tree add-on: the tree in every snapshot, and the edits it makes."""
import sys
from pathlib import Path

import pytest
from sympy import Add, Mul, cos, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # run from a checkout without installing

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import build_config  # noqa: E402
from sympy_editor_tree import ADDON, tree_of  # noqa: E402

x, y, z = symbols("x y z")


def test_the_tree_travels_with_the_snapshot():
    doc = Document(x + y * z, addons=[ADDON])
    snap = doc.snapshot()
    assert snap["addons"] == ["tree"]
    tree = snap["tree"]
    assert tree["head"] == "Add" and tree["view"] == "/"
    heads = sorted(c["head"] for c in tree["children"])
    assert heads == ["Mul", "Symbol"]
    mul = [c for c in tree["children"] if c["head"] == "Mul"][0]
    assert [c["label"] for c in mul["children"]] == ["y", "z"]
    assert mul["view"] in snap["nodes"]           # the same node in the formula


def test_the_factors_of_a_fraction_map_to_its_pieces():
    doc = Document(1 / (x * y), addons=[ADDON])
    snap = doc.snapshot()
    # The printer shows a fraction: Pow(x, -1) is not drawn as such, but x
    # is - as a factor of the denominator - and the tree points there.
    tree = snap["tree"]
    assert tree["view"] == "/"
    views = {c["src"]: c["view"] for c in tree["children"]}
    assert views == {"1/x": "/d/0", "1/y": "/d/1"}
    assert doc.get("/d/0") == x
    # the exponent -1 has no piece of its own: nothing to point at
    pow_x = [c for c in tree["children"] if c["src"] == "1/x"][0]
    assert [g["view"] for g in pow_x["children"]] == ["/d/0", None]


def test_the_numerator_and_the_denominator_of_the_demo_expression():
    doc = Document(cos(x) ** 2 + sin(x) ** 2 / x, addons=[ADDON])
    tree = doc.snapshot()["tree"]
    frac = [c for c in tree["children"] if c["head"] == "Mul"][0]
    views = {c["src"]: c["view"] for c in frac["children"]}
    assert views == {"sin(x)**2": frac["view"] + "/n", "1/x": frac["view"] + "/d"}
    assert doc.get(views["sin(x)**2"]) == sin(x) ** 2 and doc.get(views["1/x"]) == x


def test_too_big():
    assert tree_of(Add(*symbols("a0:50")), max_nodes=10)["too_big"] == 11


def _call(doc, method, **payload):
    snap = doc.handle(dict(payload, action="addon", addon="tree", method=method))
    assert not snap["error"], snap["error"]
    return snap


def test_set_head_and_history_label():
    doc = Document(x + y * z, addons=[ADDON])
    mul = [i for i, a in enumerate(doc.expr.args) if isinstance(a, Mul)][0]
    snap = _call(doc, "set_head", path=[mul], head="Add")
    assert doc.expr == x + y + z
    assert snap["addon"] == {"name": "tree", "method": "set_head"}
    assert doc.history_labels()["actions"][-1].startswith("Tree: /")
    doc.undo()
    assert doc.expr == x + y * z


def test_replace_delete_insert_wrap():
    doc = Document(x + y * z, addons=[ADDON])
    args = list(doc.expr.args)
    mul = args.index(y * z)
    _call(doc, "replace", path=[mul, 0], src="2")
    assert doc.expr == x + 2 * z
    _call(doc, "delete", path=[list(doc.expr.args).index(x)])
    assert doc.expr == 2 * z
    _call(doc, "insert", path=[], src="y")
    assert doc.expr == 2 * y * z
    _call(doc, "wrap", path=[], head="sin")
    assert doc.expr == sin(2 * y * z)
    _call(doc, "wrap", path=[0], head="cos")
    assert doc.expr == sin(cos(2 * y * z))


def test_move_a_subtree():
    doc = Document(x + y * z, addons=[ADDON])
    args = list(doc.expr.args)
    xi, mi = args.index(x), args.index(y * z)
    _call(doc, "move", **{"from": [xi], "to": [mi]})
    assert doc.expr == x * y * z
    snap = doc.handle({"action": "addon", "addon": "tree", "method": "move", "from": [], "to": [0]})
    assert "root" in snap["query"]["error"]
    snap = doc.handle({"action": "addon", "addon": "tree", "method": "move", "from": [0], "to": [0]})
    assert "into itself" in snap["query"]["error"]


def test_a_move_out_of_a_pair_lands_where_it_was_dropped():
    # Taking a term out of a sum of two leaves the other term alone in its
    # place: the destination must still be the node it was dropped on.
    from sympy import Function
    f = Function("f")
    doc = Document(x + f(y), addons=[ADDON])
    args = list(doc.expr.args)
    _call(doc, "move", **{"from": [args.index(x)], "to": [args.index(f(y))]})
    assert doc.expr == f(y, x)
    doc = Document(x + y * f(z), addons=[ADDON])
    mi = list(doc.expr.args).index(y * f(z))
    m = doc.expr.args[mi]
    _call(doc, "move", **{"from": [mi, list(m.args).index(y)], "to": [mi, list(m.args).index(f(z))]})
    assert doc.expr == x + f(z, y)
    # into its own parent: it stays a term, at the index asked for
    doc = Document(x + y, addons=[ADDON])
    _call(doc, "move", **{"from": [0], "to": []})
    assert doc.expr == x + y
    doc = Document(f(x, y, z), addons=[ADDON])
    _call(doc, "move", **{"from": [0], "to": [], "index": 2})
    assert doc.expr == f(y, x, z)


def test_the_page_carries_the_addon():
    # `available=[]`: this page carries the tree add-on and nothing else,
    # whatever else happens to be installed beside it in this Python - the
    # list micropip is given is the page's, not the machine's.
    doc = Document(x + y, addons=["sympy_editor_tree"], available=[])
    cfg = build_config(doc)
    assert [a["name"] for a in cfg["addons"]] == ["tree"]
    assert "registerAddon(\"tree\"" in cfg["addons"][0]["js"]
    assert cfg["document"]["addons"] == ["sympy_editor_tree"]
    assert {"__init__.py", "static/tree.js", "static/tree.css"} <= set(cfg["packages"]["sympy_editor_tree"])
    assert cfg["micropip"] == []


def test_every_step_of_the_history_carries_its_tree():
    doc = Document(x + y, addons=[ADDON])
    doc.replace("/", "x*y")
    steps = doc.history_labels()["steps"]
    assert [s["tree"]["head"] for s in steps] == ["Add", "Mul"]
    assert steps[1]["tree"]["view"] == "/" and "nodes" in steps[1]


def test_removable_says_what_can_leave_its_parent():
    doc = Document(sin(x) + y * z, addons=[ADDON])
    tree = doc.snapshot()["tree"]
    assert tree["removable"] is False                                 # the root
    by_src = {c["src"]: c for c in tree["children"]}
    assert by_src["sin(x)"]["removable"] and by_src["y*z"]["removable"]       # terms of a sum
    assert by_src["sin(x)"]["children"][0]["removable"] is False              # the x of sin(x)
    assert all(g["removable"] for g in by_src["y*z"]["children"])            # factors of a product
    # the server refuses the same, with words
    path = by_src["sin(x)"]["path"] + [0]
    snap = doc.handle({"action": "addon", "addon": "tree", "method": "delete", "path": path})
    assert "cannot be taken out of sin(x)" in snap["query"]["error"] and doc.expr == sin(x) + y * z
    snap = doc.handle({"action": "addon", "addon": "tree", "method": "move", "from": path, "to": by_src["y*z"]["path"]})
    assert "cannot be taken out of sin(x)" in snap["query"]["error"] and doc.expr == sin(x) + y * z


def _refused(doc, method, **payload):
    """The error a method answers with, the document left as it was."""
    before, steps = doc.expr, len(doc.history_labels()["actions"])
    snap = doc.handle(dict(payload, action="addon", addon="tree", method=method))
    assert doc.expr == before and len(doc.history_labels()["actions"]) == steps
    return (snap.get("query") or {}).get("error") or ""


@pytest.mark.parametrize("bad", [[-1], [1.5], [True], [1, -1], [1, 0.5], [7], [1, 2], [0, 0], ["x"], [None], [[0]], "1/-1", "1/x", 5, {"0": 1}])
@pytest.mark.parametrize("method, payload", [
    ("set_head", {"head": "Mul"}), ("replace", {"src": "2"}), ("delete", {}), ("insert", {"src": "2"}), ("wrap", {"head": "sin"}),
])
def test_every_method_refuses_a_path_that_names_no_node(method, payload, bad):
    """Each method read its path with ``int()`` and left the rest to whatever
    it called next, so they disagreed: ``delete`` took ``[-1]`` and removed
    the last argument, ``replace`` took ``[1.5]`` for ``[1]`` and ``[True]``
    too, while ``replace`` refused the ``[-1]`` that ``delete`` accepted.  A
    path is whole numbers, zero or more, that lead to a node - for all of
    them, and said before anything changes."""
    from sympy import Function
    doc = Document(x + y * z + Function("f")(x, y), addons=[ADDON])
    error = _refused(doc, method, path=bad, **payload)
    assert error.startswith("ValueError: Not an argument path"), error


def test_a_move_refuses_the_same_paths_at_either_end():
    """``move`` reads two paths, and ``to: [1.2]`` was taken for ``[1]``:
    both ends are checked as every other path is."""
    from sympy import Function
    doc = Document(x + y * z + Function("f")(x, y), addons=[ADDON])
    for bad in ([-1], [1.5], [True], [7], [1, 2], "1/x"):
        assert "Not a path to move from" in _refused(doc, "move", **{"from": bad, "to": [1]}), bad
        assert "Not a path to move to" in _refused(doc, "move", **{"from": [0], "to": bad}), bad
    # and the place among the arguments is a whole number too
    for method, payload in (("move", {"from": [0], "to": [1]}), ("insert", {"path": [1], "src": "2"})):
        for bad in (1.5, True, -1, "x"):
            assert "Not a place among the arguments" in _refused(doc, method, index=bad, **payload), (method, bad)


def test_the_paths_the_panel_sends_are_read_as_before():
    """What was accepted with reason still is: a list of ints, the same as
    text ("1/0"), no path at all for the root, a whole number written 1.0,
    and an index beyond the end for the end."""
    from sympy import Function
    f = Function("f")
    doc = Document(x + f(y, z), addons=[ADDON])
    at = list(doc.expr.args).index(f(y, z))
    _call(doc, "replace", path="%d/0" % at, src="2")
    assert doc.expr == x + f(2, z)
    _call(doc, "replace", path=[float(at), 1.0], src="3")
    assert doc.expr == x + f(2, 3)
    _call(doc, "insert", path=[at], src="y", index=99)
    assert doc.expr == x + f(2, 3, y)
    _call(doc, "insert", path=[at], src="z", index=0)
    assert doc.expr == x + f(z, 2, 3, y)
    _call(doc, "wrap", head="sin")
    assert doc.expr == sin(x + f(z, 2, 3, y))

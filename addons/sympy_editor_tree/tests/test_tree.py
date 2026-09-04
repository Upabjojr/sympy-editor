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
    assert "root" in snap["error"]
    snap = doc.handle({"action": "addon", "addon": "tree", "method": "move", "from": [0], "to": [0]})
    assert "into itself" in snap["error"]


def test_the_page_carries_the_addon():
    doc = Document(x + y, addons=["sympy_editor_tree"])
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
    assert "cannot be taken out of sin(x)" in snap["error"] and doc.expr == sin(x) + y * z
    snap = doc.handle({"action": "addon", "addon": "tree", "method": "move", "from": path, "to": by_src["y*z"]["path"]})
    assert "cannot be taken out of sin(x)" in snap["error"] and doc.expr == sin(x) + y * z

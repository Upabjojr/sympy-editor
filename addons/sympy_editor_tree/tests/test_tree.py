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


def test_nodes_under_a_fraction_have_no_view_path():
    doc = Document(1 / (x * y), addons=[ADDON])
    snap = doc.snapshot()
    # The printer shows a fraction: the Pow(x, -1) of the real tree is not
    # what is drawn, so it has no view path; the root has.
    tree = snap["tree"]
    assert tree["view"] == "/"
    assert any(c["view"] is None for c in tree["children"])


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

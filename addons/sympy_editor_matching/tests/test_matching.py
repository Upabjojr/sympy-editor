"""The matching add-on: wildcards typed, rules held, matched all at once."""
import sys
from pathlib import Path

import pytest
from sympy import Ne, cos, sin, srepr, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("sympy_matching")

from sympy_editor import Document  # noqa: E402
from sympy_editor.ops import node_kind  # noqa: E402
from sympy_editor_matching import ADDON, MatchingAddon, RewriteRule, parse_rule_text  # noqa: E402
from sympy_matching import WildSymbol  # noqa: E402

x, y = symbols("x y")


def _q(doc, method, **payload):
    snap = doc.handle(dict(payload, action="addon", addon="matching", method=method))
    assert not snap["error"], snap["error"]
    return snap["query"]["result"] if "query" in snap else snap


def test_a_typed_name_ending_in_underscore_is_a_wildcard():
    doc = Document(sin(x), addons=[ADDON])
    doc.replace("/0", "a_")
    w = doc.expr.args[0]
    assert isinstance(w, WildSymbol) and not w.is_optional
    doc.replace("/0", "_b_ + x")
    opt = [a for a in doc.expr.args[0].free_symbols if isinstance(a, WildSymbol)][0]
    assert opt.is_optional
    # The snapshot prints and the wildcard is drawn as one
    snap = doc.snapshot()
    assert r"\underline{b}" in snap["latex"]
    # ...and the srepr round-trips through the add-on's namespace.  Not as
    # an equal object: sympy-matching numbers every WildSymbol it makes, so
    # two of one name never compare equal - matching goes by the name.
    again = Document(srepr(doc.expr), addons=[ADDON])
    assert str(again.expr) == str(doc.expr)
    back = [a for a in again.expr.args[0].free_symbols if isinstance(a, WildSymbol)][0]
    assert back.is_optional and back.wildcard_name == opt.wildcard_name


def test_a_rule_is_a_node_with_its_own_kind():
    doc = Document("Rule(sin(a_)**2, 1 - cos(a_)**2)", addons=[ADDON])
    rule = doc.expr
    assert isinstance(rule, RewriteRule) and node_kind(rule, doc.kinds) == "rule"
    snap = doc.snapshot()
    assert r"\rightarrow" in snap["latex"] and "/0" in snap["nodes"] and "/1" in snap["nodes"]
    assert "rule_swap" in [op["name"] for op in snap["ops"] if op["kinds"] == ["rule"]]
    doc.apply("/", "rule_swap")
    assert doc.expr.pattern == 1 - cos(rule.pattern.args[0].args[0]) ** 2
    assert str(doc.expr).startswith("Rule(")


def test_rules_are_matched_all_at_once_and_applied_where_pointed():
    doc = Document(sin(x) ** 2 + y, addons=[ADDON])
    assert _q(doc, "rules")["rules"] == []
    res = _q(doc, "add_rule", src="sin(a_)**2 -> 1 - cos(a_)**2")
    _q(doc, "add_rule", src="x**m_ -> x**(m_ + 1)/(m_ + 1) if Ne(m_, -1)")
    assert [r["index"] for r in _q(doc, "rules")["rules"]] == [0, 1]
    path = [p for p, n in doc.snapshot()["nodes"].items() if n["src"] == "sin(x)**2"][0]
    hits = _q(doc, "matches", path=path)["matches"]
    assert len(hits) == 1 and hits[0]["index"] == 0 and hits[0]["bindings"] == {"a": "x"}
    assert hits[0]["result"] == "1 - cos(x)**2"
    assert _q(doc, "matches", path="/")["matches"] == []          # the root is a sum: no rule at its root
    snap = _q(doc, "rewrite", path=path, index=0)
    assert doc.expr == 1 - cos(x) ** 2 + y
    assert snap["addon"] == {"name": "matching", "method": "rewrite"}
    assert doc.history_labels()["actions"][-1] == "Rewrite: rule 1"
    doc.undo()
    assert doc.expr == sin(x) ** 2 + y


def test_the_guard_is_honoured_and_the_transform_menu_rewrites_inside():
    addon = MatchingAddon(rules=[(x ** WildSymbol("m_"), x ** (WildSymbol("m_") + 1) / (WildSymbol("m_") + 1), Ne(WildSymbol("m_"), -1))])
    doc = Document(1 / x + x ** 3, addons=[addon])
    doc.apply("/", "rewrite")                       # the op looks inside: x**3 is the piece that matches
    assert doc.expr == 1 / x + x ** 4 / 4
    doc.apply("/", "rewrite")                       # 1/x is x**-1: the guard refuses it, x**4 matches again
    assert doc.expr == 1 / x + x ** 5 / 20
    doc.apply("/", "rewrite_all")
    assert doc.expr.has(x ** 103) or doc.last_note
    n = len(doc.snapshot()["ops"])
    assert n > 3


def test_use_the_selected_rule_and_remove_it():
    doc = Document("Rule(sin(a_)**2, 1 - cos(a_)**2)", addons=[ADDON])
    res = _q(doc, "use_selection", path="/")
    assert len(res["rules"]) == 1 and res["rules"][0]["src"].startswith("Rule(")
    assert _q(doc, "remove_rule", index=0)["rules"] == []
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "remove_rule", "index": 5})
    assert "No rule" in snap["error"]


def test_rule_text():
    doc = Document(x, addons=[ADDON])
    r = parse_rule_text("a_ + b_ -> b_ + a_ if Ne(a_, b_)", doc.parse)
    assert isinstance(r, RewriteRule) and r.condition != True  # noqa: E712
    with pytest.raises(ValueError):
        parse_rule_text("no arrow here", doc.parse)


def test_rules_can_be_edited_in_place_and_through_the_editor():
    doc = Document(sin(x) ** 2, addons=[ADDON])
    _q(doc, "add_rule", src="sin(a_)**2 -> 1 - cos(a_)**2")
    rules = _q(doc, "rules")["rules"]
    assert rules[0]["text"] == "sin(a_)**2 -> 1 - cos(a_)**2"
    # as text: the field shows the text form, and what is typed replaces the rule
    res = _q(doc, "update_rule", index=0, src="sin(a_)**2 -> (1 - cos(2*a_))/2")
    assert res["rules"][0]["text"] == "sin(a_)**2 -> 1/2 - cos(2*a_)/2"
    hits = _q(doc, "matches", path="/")["matches"]
    assert hits[0]["result"] == "1/2 - cos(2*x)/2"
    res = _q(doc, "update_rule", index=0, src="sin(a_)**2 -> 1/2 - cos(2*a_)/2 if Ne(a_, 0)")
    assert res["rules"][0]["text"].endswith(" if Ne(a_, 0)")     # a guard survives the text form
    # structurally: the rule becomes the expression, edited there, saved back
    snap = _q(doc, "open_rule", index=0)
    assert isinstance(doc.expr, RewriteRule) and doc.can_undo and snap["addon"]["method"] == "open_rule"
    doc.replace("/1", "1 - cos(a_)**2")                 # the replacement side, in the formula
    res = _q(doc, "update_rule", index=0, path="/")
    assert res["rules"][0]["text"] == "sin(a_)**2 -> 1 - cos(a_)**2 if Ne(a_, 0)"
    doc.undo(); doc.undo()
    assert doc.expr == sin(x) ** 2
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "update_rule", "index": 3, "src": "a_ -> a_"})
    assert "No rule 4" in snap["error"]
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "update_rule", "index": 0, "path": "/"})
    assert "not a rule" in snap["error"]


def test_rewrite_is_one_pass_over_every_match():
    """x -> x**2 replaces every x once - and does not feed on its result."""
    doc = Document(x + sin(x) + y, addons=[MatchingAddon(rules=[(x, x ** 2)])])
    doc.apply("/", "rewrite")
    assert doc.expr == x ** 2 + sin(x ** 2) + y
    doc.apply("/", "rewrite")
    assert doc.expr == x ** 4 + sin(x ** 4) + y
    doc.apply("/", "rewrite_all")
    assert doc.last_note and "passes" in doc.last_note      # it never settles: said so
    doc.undo(); doc.undo(); doc.undo()
    # the panel's Rewrite does the same one pass at the selection
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "rewrite", "path": "/"})
    assert not snap["error"] and doc.expr == x ** 2 + sin(x ** 2) + y
    assert doc.history_labels()["actions"][-1] == "Rewrite: one pass"


def test_the_panel_gets_latex_katex_can_draw():
    """sympy.latex writes a wildcard as ``_b_{}``, which KaTeX refuses; the
    panel's LaTeX comes from the editor's printer, wildcards underlined."""
    doc = Document(x, addons=[ADDON])
    res = _q(doc, "add_rule", src="_b_ * x -> z")
    tex = res["rules"][0]["latex"]
    assert r"\underline{b}" in tex and "_b_" not in tex and r"\rightarrow" in tex

"""The matching add-on: wildcards typed, rules held, matched all at once."""
import sys
import time
from pathlib import Path

import pytest
from sympy import Ne, Symbol, cos, sin, srepr, symbols, tan

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("sympy_matching")

from sympy_editor import Document  # noqa: E402
from sympy_editor.ops import node_kind  # noqa: E402
from sympy_editor_matching import ADDON, MatchingAddon, RewriteRule, parse_rule_text, rule_state, rule_text  # noqa: E402
from sympy_matching import WildSymbol  # noqa: E402

x, y, z = symbols("x y z")


def _q(doc, method, **payload):
    snap = doc.handle(dict(payload, action="addon", addon="matching", method=method))
    assert not snap["error"], snap["error"]
    return snap["query"]["result"] if "query" in snap else snap


def _refused(doc, method, **payload):
    """The message a method refuses with - in the answer to the panel, the
    editor's own error line left alone - with nothing committed."""
    steps = len(doc.export()["history"])
    snap = doc.handle(dict(payload, action="addon", addon="matching", method=method))
    assert not snap["error"], snap["error"]
    assert len(doc.export()["history"]) == steps
    return snap["query"]["error"]


def _kept(*texts):
    """Rules as they are kept: the srepr of their parts, with the text."""
    doc = Document(x, addons=[MatchingAddon()])
    return [rule_state(parse_rule_text(text, doc.parse)) for text in texts]


def test_a_typed_name_ending_in_underscore_is_a_wildcard():
    doc = Document(sin(x), addons=[ADDON])
    doc.replace("/0", "a_")
    w = doc.expr.args[0]
    assert isinstance(w, WildSymbol) and not w.is_optional
    doc.replace("/0", "_b_ + x")
    opt = [a for a in doc.expr.args[0].free_symbols if isinstance(a, WildSymbol)][0]
    assert opt.is_optional
    # The snapshot prints and the wildcards are drawn as such: the plain one
    # with a solid underline, the optional one with a dotted underline
    snap = doc.snapshot()
    assert r"\underset{\raisebox{0.35em}{\scriptsize\ldots}}{b}" in snap["latex"] and r"\left[" not in snap["latex"]
    doc.replace("/0", "a_ + _b_")
    tex = doc.snapshot()["latex"]
    assert r"\underline{a}" in tex and r"\ldots}}{b}" in tex
    doc.undo()                                            # back to _b_ + x for the round trip below
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


def test_a_rule_index_given_as_text_is_described_too():
    # handle() takes "0" as rule 0 (int()); the history label must as well
    doc = Document(sin(x) ** 2 + y, addons=[MatchingAddon()])
    _q(doc, "add_rule", src="sin(a_)**2 -> 1 - cos(a_)**2")
    path = [p for p, n in doc.snapshot()["nodes"].items() if n["src"] == "sin(x)**2"][0]
    _q(doc, "rewrite", path=path, index="0")
    assert doc.expr == 1 - cos(x) ** 2 + y
    assert doc.history_labels()["actions"][-1] == "Rewrite: rule 1"


def test_the_guard_is_honoured_and_the_transform_menu_rewrites_inside():
    addon = MatchingAddon(rules=[(x ** WildSymbol("m_"), x ** (WildSymbol("m_") + 1) / (WildSymbol("m_") + 1), Ne(WildSymbol("m_"), -1))])
    doc = Document(1 / x + x ** 3, addons=[addon])
    doc.apply("/", "rewrite")                       # the op looks inside: x**3 is the piece that matches
    assert doc.expr == 1 / x + x ** 4 / 4
    doc.apply("/", "rewrite")                       # 1/x is x**-1: the guard refuses it, x**4 matches again
    assert doc.expr == 1 / x + x ** 5 / 20
    with pytest.raises(ValueError, match="did not settle"):
        doc.apply("/", "rewrite_all")                 # it never settles: refused, nothing changed
    assert doc.expr == 1 / x + x ** 5 / 20
    names = {op["name"] for op in doc.snapshot()["ops"]}
    assert {"rewrite", "rewrite_all"} <= names          # the Transform menu offers both


def test_the_guide_s_wildcard_examples_hold():
    """The examples of required and optional wildcards in the panel's guide
    and the README, as they are written there."""
    def at_root(rule, expr):
        doc = Document(expr, addons=[MatchingAddon()])
        _q(doc, "add_rule", src=rule)
        hits = _q(doc, "matches", path="/")["matches"]
        return hits[0]["result"] if hits else None

    assert at_root("sin(a_)**2 + cos(a_)**2 -> 1", sin(x + 1) ** 2 + cos(x + 1) ** 2) == "1"
    assert at_root("sin(a_)**2 + cos(a_)**2 -> 1", sin(x) ** 2 + cos(y) ** 2) is None
    required, optional = "x**m_ -> x**(m_ + 1)/(m_ + 1) if Ne(m_, -1)", "x**_m_ -> x**(_m_ + 1)/(_m_ + 1) if Ne(_m_, -1)"
    assert [at_root(required, e) for e in (x**3, x, 1 / x)] == ["x**4/4", None, None]
    assert [at_root(optional, e) for e in (x**3, x, 1 / x)] == ["x**4/4", "x**2/2", None]
    power = "_c_*x**_n_ -> _c_*x**(_n_ + 1)/(_n_ + 1) if Ne(_n_, -1)"
    assert [at_root(power, e) for e in (5 * x**3, 3 * x, x**4, x, 1 / x)] == ["5*x**4/4", "3*x**2/2", "x**5/5", "x**2/2", None]
    assert [at_root("c_*x**n_ -> c_*x**(n_ + 1)/(n_ + 1) if Ne(n_, -1)", e) for e in (5 * x**3, 3 * x, x**4, x)] == ["5*x**4/4", None, None, None]
    assert [at_root("_a_*x + _b_ -> -_b_/_a_", e) for e in (3 * x + 2, 3 * x, x + 2)] == ["-2/3", "0", "-2"]
    assert [at_root("a_*x + b_ -> -b_/a_", e) for e in (3 * x + 2, 3 * x, x + 2)] == ["-2/3", None, None]
    # an optional wildcard may always take its identity: this rule changes nothing
    assert at_root("sin(_a_ + b_) -> sin(_a_)*cos(b_) + cos(_a_)*sin(b_)", sin(x + y)) == "sin(x + y)"
    assert at_root("sin(a_ + b_) -> sin(a_)*cos(b_) + cos(a_)*sin(b_)", sin(x + y)) == "sin(x)*cos(y) + sin(y)*cos(x)"
    assert at_root("sin(a_ + b_) -> sin(a_)*cos(b_) + cos(a_)*sin(b_)", sin(x)) is None


def test_use_the_selected_rule_and_remove_it():
    doc = Document("Rule(sin(a_)**2, 1 - cos(a_)**2)", addons=[ADDON])
    res = _q(doc, "use_selection", path="/")
    assert len(res["rules"]) == 1 and res["rules"][0]["src"].startswith("Rule(")
    assert _q(doc, "remove_rule", index=0)["rules"] == []
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "remove_rule", "index": 5})
    assert "No rule" in snap["query"]["error"]


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
    assert "No rule 4" in snap["query"]["error"]
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "update_rule", "index": 0, "path": "/"})
    assert "not a rule" in snap["query"]["error"]


def test_rewrite_is_one_pass_over_every_match():
    """x -> x**2 replaces every x once - and does not feed on its result."""
    doc = Document(x + sin(x) + y, addons=[MatchingAddon(rules=[(x, x ** 2)])])
    doc.apply("/", "rewrite")
    assert doc.expr == x ** 2 + sin(x ** 2) + y
    doc.apply("/", "rewrite")
    assert doc.expr == x ** 4 + sin(x ** 4) + y
    snap = doc.handle({"action": "apply", "path": "/", "op": "rewrite_all"})
    assert "did not settle" in snap["error"] and doc.expr == x ** 4 + sin(x ** 4) + y   # refused, untouched
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "rewrite", "path": "/", "all": True})
    assert "did not settle" in snap["query"]["error"] and doc.expr == x ** 4 + sin(x ** 4) + y
    doc.undo(); doc.undo()
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
    assert r"\ldots}}{b}" in tex and "_b_" not in tex and r"\left[" not in tex and r"\rightarrow" in tex


def test_named_rule_sets_and_the_library():
    doc = Document(x, addons=[ADDON])
    _q(doc, "add_rule", src="sin(a_)**2 -> 1 - cos(a_)**2")
    res = _q(doc, "save_ruleset", name="trig")
    assert res["name"] == "trig" and res["library"] == ["trig"]
    one = {"text": "sin(a_)**2 -> 1 - cos(a_)**2", "pattern": "Pow(sin(WildSymbol('a_')), Integer(2))",
           "replacement": "Add(Integer(1), Mul(Integer(-1), Pow(cos(WildSymbol('a_')), Integer(2))))"}
    assert res["state"] == {"name": "trig", "rules": [one], "library": {"trig": [one]}}
    # a named set saves itself at every change: to keep "trig" as it is, the
    # new set gets its name first, then its rules
    res = _q(doc, "save_ruleset", name="square")
    assert res["library"] == ["square", "trig"] and res["name"] == "square"
    _q(doc, "remove_rule", index=0)
    _q(doc, "add_rule", src="x -> x**2")
    assert [rule_text(r) for r in doc.addon_state["matching"]["library"]["square"]] == ["x -> x**2"]
    assert [rule_text(r) for r in doc.addon_state["matching"]["library"]["trig"]] == ["sin(a_)**2 -> 1 - cos(a_)**2"]
    res = _q(doc, "load_ruleset", name="trig")
    assert res["name"] == "trig" and [r["text"] for r in res["rules"]] == ["sin(a_)**2 -> 1 - cos(a_)**2"]
    res = _q(doc, "delete_ruleset", name="trig")
    assert res["library"] == ["square"] and res["name"] is None
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "load_ruleset", "name": "trig"})
    assert "No rule set" in snap["query"]["error"]
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "save_ruleset", "name": "  "})
    assert "needs a name" in snap["query"]["error"]
    # in Jupyter the same state is Python: the widget's addon_state
    assert [str(r) for r in doc.addon_state["matching"]["rules"]] == ["Rule(sin(a_)**2, 1 - cos(a_)**2)"]
    assert list(doc.addon_state["matching"]["library"]) == ["square"]


def test_rules_travel_with_a_session_and_come_back_from_the_browsers_storage():
    doc = Document(sin(x) ** 2, addons=[ADDON])
    _q(doc, "add_rule", src="sin(a_)**2 -> 1 - cos(a_)**2 if Ne(a_, 0)")
    _q(doc, "save_ruleset", name="trig")
    state = doc.export()
    assert state["addon_state"]["matching"]["name"] == "trig"
    # a session opened again: the rules are parsed back, wildcards included
    again = Document(x, addons=[ADDON], **state)
    rules = again.addon_state["matching"]["rules"]
    assert len(rules) == 1 and isinstance(rules[0].pattern.args[0].args[0], WildSymbol)
    assert again.addon_state["matching"]["name"] == "trig" and list(again.addon_state["matching"]["library"]) == ["trig"]
    # the keeper's copy at mount: its library joins, its current set fills an empty document
    fresh = Document(x, addons=[ADDON])
    res = _q(fresh, "restore", state={"name": "trig", "rules": ["x -> x**2"], "library": {"trig": ["sin(a_)**2 -> 1 - cos(a_)**2"], "bad": ["no arrow"]}})
    assert [r["text"] for r in res["rules"]] == ["x -> x**2"] and res["name"] == "trig"
    assert res["library"] == ["bad", "trig"] and fresh.addon_state["matching"]["library"]["bad"] == []
    # ...but never over rules the document already has
    res = _q(fresh, "restore", state={"rules": ["y -> y**3"], "library": {"trig": ["a_ -> a_"]}})
    assert [r["text"] for r in res["rules"]] == ["x -> x**2"]
    assert [rule_text(r) for r in fresh.addon_state["matching"]["library"]["trig"]] == ["sin(a_)**2 -> 1 - cos(a_)**2"]


def test_a_named_set_saves_itself_and_revert_restore_step_back():
    doc = Document(x, addons=[ADDON])
    _q(doc, "add_rule", src="sin(a_)**2 -> 1 - cos(a_)**2")
    res = _q(doc, "save_ruleset", name="trig")
    assert res["dirty"] is False and res["can_restore"] is False
    # a change saves itself into the library under the set's name
    res = _q(doc, "add_rule", src="x -> x**2")
    assert res["dirty"] is True
    assert [rule_text(r) for r in doc.addon_state["matching"]["library"]["trig"]] == ["sin(a_)**2 -> 1 - cos(a_)**2", "x -> x**2"]
    assert res["state"]["library"]["trig"] == _kept("sin(a_)**2 -> 1 - cos(a_)**2", "x -> x**2")     # what the keeper keeps
    # Revert: back to the saved rules, the library follows; Restore: the change again
    res = _q(doc, "revert")
    assert [r["text"] for r in res["rules"]] == ["sin(a_)**2 -> 1 - cos(a_)**2"] and res["dirty"] is False and res["can_restore"] is True
    assert [rule_text(r) for r in doc.addon_state["matching"]["library"]["trig"]] == ["sin(a_)**2 -> 1 - cos(a_)**2"]
    res = _q(doc, "restore_reverted")
    assert [r["text"] for r in res["rules"]] == ["sin(a_)**2 -> 1 - cos(a_)**2", "x -> x**2"] and res["can_restore"] is False
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "restore_reverted"})
    assert "Nothing to restore" in snap["query"]["error"]
    # Save again: the checkpoint moves, nothing to revert
    res = _q(doc, "save_ruleset", name="trig")
    assert res["dirty"] is False
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "revert"})
    assert "Nothing to revert" in snap["query"]["error"]
    # an unnamed set has a checkpoint too (the rules at mount), and saves into no library
    fresh = Document(x, addons=[ADDON])
    res = _q(fresh, "add_rule", src="y -> y**2")
    assert res["dirty"] is True and res["library"] == []
    res = _q(fresh, "revert")
    assert res["rules"] == [] and res["can_restore"] is True


def test_the_name_field_is_the_saving():
    doc = Document(x, addons=[ADDON])
    _q(doc, "add_rule", src="sin(a_)**2 -> 1 - cos(a_)**2")
    res = _q(doc, "name_ruleset", name=" trig ")
    assert res["name"] == "trig" and res["library"] == ["trig"] and res["dirty"] is False
    _q(doc, "add_rule", src="x -> x**2")                          # saved by itself
    assert len(doc.addon_state["matching"]["library"]["trig"]) == 2
    res = _q(doc, "name_ruleset", name="")                        # unnamed again: the library keeps trig
    assert res["name"] is None and res["library"] == ["trig"]
    _q(doc, "add_rule", src="y -> y**3")                          # no longer saved into trig
    assert len(doc.addon_state["matching"]["library"]["trig"]) == 2


def test_saved_rules_are_read_never_run(tmp_path):
    """A rule set comes back from a file or a kept session (addon_state):
    it is read like the rest of the file - never run - and a rule that
    would run code is dropped, not executed."""
    from sympy import symbols

    from sympy_editor import Document
    from sympy_editor_matching import MatchingAddon

    x = symbols("x")
    marker = tmp_path / "ran"
    evil = f"__import__('pathlib').Path({str(marker)!r}).write_text('x') -> 1"
    doc = Document(x, addons=[MatchingAddon()],
                   addon_state={"matching": {"rules": ["sin(a_)**2 -> 1 - cos(a_)**2", evil]}})
    rules = doc.addons["matching"].rules(doc)
    assert not marker.exists()
    assert [str(r.pattern) for r in rules] == ["sin(a_)**2"]


def test_a_saved_set_can_be_renamed():
    """Typing another name saves a copy; rename moves the saved set: the old
    name leaves the library, the rules and the current set go along, and a
    name another set has is refused, both sets left as they were."""
    doc = Document(x, addons=[ADDON])
    _q(doc, "add_rule", src="sin(a_)**2 -> 1 - cos(a_)**2")
    _q(doc, "save_ruleset", name="trig")
    res = _q(doc, "rename_ruleset", name="identities")
    assert res["name"] == "identities" and res["library"] == ["identities"]
    assert res["state"]["library"] == {"identities": _kept("sin(a_)**2 -> 1 - cos(a_)**2")}
    assert not res["dirty"]                              # nothing of the rules changed
    # another set, then renaming it over the first one: refused
    _q(doc, "save_ruleset", name="square")
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "rename_ruleset", "name": "identities"})
    assert "saved already" in (snap["error"] or snap["query"]["error"])
    assert sorted(doc.addon_state["matching"]["library"]) == ["identities", "square"]
    # a set that is not the current one, by its old name
    res = _q(doc, "rename_ruleset", old="identities", name="trig")
    assert res["library"] == ["square", "trig"] and res["name"] == "square"
    # an unnamed set has nothing to rename
    _q(doc, "name_ruleset", name="")
    snap = doc.handle({"action": "addon", "addon": "matching", "method": "rename_ruleset", "name": "x"})
    assert "Only a saved rule set" in (snap["error"] or snap["query"]["error"])


def test_every_snapshot_says_which_rule_set_it_is():
    """The panel asked for the rules once, at mount, and went on showing
    them whatever document came after - a session opened has rules of its
    own, or none.  Every snapshot now carries a stamp (the document, how many
    rules, the set's name), and the answers to the panel the same one, so
    that a snapshot that is not of the document shown can be told."""
    doc = Document(sin(x), addons=[MatchingAddon()])
    # which document it is is made when the panel first asks: a page built
    # twice from one expression is the same page (the web app names its
    # cache by it), and a stamp with no document differs from any given out
    assert doc.snapshot()["matching"] == {"doc": None, "rules": 0, "name": None}
    assert Document(sin(x), addons=[MatchingAddon()]).snapshot()["matching"]["doc"] is None
    res = _q(doc, "add_rule", src="sin(a_) -> cos(a_)")
    first = res["stamp"]
    assert first["doc"] and (first["rules"], first["name"]) == (1, None)
    assert doc.snapshot()["matching"] == first
    res = _q(doc, "name_ruleset", name="trig")
    assert res["stamp"] == {"doc": first["doc"], "rules": 1, "name": "trig"}
    snap = doc.handle({"action": "replace", "path": "/", "src": "cos(x)"})      # an edit: the same stamp rides along
    assert snap["matching"] == res["stamp"]
    assert doc.preview("x + 1")["matching"] == res["stamp"]
    # another document - the session the editor opens - is told apart, with
    # the same rules too
    again = Document(x, addons=[MatchingAddon()], **doc.export())
    other = again.snapshot()["matching"]
    assert other["doc"] != first["doc"] and (other["rules"], other["name"]) == (1, "trig")
    # ... and so is this document once a file has brought its own rules into it
    snap = doc.handle({"action": "openfile", "text": again.save_text()})
    assert not snap["error"] and snap["matching"]["doc"] != first["doc"]
    # which document it is is not a thing to keep: the stamp is no part of what is saved
    assert sorted(doc.export()["addon_state"]["matching"]) == ["library", "name", "rules"]


def test_a_selected_range_is_what_is_matched_and_rewritten():
    """The panel sent the selection's path and never the range: with two
    terms of three selected, Rewrite rewrote all three, and a rule over two
    terms of a longer sum could not be applied at all.  ``children`` names
    the range, as in the editor's own messages."""
    doc = Document(sin(x) + sin(y) + sin(z), addons=[MatchingAddon()])
    _q(doc, "add_rule", src="sin(a_) -> cos(a_)")
    two = [i for i, arg in enumerate(doc.expr.args) if arg in (sin(x), sin(y))]
    res = _q(doc, "matches", path="/", children=two)
    assert res["src"] == "sin(x) + sin(y)" and res["children"] == two
    _q(doc, "rewrite", path="/", children=two)
    assert doc.expr == cos(x) + cos(y) + sin(z)
    doc.undo()
    _q(doc, "rewrite", path="/", children=two, all=True)
    assert doc.expr == cos(x) + cos(y) + sin(z)
    # a rule over two terms, in a sum of three: it matches the range at its root
    doc = Document(sin(x) ** 2 + cos(x) ** 2 + x, addons=[MatchingAddon()])
    _q(doc, "add_rule", src="sin(a_)**2 + cos(a_)**2 -> 1")
    assert _q(doc, "matches", path="/")["matches"] == []
    two = [i for i, arg in enumerate(doc.expr.args) if arg != x]
    hits = _q(doc, "matches", path="/", children=two)["matches"]
    assert [(h["index"], h["bindings"], h["result"]) for h in hits] == [(0, {"a": "x"}, "1")]
    _q(doc, "rewrite", path="/", children=two, index=0, bindings=hits[0]["bindings"])
    assert doc.expr == x + 1
    assert doc.history_labels()["actions"][-1] == "Rewrite: rule 1"
    assert "range" in _refused(doc, "rewrite", path="/", children=[])


def test_a_wildcard_the_pattern_does_not_have_is_refused():
    """``x -> x + b_`` was taken, and Rewrite put the wildcard itself in the
    formula (``x + b_ + 1``, a WildSymbol in the document); a condition over
    a wildcard nothing binds held whatever was matched.  Refused wherever a
    rule comes in, naming the wildcard."""
    doc = Document(x + 1, addons=[MatchingAddon()])
    message = _refused(doc, "add_rule", src="x -> x + b_")
    assert "b_" in message and "replacement" in message and "pattern" in message
    message = _refused(doc, "add_rule", src="a_**2 -> a_ if _c_ > 0")
    assert "_c_" in message and "condition" in message
    assert _q(doc, "rules")["rules"] == []
    _q(doc, "add_rule", src="x -> x + 1")
    assert "b_" in _refused(doc, "update_rule", index=0, src="x -> b_")
    doc.replace("/", "Rule(sin(a_), cos(b_))")
    assert "b_" in _refused(doc, "use_selection", path="/")
    assert "b_" in _refused(doc, "update_rule", index=0, path="/")
    assert [r["text"] for r in _q(doc, "rules")["rules"]] == ["x -> x + 1"]
    with pytest.raises(ValueError, match="b_"):
        MatchingAddon(rules=[(x, x + WildSymbol("b_"))])
    # an optional wildcard and the plain one of the same letter are two
    assert "a_" in _refused(doc, "add_rule", src="_a_*x -> a_")
    # a rule that got into the list all the same (it is a plain list, in
    # Jupyter) is not applied: no wildcard goes into the formula
    doc = Document(x + 1, addons=[MatchingAddon()])
    doc.addons["matching"].rules(doc).append(RewriteRule(x, x + WildSymbol("b_")))
    assert "Rule 1 cannot be applied" in _refused(doc, "rewrite", path="/")
    hit = _q(doc, "matches", path="/0" if doc.get("/0") == x else "/1")["matches"][0]
    assert "b_" in hit["error"] and "result" not in hit
    assert doc.expr == x + 1 and not doc.expr.atoms(WildSymbol)


def test_each_way_a_rule_matches_has_its_own_result():
    """``a_ + b_ -> a_ - b_`` takes ``x + y`` both ways round: the two were
    listed with the result of the first, and Apply on either applied the
    first."""
    doc = Document(x + y, addons=[MatchingAddon()])
    _q(doc, "add_rule", src="a_ + b_ -> a_ - b_")
    hits = _q(doc, "matches", path="/")["matches"]
    assert sorted((h["bindings"]["a"], h["bindings"]["b"], h["result"]) for h in hits) == \
        [("x", "y", "x - y"), ("y", "x", "-x + y")]
    for hit in hits:
        _q(doc, "rewrite", path="/", index=0, bindings=hit["bindings"])
        assert str(doc.expr) == hit["result"]
        doc.undo()
    message = _refused(doc, "rewrite", path="/", index=0, bindings={"a": "x", "b": "z"})
    assert "No rule matches" in message and doc.expr == x + y


@pytest.mark.parametrize("kept", [
    {"library": {"a": 5}}, {"library": [1]}, {"library": {"": None, "b": "x -> y"}}, {"rules": 5}, {"rules": {"a": 1}},
    {"rules": [5, None, ["x"], {"pattern": 3}, {"pattern": "Symbol('x')", "replacement": "nonsense("}], "name": 5},
    [1, 2], "abc", 7, None,
])
def test_kept_state_of_any_shape_opens(kept):
    """A library whose set was a number, a list where the library belongs,
    rules that were no list: each raised from ``restore_state``, so the
    session or the file holding it could not be opened at all - and from
    ``restore``, so the panel showed no rule while Python had them."""
    doc = Document(y, addons=[MatchingAddon()], addon_state={"matching": kept})
    state = doc.addon_state["matching"]
    assert state["rules"] == [] and state["name"] is None
    assert all(rules == [] for rules in state["library"].values())
    from sympy_editor.server import load_session
    base = Document(y, addons=[MatchingAddon()])
    session = base.export()
    session["addon_state"] = {"matching": kept}
    assert load_session(base, session).expr == y
    # the keeper's copy, which the panel hands over as it found it
    doc = Document(y, addons=[MatchingAddon(rules=[(sin(x), cos(x))])])
    res = _q(doc, "restore", state=kept)
    assert [r["text"] for r in res["rules"]] == ["sin(x) -> cos(x)"]


def test_what_is_right_in_kept_state_is_taken_beside_what_is_wrong():
    doc = Document(y, addons=[MatchingAddon()])
    good = _kept("sin(a_) -> cos(a_)")
    res = _q(doc, "restore", state={"rules": [5, "x -> y", None] + good, "name": " trig ",
                                    "library": {"good": good + ["junk"], "bad": 5, "old": ["x -> x**2"]}})
    assert [r["text"] for r in res["rules"]] == ["x -> y", "sin(a_) -> cos(a_)"] and res["name"] == "trig"
    assert res["library"] == ["good", "old"]
    assert [rule_text(r) for r in doc.addon_state["matching"]["library"]["good"]] == ["sin(a_) -> cos(a_)"]


#: Rules of every sort the field takes, for the round trip.
CORPUS = [
    "sin(a_)**2 -> 1 - cos(a_)**2", "sin(a_)**2 + cos(a_)**2 -> 1", "x**m_ -> x**(m_ + 1)/(m_ + 1) if Ne(m_, -1)",
    "_c_*x**_n_ -> _c_*x**(_n_ + 1)/(_n_ + 1) if Ne(_n_, -1)", "_a_*x + _b_ -> -_b_/_a_",
    "f(a_) -> g(a_)", "f(a_, b_) -> f(b_, a_)", "a_ -> 1 if (a_ > 0) & (a_ < 1)", "a_**2 -> a_ if a_ > 0",
    "Integral(a_, x) -> a_*x", "Derivative(f(x), x) -> g(x)", "exp(a_)*exp(b_) -> exp(a_ + b_)",
    "log(a_*b_) -> log(a_) + log(b_)", "sqrt(a_**2) -> Abs(a_)", "a_ + 0.5 -> a_", "t -> 2",
    "gamma(a_ + 1) -> a_*gamma(a_)", "`beta` -> 3", "beta(a_, b_) -> gamma(a_)*gamma(b_)/gamma(a_ + b_)",
    "Sum(a_, (k, 1, n)) -> n*a_", "Matrix([[a_, 0], [0, b_]]) -> a_*b_", "Eq(a_, b_) -> Eq(b_, a_)",
    "Piecewise((a_, b_), (0, True)) -> a_", "lamda -> 1", "oo -> zoo", "I*a_ -> a_", "E**a_ -> 1", "x/y -> y/x",
    "1/a_ -> a_", "-a_ -> a_", "a_ - b_ -> b_ - a_", "a_ -> a_/3 if Or(a_ > 1, Eq(a_, -1))", "pi*a_ -> 180*a_",
    # symbols that have the names of SymPy's own: the text of the rule does not tell them apart
    "`E`*a_ -> a_*`pi`", "`I`**a_ -> `gamma`",
]


def _parts(rules):
    """Rules as what they are, part by part.  Not as objects: sympy-matching
    numbers every wildcard it makes, so two of one name never compare equal."""
    return [[srepr(part) for part in rule.args] for rule in rules]


def test_rules_come_back_as_they_were_kept():
    """The rules were kept as the text of the field and read back as a saved
    line is - which is not how typed input is read: ``Q.positive(a_)`` could
    not be read and the rule was gone, without a word, at the next start;
    ``beta`` came back a symbol where the rule held SymPy's function.  They
    are kept as the srepr of their parts, which reads back as the very rule."""
    doc = Document(x + 1, addons=[MatchingAddon()])
    for text in CORPUS:
        _q(doc, "add_rule", src=text)
    rules = doc.addons["matching"].rules(doc)
    assert len(rules) == len(CORPUS)
    _q(doc, "name_ruleset", name="all")
    import json
    exported = json.loads(json.dumps(doc.export()))                       # as a session is kept
    again = Document(y, addons=[MatchingAddon()], **exported)
    assert _parts(again.addons["matching"].rules(again)) == _parts(rules)
    assert _parts(again.addon_state["matching"]["library"]["all"]) == _parts(rules)
    assert _q(again, "rules")["dirty"] is False
    kept = json.loads(json.dumps(_q(doc, "rules")["state"]))              # as the keeper has it
    fresh = Document(y, addons=[MatchingAddon()])
    _q(fresh, "restore", state=kept)
    assert _parts(fresh.addons["matching"].rules(fresh)) == _parts(rules)
    # ... and they still match: a wildcard read twice is one wildcard
    for back in (again, fresh):
        back.replace("/", sin(y) ** 2 + cos(y) ** 2)
        hits = _q(back, "matches", path="/")["matches"]
        assert [(h["index"], h["bindings"], h["result"]) for h in hits if h["index"] == 1] == [(1, {"a": "y"}, "1")]
        optional = [a for a in back.addons["matching"].rules(back)[4].pattern.atoms(WildSymbol)]
        assert len(optional) == 2 and all(w.is_optional for w in optional)


def test_the_text_form_kept_by_earlier_versions_is_still_read(tmp_path):
    """What is kept already is the text of the rules: it is read as before -
    and never run, in either form."""
    marker = tmp_path / "ran"
    evil = f"__import__('pathlib').Path({str(marker)!r}).write_text('x')"
    old = {"name": "trig", "rules": ["sin(a_)**2 -> 1 - cos(a_)**2", "x**m_ -> x**(m_ + 1)/(m_ + 1) if Ne(m_, -1)",
                                     evil + " -> 1"],
           "library": {"trig": ["sin(a_)**2 -> 1 - cos(a_)**2"]}}
    new = {"rules": [{"pattern": evil, "replacement": "Integer(1)"}, {"pattern": "Symbol('x')", "replacement": evil},
                     {"pattern": "Symbol('x')", "replacement": "Integer(1)", "condition": evil},
                     {"text": evil + " -> 1"}, {"text": "y -> 2"}]}
    doc = Document(x, addons=[MatchingAddon()], addon_state={"matching": old})
    assert [rule_text(r) for r in doc.addons["matching"].rules(doc)] == \
        ["sin(a_)**2 -> 1 - cos(a_)**2", "x**m_ -> x**(m_ + 1)/(m_ + 1) if Ne(m_, -1)"]
    assert doc.addon_state["matching"]["name"] == "trig" and list(doc.addon_state["matching"]["library"]) == ["trig"]
    doc = Document(x, addons=[MatchingAddon()], addon_state={"matching": new})
    assert [rule_text(r) for r in doc.addons["matching"].rules(doc)] == ["y -> 2"]
    fresh = Document(x, addons=[MatchingAddon()])
    assert [r["text"] for r in _q(fresh, "restore", state=new)["rules"]] == ["y -> 2"]
    assert not marker.exists()


def test_a_rule_that_would_not_come_back_is_refused_when_it_is_added():
    """A condition with ``Q.…`` was taken and lost at the next start (the
    reader of saved text takes no attribute); SymPy's ``beta`` by itself is a
    function, not an expression.  Each is refused at once, with the reason
    and what to write instead."""
    doc = Document(x ** 2, addons=[MatchingAddon()])
    message = _refused(doc, "add_rule", src="a_**2 -> a_ if Q.positive(a_)")
    assert "Q.positive(a_)" in message and "cannot be kept" in message and "a_ > 0" in message
    message = _refused(doc, "add_rule", src="beta -> 3")
    assert "beta" in message and "backticks" in message
    assert _q(doc, "rules")["rules"] == []
    _q(doc, "add_rule", src="a_**2 -> a_ if a_ > 0")
    assert "cannot be kept" in _refused(doc, "update_rule", index=0, src="a_**2 -> a_ if Q.positive(a_)")
    assert [r["text"] for r in _q(doc, "rules")["rules"]] == ["a_**2 -> a_ if a_ > 0"]


def test_a_rewrite_that_changes_nothing_is_no_step_of_the_history():
    """Rewrite all where no rule matched, and any rewrite by a rule that
    gives back what it matched, committed a step - "Rewrite: until nothing
    matches" in the history, the same formula before and after, and not a
    word.  They answer instead, and nothing is committed."""
    doc = Document(x + 1, addons=[MatchingAddon()])
    _q(doc, "add_rule", src="sin(a_) -> cos(a_)")
    assert "No rule matches" in _refused(doc, "rewrite", path="/", all=True)
    assert "No rule matches" in _refused(doc, "rewrite", path="/")
    doc = Document(x + sin(x), addons=[MatchingAddon()])
    _q(doc, "add_rule", src="a_ -> a_")
    assert "Nothing changed" in _refused(doc, "rewrite", path="/", all=True)
    assert "Nothing changed" in _refused(doc, "rewrite", path="/")
    assert "Nothing changed" in _refused(doc, "rewrite", path="/", index=0, bindings={"a": "x + sin(x)"})
    assert not doc.can_undo and doc.history_labels()["actions"] == [None]
    # from the Transform menu an op that changes nothing says so in the status line
    doc = Document(x + 1, addons=[MatchingAddon(rules=[(sin(WildSymbol("a_")), cos(WildSymbol("a_")))])])
    snap = doc.handle({"action": "apply", "path": "/", "op": "rewrite_all"})
    assert not snap["error"] and "No rule" in snap["note"] and doc.expr == x + 1


def test_rewrite_all_stops_when_the_expression_keeps_growing():
    """Fifty passes do not bound the work: the half-angle formulas of sin and
    cos feed each other and double the expression at every pass - the
    fifteenth took half a minute, and the fiftieth would never have come.
    It stops as soon as the expression has outgrown its bound, and says so."""
    doc = Document(sin(x), addons=[MatchingAddon()])
    _q(doc, "add_rule", src="sin(a_) -> 2*sin(a_/2)*cos(a_/2)")
    _q(doc, "add_rule", src="cos(a_) -> cos(a_/2)**2 - sin(a_/2)**2")
    began = time.time()
    message = _refused(doc, "rewrite", path="/", all=True)
    assert time.time() - began < 20
    assert "stopped after" in message and "grown" in message and "nothing changed" in message
    assert doc.expr == sin(x) and not doc.can_undo
    snap = doc.handle({"action": "apply", "path": "/", "op": "rewrite_all"})        # the Transform menu's is the same
    assert "stopped after" in snap["error"] and doc.expr == sin(x)
    # a large expression is not refused for being large: the bound is on what the rules add
    big = sum(sin(Symbol(f"x{i}")) for i in range(1500))
    doc = Document(big, addons=[MatchingAddon()])
    _q(doc, "add_rule", src="sin(a_) -> cos(a_)**2")
    _q(doc, "rewrite", path="/", all=True)
    assert doc.expr == sum(cos(Symbol(f"x{i}")) ** 2 for i in range(1500))


def test_a_name_typed_over_another_saved_set_is_refused():
    """Rename refused a name another saved set had; the name field, given the
    same name, wrote the current rules over that set without a word."""
    doc = Document(x, addons=[MatchingAddon()])
    _q(doc, "add_rule", src="sin(a_) -> cos(a_)")
    _q(doc, "name_ruleset", name="trig")
    _q(doc, "name_ruleset", name="other")                       # a copy, under a name that was free
    _q(doc, "remove_rule", index=0)
    _q(doc, "add_rule", src="tan(a_) -> sin(a_)/cos(a_)")
    for method in ("name_ruleset", "save_ruleset"):
        message = _refused(doc, method, name="trig")
        assert "saved already" in message and "'trig'" in message
    state = doc.addon_state["matching"]
    assert state["name"] == "other"
    assert [rule_text(r) for r in state["library"]["trig"]] == ["sin(a_) -> cos(a_)"]
    assert [rule_text(r) for r in state["library"]["other"]] == ["tan(a_) -> sin(a_)/cos(a_)"]
    # nothing is lost where the two sets are the same, nor where the message says so
    _q(doc, "load_ruleset", name="trig")
    _q(doc, "name_ruleset", name="")
    assert _q(doc, "name_ruleset", name="trig")["name"] == "trig"
    _q(doc, "load_ruleset", name="other")
    res = _q(doc, "save_ruleset", name="trig", overwrite=True)
    assert res["name"] == "trig" and [rule_text(r) for r in state["library"]["trig"]] == ["tan(a_) -> sin(a_)/cos(a_)"]


@pytest.mark.parametrize("method, payload", [
    ("rewrite", {"path": "/", "index": "zz"}), ("rewrite", {"path": "/", "index": [1]}),
    ("open_rule", {"index": "zz"}), ("open_rule", {"index": None}), ("open_rule", {}),
    ("remove_rule", {"index": "a"}), ("remove_rule", {"index": None}), ("update_rule", {"index": {}, "src": "x -> y"}),
])
def test_an_index_that_is_no_number_is_the_panel_s_to_show(method, payload):
    """The label of a step is made before the method runs, outside what
    hands a failure back to the panel: ``int()`` of an index that is no
    number raised there, and the message landed in the editor's own error
    line - which is for the editor's edits."""
    doc = Document(sin(x), addons=[MatchingAddon()])
    _q(doc, "add_rule", src="sin(a_) -> cos(a_)")
    assert "No rule" in _refused(doc, method, **payload)
    assert doc.expr == sin(x) and len(_q(doc, "rules")["rules"]) == 1

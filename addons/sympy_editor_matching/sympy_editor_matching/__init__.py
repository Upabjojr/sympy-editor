"""sympy-editor add-on: rewrite rules with wildcards, matched many-to-one.

Three things come from `sympy-matching <https://github.com/Upabjojr/sympy-matching>`_:
``WildSymbol`` (a Symbol that is a pattern variable - ``a_``; ``_a_`` an
optional one that takes the identity of its slot when absent), the rule
(``SymPyReplacementPattern``: pattern, constraints, replacement) and
``build_replacer``, which compiles a whole rule set into *one* OmniMatch
many-to-one matcher.  This add-on puts them in the editor:

* **a node**: :class:`RewriteRule` - ``Rule(pattern, replacement[,
  condition])`` - shown as ``p → r  [if c]``, with its own kind ("rule") and
  tools, so that a rule is an expression the editor can hold and edit;
* **typed input**: a new name ending in ``_`` is a wildcard
  (:meth:`MatchingAddon.make_symbol`);
* **a panel** (``static/matching.js``): the rule set, the rules matching the
  selection with their bindings, and the buttons that apply them;
* **methods**: ``rules``, ``add_rule``, ``remove_rule``, ``use_selection``,
  ``matches`` (queries) and ``rewrite`` (a change);
* **ops**: *Rewrite* / *Rewrite all* in the Transform menu (they read the
  document's rule set, hence ``context=True``), *Swap sides* on a rule.

The rule set is kept per document (``doc.addon_state["matching"]``:
``rules``, the set's ``name`` once it has one, and a ``library`` of named
sets) and compiled again only when it changes; a query walks the compiled
matcher once, whatever the number of rules.  In Jupyter the same dict is
``w.addon_state["matching"]``, live.  It travels with a session
(``export_state``/``restore_state``, as texts a document parses back), and
the panel mirrors the library and the current set to the browser's
storage, so they are there again after a reload - in a page, in the
apps' web views and in JupyterLab alike.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sympy import Basic, S, Symbol, sympify
from sympy.printing.latex import LatexPrinter

from sympy_editor.addons import Addon
from sympy_editor.ops import make_op
from sympy_editor.printer import AnnotatedLatexPrinter, rebuild

try:
    from sympy_matching import (IDENTITY_ELEMENT, SymPyReplacementPattern, WildSymbol, build_replacer,
                                omnimatch_to_sympy, to_omnimatch_expression)
    AVAILABLE = True
except ImportError:  # pragma: no cover - the add-on is still importable, activate() says what is missing
    AVAILABLE = False
    WildSymbol = None  # type: ignore[assignment,misc]

__all__ = ["MatchingAddon", "RewriteRule", "ADDON", "parse_rule_text", "rule_text"]

STATIC = Path(__file__).parent / "static"


class RewriteRule(Basic):
    """``Rule(pattern, replacement, condition=true)``: a rewrite rule as an
    expression, so that the editor can show and edit one.  Its ``args`` are
    the three parts, which makes each selectable in the formula."""

    def __new__(cls, pattern, replacement, condition=S.true):
        return Basic.__new__(cls, sympify(pattern), sympify(replacement), sympify(condition))

    @property
    def pattern(self) -> Basic:
        return self.args[0]

    @property
    def replacement(self) -> Basic:
        return self.args[1]

    @property
    def condition(self) -> Basic:
        return self.args[2]

    def _latex(self, printer) -> str:
        out = r"%s \;\rightarrow\; %s" % (printer._print(self.pattern), printer._print(self.replacement))
        if self.condition is not S.true:
            out += r" \quad \text{if } %s" % printer._print(self.condition)
        return out

    def _sympystr(self, printer) -> str:
        parts = [printer._print(self.pattern), printer._print(self.replacement)]
        if self.condition is not S.true:
            parts.append(printer._print(self.condition))
        return "Rule(%s)" % ", ".join(parts)

    def to_pattern(self, index: int = 0, module: str = "editor") -> "SymPyReplacementPattern":
        constraints = () if self.condition is S.true else (self.condition,)
        return SymPyReplacementPattern(pattern=self.pattern, constraints=constraints, replacement=self.replacement,
                                       module_name=module, rule_number=index)


def _print_wild(printer, expr) -> str:
    """A wildcard underlined, an optional one in brackets: ``a_`` is
    ``\\underline{a}``, ``_a_`` is ``[\\underline{a}]``."""
    base = LatexPrinter._print_Symbol(printer, Symbol(expr.wildcard_name.strip("_") or expr.wildcard_name))
    out = r"\underline{%s}" % base
    return r"\left[%s\right]" % out if getattr(expr, "is_optional", False) else out


def _wild_from_srepr(name, **kwargs):
    """``WildSymbol('_b_')`` read back from an srepr: the optional value is
    not in the srepr, so the naming convention restores it."""
    if "optional_value" not in kwargs and str(name).startswith("_") and str(name).endswith("_"):
        kwargs["optional_value"] = IDENTITY_ELEMENT
    return WildSymbol(name, **kwargs)


RULE_RE = re.compile(r"^\s*(?P<p>.+?)\s*(?:->|→|=>)\s*(?P<r>.+?)\s*(?:\b(?:if|where)\b\s*(?P<c>.+?))?\s*$")


def parse_rule_text(text: str, parse) -> RewriteRule:
    """``"sin(a_)**2 -> 1 - cos(a_)**2 if Ne(a_, 0)"`` as a rule; ``parse`` is
    the document's parser (it reads ``a_`` as a wildcard)."""
    m = RULE_RE.match(text or "")
    if not m:
        raise ValueError("A rule is written  pattern -> replacement  (optionally  if condition)")
    pattern, replacement = parse(m.group("p")), parse(m.group("r"))
    condition = parse(m.group("c")) if m.group("c") else S.true
    return RewriteRule(pattern, replacement, condition)


def rule_text(rule: RewriteRule) -> str:
    """The text form :func:`parse_rule_text` reads: ``pattern -> replacement``,
    ``if condition`` appended when there is one - what the panel shows in
    the field when a rule is edited in place."""
    out = f"{rule.pattern} -> {rule.replacement}"
    if rule.condition is not S.true:
        out += f" if {rule.condition}"
    return out


class MatchingAddon(Addon):
    name = "matching"
    label = "Rewrite rules"
    requires = ("sympy-matching",)
    kinds = {"rule": (RewriteRule,)}
    kind_labels = {"rule": "Rule"}
    js = (STATIC / "matching.js").read_text(encoding="utf-8")
    css = (STATIC / "matching.css").read_text(encoding="utf-8")
    #: How many passes *Rewrite all* makes before it gives up.
    max_rounds = 50

    def __init__(self, rules=()):
        self.initial_rules: List[RewriteRule] = [self._as_rule(r) for r in rules]
        self.ops = [
            make_op("rewrite", self._op_rewrite, label="Rewrite (one pass of the rules)", context=True,
                    doc="Replace every piece of the selection a rule of the panel matches, outermost first, "
                        "in one pass - what a rule produced is not rewritten again."),
            make_op("rewrite_all", self._op_rewrite_all, label="Rewrite all (until no rule matches)", context=True,
                    doc="One pass after another until no rule matches any more; refused when it never settles."),
            make_op("rule_swap", lambda r: RewriteRule(r.replacement, r.pattern, r.condition), label="Swap sides",
                    kinds=("rule",), doc="The rule the other way round."),
        ]

    @property
    def latex_printers(self):
        return {"WildSymbol": _print_wild} if AVAILABLE else {}

    def activate(self) -> None:
        if not AVAILABLE:
            raise ImportError("The matching add-on needs sympy-matching: pip install sympy-matching")
        super().activate()

    # -- the tree -----------------------------------------------------------------

    def namespace(self) -> Dict[str, Any]:
        ns: Dict[str, Any] = {"Rule": RewriteRule, "RewriteRule": RewriteRule, "true": S.true, "false": S.false}
        if AVAILABLE:
            ns["WildSymbol"] = _wild_from_srepr
            ns["IDENTITY_ELEMENT"] = IDENTITY_ELEMENT
        return ns

    def make_symbol(self, name: str) -> Optional[Basic]:
        if not AVAILABLE or len(name) < 2 or not name.endswith("_"):
            return None
        if name.startswith("_"):
            return WildSymbol(name, optional_value=IDENTITY_ELEMENT)
        return WildSymbol(name)

    # -- the rule set ----------------------------------------------------------------

    @staticmethod
    def _as_rule(rule) -> RewriteRule:
        if isinstance(rule, RewriteRule):
            return rule
        if isinstance(rule, (tuple, list)) and len(rule) in (2, 3):
            return RewriteRule(*rule)
        raise TypeError(f"Not a rule: {rule!r} (Rule(pattern, replacement[, condition]) or a pair)")

    def _state(self, doc) -> Dict[str, Any]:
        state = doc.addon_state.setdefault(self.name, {})
        if "rules" not in state:
            state["rules"] = list(self.initial_rules)
            state["compiled"] = None
        state.setdefault("name", None)
        state.setdefault("library", {})
        return state

    # -- sessions and storage --------------------------------------------------------

    def export_state(self, doc) -> Dict[str, Any]:
        state = self._state(doc)
        return {"name": state["name"], "rules": [rule_text(r) for r in state["rules"]],
                "library": {name: [rule_text(r) for r in rules] for name, rules in state["library"].items()}}

    def restore_state(self, doc, data) -> None:
        state = self._state(doc)
        if not isinstance(data, dict):
            return
        state["rules"] = self._parse_all(doc, data.get("rules") or [])
        state["compiled"] = None
        state["name"] = data.get("name") or None
        for name, texts in (data.get("library") or {}).items():
            state["library"][str(name)] = self._parse_all(doc, texts)

    @staticmethod
    def _parse_all(doc, texts) -> List[RewriteRule]:
        out = []
        for text in texts:
            try:
                out.append(parse_rule_text(str(text), doc.parse))
            except Exception:
                continue                       # a rule that no longer parses is dropped, not the set
        return out

    def rules(self, doc) -> List[RewriteRule]:
        """The document's rule set (a list: append, remove, reorder - then
        the matcher is compiled again at the next query)."""
        return self._state(doc)["rules"]

    def _replacer(self, doc):
        state = self._state(doc)
        key = tuple(state["rules"])
        if state["compiled"] is None or state["compiled"][0] != key:
            replacer = build_replacer([r.to_pattern(i) for i, r in enumerate(key)]) if key else None
            state["compiled"] = (key, replacer)
        return state["compiled"][1]

    def matches(self, doc, node: Basic) -> List[Tuple[int, Dict[str, Basic]]]:
        """The rules matching ``node`` at its root, with the bindings of
        their wildcards - one walk of the compiled matcher for all of them."""
        replacer = self._replacer(doc)
        if replacer is None:
            return []
        out = []
        for replacement, subst in replacer.matcher.match(to_omnimatch_expression(node)):
            index = getattr(replacement, "_rule_index", None)
            bindings = {str(k): omnimatch_to_sympy(v) for k, v in dict(subst).items()}
            out.append((index, bindings))
        out.sort(key=lambda hit: (hit[0] is None, hit[0]))
        return out

    def _apply(self, doc, node: Basic, index: Optional[int] = None) -> Optional[Basic]:
        """``node`` rewritten by the first rule matching at its root (or by
        rule ``index`` when given), or None when none matches."""
        for i, bindings in self.matches(doc, node):
            if index is not None and i != index:
                continue
            rule = self.rules(doc)[i]
            by_name = {w: bindings[w.wildcard_name] for w in rule.replacement.atoms(WildSymbol) if w.wildcard_name in bindings}
            return sympify(rule.replacement.xreplace(by_name))
        return None

    def rewrite_once(self, doc, node: Basic, index: Optional[int] = None) -> Optional[Basic]:
        """One pass, outermost first: every piece of ``node`` a rule matches
        is replaced, and what a rule produced is left alone in this pass
        (the ``ReplaceAll`` of term rewriting: ``x -> x**2`` on ``x + sin(x)``
        gives ``x**2 + sin(x**2)``, and no more).  None when nothing matched.
        ``index`` restricts it to one rule."""
        done = self._apply(doc, node, index)
        if done is not None:
            return done
        if not node.args:
            return None
        new_args = [self.rewrite_once(doc, arg, index) for arg in node.args]
        if all(new is None for new in new_args):
            return None
        return sympify(rebuild(node, [new if new is not None else old for new, old in zip(new_args, node.args)]))

    def rewrite_all(self, doc, node: Basic) -> Basic:
        """:meth:`rewrite_once` again and again until nothing matches (the
        ``ReplaceRepeated`` of term rewriting).  A rule whose result it
        matches again (``x -> x**2``) never settles: after ``max_rounds``
        passes this raises, and the expression stays as it was - whatever
        the fiftieth pass left is not an answer."""
        for _ in range(self.max_rounds):
            new = self.rewrite_once(doc, node)
            if new is None or new == node:
                return node
            node = new
        raise ValueError(f"Rewrite all did not settle in {self.max_rounds} passes: a rule keeps matching what it "
                         "produces (x -> x**2 grows for ever); nothing changed. Rewrite does one pass.")

    def _op_rewrite(self, expr, doc=None):
        new = self.rewrite_once(doc, expr)
        if new is None:
            doc.last_note = "No rule of the panel matches here"
            return expr
        return new

    def _op_rewrite_all(self, expr, doc=None):
        return self.rewrite_all(doc, expr)

    # -- methods -------------------------------------------------------------------------

    def _rules_answer(self, doc) -> Dict[str, Any]:
        # The editor's printer, not sympy.latex: it knows how a wildcard is
        # drawn (sympy's own gives KaTeX "x _b_{}", which it refuses, and the
        # panel then showed the source instead of the formula).
        printer = AnnotatedLatexPrinter(dict(doc.printer_settings))
        state = self._state(doc)
        out = []
        for i, rule in enumerate(state["rules"]):
            out.append({"index": i, "src": str(rule), "text": rule_text(rule), "latex": printer.doprint(rule)})
        return {"rules": out, "name": state["name"], "library": sorted(state["library"]),
                "state": self.export_state(doc)}      # what the panel mirrors to the browser's storage

    def describe(self, method: str, payload: Dict[str, Any]) -> Optional[str]:
        if method == "rewrite":
            which = f"rule {payload['index'] + 1}" if payload.get("index") is not None else ("until nothing matches" if payload.get("all") else "one pass")
            return f"Rewrite: {which}"
        if method == "open_rule":
            return f"Rules: open rule {int(payload.get('index', 0)) + 1} in the editor"
        return f"Rules: {method}"

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        state = self._state(doc)
        if method == "rules":
            return self._rules_answer(doc)
        if method == "save_ruleset":
            # The current set under a name, in the library (over an old one
            # of that name); the set is called that from now on.
            name = str(payload.get("name") or "").strip()
            if not name:
                raise ValueError("A rule set needs a name to be saved under")
            state["library"][name] = list(state["rules"])
            state["name"] = name
            return self._rules_answer(doc)
        if method == "load_ruleset":
            name = str(payload.get("name") or "")
            if name not in state["library"]:
                raise ValueError(f"No rule set named {name!r}")
            state["rules"] = list(state["library"][name])
            state["compiled"] = None
            state["name"] = name
            return self._rules_answer(doc)
        if method == "delete_ruleset":
            name = str(payload.get("name") or "")
            state["library"].pop(name, None)
            if state["name"] == name:
                state["name"] = None
            return self._rules_answer(doc)
        if method == "restore":
            # The browser's storage, at mount: the library it kept joins this
            # document's (a set kept in Python wins over the stored one of
            # the same name), and the stored current set fills an empty one.
            data = payload.get("state") or {}
            for name, texts in (data.get("library") or {}).items():
                if str(name) not in state["library"]:
                    state["library"][str(name)] = self._parse_all(doc, texts)
            if not state["rules"] and data.get("rules"):
                state["rules"] = self._parse_all(doc, data["rules"])
                state["compiled"] = None
                state["name"] = data.get("name") or None
            return self._rules_answer(doc)
        if method == "add_rule":
            rule = parse_rule_text(str(payload.get("src", "")), doc.parse)
            self.rules(doc).append(rule)
            return self._rules_answer(doc)
        if method == "update_rule":
            # A rule changed in place: from its text form (``src``), or from
            # the Rule node at ``path`` in the expression (a rule opened in
            # the editor, edited there, and saved back over its entry).
            rules = self.rules(doc)
            index = int(payload.get("index", -1))
            if not 0 <= index < len(rules):
                raise ValueError(f"No rule {index + 1}")
            if payload.get("src") is not None:
                rules[index] = parse_rule_text(str(payload["src"]), doc.parse)
            else:
                node = doc.get(payload.get("path") or "/")
                if not isinstance(node, RewriteRule):
                    raise ValueError(f"{node} is not a rule: select a Rule(pattern, replacement)")
                rules[index] = node
            return self._rules_answer(doc)
        if method == "open_rule":
            # The rule as the expression, to edit it structurally; undoable.
            rules = self.rules(doc)
            index = int(payload.get("index", -1))
            if not 0 <= index < len(rules):
                raise ValueError(f"No rule {index + 1}")
            doc.replace("/", rules[index])
            return None
        if method == "remove_rule":
            rules = self.rules(doc)
            index = int(payload.get("index", -1))
            if not 0 <= index < len(rules):
                raise ValueError(f"No rule {index + 1}")
            del rules[index]
            return self._rules_answer(doc)
        if method == "use_selection":
            node = doc.get(payload.get("path") or "/")
            if not isinstance(node, RewriteRule):
                raise ValueError(f"{node} is not a rule: select a Rule(pattern, replacement)")
            self.rules(doc).append(node)
            return self._rules_answer(doc)
        if method == "matches":
            node = doc.get(payload.get("path") or "/")
            hits = []
            for index, bindings in self.matches(doc, node):
                hits.append({"index": index, "bindings": {k: str(v) for k, v in bindings.items()},
                             "result": str(self._apply(doc, node, index))})
            return {"path": payload.get("path") or "/", "src": str(node), "matches": hits}
        if method == "rewrite":
            path = payload.get("path") or "/"
            node = doc.get(path)
            index = payload.get("index")
            if payload.get("all"):
                new = self.rewrite_all(doc, node)
            else:
                new = self.rewrite_once(doc, node, None if index is None else int(index))
                if new is None:
                    raise ValueError("No rule matches there")
            doc.replace(path, new)
            return None
        raise ValueError(f"The rules panel has no method {method!r}")


ADDON = MatchingAddon()

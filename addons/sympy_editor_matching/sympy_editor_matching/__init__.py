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
  ``matches`` (queries) and ``rewrite`` (a change) - the last two on the node
  at ``path`` or, with ``children``, on the range of those arguments of it;
  ``rewrite`` is one pass of all the rules, of rule ``index``, the one match
  of it that bound ``bindings``, or - ``all`` - pass after pass;
* **ops**: *Rewrite* / *Rewrite all* in the Transform menu (they read the
  document's rule set, hence ``context=True``), *Swap sides* on a rule.

The rule set is kept per document (``doc.addon_state["matching"]``:
``rules``, the set's ``name`` once it has one, and a ``library`` of named
sets) and compiled again only when it changes; a query walks the compiled
matcher once, whatever the number of rules.  In Jupyter the same dict is
``w.addon_state["matching"]``, live.  It travels with a session
(``export_state``/``restore_state``: each rule as the ``srepr`` of its parts,
which a document reads back - never runs - as the very rule; see
:func:`rule_state`), every snapshot carries a stamp of it
(``snap["matching"]``: which document, how many rules, the set's name) so
that the panel notices a document it has not asked yet, and
the panel mirrors the library and the current set to the editor's keeper
(``api.keep``), so they are there again after a reload: the app's own
storage on Android, iOS and the Mac, the server's store under ``serve()``,
the kernel's store in Jupyter (:class:`sympy_editor.store.Store` both), and
the browser's only on a standalone page.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sympy import Basic, S, Symbol, preorder_traversal, sympify
from sympy.assumptions.assume import AppliedPredicate
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

__all__ = ["MatchingAddon", "RewriteRule", "ADDON", "parse_rule_text", "rule_text", "rule_state", "rule_problem"]

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


#: How a wildcard is drawn: a solid underline for one that must be there
#: (``a_``), a dotted one for one that may be absent (``_a_``) - the same
#: mark in two weights, as a dashed line stands for what may be missing.
#: (KaTeX has no dotted underline of its own: the dots are set under the
#: letter with ``\\underset``, raised to sit where an underline would.)
WILD_TEX = r"\underline{%s}"
OPTIONAL_WILD_TEX = r"\underset{\raisebox{0.35em}{\scriptsize\ldots}}{%s}"


def _print_wild(printer, expr) -> str:
    """A wildcard underlined; an optional one with a dotted underline."""
    base = LatexPrinter._print_Symbol(printer, Symbol(expr.wildcard_name.strip("_") or expr.wildcard_name))
    return (OPTIONAL_WILD_TEX if getattr(expr, "is_optional", False) else WILD_TEX) % base


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


def _srepr(expr) -> str:
    """The ``srepr`` the editor writes its own steps with (argument order
    kept), so that what a document reads back prints as the very text."""
    try:
        from sympy_editor.document import srepr
    except ImportError:             # an editor from before it had a writer of its own
        from sympy import srepr
    return srepr(expr)


def rule_state(rule: RewriteRule) -> Dict[str, str]:
    """A rule as it is kept - in a session, a file, the keeper: the ``srepr``
    of each part, which reads back as the same part without running anything,
    and the text form for whoever opens the file.  (The text alone was what
    used to be kept, and it does not say enough: ``beta`` is SymPy's function
    to the one who typed it and a symbol to the reader of a saved line.)"""
    out = {"text": rule_text(rule), "pattern": _srepr(rule.pattern), "replacement": _srepr(rule.replacement)}
    if rule.condition is not S.true:
        out["condition"] = _srepr(rule.condition)
    return out


def _wild_names(part) -> Dict[str, str]:
    """The wildcards of a part of a rule: ``{the name it binds under: the
    name as typed}`` (``a`` for ``a_``, ``_a`` for ``_a_``)."""
    if not AVAILABLE or not isinstance(part, Basic):
        return {}
    return {w.wildcard_name: w.name for w in part.atoms(WildSymbol)}


def rule_problem(rule: RewriteRule) -> Optional[str]:
    """What is wrong with a rule that no matcher would say, or None: a
    wildcard of the replacement or the condition that the pattern does not
    have.  Nothing binds it, so the replacement would put the wildcard itself
    in the formula, and a condition over it holds whatever was matched."""
    bound = _wild_names(rule.pattern)
    for what, part in (("replacement", rule.replacement), ("condition", rule.condition)):
        free = sorted(typed for name, typed in _wild_names(part).items() if name not in bound)
        if free:
            return (f"{', '.join(free)} in the {what} {'is' if len(free) == 1 else 'are'} not in the pattern: "
                    "a wildcard stands for what the pattern matched, so every wildcard of a rule must be in its pattern")
    return None


class MatchingAddon(Addon):
    name = "matching"
    label = "Rewrite rules"
    requires = ("sympy-matching>=0.0.4",)
    kinds = {"rule": (RewriteRule,)}
    kind_labels = {"rule": "Rule"}
    js = (STATIC / "matching.js").read_text(encoding="utf-8")
    css = (STATIC / "matching.css").read_text(encoding="utf-8")
    #: How many passes *Rewrite all* makes before it gives up.
    max_rounds = 50
    #: ... and how large it lets the expression grow, in nodes: past this,
    #: and past ``max_growth`` times what it started with, it gives up too.
    #: Passes alone do not bound the work: two rules feeding each other
    #: double the expression at each pass, and the fifteenth took half a minute.
    max_size = 2000
    max_growth = 4

    def __init__(self, rules=()):
        self.initial_rules: List[RewriteRule] = [self._as_rule(r) for r in rules]
        self.ops = [
            make_op("rewrite", self._op_rewrite, label="Rewrite (one pass of the rules)", context=True,
                    doc="Replace every piece of the selection a rule of the panel matches, outermost first, "
                        "in one pass - what a rule produced is not rewritten again."),
            make_op("rewrite_all", self._op_rewrite_all, label="Rewrite all (until no rule matches)", context=True,
                    doc="One pass after another until no rule matches any more; refused when it never settles "
                        "or the expression keeps growing."),
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
        if isinstance(rule, (tuple, list)) and len(rule) in (2, 3):
            rule = RewriteRule(*rule)
        if not isinstance(rule, RewriteRule):
            raise TypeError(f"Not a rule: {rule!r} (Rule(pattern, replacement[, condition]) or a pair)")
        problem = rule_problem(rule)
        if problem:
            raise ValueError(f"{rule}: {problem}")
        return rule

    def _state(self, doc) -> Dict[str, Any]:
        state = doc.addon_state.setdefault(self.name, {})
        if "rules" not in state:
            state["rules"] = list(self.initial_rules)
            state["compiled"] = None
        state.setdefault("name", None)
        state.setdefault("library", {})
        #: Which document this is, for the stamp: another session, a file
        #: opened, a Python started again are all "not the one you asked".
        #: Made when the panel first asks, not before: a page built twice
        #: from one expression must come out the same (the web app names its
        #: cache by the page), and a stamp without one differs from any the
        #: panel was given.
        state.setdefault("token", None)
        #: The rules as they were saved, loaded or restored last (Revert goes
        #: back to it), and what Revert discarded (Restore brings it back).
        state.setdefault("checkpoint", list(state["rules"]))
        state.setdefault("reverted", None)
        return state

    @staticmethod
    def _texts(rules) -> List[str]:
        return [rule_text(r) for r in rules]

    def _changed(self, doc) -> None:
        """After any change to the rules: a named set saves itself into the
        library (the panel mirrors the library to the editor's keeper)."""
        state = self._state(doc)
        state["compiled"] = None
        if state["name"]:
            state["library"][state["name"]] = list(state["rules"])

    def _checkpoint(self, doc) -> None:
        state = self._state(doc)
        state["checkpoint"] = list(state["rules"])
        state["reverted"] = None

    # -- sessions and storage --------------------------------------------------------

    def contribute(self, doc, snap, expr) -> None:
        """The stamp: which document, how many rules, under what name.  The
        panel compares it with what it shows, and asks when they differ - a
        session opened is another document, and the panel went on showing
        the rules of the one before."""
        snap[self.name] = self._stamp(doc)

    def _stamp(self, doc, mint: bool = False) -> Dict[str, Any]:
        state = self._state(doc)
        if mint and state["token"] is None:
            state["token"] = uuid.uuid4().hex[:12]
        return {"doc": state["token"], "rules": len(state["rules"]), "name": state["name"]}

    @staticmethod
    def _states(rules) -> List[Dict[str, str]]:
        out = []
        for rule in rules:
            try:
                out.append(rule_state(rule))
            except Exception:
                continue                       # put there from Python, and it does not print: the others are kept
        return out

    def export_state(self, doc) -> Dict[str, Any]:
        state = self._state(doc)
        return {"name": state["name"], "rules": self._states(state["rules"]),
                "library": {name: self._states(rules) for name, rules in state["library"].items()}}

    def restore_state(self, doc, data) -> None:
        state = self._state(doc)
        state["token"] = None                            # a file opened in this document: what the panel shows is of before
        if not isinstance(data, dict):
            return
        state["rules"] = self._read_all(doc, data.get("rules"))
        state["compiled"] = None
        state["name"] = self._set_name(data.get("name"))
        state["library"].update(self._read_library(doc, data.get("library")))
        self._checkpoint(doc)

    @staticmethod
    def _set_name(name) -> Optional[str]:
        """A set's name as it was kept: text, or the set has none."""
        return (name.strip() or None) if isinstance(name, str) else None

    @staticmethod
    def _read_part(doc, text: str) -> Basic:
        """One part of a kept rule, read by the document as it reads the
        steps of a saved history: never run."""
        return doc._coerce(text)

    @classmethod
    def _read_rule(cls, doc, kept) -> RewriteRule:
        """A rule as it was kept: the ``srepr`` of its parts
        (:func:`rule_state`), or the text form of the versions before -
        read, never run, either way: it comes from a file or a kept session."""
        if isinstance(kept, dict) and isinstance(kept.get("pattern"), str) and isinstance(kept.get("replacement"), str):
            parts = [cls._read_part(doc, kept["pattern"]), cls._read_part(doc, kept["replacement"])]
            if isinstance(kept.get("condition"), str):
                parts.append(cls._read_part(doc, kept["condition"]))
            return RewriteRule(*parts)
        if isinstance(kept, dict):
            kept = kept.get("text")
        if not isinstance(kept, str):
            raise ValueError("Not a rule")
        return parse_rule_text(kept, doc.parse_saved)

    @classmethod
    def _read_all(cls, doc, kept) -> List[RewriteRule]:
        """The rules of a kept set.  Whatever is not as it should be is left
        out - a rule that no longer reads, a set that is not a list - and
        never raised: the session or the file holding it could not be
        opened at all."""
        out = []
        for one in kept if isinstance(kept, (list, tuple)) else ():
            try:
                rule = cls._read_rule(doc, one)
                if all(isinstance(part, Basic) for part in rule.args):
                    out.append(rule)
            except Exception:
                continue                       # a rule that no longer reads is dropped, not the set
        return out

    @classmethod
    def _read_library(cls, doc, kept) -> Dict[str, List[RewriteRule]]:
        out = {}
        for name, rules in (kept.items() if isinstance(kept, dict) else ()):
            if isinstance(name, str) and name.strip() and isinstance(rules, (list, tuple)):
                out[name.strip()] = cls._read_all(doc, rules)
        return out

    def _checked(self, doc, rule: RewriteRule) -> RewriteRule:
        """``rule`` when it can join the set, refused with the reason
        otherwise: a part that is not an expression, a wildcard nothing
        binds, or a rule that would not come back from where it is kept - it
        used to be taken, and was gone without a word at the next start."""
        for what, part in zip(("pattern", "replacement", "condition"), rule.args):
            if not isinstance(part, Basic):
                name = getattr(part, "__name__", str(part))
                raise ValueError(f"The {what} {name} is SymPy's {name}, not an expression: write `{name}` in "
                                 f"backticks for a symbol of that name, or give the function its arguments")
        problem = rule_problem(rule)
        if problem:
            raise ValueError(problem)
        for what, part in zip(("pattern", "replacement", "condition"), rule.args):
            try:
                text = _srepr(part)
                same = _srepr(self._read_part(doc, text)) == text
                reason = "it reads back as something else"
            except Exception as exc:
                same, reason = False, str(exc)
            if not same:
                hint = ""
                if what == "condition" and part.has(AppliedPredicate):
                    hint = (": a condition is a relation over the wildcards, such as a_ > 0 - a Q.… predicate is "
                            "only a statement, which no match makes true")
                reason = reason.rsplit('": ', 1)[-1]       # the reader quotes the whole text first
                raise ValueError(f"The {what} {part} cannot be kept with the rule set ({reason}), so the rule "
                                 f"is not taken{hint}")
        return rule

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
        out, seen = [], set()
        for replacement, subst in replacer.matcher.match(to_omnimatch_expression(node)):
            index = getattr(replacement, "_rule_index", None)
            bindings = {str(k): omnimatch_to_sympy(v) for k, v in dict(subst).items()}
            key = (index, tuple(sorted((k, str(v)) for k, v in bindings.items())))
            if key not in seen:                # the same match found twice is one match
                seen.add(key)
                out.append((index, bindings))
        out.sort(key=lambda hit: (hit[0] is None, hit[0]))
        return out

    def result(self, doc, index: int, bindings: Dict[str, Basic]) -> Basic:
        """What rule ``index`` makes of one match: its replacement with every
        wildcard replaced by what *that* match bound.  A rule with a wildcard
        nothing binds (put in the list from Python, or kept by a version that
        took it) is refused here, whoever asks: the wildcard would go into
        the formula."""
        rule = self.rules(doc)[index]
        problem = rule_problem(rule)
        if problem:
            raise ValueError(f"Rule {index + 1} cannot be applied: {problem}")
        by_name = {w: bindings[w.wildcard_name] for w in rule.replacement.atoms(WildSymbol)
                   if w.wildcard_name in bindings}
        return sympify(rule.replacement.xreplace(by_name))

    @staticmethod
    def _bound(bindings: Dict[str, Basic]) -> Dict[str, str]:
        """Bindings as the panel shows them, and names a match by."""
        return {name: str(value) for name, value in bindings.items()}

    def _apply(self, doc, node: Basic, index: Optional[int] = None,
               bindings: Optional[Dict[str, str]] = None) -> Optional[Basic]:
        """``node`` rewritten by the first rule matching at its root (or by
        rule ``index`` when given, and by the match that bound ``bindings``
        when a rule matches in several ways: ``a_ + b_`` takes ``x + y`` both
        ways round), or None when none matches."""
        for i, bound in self.matches(doc, node):
            if index is not None and i != index:
                continue
            if bindings is not None and self._bound(bound) != bindings:
                continue
            return self.result(doc, i, bound)
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
        the fiftieth pass left is not an answer.  Nor do rules that feed each
        other (the half-angle formulas of ``sin`` and ``cos``), which double
        the expression at every pass: it raises as soon as the expression
        has outgrown ``max_size`` nodes and ``max_growth`` times its own
        size, long before the passes run out."""
        new = self._repeat(doc, node)
        return node if new is None else new

    def _repeat(self, doc, node: Basic) -> Optional[Basic]:
        """:meth:`rewrite_all`, with None when no rule matched at all."""
        start = self._size(node)
        limit = max(self.max_size, self.max_growth * start)
        matched = False
        for done in range(1, self.max_rounds + 1):
            new = self.rewrite_once(doc, node)
            if new is None or new == node:
                return node if matched or new is not None else None
            matched = True
            size = self._size(new)
            if size > limit:
                raise ValueError(f"Rewrite all stopped after {done} passes: the expression had grown from {start} "
                                 f"to {size} pieces and the rules still matched (they feed each other: what one "
                                 "produces another matches); nothing changed. Rewrite does one pass.")
            node = new
        raise ValueError(f"Rewrite all did not settle in {self.max_rounds} passes: a rule keeps matching what it "
                         "produces (x -> x**2 grows for ever); nothing changed. Rewrite does one pass.")

    @staticmethod
    def _size(node: Basic) -> int:
        """The nodes of the tree, each as often as it is drawn: what a pass
        has to walk."""
        return sum(1 for _ in preorder_traversal(node))

    def _op_rewrite(self, expr, doc=None):
        new = self.rewrite_once(doc, expr)
        if new is None:
            doc.last_note = "No rule of the panel matches here"
            return expr
        return new

    def _op_rewrite_all(self, expr, doc=None):
        new = self._repeat(doc, expr)
        if new is None:
            doc.last_note = "No rule of the panel matches here"
            return expr
        return new

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
                "dirty": self._texts(state["rules"]) != self._texts(state["checkpoint"]),   # Revert has something to go back to
                "can_restore": state["reverted"] is not None,
                "stamp": self._stamp(doc, mint=True),            # what the snapshots will carry, while nothing changes
                "state": self.export_state(doc)}      # what the panel mirrors to the editor's keeper

    def describe(self, method: str, payload: Dict[str, Any]) -> Optional[str]:
        # Never raises: the document asks for the label before it calls the
        # method, outside what sends a failure back to the panel - an index
        # that is no number landed in the editor's own error line.
        if method == "rewrite":
            number = self._number(payload.get("index"))
            which = f"rule {number}" if number is not None else ("until nothing matches" if payload.get("all") else "one pass")
            return f"Rewrite: {which}"
        if method == "open_rule":
            return f"Rules: open rule {self._number(payload.get('index')) or '?'} in the editor"
        return f"Rules: {method}"

    @staticmethod
    def _number(index) -> Optional[int]:
        """A rule's number as the panel counts them (from 1), or None when
        ``index`` is not one."""
        try:
            return int(index) + 1
        except (TypeError, ValueError):
            return None

    def _index(self, doc, payload: Dict[str, Any]) -> int:
        """The index a message names, of a rule there is."""
        number = self._number(payload.get("index"))
        if number is None:
            raise ValueError(f"No rule {payload.get('index')!r}: a rule is named by its place in the list")
        if not 1 <= number <= len(self.rules(doc)):
            raise ValueError(f"No rule {number}")
        return number - 1

    @staticmethod
    def _target(doc, payload: Dict[str, Any]) -> Tuple[str, Optional[List[int]], Basic]:
        """What a message points at: the node at ``path``, or - with
        ``children``, as the editor's own messages name a range - the
        expression those arguments of it form."""
        path = payload.get("path") or "/"
        children = payload.get("children")
        if children is None:
            return path, None, doc.get(path)
        if not isinstance(children, (list, tuple)) or not children:
            raise ValueError("A range is named by the arguments in it")
        children = [int(c) for c in children]
        return path, children, doc._extract_range(doc.expr, doc._path(path), children)

    def _free_name(self, doc, name: str, payload: Dict[str, Any]) -> None:
        """Refuse a name another saved set has, unless the message says
        ``overwrite`` or the two sets are the same: a name typed in the
        field used to replace that set without a word."""
        state = self._state(doc)
        if name == state["name"] or name not in state["library"] or payload.get("overwrite"):
            return
        if self._texts(state["library"][name]) == self._texts(state["rules"]):
            return
        raise ValueError(f"A rule set named {name!r} is saved already: load it from the menu, delete it first, "
                         "or pick another name")

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        state = self._state(doc)
        if method == "rules":
            return self._rules_answer(doc)
        if method == "save_ruleset":
            # The current set under a name, in the library; the set is called
            # that from now on, and keeps itself saved there at every change.
            # Another set saved under that name is not written over, unless
            # the message says ``overwrite``.
            name = str(payload.get("name") or "").strip()
            if not name:
                raise ValueError("A rule set needs a name to be saved under")
            self._free_name(doc, name, payload)
            state["library"][name] = list(state["rules"])
            state["name"] = name
            self._checkpoint(doc)
            return self._rules_answer(doc)
        if method == "name_ruleset":
            # The panel's name field: a name puts the set in the library under
            # it (as save_ruleset does); an empty one leaves the set unnamed,
            # the library entry of the old name kept as it was.
            name = str(payload.get("name") or "").strip()
            if name:
                self._free_name(doc, name, payload)
                state["library"][name] = list(state["rules"])
                state["name"] = name
                self._checkpoint(doc)
            else:
                state["name"] = None
            return self._rules_answer(doc)
        if method == "rename_ruleset":
            # A saved set under another name: its library entry moves (the
            # name field alone saves a copy under the new name).  The set is
            # the current one unless ``old`` names another; nothing of its
            # rules changes, so Revert and Restore are left as they were.
            old = str(payload.get("old") or state["name"] or "")
            name = str(payload.get("name") or "").strip()
            if not old or old not in state["library"]:
                raise ValueError("Only a saved rule set can be renamed: give this one a name first")
            if not name:
                raise ValueError("A rule set needs a name")
            if name != old:
                if name in state["library"]:
                    raise ValueError(f"A rule set named {name!r} is saved already: delete it first, or pick another name")
                state["library"][name] = state["library"].pop(old)
                if state["name"] == old:
                    state["name"] = name
            return self._rules_answer(doc)
        if method == "load_ruleset":
            name = str(payload.get("name") or "")
            if name not in state["library"]:
                raise ValueError(f"No rule set named {name!r}")
            state["rules"] = list(state["library"][name])
            state["compiled"] = None
            state["name"] = name
            self._checkpoint(doc)
            return self._rules_answer(doc)
        if method == "revert":
            # Back to the rules as they were saved, loaded or restored last;
            # what changed since is kept for Restore.
            if self._texts(state["rules"]) == self._texts(state["checkpoint"]):
                raise ValueError("Nothing to revert: the rules are as they were saved")
            state["reverted"] = list(state["rules"])
            state["rules"] = list(state["checkpoint"])
            self._changed(doc)
            return self._rules_answer(doc)
        if method == "restore_reverted":
            if state["reverted"] is None:
                raise ValueError("Nothing to restore: nothing was reverted")
            state["rules"] = list(state["reverted"])
            state["reverted"] = None
            self._changed(doc)
            return self._rules_answer(doc)
        if method == "delete_ruleset":
            name = str(payload.get("name") or "")
            state["library"].pop(name, None)
            if state["name"] == name:
                state["name"] = None
            return self._rules_answer(doc)
        if method == "restore":
            # The keeper's copy, at mount and whenever the panel finds
            # itself before another document: the library it kept joins this
            # document's (a set kept in Python wins over the stored one of
            # the same name), and the stored current set fills an empty one.
            # What was kept may be anything - another version's, a file
            # edited by hand: what is not as it should be is left out.
            data = payload.get("state")
            data = data if isinstance(data, dict) else {}
            for name, rules in self._read_library(doc, data.get("library")).items():
                state["library"].setdefault(name, rules)
            if not state["rules"]:
                rules = self._read_all(doc, data.get("rules"))
                if rules:
                    state["rules"] = rules
                    state["compiled"] = None
                    state["name"] = self._set_name(data.get("name"))
                    self._checkpoint(doc)
            return self._rules_answer(doc)
        if method == "add_rule":
            rule = self._checked(doc, parse_rule_text(str(payload.get("src", "")), doc.parse))
            self.rules(doc).append(rule)
            self._changed(doc)
            return self._rules_answer(doc)
        if method == "update_rule":
            # A rule changed in place: from its text form (``src``), or from
            # the Rule node at ``path`` in the expression (a rule opened in
            # the editor, edited there, and saved back over its entry).
            rules = self.rules(doc)
            index = self._index(doc, payload)
            if payload.get("src") is not None:
                rules[index] = self._checked(doc, parse_rule_text(str(payload["src"]), doc.parse))
            else:
                node = doc.get(payload.get("path") or "/")
                if not isinstance(node, RewriteRule):
                    raise ValueError(f"{node} is not a rule: select a Rule(pattern, replacement)")
                rules[index] = self._checked(doc, node)
            self._changed(doc)
            return self._rules_answer(doc)
        if method == "open_rule":
            # The rule as the expression, to edit it structurally; undoable.
            doc.replace("/", self.rules(doc)[self._index(doc, payload)])
            return None
        if method == "remove_rule":
            del self.rules(doc)[self._index(doc, payload)]
            self._changed(doc)
            return self._rules_answer(doc)
        if method == "use_selection":
            node = doc.get(payload.get("path") or "/")
            if not isinstance(node, RewriteRule):
                raise ValueError(f"{node} is not a rule: select a Rule(pattern, replacement)")
            self.rules(doc).append(self._checked(doc, node))
            self._changed(doc)
            return self._rules_answer(doc)
        if method == "matches":
            # Every way every rule matches the target at its root, each with
            # the result of its own bindings (they all used to show the
            # first one's).
            path, children, node = self._target(doc, payload)
            hits = []
            for index, bindings in self.matches(doc, node):
                hit = {"index": index, "bindings": self._bound(bindings)}
                try:
                    hit["result"] = str(self.result(doc, index, bindings))
                except ValueError as exc:                  # a rule that cannot be applied says why, in its place
                    hit["error"] = str(exc)
                hits.append(hit)
            return {"path": path, "children": children, "src": str(node), "matches": hits}
        if method == "rewrite":
            # On the node at ``path``, or on the range ``children`` name.
            # ``index`` alone is one pass of that rule; with ``bindings`` (as
            # ``matches`` gave them) it is that one match, at the root.
            path, children, node = self._target(doc, payload)
            index = None if payload.get("index") is None else self._index(doc, payload)
            bindings = payload.get("bindings")
            if payload.get("all"):
                new = self._repeat(doc, node)
            elif isinstance(bindings, dict):
                new = self._apply(doc, node, index, {str(k): str(v) for k, v in bindings.items()})
            else:
                new = self.rewrite_once(doc, node, index)
            # Nothing to do is an answer, not a step of the history.
            if new is None:
                raise ValueError("No rule matches there")
            if new == node:
                raise ValueError("Nothing changed: what the rules make of it is what was there")
            doc.replace(path, new, children=children)
            return None
        raise ValueError(f"The rules panel has no method {method!r}")


ADDON = MatchingAddon()

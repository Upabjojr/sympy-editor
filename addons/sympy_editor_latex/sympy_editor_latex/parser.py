"""Reading LaTeX into SymPy, ambiguities and all.

SymPy's Lark-based LaTeX parser (``sympy.parsing.latex.lark``) parses with an
Earley parser that keeps *every* reading of an ambiguous string - ``f(x)`` is
a function applied or a product, ``\\sin x \\cos y`` is ``sin(x) cos(y)`` or
``sin(x cos(y))`` - and hands back a forest.  This module walks that forest:

* every ambiguous node is a **choice point**, named by its rule and the span
  of the text it covers (``"_expression_mul@0-13"``);
* a first reading picks an alternative at each point by a few conventions
  (:func:`tree_cost`: a function without parentheses takes the product that
  follows but not a sum nor another function, ``f``/``g``/``h`` and the
  document's own functions are applied while other letters multiply...);
* the caller's ``choices`` (``{point: alternative index}``) override those,
  and the answer lists every point with the whole expression under each of
  its alternatives, so a user can pick another reading;
* names that usually stand for constants (``\\pi``, ``e``, ``i``...) are read
  as symbols by the grammar and turned into SymPy's constants here, each one
  on or off (:data:`CONSTANTS`, ``constants={"pi": False}``).

The grammar is the add-on's own copy of SymPy's (``static/grammar``), with
``\\pi`` and the other letters SymPy's grammar leaves out.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sympy
from sympy import Basic, Symbol

GRAMMAR_DIR = Path(__file__).parent / "static" / "grammar"

#: Names the grammar reads as symbols that usually mean a constant: name ->
#: (the constant, whether it is applied unless the user says otherwise, a label).
CONSTANTS: Dict[str, Tuple[Basic, bool, str]] = {
    "pi": (sympy.pi, True, "π, the circle constant"),
    "e": (sympy.E, True, "Euler's number (the base of exp)"),
    "i": (sympy.I, True, "the imaginary unit"),
    "gamma": (sympy.EulerGamma, False, "the Euler–Mascheroni constant"),
    "E": (sympy.E, False, "Euler's number (the base of exp)"),
    "I": (sympy.I, False, "the imaginary unit"),
    "j": (sympy.I, False, "the imaginary unit (engineering)"),
}

#: Function rules whose argument is *not* delimited by the rule itself (``\sin x``):
#: how far the argument reaches is the ambiguity these conventions settle.
BARE_FUNCTIONS = frozenset("""sin cos tan csc sec cot sin_power cos_power tan_power csc_power sec_power cot_power
    arcsin arccos arctan arccsc arcsec arccot sinh cosh tanh asinh acosh atanh exponential log
    determinant trace adjugate""".split())
#: Every rule that applies a function (the delimited ones included).
FUNCTIONS = BARE_FUNCTIONS | frozenset("function_applied abs floor ceil square_root conjugate min max".split())
#: Letters that name a function when followed by parentheses, by convention.
FUNCTION_LETTERS = frozenset("f g h F G H".split())

#: What an unfinished text is told: it stops in the middle of an expression
#: (``\frac{x``, ``x +``), or of a command (``\fr``) - being typed, not wrong.
INCOMPLETE = "Not finished yet: the LaTeX stops in the middle of an expression"

MAX_POINTS = 40
MAX_ALTERNATIVES = 8


@dataclass
class Point:
    """A choice point: the alternatives of one ambiguous node."""
    key: str
    rule: str
    start: int
    end: int
    count: int
    fragment: str = ""
    choice: int = 0
    options: List[Dict[str, Any]] = field(default_factory=list)   # {"src", "latex"} per alternative


def _lark():
    try:
        import lark  # noqa: F401
    except ImportError:  # pragma: no cover - the add-on refuses to activate before this
        raise ImportError("The LaTeX add-on needs the lark package: pip install lark") from None
    import lark as _l
    return _l


def _name(data) -> str:
    return data if isinstance(data, str) else str(getattr(data, "value", data))


def _contains_function(tree) -> bool:
    lark = _lark()
    if not isinstance(tree, lark.Tree):
        return False
    return any(_name(t.data) in FUNCTIONS for t in tree.iter_subtrees())


def _tree_children(tree) -> list:
    lark = _lark()
    return [c for c in tree.children if isinstance(c, lark.Tree)]


def _head_letter(tree) -> Optional[str]:
    """The letter a ``function_applied`` node applies (its first token)."""
    lark = _lark()
    for c in tree.children:
        if isinstance(c, lark.Token):
            return str(c)
        if isinstance(c, lark.Tree):
            return _head_letter(c)
    return None


def tree_cost(tree, known_functions=()) -> int:
    """How far a parse tree strays from the usual conventions: the reading
    with the lowest cost is offered first.

    - a function written without parentheses (``\\sin x``) takes the product
      after it, but not a sum (``\\sin x + 1`` is ``sin(x) + 1``) nor another
      function (``\\sin x \\cos y`` is a product of two sines);
    - when its argument *is* in parentheses, the parentheses end it
      (``\\ln(x) y`` is ``y ln(x)``, ``\\sin(x)^2`` is ``sin(x)^2``);
    - a letter followed by parentheses is a function when it is ``f``, ``g``,
      ``h`` (or their capitals) or a function the document already uses, and
      a factor otherwise (``a(b+c)`` is ``a (b + c)``).
    """
    lark = _lark()
    total = 0
    for node in tree.iter_subtrees():
        kind = _name(node.data)
        kids = _tree_children(node)
        if kind in BARE_FUNCTIONS and kids:
            arg = kids[-1]
            akind = _name(arg.data)
            if akind in ("add", "sub"):
                total += 2                                   # the argument swallowed a sum
            elif _contains_function(arg):
                total += 2                                   # ... or another function
            elif akind in ("adjacent_expressions", "superscript"):
                first = _tree_children(arg)
                if first and _name(first[0].data) == "group_round_parentheses":
                    total += 2                               # (x) y: the parentheses ended the argument
        elif kind == "adjacent_expressions" and len(kids) >= 2:
            left, right = kids[0], kids[-1]
            lkind = _name(left.data)
            if lkind in BARE_FUNCTIONS and not _contains_function(right):
                total += 1                                   # the function stopped short of its product
            if lkind == "group_round_parentheses":
                pass
            # a letter times a parenthesis, where the letter is a function by convention
            tokens = [c for c in node.children if isinstance(c, lark.Token)]
            if tokens and _name(right.data) == "group_round_parentheses" and (str(tokens[0]) in FUNCTION_LETTERS or str(tokens[0]) in known_functions):
                total += 1
        elif kind == "function_applied":
            head = _head_letter(node)
            if head is not None and head not in FUNCTION_LETTERS and head not in known_functions:
                total += 1                                   # a(b+c): a product, unless a is a function here
    return total


class _Transformer:
    """Built lazily: SymPy's transformer, with the rules the grammar adds."""

    def __new__(cls):
        from sympy.parsing.latex.lark.transformer import TransformToSymPyExpr

        class Transformer(TransformToSymPyExpr):
            # SymPy hands lark's Tokens (a str subclass) to Symbol and Function;
            # here the names are plain strings, so they compare equal to the
            # user's own Symbol("f") and print without the Token in srepr.
            def SYMBOL(self, token):
                return Symbol(str(token))

            def multi_letter_symbol(self, tokens):
                return Symbol(str(tokens[2]) + (str(tokens[4]) if len(tokens) == 5 else ""))

            def function_applied(self, tokens):
                head = tokens[0]
                name = head.name if isinstance(head, Symbol) else str(head)
                return sympy.Function(str(name))(*tokens[2])

            def PARTIAL(self, token):
                return Symbol("d")                       # \partial behaves as the letter d does

            def decorated_symbol(self, tokens):
                lark = _lark()
                cmd = str(tokens[0])
                inner = [t for t in tokens[1:] if not (isinstance(t, lark.Token) and t.type in ("L_BRACE", "R_BRACE"))][0]
                name = inner.name if isinstance(inner, Symbol) else str(inner)
                return Symbol("%s{%s}" % (cmd, name))

            def fraction(self, tokens):
                # SymPy reads \\frac{d}{dx} as an operator awaiting its operand
                # ("derivative", x) and forgets the numerator of \\frac{dy}{dx}.
                # Here a differential over a differential is Derivative(y, x),
                # and d^n over dx^n (the numerator d**n alone, or d**n times
                # the function) is the n-th derivative.
                num, den = tokens[1], tokens[2]
                d = Symbol("d")
                if isinstance(den, tuple) and len(den) == 2 and den[0] == d:
                    var, order = den[1], 1
                    if isinstance(var, sympy.Pow) and var.exp.is_Integer and var.exp > 1:
                        var, order = var.base, int(var.exp)
                    wrt = (var, order) if order > 1 else var
                    if isinstance(num, tuple) and len(num) == 2 and num[0] == d:
                        return sympy.Derivative(num[1], wrt)
                    if isinstance(num, Basic) and num.has(d):
                        rest = sympy.cancel(num / d ** order)
                        if not rest.has(d):
                            if rest == 1:
                                return "derivative", wrt
                            return sympy.Derivative(rest, wrt)
                return super().fraction(tokens)

        return Transformer()


class LatexReader:
    """Reads LaTeX with the add-on's grammar; see the module docstring.
    One instance serves every document (the parsers are built once)."""

    def __init__(self, grammar_dir: Path = GRAMMAR_DIR):
        self.grammar_dir = Path(grammar_dir)
        self._forest_parser = None
        self._callbacks = None
        self._transformer = None
        self._lock = threading.Lock()

    # -- the parsers ---------------------------------------------------------

    def _build(self) -> None:
        if self._forest_parser is not None:
            return
        with self._lock:                  # a warm-up thread may be building them this moment: wait for it
            if self._forest_parser is not None:
                return
            lark = _lark()
            grammar = (self.grammar_dir / "latex.lark").read_text(encoding="utf-8")
            common = dict(source_path=str(self.grammar_dir) + "/", parser="earley", start="latex_string", lexer="auto",
                          propagate_positions=True, maybe_placeholders=False, keep_all_tokens=True)
            forest_parser = lark.Lark(grammar, ambiguity="forest", **common)
            # The forest carries no tree-building callbacks of its own: a twin
            # parser (any ambiguity mode that builds trees) lends its table.
            self._callbacks = lark.Lark(grammar, ambiguity="explicit", **common).parser.parser.callbacks
            self._transformer = _Transformer()
            self._forest_parser = forest_parser          # last: it is what says the rest is ready

    def warm(self, background: bool = False) -> None:
        """Build the parsers now rather than at the first reading, which would
        wait for them - half a second on a laptop, seconds on a phone, while
        the user is typing.  ``background``: in a thread of its own, so that
        nothing waits, where there are threads (Pyodide has none: there the
        first call builds them)."""
        if self._forest_parser is not None:
            return
        if not background:
            self._build()
            return
        def build():
            try:
                self._build()
            except Exception:  # noqa: BLE001 - the first reading builds them again, and says what is wrong
                pass
        try:
            threading.Thread(target=build, name="latex-grammar", daemon=True).start()
        except RuntimeError:
            pass

    def _chooser(self, choices: Dict[str, int], known_functions=(), memo: Optional[Dict[str, int]] = None):
        lark = _lark()
        from lark.parsers import earley_forest as ef
        callbacks = self._callbacks
        reader = self
        memo = {} if memo is None else memo

        class Chooser(ef.ForestToParseTree):
            """Turns the forest into one tree.  At an ambiguous node it takes the
            alternative `choices` names; at any other it decides *locally*: the
            subtree of each alternative is built (this same way, inside) and the
            one that costs least under tree_cost wins - a decision that depends
            on nothing outside the node, hence memoised by node.  Every choice
            point gone through is noted in `points`."""

            def __init__(self):
                super().__init__(lark.Tree, callbacks, ef.ForestSumVisitor(), False, False)
                self.points: Dict[str, Point] = {}

            def visit_symbol_node_in(self, node):
                kids = super().visit_symbol_node_in(node)
                if kids is None:
                    return kids
                kids = list(kids)
                if node.is_ambiguous and len(kids) > 1:
                    rule = _name(getattr(node.s, "name", node.s))
                    key = "%s@%s-%s" % (rule, node.start, node.end)
                    if key in choices and 0 <= choices[key] < len(kids):
                        idx = choices[key]
                    elif key in memo:
                        idx = memo[key]
                    else:
                        idx = self._decide(node, key, len(kids))
                        memo[key] = idx
                    self.points.setdefault(key, Point(key, rule, int(node.start), int(node.end), len(kids), choice=idx))
                    return [kids[idx]]
                return kids

            def _decide(self, node, key, count):
                best, best_cost = 0, None
                for i in range(min(count, MAX_ALTERNATIVES)):
                    forced = dict(choices)
                    forced[key] = i
                    try:
                        sub = reader._chooser(forced, known_functions, memo).transform(node)
                        cost = tree_cost(sub, known_functions)
                    except Exception:  # noqa: BLE001 - an alternative Lark cannot build
                        continue
                    if best_cost is None or cost < best_cost:
                        best, best_cost = i, cost
                return best

        return Chooser()

    def _tree(self, forest, choices: Dict[str, int], known_functions=()):
        chooser = self._chooser(choices, known_functions)
        tree = chooser.transform(forest)
        return tree, chooser.points

    # -- reading ------------------------------------------------------------------

    def read(self, latex: str, choices: Optional[Dict[str, int]] = None, constants: Optional[Dict[str, bool]] = None,
             known: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Read ``latex``.  ``choices`` fixes alternatives at choice points
        (by key), ``constants`` says which constant names are constants
        (``{"pi": True, "e": False}``; the defaults of :data:`CONSTANTS`
        otherwise), ``known`` maps names to the document's own symbols and
        functions (a symbol of the same name is reused, with its assumptions).

        Returns ``{"ok": True, "expr", "src", "latex", "ambiguities": [...],
        "constants": [...]}`` or ``{"ok": False, "error": ...}`` - with
        ``"incomplete": True`` when the text only stops too early (it is
        being typed: :data:`INCOMPLETE`).  Each
        ambiguity is ``{"key", "fragment", "choice", "options": [{"src",
        "latex"}]}`` - the whole expression under each alternative - and each
        constant ``{"name", "on", "value", "label"}``.
        """
        latex = (latex or "").strip()
        if not latex:
            return {"ok": False, "error": "Nothing to read"}
        self._latex_text = latex
        self._build()
        lark = _lark()
        known = dict(known or {})
        known_functions = {n for n, v in known.items() if callable(v) and not isinstance(v, Basic)} | {
            n for n, v in known.items() if isinstance(v, sympy.core.function.FunctionClass)}
        try:
            forest = self._forest_parser.parse(latex)
        except lark.exceptions.UnexpectedEOF:
            return {"ok": False, "incomplete": True, "error": INCOMPLETE}
        except lark.exceptions.UnexpectedCharacters as exc:
            # a command being typed at the end (\fr on the way to \frac) is
            # unfinished too, not wrong
            tail = re.search(r"\\[A-Za-z]*$", latex)
            if tail and getattr(exc, "pos_in_stream", -1) >= tail.start():
                return {"ok": False, "incomplete": True, "error": INCOMPLETE}
            return self._unreadable(latex, exc)
        except lark.exceptions.UnexpectedInput as exc:
            return self._unreadable(latex, exc)
        except lark.exceptions.LarkError as exc:
            return {"ok": False, "error": f"This LaTeX could not be read: {str(exc).splitlines()[0][:120]}"}

        fixed = {str(k): int(v) for k, v in (choices or {}).items() if isinstance(v, (int, float)) and str(k)}
        tree, points = self._tree(forest, fixed, known_functions)
        chosen = dict(fixed)
        chosen.update({k: p.choice for k, p in points.items() if k not in chosen})
        try:
            expr = self._to_expr(tree)
        except Exception as exc:  # noqa: BLE001 - the transformer's own errors
            return {"ok": False, "error": f"This LaTeX could not be turned into an expression: {str(exc).splitlines()[0][:120]}"}

        expr, consts = self._apply_constants(expr, constants or {})
        expr = self._reuse_known(expr, known)
        finish = lambda e: self._reuse_known(self._apply_constants(e, constants or {})[0], known)   # noqa: E731
        ambiguities = self._describe_points(forest, chosen, points, known_functions, finish)
        # ``choices`` is every decision taken, the user's and the reader's own:
        # sent back with one changed, the others stay as they were, so a pick
        # changes just what was picked (a decision made afresh could flip).
        return {"ok": True, "expr": expr, "src": str(expr), "latex": sympy.latex(expr),
                "ambiguities": ambiguities, "constants": consts, "choices": dict(chosen)}

    @staticmethod
    def _unreadable(latex: str, exc) -> Dict[str, Any]:
        col = getattr(exc, "column", None)
        where = f" at position {col}" if isinstance(col, int) and col > 0 else ""
        snippet = latex[max(0, (col or 1) - 1):(col or 1) + 11] if col else ""
        return {"ok": False, "error": f"This LaTeX could not be read{where}" + (f": near {snippet!r}" if snippet else "")}

    def _to_expr(self, tree) -> Basic:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")          # SymPy's transformer warns on readings it then rejects
            result = self._transformer.transform(tree)
        if isinstance(result, (tuple, list)):
            raise ValueError("a differential or a derivative that stands alone")
        if not isinstance(result, Basic):
            result = sympy.sympify(result)
        return result

    def _describe_points(self, forest, chosen, points, known_functions, finish) -> List[Dict[str, Any]]:
        """Every choice point of the reading, with the whole expression under
        each of its alternatives (``finish`` gives each the reading's constants
        and known names) - points whose alternatives all read the same, and
        points that repeat another's alternatives, left out."""
        out: List[Dict[str, Any]] = []
        seen_sets = set()
        latex_src = None
        for point in sorted(points.values(), key=lambda p: (p.start, -p.end))[:MAX_POINTS]:
            options = []
            for i in range(min(point.count, MAX_ALTERNATIVES)):
                trial = dict(chosen)
                trial[point.key] = i
                try:
                    t, _pts = self._tree(forest, trial, known_functions)
                    e = finish(self._to_expr(t))
                    options.append({"src": str(e), "latex": sympy.latex(e)})
                except Exception:  # noqa: BLE001
                    options.append(None)
            valid = [o for o in options if o is not None]
            if len({o["src"] for o in valid}) < 2:
                continue                                  # every alternative reads the same: not a choice
            signature = tuple(o["src"] if o else None for o in options)
            if signature in seen_sets:
                continue
            seen_sets.add(signature)
            out.append({"key": point.key, "fragment": self._fragment(point), "choice": chosen.get(point.key, 0),
                        "options": [o or {"src": "(not a reading)", "latex": "", "invalid": True} for o in options]})
        return out

    _latex_text: str = ""

    def _fragment(self, point: Point) -> str:
        return self._latex_text[point.start:point.end].strip()

    def _apply_constants(self, expr: Basic, wanted: Dict[str, bool]):
        """Turn the constant names among the free symbols into constants (on
        by default for some, see :data:`CONSTANTS`; ``wanted`` overrides),
        and list every one that occurs with its state."""
        consts = []
        subs = {}
        free = {s.name: s for s in expr.free_symbols if isinstance(s, Symbol)}
        for name, (value, default, label) in CONSTANTS.items():
            if name not in free:
                continue
            on = bool(wanted.get(name, default))
            consts.append({"name": name, "on": on, "value": str(value), "label": label})
            if on:
                subs[free[name]] = value
        if subs:
            expr = expr.subs(subs)
        return expr, consts

    def _reuse_known(self, expr: Basic, known: Dict[str, Any]) -> Basic:
        """A free symbol named like one the document already has becomes
        that one (its assumptions, or its matrix shape, come along)."""
        subs = {}
        for s in expr.free_symbols:
            other = known.get(getattr(s, "name", None))
            if isinstance(other, Basic) and other != s and (isinstance(other, Symbol) or getattr(other, "is_MatrixExpr", False)):
                subs[s] = other
        return expr.subs(subs) if subs else expr


def read_latex(latex: str, **kwargs) -> Dict[str, Any]:
    """One-off reading with a fresh :class:`LatexReader` (tests, scripts)."""
    return LatexReader().read(latex, **kwargs)

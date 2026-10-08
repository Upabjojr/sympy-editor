"""Reading a selection as something to solve, solving it within a time limit,
and checking a solution by substituting it back.

Nothing here knows the editor: the add-on (``__init__.py``) hands in SymPy
objects and gets SymPy objects and plain dicts back.
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from sympy import (And, Basic, ConditionSet, Dummy, EmptySet, Eq, Expr, FiniteSet, ImageSet, Intersection,
                   Interval, Or, Poly, S, Symbol, Tuple, Union, evaluate, latex, linsolve, nonlinsolve, oo,
                   preorder_traversal, simplify, solve, solveset)
from sympy.core.relational import Equality, Relational, Unequality
from sympy.logic.boolalg import BooleanFalse, BooleanTrue
from sympy.sets.sets import Complement, Set

__all__ = ["TimeUp", "time_boxed", "Problem", "read_problem", "default_unknowns", "DOMAINS", "Solution", "Item",
           "solve_problem", "check_item", "describe_set"]


# -- a time limit -------------------------------------------------------------

class TimeUp(BaseException):
    """Raised inside a computation that has run past its time limit.  A
    ``BaseException``, as ``KeyboardInterrupt`` is: SymPy's solvers catch
    ``Exception`` here and there to try another way, and a time limit they
    swallowed would only start the next attempt."""


def time_boxed(fn: Callable[[], Any], seconds: float) -> Any:
    """``fn()``, stopped with :class:`TimeUp` once it has run ``seconds``.

    The check rides on the profiler hook (``sys.setprofile``), which Python
    calls on every function call and return of this thread: no thread, no
    signal, so it works the same in the server's handler threads, the
    widget's, the apps' and in Pyodide, which has neither.  SymPy is Python
    calling Python, so the limit is kept to a fraction of a second; a long
    computation inside one C call (a big integer factorisation) is only
    stopped when it comes back.  The previous hook is put back afterwards."""
    deadline = time.monotonic() + max(0.05, float(seconds))
    counter = [0]
    previous = sys.getprofile()

    def hook(frame, event, arg):
        counter[0] += 1
        if not counter[0] & 127 and time.monotonic() > deadline:
            raise TimeUp()

    sys.setprofile(hook)
    try:
        return fn()
    finally:
        sys.setprofile(previous)


# -- what is being solved -----------------------------------------------------

#: The choices of the domain menu: (the set, its symbol, its name in words).
DOMAINS = {
    "complex": (S.Complexes, "ℂ", "the complex numbers"),
    "real": (S.Reals, "ℝ", "the real numbers"),
    "positive": (Interval.open(0, oo), "ℝ⁺", "the positive real numbers"),
    "integers": (S.Integers, "ℤ", "the integers"),
}

#: Names a reader expects to be the unknowns, in that order of preference.
PREFERRED = ("x", "y", "z", "w", "t", "u", "v", "s", "r")


def _is_inequality(part: Basic) -> bool:
    return isinstance(part, Relational) and not isinstance(part, (Equality, Unequality))


@dataclass
class Problem:
    """A selection read as something to solve: ``parts`` are relations or
    expressions (an expression meaning ``= 0``), ``symbols`` the free
    symbols that can be unknowns, in name order."""

    node: Basic
    parts: List[Basic]
    symbols: List[Symbol]
    system: bool

    @property
    def kind(self) -> str:
        if self.system:
            return "system"
        part = self.parts[0]
        if isinstance(part, Equality):
            return "equation"
        if isinstance(part, Relational):
            return "inequality"
        return "expression"

    def words(self) -> str:
        n = len(self.parts)
        if self.system:
            what = "conditions" if any(isinstance(p, Relational) and not isinstance(p, Equality) for p in self.parts) \
                else "equations"
            exprs = sum(1 for p in self.parts if not isinstance(p, Relational))
            tail = f" ({exprs} of them an expression, solved as = 0)" if exprs else ""
            return f"a system of {n} {what}{tail}"
        return {"equation": "an equation", "inequality": "an inequality",
                "expression": "an expression, solved as = 0"}[self.kind]

    def as_relations(self) -> List[Basic]:
        """Every part as a relation: an expression ``e`` becomes ``e = 0``."""
        return [p if isinstance(p, Relational) else Eq(p, 0, evaluate=False) for p in self.parts]

    def latex(self) -> str:
        rows = [latex(r) for r in self.as_relations()]
        if len(rows) == 1:
            return rows[0]
        return r"\begin{cases} " + r" \\ ".join(rows) + r" \end{cases}"


def read_problem(node: Basic) -> Problem:
    """The selection as a problem, or ``ValueError`` saying why it is none.

    A relation is one equation or inequality, an expression means ``= 0``,
    and an ``And``, a set or a tuple of them - or a range of an ``And``'s
    arguments, which the editor extracts as an ``And`` - is a system."""
    system = isinstance(node, (And, FiniteSet, Tuple))
    parts = list(node.args) if system else [node]
    if isinstance(node, (BooleanTrue, BooleanFalse)):
        raise ValueError(f"The selection is {node}: there is nothing left to solve")
    if not parts:
        raise ValueError("The selection is empty: there is nothing to solve")
    for part in parts:
        if getattr(part, "is_Matrix", False) or (isinstance(part, Relational) and
                                                 any(getattr(s, "is_Matrix", False) for s in part.args)):
            raise ValueError("A matrix equation: write it as a system of its entries to solve it here")
        if isinstance(part, Or):
            raise ValueError("An alternative (or): select one side of it and solve that")
        if isinstance(part, (BooleanTrue, BooleanFalse)):
            continue
        if isinstance(part, Relational) or isinstance(part, Expr):
            continue
        raise ValueError(f"A {type(part).__name__} is not an equation, an inequality nor an expression to solve")
    found = set()
    for part in parts:
        found |= {s for s in part.free_symbols if isinstance(s, Symbol)}
    symbols = sorted(found, key=lambda s: (s.name, str(s.assumptions0)))
    names = [s.name for s in symbols]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise ValueError(f"Two different symbols are called {', '.join(twice)} (the same name, other assumptions): "
                         "give one of them another name to solve here")
    if not symbols:
        raise ValueError("The selection has no free symbol: there is no unknown to solve for")
    return Problem(node, parts, symbols, system)


def default_unknowns(problem: Problem) -> List[Symbol]:
    """As many unknowns as there are equations (one at least): the names one
    expects to be unknowns (x, y, z...) first, then by name; in name order."""
    want = max(1, min(len(problem.parts), len(problem.symbols)))
    rank = {name: i for i, name in enumerate(PREFERRED)}
    chosen = sorted(problem.symbols, key=lambda s: (rank.get(s.name, len(PREFERRED)), s.name))[:want]
    return [s for s in problem.symbols if s in chosen]


# -- the solutions ----------------------------------------------------------------

@dataclass
class Item:
    """One line of the list: a solution (``kind == "point"``: a value for
    each unknown, in ``assign``) or a piece of the solution set that is not
    one value (``"set"``: an interval, a family, a condition...).  ``assign``
    is what is substituted to check it - for a family, its general member -
    and ``insert`` what *Insert* puts in the formula."""

    kind: str
    latex: str
    words: str
    insert: Basic
    assign: Optional[Dict[Symbol, Basic]] = None
    check_words: str = ""

    def as_json(self, index: int) -> Dict[str, Any]:
        return {"index": index, "kind": self.kind, "latex": self.latex, "words": self.words,
                "src": str(self.insert), "check": self.assign is not None}


@dataclass
class Solution:
    unknowns: List[Symbol]
    domain: str
    method: str
    whole: Basic                      # the solution set (one unknown) or the set of tuples (several)
    items: List[Item]
    summary: str
    notes: List[str] = field(default_factory=list)

    def as_json(self) -> Dict[str, Any]:
        names = [u.name for u in self.unknowns]
        if len(self.unknowns) == 1:
            head = latex(self.unknowns[0])
        else:
            head = r"\left(" + ", ".join(latex(u) for u in self.unknowns) + r"\right)"
        return {"unknowns": names, "domain": DOMAINS[self.domain][1], "domain_words": DOMAINS[self.domain][2],
                "method": self.method, "summary": self.summary, "notes": list(self.notes),
                "set_latex": head + r" \in " + latex(self.whole), "set_src": str(self.whole),
                "items": [item.as_json(i) for i, item in enumerate(self.items)]}


def _fresh(base: str, taken: set) -> str:
    base = base.lstrip("_") or "n"
    name, i = base, 0
    while name in taken:
        i += 1
        name = f"{base}_{i}"
    taken.add(name)
    return name


_COUNTING = (S.Integers, S.Naturals, S.Naturals0)


def tidy(obj: Basic, taken: set) -> Basic:
    """``obj`` with every ``Dummy`` a solver made (the ``_n`` of an image
    set) replaced by a plain symbol under a name nothing else uses - an
    integer one when it runs over the integers, so that checking ``2πn + π/2``
    knows ``sin`` of it.  Such a symbol prints as itself, is read back from a
    saved session and can be inserted into the formula."""
    mapping: Dict[Basic, Basic] = {}
    for node in preorder_traversal(obj):
        if isinstance(node, ImageSet):
            for var, base in zip(node.lamda.variables, node.base_sets):
                if isinstance(var, Dummy) and var not in mapping:
                    mapping[var] = Symbol(_fresh(var.name, taken), integer=True) if base in _COUNTING \
                        else Symbol(_fresh(var.name, taken))
    for dummy in sorted(obj.atoms(Dummy), key=str):
        if dummy not in mapping:
            mapping[dummy] = Symbol(_fresh(dummy.name, taken), **dummy.assumptions0)
    return obj.xreplace(mapping) if mapping else obj


def _set_name(s: Basic) -> str:
    for key, (dom, sign, _words) in DOMAINS.items():
        if s == dom:
            return sign
    if s == S.Naturals:
        return "ℕ"
    if s == S.Naturals0:
        return "ℕ₀"
    return str(s)


def describe_set(s: Basic, unknown: str) -> str:
    """A set of solutions in words - what the symbols alone do not say."""
    if s is S.EmptySet or isinstance(s, type(EmptySet)):
        return "no solution"
    if isinstance(s, FiniteSet):
        n = len(s)
        return "one solution" if n == 1 else f"{n} solutions"
    if s in (S.Reals, S.Complexes, S.Integers, S.Naturals, S.Naturals0) or s == Interval.open(0, oo):
        return f"every value of {unknown} in {_set_name(s)}: it holds for all of them"
    if isinstance(s, Interval):
        ends = "an unbounded" if (s.start.is_infinite or s.end.is_infinite) else "a"
        return f"infinitely many: every value of {unknown} in {ends} interval"
    if isinstance(s, ImageSet):
        variables = ", ".join(str(v) for v in s.lamda.variables)
        bases = " and ".join(_set_name(b) for b in s.base_sets)
        kind = "integer" if all(b == S.Integers for b in s.base_sets) else None
        each = f"every {kind} {variables}" if kind else f"every {variables} in {bases}"
        return f"infinitely many: one for {each}"
    if isinstance(s, ConditionSet):
        return (f"SymPy could not solve this in closed form: the solutions are the values of {s.sym} "
                f"in {_set_name(s.base_set)} for which the condition holds")
    if isinstance(s, Complement):
        return f"every value of {unknown} in the first set except those in the second"
    if isinstance(s, Intersection):
        return "the values that lie in all of these sets (SymPy left the intersection as it is)"
    if isinstance(s, Union):
        return "the values in any of these sets"
    return f"a set of solutions ({type(s).__name__})"


def _pieces(s: Basic) -> List[Basic]:
    """A union's parts, each finite set split into its elements' sets."""
    if isinstance(s, Union):
        return [p for a in s.args for p in _pieces(a)]
    return [s]


def _linear(exprs: Sequence[Expr], unknowns: Sequence[Symbol]) -> bool:
    try:
        return all(Poly(e, *unknowns).total_degree() <= 1 for e in exprs)
    except Exception:
        return False


def _point_latex(unknowns: Sequence[Symbol], values: Sequence[Basic]) -> str:
    return r",\quad ".join(f"{latex(u)} = {latex(v)}" for u, v in zip(unknowns, values))


def _family_words(values: Sequence[Basic], unknowns: Sequence[Symbol]) -> str:
    free = sorted({str(u) for v in values for u in getattr(v, "free_symbols", ()) if u in unknowns})
    if free:
        return f"infinitely many: {', '.join(free)} {'is' if len(free) == 1 else 'are'} free (any value)"
    return ""


def solve_problem(problem: Problem, unknowns: Sequence[Symbol], domain: str = "complex",
                  taken: Optional[set] = None) -> Solution:
    """Solve ``problem`` for ``unknowns`` over the domain named ``domain``.

    One unknown: ``solveset`` of every part over the domain, intersected - an
    equation, an inequality, or a system with mixed conditions.  Several:
    ``linsolve`` when the system is linear in them, ``nonlinsolve``
    otherwise, ``solve`` when that one gives up; the domain then filters the
    solutions found (it cannot steer those solvers)."""
    if domain not in DOMAINS:
        raise ValueError(f"No domain {domain!r}: one of {', '.join(DOMAINS)}")
    unknowns = list(unknowns)
    if not unknowns:
        raise ValueError("Tick at least one unknown")
    taken = set(taken or ()) | {s.name for s in problem.symbols}
    dom, sign, _words = DOMAINS[domain]
    notes: List[str] = []
    rels = problem.as_relations()
    if len(unknowns) == 1:
        x = unknowns[0]
        if domain == "complex" and any(_is_inequality(r) for r in rels):
            domain, dom, sign = "real", S.Reals, DOMAINS["real"][1]
            notes.append("An inequality has no meaning among the complex numbers: solved over ℝ.")
        whole = None
        for rel in rels:
            if isinstance(rel, BooleanFalse):
                part = S.EmptySet
            elif isinstance(rel, BooleanTrue):
                part = dom
            else:
                part = solveset(rel, x, dom)
            whole = part if whole is None else Intersection(whole, part)
        whole = tidy(whole, taken)
        items: List[Item] = []
        for piece in _pieces(whole):
            if isinstance(piece, FiniteSet):
                for value in piece.args:
                    items.append(Item("point", f"{latex(x)} = {latex(value)}", "", Eq(x, value, evaluate=False),
                                      {x: value}))
            elif piece is S.EmptySet:
                continue
            else:
                assign, check_words = None, ""
                if isinstance(piece, ImageSet) and len(piece.lamda.variables) == 1:
                    assign = {x: piece.lamda.expr}
                    var = piece.lamda.variables[0]
                    check_words = f"for every {'integer ' if var.is_integer else ''}{var}"
                items.append(Item("set", f"{latex(x)} \\in {latex(piece)}", describe_set(piece, str(x)), piece,
                                  assign, check_words))
        summary = _summary(whole, items, sign)
        return Solution([x], domain, "solveset", whole, items, summary, notes)

    if any(_is_inequality(r) or isinstance(r, Unequality) for r in rels):
        raise ValueError("SymPy's solvers do not reduce inequalities in several unknowns: tick one unknown")
    exprs = [r.lhs - r.rhs if isinstance(r, Equality) else (S.Zero if isinstance(r, BooleanTrue) else S.One)
             for r in rels]
    method = "linsolve" if _linear(exprs, unknowns) else "nonlinsolve"
    found: Optional[Basic] = None
    if method == "linsolve":
        found = linsolve(exprs, unknowns)
    else:
        try:
            found = nonlinsolve(exprs, unknowns)
        except Exception as exc:                     # NotImplementedError and the like: try solve
            notes.append(f"nonlinsolve gave up ({type(exc).__name__}); solve was used instead.")
            found = None
        if found is None or isinstance(found, ConditionSet):
            method = "solve"
            dicts = solve(exprs, unknowns, dict=True)
            found = FiniteSet(*[Tuple(*[d.get(u, u) for u in unknowns]) for d in dicts])
    found = tidy(found, taken)
    items = []
    dropped = 0
    kept: List[Basic] = []
    for piece in _pieces(found):
        if piece is S.EmptySet:
            continue
        if not isinstance(piece, FiniteSet):
            items.append(Item("set", latex(piece), describe_set(piece, "the unknowns"), piece))
            kept.append(piece)
            continue
        for point in piece.args:
            values = list(point.args) if isinstance(point, Tuple) else [point]
            words = []
            if domain != "complex":
                verdicts = [dom.contains(v) if not isinstance(v, Set) else None for v in values]
                if any(v is S.false for v in verdicts):
                    dropped += 1
                    continue
                if any(v is not None and v is not S.true for v in verdicts):
                    words.append(f"if the values lie in {sign}")
            family = _family_words(values, unknowns)
            if family:
                words.insert(0, family)
            kept.append(point)
            if any(isinstance(v, Set) for v in values):
                items.append(Item("set", _point_latex(unknowns, values), "; ".join(words) or
                                  "a family of solutions", FiniteSet(Tuple(*values))))
                continue
            # an unknown left free (y = y) is said in words, not written as an equation
            pairs = [(u, v) for u, v in zip(unknowns, values) if v != u] or list(zip(unknowns, values))
            eqs = [Eq(u, v, evaluate=False) for u, v in pairs]
            items.append(Item("point", _point_latex([u for u, _ in pairs], [v for _, v in pairs]), "; ".join(words), And(*eqs, evaluate=False),
                              dict(zip(unknowns, values))))
    if dropped:
        notes.append(f"{dropped} solution{'s' if dropped > 1 else ''} outside {sign} left out.")
    whole = found if not dropped else FiniteSet(*[k for k in kept if not isinstance(k, Set)])
    summary = _summary(whole, items, sign)
    return Solution(unknowns, domain, method, whole, items, summary, notes)


def _summary(whole: Basic, items: List[Item], sign: str) -> str:
    if whole is S.EmptySet or not items:
        return f"No solution in {sign}."
    if any(isinstance(p, ConditionSet) for p in preorder_traversal(whole) if isinstance(p, Basic)):
        return "SymPy could not solve all of it: what it could not is left as a condition."
    points = sum(1 for i in items if i.kind == "point")
    if points == len(items) and not any("free" in i.words for i in items):
        return "One solution." if points == 1 else f"{points} solutions."
    return "Infinitely many solutions."


# -- checking a solution ------------------------------------------------------------

def _residue(rel: Basic) -> Basic:
    """``lhs - rhs`` of an equation, simplified."""
    return simplify(rel.lhs - rel.rhs)


def check_item(problem: Problem, item: Item) -> List[Dict[str, Any]]:
    """Every part of the problem with the solution substituted, and what it
    simplifies to: ``holds`` (True), ``fails`` (False), or ``open`` with the
    residue SymPy could not decide (a parameter left, a condition)."""
    if item.assign is None:
        raise ValueError("This is a set of values, not one value to substitute: there is nothing to check")
    out = []
    for rel in problem.as_relations():
        if isinstance(rel, (BooleanTrue, BooleanFalse)):
            out.append({"before": latex(rel), "after": latex(rel), "verdict": "holds" if rel else "fails"})
            continue
        with evaluate(False):                         # shown as substituted: (-2)**2 = 4, not 4 = 4
            shown = rel.func(rel.lhs.xreplace(item.assign), rel.rhs.xreplace(item.assign))
        lhs = rel.lhs.subs(item.assign, simultaneous=True)
        rhs = rel.rhs.subs(item.assign, simultaneous=True)
        before = rel.func(lhs, rhs, evaluate=False)
        verdict, after = "open", None
        if isinstance(rel, (Equality, Unequality)):
            d = _residue(before)
            if d == 0:
                ok = True
            elif d.is_zero is False:
                ok = False
            else:
                ok = _numerically_zero(d)
            if ok is None:
                after = d if isinstance(rel, Equality) else rel.func(d, 0)
            else:
                ok = ok if isinstance(rel, Equality) else not ok
                after = S.true if ok else S.false
                verdict = "holds" if ok else "fails"
                if not ok and isinstance(rel, Equality):
                    after = Eq(d, 0, evaluate=False)
        else:
            after = simplify(rel.func(lhs, rhs))
            if after is S.true:
                verdict = "holds"
            elif after is S.false:
                verdict = "fails"
        out.append({"before": latex(shown), "after": latex(after), "verdict": verdict})
    return out


def _numerically_zero(d: Basic) -> Optional[bool]:
    """Whether a residue with no symbol left is zero, by evaluating it to 30
    digits (a root of a quintic, an ``RootOf``): None when it has symbols or
    no value."""
    if getattr(d, "free_symbols", None):
        return None
    try:
        value = complex(d.evalf(30))
    except Exception:
        return None
    if not (math.isfinite(value.real) and math.isfinite(value.imag)):
        return None
    return abs(value) < 1e-20

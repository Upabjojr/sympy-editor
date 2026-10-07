"""Step-by-step explanations: integrals, derivatives, simple equations.

Pure SymPy, nothing of the editor's: :func:`explain` takes an expression and
returns an :class:`Explanation` - where it starts, the steps, each a word
about the rule and the expression it leaves, and what it could not explain,
said in words.

* **Integrals** follow :func:`sympy.integrals.manualintegrate.integral_steps`:
  its tree of rules is flattened one rule at a time (each rule evaluated with
  its sub-rules replaced by :class:`DontKnowRule`, which leaves the integrals
  they would do standing), as ``examples/manualintegrate_steps.ipynb`` shows.
* **Derivatives** are worked here: the expression keeps unevaluated
  ``Derivative`` holes, and each step applies one rule - sum, constant
  multiple, product, quotient, power, chain, a known derivative - to the
  first hole.
* **Equations** of degree one or two in one unknown: expand, collect, divide;
  for a quadratic the discriminant and the formula, or a factor ``x`` /
  a square root when a coefficient is zero.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sympy import (Add, Basic, Derivative, Dummy, Eq, Expr, Integral, Mul, Or, Poly, Pow, S, Symbol, diff, exp,
                   expand, fraction, integrate, latex, log, preorder_traversal, simplify, sqrt, together)
from sympy.core.function import AppliedUndef, ArgumentIndexError, Function
from sympy.core.relational import Equality, Relational
from sympy.integrals.manualintegrate import AlternativeRule, DontKnowRule, Rule, URule, integral_steps

__all__ = ["Step", "Explanation", "explain", "integration_steps", "differentiation_steps", "solving_steps",
           "TASKS"]

#: What :func:`explain` can be asked to do.  "auto" picks by the node's type.
TASKS = ("auto", "integrate", "differentiate", "solve")

#: More steps than this is not an explanation any more (and a rule that
#: keeps producing work would otherwise never stop).
MAX_STEPS = 80


@dataclass
class Step:
    """One step: what was done (``text``, words), the whole expression after
    it (``expr``; None for a remark - the discriminant, the coefficients -
    which is shown but has nothing to apply), and the rule's own parameters
    as LaTeX (``detail``: ``u = x,\\ dv = \\sin x``)."""
    text: str
    expr: Optional[Basic] = None
    detail: str = ""
    shown: Optional[str] = None       # LaTeX to show instead of the expression's own
    apply: bool = True                # False: shown, but not a value of the start (a definite
                                      # integral's antiderivative), so not offered to apply

    @property
    def applicable(self) -> bool:
        return self.apply and self.expr is not None

    def latex(self) -> str:
        if self.shown is not None:
            return self.shown
        return latex(self.expr) if self.expr is not None else ""


@dataclass
class Explanation:
    """What :func:`explain` found: the ``task`` it did, the expression it
    starts from (``start``: the integral, the derivative, the equation), the
    steps, and ``message``: what it could not explain, in words (None when
    the steps go all the way).  ``offers`` are the tasks that make sense for
    a node no task was chosen for, ``vars`` the symbols one may pick."""
    task: str
    start: Optional[Basic]
    steps: List[Step] = field(default_factory=list)
    message: Optional[str] = None
    var: Optional[Symbol] = None
    vars: List[Symbol] = field(default_factory=list)
    offers: List[str] = field(default_factory=list)

    @property
    def result(self) -> Optional[Basic]:
        for step in reversed(self.steps):
            if step.expr is not None:
                return step.expr
        return None


class CannotExplain(ValueError):
    """Raised inside the engines for what has no steps; :func:`explain` turns
    it into an explanation's ``message``."""


# ---------------------------------------------------------------------------
# helpers

def _symbols(expr: Basic) -> List[Symbol]:
    return sorted((s for s in expr.free_symbols if isinstance(s, Symbol)), key=lambda s: (s.name, str(s)))


def _pick_var(expr: Basic, var) -> Symbol:
    """The variable: the one asked for (a Symbol, or a name looked up among
    the expression's symbols), else the expression's only free symbol."""
    free = _symbols(expr)
    if var is not None:
        if isinstance(var, Symbol):
            return var
        for s in free:
            if s.name == str(var):
                return s
        return Symbol(str(var))
    if len(free) == 1:
        return free[0]
    if not free:
        raise CannotExplain(f"{expr} has no variable")
    names = ", ".join(s.name for s in free)
    raise CannotExplain(f"{expr} has several symbols ({names}): choose the variable")


def _fresh_names(dummies: Iterable[Dummy], taken: Iterable[Basic]) -> Dict[Dummy, Symbol]:
    """A plain Symbol for every Dummy (the ``_u`` of a substitution), named
    after it when the name is free (``u``), else ``v``, ``w``, ``t``...: a
    step is something one can apply to the formula, where a Dummy would be a
    name nobody can type."""
    used = {str(s) for s in taken}
    out: Dict[Dummy, Symbol] = {}
    for d in sorted(dummies, key=lambda d: (d.name, d.dummy_index)):
        for name in [d.name.lstrip("_") or "u", "u", "v", "w", "t", "s", "z"] + [f"u{i}" for i in range(1, 50)]:
            if name not in used:
                used.add(name)
                out[d] = Symbol(name, **d.assumptions0)
                break
    return out


def _steps_without_dummies(steps: List[Step], start: Basic) -> List[Step]:
    dummies = set()
    for st in steps:
        if st.expr is not None:
            dummies |= st.expr.atoms(Dummy)
    if not dummies:
        return steps
    names = _fresh_names(dummies, start.free_symbols)
    detail_names = {d: s for d, s in names.items()}
    out = []
    for st in steps:
        expr = st.expr.xreplace(names) if st.expr is not None else None
        detail = st.detail
        for d, s in detail_names.items():
            detail = detail.replace(latex(d), latex(s))
        out.append(dataclasses.replace(st, expr=expr, detail=detail))
    return out


def _tex(value: Any) -> str:
    return latex(value) if isinstance(value, Basic) else latex(S(value))


# ---------------------------------------------------------------------------
# integrals (after examples/manualintegrate_steps.ipynb)

#: Rules in words, by class name.  Anything not here is named after its
#: class: "FresnelSRule" reads "Fresnel S rule".
INTEGRAL_RULES = {
    "ConstantRule": "Integral of a constant: ∫ c dx = c·x",
    "ConstantTimesRule": "Constant multiple: take the constant factor out of the integral",
    "PowerRule": "Power rule: ∫ xⁿ dx = xⁿ⁺¹/(n + 1)",
    "NestedPowRule": "Power rule, for a power of a power",
    "AddRule": "Sum rule: integrate term by term",
    "URule": "Substitution",
    "PartsRule": "Integration by parts: ∫ u dv = u·v − ∫ v du",
    "CyclicPartsRule": "Integration by parts, repeated until the integral comes back, then solved for it",
    "ExpRule": "Exponential: ∫ aˣ dx = aˣ/ln a",
    "ReciprocalRule": "Reciprocal: ∫ 1/x dx = ln x",
    "SinRule": "Known integral: ∫ sin x dx = −cos x",
    "CosRule": "Known integral: ∫ cos x dx = sin x",
    "SecTanRule": "Known integral: ∫ sec x tan x dx = sec x",
    "CscCotRule": "Known integral: ∫ csc x cot x dx = −csc x",
    "Sec2Rule": "Known integral: ∫ sec² x dx = tan x",
    "Csc2Rule": "Known integral: ∫ csc² x dx = −cot x",
    "SinhRule": "Known integral: ∫ sinh x dx = cosh x",
    "CoshRule": "Known integral: ∫ cosh x dx = sinh x",
    "ArcsinRule": "Known integral: ∫ 1/√(1 − x²) dx = arcsin x",
    "ArcsinhRule": "Known integral: ∫ 1/√(1 + x²) dx = arsinh x",
    "ArctanRule": "Arctangent form: ∫ 1/(a + b x²) dx",
    "ReciprocalSqrtQuadraticRule": "Reciprocal of the square root of a quadratic",
    "SqrtQuadraticRule": "Square root of a quadratic",
    "RewriteRule": "Rewrite the integrand",
    "TrigSubstitutionRule": "Trigonometric substitution",
    "PiecewiseRule": "Integrate each piece",
    "HeavisideRule": "Heaviside step function",
    "DiracDeltaRule": "Dirac delta",
    "ErfRule": "Gaussian integral: the result is an error function",
    "DerivativeRule": "The integral of a derivative is the function itself",
    "DontKnowRule": "No rule is known for this integral",
}

#: How a rule's own fields are named in its detail (None: not shown - the
#: integrand and the variable are in the formula already, a sub-rule is a
#: step of its own).
INTEGRAL_FIELDS = {"integrand": None, "variable": None, "u_var": None, "other": None, "rewritten": None,
                   "substep": None, "substeps": None, "second_step": None, "v_step": None, "alternatives": None,
                   "constant": "c", "exp": "n", "base": None, "u": "u", "dv": "dv", "u_func": "u"}


def _rule_fields(rule) -> List[str]:
    return [f.name for f in dataclasses.fields(rule)]


def _sub_rules(value):
    if isinstance(value, Rule):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _sub_rules(item)


def _children(rule) -> List[Rule]:
    return [c for slot in _rule_fields(rule) for c in _sub_rules(getattr(rule, slot))]


def _stub(value):
    """``value`` with every rule in it replaced by a DontKnowRule, which
    evaluates to the integral it would have done: a hole for a later step."""
    if isinstance(value, Rule):
        return DontKnowRule(value.integrand, value.variable)
    if isinstance(value, list):
        return [_stub(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_stub(v) for v in value)
    return value


def _one_rule(rule) -> Tuple[Basic, bool]:
    """What this rule alone does, and whether its sub-rules are left to do.
    A rule that cannot be run half way (CyclicPartsRule reaches into its
    sub-rules) does all of its work in one step."""
    try:
        return type(rule)(*[_stub(getattr(rule, s)) for s in _rule_fields(rule)]).eval(), True
    except Exception:
        return rule.eval(), False


def _rule_text(rule) -> str:
    name = type(rule).__name__
    if name in INTEGRAL_RULES:
        return INTEGRAL_RULES[name]
    short = name[:-4] if name.endswith("Rule") else name
    words = re.findall(r"[A-Z][a-z0-9']*|[A-Z]+(?![a-z])", short) or [short]
    return " ".join([words[0]] + [w.lower() for w in words[1:]]) + " rule"


def _rule_detail(rule) -> str:
    if isinstance(rule, URule):
        du = diff(rule.u_func, rule.variable)
        return (f"u = {latex(rule.u_func)},\\quad du = {latex(du)}\\,d{latex(rule.variable)}"
                .replace("u = ", f"{latex(rule.u_var)} = ", 1).replace("du = ", f"d{latex(rule.u_var)} = ", 1))
    parts = []
    for slot in _rule_fields(rule):
        label = INTEGRAL_FIELDS.get(slot, f"\\text{{{slot}}}")
        if slot == "exp" and type(rule).__name__ != "PowerRule":
            label = None
        if label is None:
            continue
        value = getattr(rule, slot)
        if list(_sub_rules(value)) or not isinstance(value, (Basic, int)):
            continue
        parts.append(f"{label} = {_tex(value)}")
    return ",\\quad ".join(parts)


def _find_hole(expr: Basic, rule) -> Optional[Integral]:
    for node in preorder_traversal(expr):
        if isinstance(node, Integral) and node.function == rule.integrand and list(node.variables) == [rule.variable] \
                and all(len(lim) == 1 for lim in node.limits):
            return node
    return None


def _flatten(rule, expr: Basic, out: List[Step]) -> Basic:
    """Fill the hole ``rule`` explains in ``expr``, one step, then its
    sub-rules' holes in turn."""
    if len(out) > MAX_STEPS:
        raise CannotExplain("This integral takes too many steps to show")
    hole = _find_hole(expr, rule)
    if hole is None:
        return expr                       # an alternative already done: nothing of this rule is left
    if isinstance(rule, URule):
        # In the new variable, as one writes it - the rule's own eval puts the
        # old one back at once, which reads as an integral over cos(x).
        result, partial = Integral(rule.substep.integrand, rule.u_var), True
    elif isinstance(rule, DontKnowRule):
        return expr
    else:
        result, partial = _one_rule(rule)
    filled = expr.xreplace({hole: result})
    if filled != expr and not isinstance(rule, AlternativeRule):
        out.append(Step(_rule_text(rule), filled, _rule_detail(rule)))
        expr = filled
    if partial:
        for child in _children(rule):
            expr = _flatten(child, expr, out)
    if isinstance(rule, URule):
        back = expr.xreplace({rule.u_var: rule.u_func})
        if back != expr:
            out.append(Step(f"Substitute back", back, f"{latex(rule.u_var)} = {latex(rule.u_func)}"))
            expr = back
    return expr


def integration_steps(integrand: Expr, var: Symbol, limits: Tuple = ()) -> Explanation:
    """The steps of ``Integral(integrand, (var, *limits))``."""
    start = Integral(integrand, (var, *limits)) if limits else Integral(integrand, var)
    expl = Explanation("integrate", start, var=var)
    rule = integral_steps(integrand, var)
    steps: List[Step] = []
    indefinite = Integral(integrand, var)
    if isinstance(rule, DontKnowRule):
        expl.message = "SymPy knows no rule-by-rule way to do this integral"
        try:
            whole = integrate(integrand, var)
        except Exception:
            whole = None
        if whole is not None and not whole.has(Integral):
            expl.message += ": the result below is SymPy's integrate, with no steps to show"
            steps.append(Step("SymPy's integrate (no steps to show)", whole))
    else:
        try:
            antider = _flatten(rule, indefinite, steps)
        except CannotExplain as exc:
            expl.message = str(exc)
            antider = None
        if antider is not None and antider.has(Integral):
            expl.message = "Some integrals are left: SymPy knows no rule for them"
    steps = _steps_without_dummies(steps, start)
    if limits and len(limits) == 2:
        # A definite integral: the steps so far find an antiderivative, which
        # is not the integral's value - shown, not offered to apply - and the
        # last steps evaluate it between the limits.
        a, b = limits
        steps = [dataclasses.replace(st, apply=False) for st in steps]
        steps.insert(0, Step("First find an antiderivative", None, shown=latex(indefinite)))
        if not expl.message and len(steps) > 1:
            F = steps[-1].expr
            value = F.subs(var, b) - F.subs(var, a)
            steps.append(Step("Evaluate the antiderivative at the limits: F(b) \u2212 F(a)", value,
                              shown=f"\\left[{latex(F)}\\right]_{{{latex(a)}}}^{{{latex(b)}}} = {latex(value)}"))
            nice = simplify(value)
            if nice != value:
                steps.append(Step("Simplify", nice))
    elif steps and not expl.message:
        steps.append(Step("Add the constant of integration", None, shown=f"{latex(steps[-1].expr)} + C"))
    expl.steps = steps
    return expl


# ---------------------------------------------------------------------------
# derivatives

def _is_hole(node: Basic, var: Symbol) -> bool:
    return (isinstance(node, Derivative) and tuple(node.variable_count) == ((var, 1),)
            and not isinstance(node.expr, AppliedUndef))


def _derivative_rule(g: Expr, x: Symbol) -> Tuple[Expr, str, str]:
    """One rule applied to ``d/dx g``: the expression that replaces it (with
    holes ``Derivative(h, x)`` for what is left), the rule in words, and its
    parameters as LaTeX."""
    D = lambda h: Derivative(h, x)       # noqa: E731 - a hole
    if not g.has(x):
        return S.Zero, "Constant rule: the derivative of a constant is 0", ""
    if g == x:
        return S.One, f"The derivative of {x} with respect to {x} is 1", ""
    if isinstance(g, Add):
        terms = [t for t in g.args if t.has(x)]
        dropped = len(terms) < len(g.args)
        text = "Sum rule: differentiate term by term" + (" (the constant terms drop out)" if dropped else "")
        return Add(*[D(t) for t in terms]), text, ""
    if isinstance(g, Mul):
        coeff, rest = g.as_independent(x, as_Add=False)
        if coeff != 1:
            return coeff * D(rest), "Constant multiple rule: take the constant factor out", f"c = {latex(coeff)}"
        num, den = fraction(rest, exact=True)
        if den.has(x) and den != 1 and num != 1:
            new = (D(num) * den - num * D(den)) / den**2
            return new, "Quotient rule: (f/g)′ = (f′g − fg′)/g²", \
                f"f = {latex(num)},\\quad g = {latex(den)}"
        factors = list(rest.args)
        new = Add(*[Mul(*[D(f) if j == i else f for j, f in enumerate(factors)]) for i in range(len(factors))])
        return new, "Product rule: (fg)′ = f′g + fg′", \
            ",\\quad ".join(f"{n} = {latex(f)}" for n, f in zip("fghkmn", factors))
    if isinstance(g, Pow):
        b, e = g.args
        if not e.has(x):
            if b == x:
                return e * x ** (e - 1), "Power rule: (xⁿ)′ = n·xⁿ⁻¹", f"n = {latex(e)}"
            return e * b ** (e - 1) * D(b), "Chain rule with the power rule: (uⁿ)′ = n·uⁿ⁻¹·u′", \
                f"u = {latex(b)},\\quad n = {latex(e)}"
        if not b.has(x):
            if e == x:
                return g * log(b), "Exponential rule: (aˣ)′ = aˣ·ln a", f"a = {latex(b)}"
            return g * log(b) * D(e), "Chain rule with the exponential rule: (aᵘ)′ = aᵘ·ln a·u′", \
                f"a = {latex(b)},\\quad u = {latex(e)}"
        return g * D(e * log(b)), "Logarithmic differentiation: (bᵉ)′ = bᵉ·(e·ln b)′", \
            f"b = {latex(b)},\\quad e = {latex(e)}"
    if isinstance(g, AppliedUndef):
        return g.diff(x), f"{g.func} is not a known function: its derivative stays as it is", ""
    if isinstance(g, Function) and len(g.args) == 1:
        u = g.args[0]
        try:
            outer = g.fdiff(1)
        except (ArgumentIndexError, NotImplementedError, TypeError):
            outer = None
        if outer is not None and not outer.has(Derivative, Integral):
            name = type(g).__name__
            if u == x:
                return outer, f"Known derivative of {name}", f"\\frac{{d}}{{d{latex(x)}}}{latex(g)} = {latex(outer)}"
            return outer * D(u), f"Chain rule: the derivative of the outer {name} times the derivative of the inside", \
                _chain_detail(g, u, x)
    # Anything else (several arguments, an Abs, a Piecewise, an integral):
    # SymPy's own diff, said so.
    return g.diff(x), f"SymPy differentiates {type(g).__name__} directly (no rule spelled out for it)", ""


def _chain_detail(g: Expr, u: Expr, x: Symbol) -> str:
    """``u = x^2,  d/dx sin(u) = cos(u) du/dx``: the chain rule's parts."""
    U = Symbol("u")
    try:
        outer = g.func(U).fdiff(1)
    except Exception:
        return f"u = {latex(u)}"
    return f"u = {latex(u)},\\quad \\frac{{d}}{{du}}{latex(g.func(U))} = {latex(outer)}"


def differentiation_steps(expr: Expr, var: Symbol, order: int = 1, more: Tuple = ()) -> Explanation:
    """The steps of ``Derivative(expr, (var, order))``; ``more``: further
    ``(variable, count)`` pairs, differentiated after it."""
    counts = [(var, order)] + list(more)
    start = Derivative(expr, *counts)
    expl = Explanation("differentiate", start, var=var)
    rounds = [v for v, n in counts for _ in range(int(n))]
    current = expr
    steps: List[Step] = []
    for k, v in enumerate(rounds):
        remaining = rounds[k + 1:]

        def outer(e, remaining=remaining):
            return Derivative(e, *remaining) if remaining else e
        if k:
            steps.append(Step(f"Differentiate again, with respect to {v}", None,
                              shown=latex(Derivative(current, v))))
        work = Derivative(current, v)
        stuck = set()
        while True:
            hole = next((n for n in preorder_traversal(work) if _is_hole(n, v) and n not in stuck), None)
            if hole is None:
                break
            if len(steps) > MAX_STEPS:
                expl.message = "This derivative takes too many steps to show"
                break
            new, text, detail = _derivative_rule(hole.expr, v)
            if new.has(hole):
                stuck.add(hole)
                continue
            work = work.xreplace({hole: new})
            steps.append(Step(text, outer(work), detail))
        if stuck:
            expl.message = "Some derivatives are left as they stand: no rule for them here"
        current = work
        nice = simplify(current)
        if not current.has(Derivative) and nice != current and _shorter(nice, current):
            current = nice
            steps.append(Step("Simplify", outer(current)))
    expl.steps = steps
    return expl


def _shorter(a: Basic, b: Basic) -> bool:
    from sympy import count_ops
    return count_ops(a) < count_ops(b)


# ---------------------------------------------------------------------------
# equations

def solving_steps(lhs: Expr, rhs: Expr, var: Symbol) -> Explanation:
    """The steps of solving ``lhs = rhs`` for ``var``: degree one or two."""
    x = var
    start = Eq(lhs, rhs, evaluate=False)
    expl = Explanation("solve", start, var=x)
    steps: List[Step] = []
    L, R = expand(lhs), expand(rhs)
    if (L, R) != (lhs, rhs):
        steps.append(Step("Expand both sides", Eq(L, R, evaluate=False)))
    P = L - R
    if not P.is_polynomial(x):
        n, d = fraction(together(P))
        if d.has(x) and n.is_polynomial(x):
            raise CannotExplain(f"{x} is in a denominator: only polynomial equations are explained step by step "
                                f"(multiply both sides by {d} first, where it is not zero)")
        raise CannotExplain(f"Only polynomial equations of degree one or two in {x} are explained step by step")
    poly = Poly(P, x)
    deg = poly.degree()
    if deg <= 0:
        holds = simplify(P) == 0
        raise CannotExplain(f"There is no {x} left to solve for: the equation is "
                            + ("always true" if holds else "never true" if P.is_number else "free of it"))
    if deg > 2:
        raise CannotExplain(f"The equation has degree {deg} in {x}: only degree one and two are explained step by step")
    coeffs = poly.all_coeffs()
    symbolic = any(c.free_symbols for c in coeffs[:1])
    assume = f" (assuming {coeffs[0]} ≠ 0)" if symbolic else ""
    if deg == 1:
        a, b = coeffs
        moved = Eq(a * x, -b, evaluate=False)
        if moved != (steps[-1].expr if steps else start):
            steps.append(Step(f"Collect the terms with {x} on the left and the rest on the right", moved))
        if a != 1:
            if a == -1:
                steps.append(Step("Multiply both sides by −1", Eq(x, b, evaluate=False)))
            else:
                steps.append(Step(f"Divide both sides by {a}" + assume, Eq(x, -b / a, evaluate=False),
                                  shown=f"{latex(x)} = \\frac{{{latex(-b)}}}{{{latex(a)}}}"))
                value = simplify(-b / a)
                steps.append(Step("Simplify", Eq(x, value, evaluate=False)))
        expl.steps = _dedupe(steps)
        return expl
    a, b, c = coeffs
    if R != 0 and b != 0:
        steps.append(Step("Bring every term to the left side", Eq(P, 0, evaluate=False)))
    if b == 0 and c == 0:
        steps.append(Step(f"Divide by {a}: {x}² = 0" + assume, Eq(x**2, 0, evaluate=False)))
        steps.append(Step("A square is zero only when its base is: one (double) solution", Eq(x, 0, evaluate=False)))
    elif c == 0:
        steps.append(Step(f"Factor out {x}", Eq(x * (a * x + b), 0, evaluate=False)))
        steps.append(Step("A product is zero when one of its factors is",
                          Or(Eq(x, 0, evaluate=False), Eq(a * x + b, 0, evaluate=False), evaluate=False)))
        steps.append(Step("Solve the linear equation" + assume,
                          Or(Eq(x, 0, evaluate=False), Eq(x, simplify(-b / a), evaluate=False), evaluate=False)))
    elif b == 0:
        q = simplify(-c / a)
        steps.append(Step(f"Isolate {x}²" + assume, Eq(x**2, q, evaluate=False)))
        r = sqrt(q)
        text = "Take the square root of both sides: two solutions, ±"
        if q.is_negative:
            text += " (the number under the root is negative: no real solution, two complex ones)"
        steps.append(Step(text, Or(Eq(x, r, evaluate=False), Eq(x, -r, evaluate=False), evaluate=False)))
    else:
        steps.append(Step("Read off the coefficients of a·x² + b·x + c = 0", None,
                          shown=f"a = {latex(a)},\\quad b = {latex(b)},\\quad c = {latex(c)}"))
        D = expand(b**2 - 4 * a * c)
        steps.append(Step("The discriminant Δ = b² − 4ac", None,
                          shown=f"\\Delta = b^{{2}} - 4ac = {latex(D)}"))
        steps.append(Step("The quadratic formula" + assume, None,
                          shown=f"{latex(x)} = \\frac{{-b \\pm \\sqrt{{\\Delta}}}}{{2a}} = "
                                f"\\frac{{{latex(-b)} \\pm \\sqrt{{{latex(D)}}}}}{{{latex(2 * a)}}}"))
        if D == 0:
            steps.append(Step("Δ = 0: one (double) solution", Eq(x, simplify(-b / (2 * a)), evaluate=False)))
        else:
            r1 = simplify((-b + sqrt(D)) / (2 * a))
            r2 = simplify((-b - sqrt(D)) / (2 * a))
            if D.is_negative:
                text = "Δ < 0: no real solution, two complex ones"
            elif D.is_positive:
                text = "Δ > 0: two real solutions"
            else:
                text = "Two solutions"
            steps.append(Step(text, Or(Eq(x, r1, evaluate=False), Eq(x, r2, evaluate=False), evaluate=False)))
    expl.steps = _dedupe(steps)
    return expl


def _dedupe(steps: List[Step]) -> List[Step]:
    out: List[Step] = []
    for st in steps:
        if out and st.expr is not None and out[-1].expr is not None and st.expr == out[-1].expr and st.shown is None:
            continue
        out.append(st)
    return out


# ---------------------------------------------------------------------------
# the entry point

def _offers(node: Basic) -> List[str]:
    offers: List[str] = []
    if isinstance(node, Expr) and not getattr(node, "is_Matrix", False) and node.free_symbols:
        offers += ["differentiate", "integrate"]
        for s in _symbols(node):
            try:
                P = expand(node)
                if P.is_polynomial(s) and 1 <= Poly(P, s).degree() <= 2:
                    offers.append("solve")
                    break
            except Exception:
                pass
    if isinstance(node, Equality):
        offers.append("solve")
    return offers


def explain(node: Basic, task: str = "auto", var=None) -> Explanation:
    """Explain ``node``: integrate it, differentiate it or solve it.

    ``task="auto"`` picks by the node: an ``Integral`` is worked out, a
    ``Derivative`` too, an equation (``Eq``) is solved; anything else gets
    an explanation with no steps, a ``message`` and the ``offers`` that make
    sense for it.  ``var``: the variable (a Symbol or a name) when the node
    does not name one."""
    if task not in TASKS:
        raise ValueError(f"No task {task!r}: one of {', '.join(TASKS)}")
    free = _symbols(node) if isinstance(node, Basic) else []
    try:
        if task == "auto":
            if isinstance(node, Integral):
                return _explain_integral(node)
            if isinstance(node, Derivative):
                return _explain_derivative(node)
            if isinstance(node, Equality):
                x = _pick_var(node, var)
                return solving_steps(node.lhs, node.rhs, x)
            expl = Explanation("auto", None, vars=free, offers=_offers(node))
            if isinstance(node, Relational):
                expl.message = "Only equations (=) are solved step by step, not inequalities"
            elif expl.offers:
                expl.message = ("Steps are shown for integrals, derivatives and equations: "
                                "choose what to do with this expression")
            else:
                what = "it has no variable to work with" if not free else f"not for a {type(node).__name__}"
                expl.message = f"Steps are shown for integrals, derivatives and equations: {what}"
            return expl
        if not isinstance(node, Expr) and not isinstance(node, Equality):
            raise CannotExplain(f"A {type(node).__name__} cannot be {task}d step by step")
        if task == "solve":
            x = _pick_var(node, var)
            if isinstance(node, Equality):
                expl = solving_steps(node.lhs, node.rhs, x)
            else:
                expl = solving_steps(node, S.Zero, x)
        elif isinstance(node, Equality):
            raise CannotExplain(f"An equation is solved, not {task}d")
        elif task == "integrate":
            if isinstance(node, Integral) and var is None:
                return _explain_integral(node)
            x = _pick_var(node, var)
            expl = integration_steps(node, x)
        else:
            if isinstance(node, Derivative) and var is None:
                return _explain_derivative(node)
            x = _pick_var(node, var)
            expl = differentiation_steps(node, x)
        expl.vars = free
        return expl
    except CannotExplain as exc:
        return Explanation(task, None, message=str(exc), vars=free, offers=_offers(node),
                           var=var if isinstance(var, Symbol) else None)


def _explain_integral(node: Integral) -> Explanation:
    if len(node.limits) != 1:
        raise CannotExplain("Only single integrals are explained step by step: select the inner one")
    lim = node.limits[0]
    var, limits = lim[0], tuple(lim[1:])
    if len(limits) == 1:
        raise CannotExplain("An integral with one limit only is not explained step by step")
    expl = integration_steps(node.function, var, limits)
    expl.start = node
    expl.vars = _symbols(node)
    return expl


def _explain_derivative(node: Derivative) -> Explanation:
    counts = list(node.variable_count)
    v, n = counts[0]
    if not isinstance(v, Symbol) or not all(isinstance(c[1], (int,)) or c[1].is_Integer for c in counts):
        raise CannotExplain("Only derivatives with respect to symbols, a whole number of times, are explained")
    expl = differentiation_steps(node.expr, v, int(n), tuple((w, int(k)) for w, k in counts[1:]))
    expl.start = node
    expl.vars = _symbols(node)
    return expl

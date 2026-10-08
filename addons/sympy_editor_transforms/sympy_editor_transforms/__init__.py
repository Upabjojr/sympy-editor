"""sympy-editor add-on: integral transforms of the selection.

Laplace, Fourier, sine, cosine, Mellin and Hankel transforms and their
inverses are SymPy's own (``laplace_transform``...), asked for their
conditions (``noconds=False``) so that the panel can say where the result
holds - "converges for Re(s) > -a".  The one-sided z-transform and its
inverse for rational functions are this add-on's (:mod:`.ztransform`):
SymPy has none.

Two ways in, one computation behind both:

* the **panel** - pick a transform, the variable of the selection and the
  name of the new one, *Compute* shows the result and its conditions in
  words (a query: nothing changes), *Apply* replaces the selection (a step
  of the history), unevaluated (``LaplaceTransform(f, t, s)``) when the
  editor's "unevaluated" toggle is on;
* **ops** in the Transform menu - Laplace…, Inverse Laplace…, Fourier…,
  Inverse Fourier…, z-transform…, Inverse z-transform… - which ask for the
  variables in the form the core's Differentiate… uses, and leave the
  conditions in the status line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sympy import (Abs, And, Basic, arg, Expr, Or, S, Sum, Symbol, Tuple as STuple, cosine_transform, fourier_transform,
                   hankel_transform, inverse_cosine_transform, inverse_fourier_transform, inverse_hankel_transform,
                   inverse_laplace_transform, inverse_mellin_transform, inverse_sine_transform, laplace_transform,
                   latex, mellin_transform, oo, sine_transform)
from sympy.core.relational import Relational
from sympy.integrals.transforms import (CosineTransform, FourierTransform, HankelTransform, IntegralTransform,
                                        IntegralTransformError, InverseCosineTransform, InverseFourierTransform,
                                        InverseHankelTransform, InverseLaplaceTransform, InverseMellinTransform,
                                        InverseSineTransform, LaplaceTransform, MellinTransform, SineTransform)
from sympy.printing.str import StrPrinter

from sympy_editor.addons import Addon
from sympy_editor.ops import make_op

from .ztransform import ZTransformError, inverse_z_transform, z_transform

__all__ = ["TransformsAddon", "ADDON", "TRANSFORMS", "transform", "unevaluated", "words",
           "z_transform", "inverse_z_transform"]

STATIC = Path(__file__).parent / "static"

#: The transforms, by key: the label, the name of the new variable by
#: default, the names the variable of the selection is looked for under
#: first, and the extra value one of them needs (Hankel's order, the strip
#: of an inverse Mellin transform).
TRANSFORMS: Dict[str, Dict[str, Any]] = {
    "laplace": {"label": "Laplace", "new": "s", "vars": ["t", "x"]},
    "inverse_laplace": {"label": "Inverse Laplace", "new": "t", "vars": ["s", "p"]},
    "fourier": {"label": "Fourier", "new": "k", "vars": ["x", "t"]},
    "inverse_fourier": {"label": "Inverse Fourier", "new": "x", "vars": ["k", "omega", "w", "nu"]},
    "sine": {"label": "Sine", "new": "k", "vars": ["x", "t"]},
    "inverse_sine": {"label": "Inverse sine", "new": "x", "vars": ["k", "omega", "w"]},
    "cosine": {"label": "Cosine", "new": "k", "vars": ["x", "t"]},
    "inverse_cosine": {"label": "Inverse cosine", "new": "x", "vars": ["k", "omega", "w"]},
    "mellin": {"label": "Mellin", "new": "s", "vars": ["x", "t"]},
    "inverse_mellin": {"label": "Inverse Mellin", "new": "x", "vars": ["s"],
                       "extra": {"name": "strip", "default": "(-oo, oo)",
                                 "title": "The fundamental strip a < Re(s) < b, as (a, b)"}},
    "hankel": {"label": "Hankel", "new": "k", "vars": ["r", "x", "t"],
               "extra": {"name": "order", "default": "0", "title": "The order nu of the Bessel function J_nu"}},
    "inverse_hankel": {"label": "Inverse Hankel", "new": "r", "vars": ["k"],
                       "extra": {"name": "order", "default": "0", "title": "The order nu of the Bessel function J_nu"}},
    "z": {"label": "z-transform", "new": "z", "vars": ["n", "k", "m"]},
    "inverse_z": {"label": "Inverse z-transform", "new": "n", "vars": ["z"]},
}

#: The conventions, said once with each result: SymPy's, which are not the
#: only ones in use.
CONVENTIONS = {
    "fourier": "SymPy's convention: F(k) = ∫ f(x) e^(−2πi·x·k) dx over the whole line.",
    "inverse_fourier": "SymPy's convention: f(x) = ∫ F(k) e^(2πi·x·k) dk over the whole line.",
    "sine": "SymPy's convention: F(k) = √(2/π) ∫₀^∞ f(x) sin(k·x) dx.",
    "inverse_sine": "SymPy's convention: f(x) = √(2/π) ∫₀^∞ F(k) sin(k·x) dk.",
    "cosine": "SymPy's convention: F(k) = √(2/π) ∫₀^∞ f(x) cos(k·x) dx.",
    "inverse_cosine": "SymPy's convention: f(x) = √(2/π) ∫₀^∞ F(k) cos(k·x) dk.",
    "hankel": "F(k) = ∫₀^∞ f(r) J_ν(k·r) r dr.",
    "inverse_hankel": "f(r) = ∫₀^∞ F(k) J_ν(k·r) k dk.",
    "laplace": "One-sided: F(s) = ∫₀^∞ f(t) e^(−s·t) dt.",
    "mellin": "F(s) = ∫₀^∞ x^(s−1) f(x) dx.",
    "z": "One-sided: F(z) = Σ_{n ≥ 0} f(n) z^(−n).",
}


class _Words(StrPrinter):
    """Conditions as one reads them: |x|, Re(s), ≤, ≠, "and"."""

    def _print_Abs(self, e):
        return f"|{self._print(e.args[0])}|"

    def _print_re(self, e):
        return f"Re({self._print(e.args[0])})"

    def _print_im(self, e):
        return f"Im({self._print(e.args[0])})"

    def _print_Relational(self, e):
        # |arg(k)| = 0, SymPy's way of saying k is a positive number
        if e.rel_op == "==" and e.rhs == 0 and isinstance(e.lhs, Abs) and isinstance(e.lhs.args[0], arg):
            return f"{self._print(e.lhs.args[0].args[0])} is real and positive"
        op = {"==": "=", "!=": "≠", "<=": "≤", ">=": "≥"}.get(e.rel_op, e.rel_op)
        return f"{self._print(e.lhs)} {op} {self._print(e.rhs)}"

    def _print_arg(self, e):
        return f"arg({self._print(e.args[0])})"

    def _print_And(self, e):
        return " and ".join(f"({self._print(a)})" if isinstance(a, Or) else self._print(a) for a in e.args)

    def _print_Or(self, e):
        return " or ".join(f"({self._print(a)})" if isinstance(a, And) else self._print(a) for a in e.args)

    def _print_Infinity(self, e):
        return "∞"

    def _print_NegativeInfinity(self, e):
        return "−∞"


def words(cond: Basic) -> str:
    """A condition (or any expression) as text to read."""
    return _Words().doprint(cond)


def _condition(cond) -> List[str]:
    """What a condition SymPy returned adds: nothing when it is True."""
    if cond is True or cond is S.true or cond is None:
        return []
    if cond is False or cond is S.false:
        return ["SymPy says the result holds nowhere (its condition is False)"]
    return ["provided " + words(cond)]


def _unpack(res) -> Tuple[Basic, tuple]:
    """SymPy's answers with conditions are tuples; without, the expression."""
    if isinstance(res, tuple):
        return res[0], tuple(res[1:])
    return res, ()


def _name(key: str) -> str:
    """"the Laplace transform", "the z-transform"..."""
    label = TRANSFORMS[key]["label"]
    label = label[0].lower() + label[1:] if label.startswith("Inverse") else label
    return label if label.endswith("transform") else label + " transform"


def _check(key: str, result: Basic, expr: Basic) -> Basic:
    """A transform SymPy could not do comes back as itself, unevaluated."""
    if isinstance(result, Basic) and result.has(IntegralTransform):
        raise ValueError(f"SymPy could not find the {_name(key)} of {expr}")
    return result


def _band(a: Basic, b: Basic, s: Symbol) -> str:
    """The strip a < Re(s) < b in words, an infinite end left out."""
    low, high = a in (S.NegativeInfinity,), b in (S.Infinity,)
    if low and high:
        return f"every {s}"
    if high:
        return f"Re({s}) > {words(a)}"
    if low:
        return f"Re({s}) < {words(b)}"
    return f"{words(a)} < Re({s}) < {words(b)}"


def _strip(extra) -> Tuple[Basic, Basic]:
    if extra is None:
        return S.NegativeInfinity, S.Infinity
    if isinstance(extra, (tuple, list, STuple)) and len(extra) == 2:
        return S(extra[0]), S(extra[1])
    raise ValueError(f"The strip must be two numbers (a, b), for a < Re(s) < b, not {extra}")


def _order(extra) -> Basic:
    return S.Zero if extra is None else S(extra)


def transform(key: str, expr: Basic, var: Symbol, new: Symbol, extra=None) -> Dict[str, Any]:
    """``expr`` transformed: ``{"result", "conditions": [text], "convention"}``.

    ``key`` is one of :data:`TRANSFORMS`; ``var`` the variable of ``expr``,
    ``new`` the variable of the result; ``extra`` Hankel's order or the
    inverse Mellin transform's strip ``(a, b)``.  Raises ``ValueError`` with
    a sentence when SymPy cannot do it."""
    if key not in TRANSFORMS:
        raise ValueError(f"No transform {key!r}; there are {', '.join(TRANSFORMS)}")
    if not isinstance(var, Symbol) or not isinstance(new, Symbol):
        raise ValueError("The two variables must be names (symbols)")
    if var == new:
        raise ValueError(f"The new variable must differ from {var}")
    if new in expr.free_symbols:
        raise ValueError(f"{new} already occurs in {expr}: name the new variable otherwise")
    conds: List[str] = []
    try:
        if key == "laplace":
            F, rest = _unpack(laplace_transform(expr, var, new, noconds=False))
            if rest:
                a, cond = rest
                conds.append(f"converges for Re({new}) > {words(a)}" if a not in (S.NegativeInfinity, -oo)
                             else f"converges for every {new}")
                conds += _condition(cond)
        elif key == "inverse_laplace":
            F, rest = _unpack(inverse_laplace_transform(expr, var, new, noconds=False))
            conds.append(f"valid for {new} > 0, the original of a one-sided transform"
                         + (f" (the factor Heaviside({new}) says so)" if "Heaviside" in str(F) else ""))
            if rest:
                conds += _condition(rest[0])
        elif key == "mellin":
            F, rest = _unpack(mellin_transform(expr, var, new, noconds=False))
            if rest:
                (a, b), cond = rest
                conds.append("converges for " + _band(a, b, new))
                conds += _condition(cond)
        elif key == "inverse_mellin":
            strip = _strip(extra)
            F, rest = _unpack(inverse_mellin_transform(expr, var, new, strip, noconds=False))
            conds.append("inverted along a line in the strip " + _band(strip[0], strip[1], var))
            if rest:
                conds += _condition(rest[0])
        elif key in ("hankel", "inverse_hankel"):
            nu = _order(extra)
            fn = hankel_transform if key == "hankel" else inverse_hankel_transform
            F, rest = _unpack(fn(expr, var, new, nu, noconds=False))
            conds.append(f"of order ν = {words(nu)}")
            if rest:
                conds += _condition(rest[0])
        elif key == "z":
            F, R, nonzero = z_transform(expr, var, new)
            if R == 0:
                conds.append(f"converges for every {new} ≠ 0" if nonzero or expr.has(var)
                             else f"converges for every {new}")
            else:
                conds.append(f"converges for |{new}| > {words(R)}")
            conds.append(f"the sequence is taken for {var} ≥ 0 (one-sided)")
        elif key == "inverse_z":
            F = inverse_z_transform(expr, var, new)
            conds.append(f"the sequence for {new} ≥ 0 (one-sided, a causal sequence)")
        else:
            fn = {"fourier": fourier_transform, "inverse_fourier": inverse_fourier_transform,
                  "sine": sine_transform, "inverse_sine": inverse_sine_transform,
                  "cosine": cosine_transform, "inverse_cosine": inverse_cosine_transform}[key]
            F, rest = _unpack(fn(expr, var, new, noconds=False))
            if rest:
                conds += _condition(rest[0])
    except IntegralTransformError as exc:
        raise ValueError(f"SymPy could not find the {_name(key)}: {exc}") from None
    except ZTransformError as exc:
        raise ValueError(f"No {_name(key)}: {exc}") from None
    F = _check(key, F, expr)
    if not conds:
        conds.append("holds with no condition")
    return {"result": F, "conditions": conds, "convention": CONVENTIONS.get(key, "")}


def unevaluated(key: str, expr: Basic, var: Symbol, new: Symbol, extra=None) -> Optional[Basic]:
    """The transform as an object, not computed (``LaplaceTransform(f, t, s)``);
    None for the inverse z-transform, which has no such form."""
    if key == "laplace":
        return LaplaceTransform(expr, var, new)
    if key == "inverse_laplace":
        # the abscissa is not known before the transform is computed: none
        return InverseLaplaceTransform(expr, var, new, S.NegativeInfinity)
    if key == "mellin":
        return MellinTransform(expr, var, new)
    if key == "inverse_mellin":
        a, b = _strip(extra)
        return InverseMellinTransform(expr, var, new, a, b)
    if key == "hankel":
        return HankelTransform(expr, var, new, _order(extra))
    if key == "inverse_hankel":
        return InverseHankelTransform(expr, var, new, _order(extra))
    if key == "z":
        return Sum(expr * new ** (-var), (var, 0, oo))
    cls = {"fourier": FourierTransform, "inverse_fourier": InverseFourierTransform,
           "sine": SineTransform, "inverse_sine": InverseSineTransform,
           "cosine": CosineTransform, "inverse_cosine": InverseCosineTransform}.get(key)
    return cls(expr, var, new) if cls is not None else None


def guess_variable(key: str, expr: Basic) -> Optional[Symbol]:
    """The variable of ``expr`` a transform most likely means: the first of
    its usual names that occurs, else the first free symbol by name."""
    free = sorted(getattr(expr, "free_symbols", set()), key=lambda s: str(s))
    names = {str(s): s for s in free}
    for name in TRANSFORMS[key]["vars"]:
        if name in names:
            return names[name]
    return free[0] if free else None


# ---- the ops in the Transform menu ----

def _op_symbol(doc, value, default: str) -> Symbol:
    """The new variable as the op received it: a parsed symbol, or the
    default name read in the document's namespace."""
    if value is None:
        value = doc.parse(default) if doc is not None else Symbol(default)
    if not isinstance(value, Symbol):
        raise ValueError(f"The new variable must be a name, not {value}")
    return value


def _op(key: str):
    def run(expr, var=None, new=None, doc=None):
        var = var if var is not None else guess_variable(key, expr)
        if var is None:
            raise ValueError(f"{expr} has no variable to transform")
        res = transform(key, expr, var, _op_symbol(doc, new, TRANSFORMS[key]["new"]))
        if doc is not None:
            doc.last_note = TRANSFORMS[key]["label"] + ": " + "; ".join(res["conditions"])
        return res["result"]

    def lazy(expr, var=None, new=None, doc=None):
        var = var if var is not None else guess_variable(key, expr)
        if var is None:
            raise ValueError(f"{expr} has no variable to transform")
        return unevaluated(key, expr, var, _op_symbol(doc, new, TRANSFORMS[key]["new"]))

    return run, lazy


def _make_ops():
    out = []
    for key, label, doc in (
        ("laplace", "Laplace transform…", "F(s) = ∫₀^∞ f(t) e^(−st) dt, and where it converges."),
        ("inverse_laplace", "Inverse Laplace transform…", "The f(t), t ≥ 0, whose Laplace transform is the selection."),
        ("fourier", "Fourier transform…", "F(k) = ∫ f(x) e^(−2πixk) dx (SymPy's convention)."),
        ("inverse_fourier", "Inverse Fourier transform…", "f(x) = ∫ F(k) e^(2πixk) dk (SymPy's convention)."),
        ("z", "z-transform…", "F(z) = Σ_{n ≥ 0} f(n) z^(−n), and the |z| it converges for."),
        ("inverse_z", "Inverse z-transform…", "The sequence f(n), n ≥ 0, of a rational F(z), by partial fractions."),
    ):
        run, lazy = _op(key)
        out.append(make_op(
            "transforms_" + key, run, label=label, doc=doc, context=True,
            # the lazy form gets doc= as the op does (context ops are called with it)
            lazy=None if key == "inverse_z" else lazy,
            params=[("of the variable", "symbol", False, None),
                    ("new variable, e.g. " + TRANSFORMS[key]["new"], "text", True, TRANSFORMS[key]["new"])]))
    return tuple(out)


class TransformsAddon(Addon):
    name = "transforms"
    label = "Transforms"
    requires = ()

    ops = _make_ops()

    js = (STATIC / "transforms.js").read_text(encoding="utf-8")
    css = (STATIC / "transforms.css").read_text(encoding="utf-8")

    def client_options(self) -> Dict[str, Any]:
        return {"transforms": [dict({"key": k}, **v) for k, v in TRANSFORMS.items()]}

    # ---- the panel's methods ----

    def _target(self, doc, payload: Dict[str, Any]) -> Basic:
        path = payload.get("path") or "/"
        children = payload.get("children")
        return doc._extract_range(doc.expr, doc._path(path), children) if children is not None else doc.get(path)

    def _symbol(self, doc, text, what: str) -> Symbol:
        text = str(text or "").strip()
        if not text:
            raise ValueError(f"Name the {what}")
        value = doc.parse(text)
        if not isinstance(value, Symbol):
            raise ValueError(f"The {what} must be a name, not {text}")
        return value

    def _compute(self, doc, payload: Dict[str, Any]):
        key = str(payload.get("transform") or "")
        if key not in TRANSFORMS:
            raise ValueError(f"No transform {key!r}")
        target = self._target(doc, payload)
        if payload.get("var"):
            var = self._symbol(doc, payload.get("var"), "variable of the selection")
        else:
            var = guess_variable(key, target)
            if var is None:
                raise ValueError(f"{target} has no variable to transform")
        new = self._symbol(doc, payload.get("new") or TRANSFORMS[key]["new"], "new variable")
        extra = None
        raw = str(payload.get("extra") or "").strip()
        if "extra" in TRANSFORMS[key]:
            extra = doc.parse(raw or TRANSFORMS[key]["extra"]["default"])
        return key, target, var, new, extra

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "compute":
            key, target, var, new, extra = self._compute(doc, payload)
            res = transform(key, target, var, new, extra)
            return {"transform": key, "label": TRANSFORMS[key]["label"], "var": str(var), "new": str(new),
                    "src": str(res["result"]), "latex": latex(res["result"]),
                    "conditions": res["conditions"], "convention": res["convention"]}
        if method == "apply":
            key, target, var, new, extra = self._compute(doc, payload)
            result, said = None, ""
            if payload.get("lazy"):
                result = unevaluated(key, target, var, new, extra)
                if result is None:
                    said = f"{TRANSFORMS[key]['label']} has no unevaluated form: applied. "
            if result is None:
                res = transform(key, target, var, new, extra)
                result = res["result"]
                doc.last_note = said + TRANSFORMS[key]["label"] + ": " + "; ".join(res["conditions"])
            doc.replace(payload.get("path") or "/", result, children=payload.get("children"))
            return None
        raise ValueError(f"The transforms add-on has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]) -> Optional[str]:
        if method != "apply":
            return None
        key = str(payload.get("transform") or "")
        label = _name(key) if key in TRANSFORMS else key
        label = label[0].upper() + label[1:]
        new = payload.get("new") or TRANSFORMS.get(key, {}).get("new", "")
        var = payload.get("var") or "…"
        return f"Transforms: {label}, {var} → {new}" + (" (unevaluated)" if payload.get("lazy") else "")


ADDON = TransformsAddon()

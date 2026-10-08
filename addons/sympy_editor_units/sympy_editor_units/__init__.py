"""sympy-editor add-on: physical units, with ``sympy.physics.units``.

With the add-on on, the names of SymPy's units and physical constants can be
typed in the formula (``5*meter/second``, ``km/hour``, ``speed_of_light``,
``gravitational_constant``): the add-on's :meth:`UnitsAddon.namespace` puts
them in scope.  The one-letter abbreviations (``m``, ``s``, ``g``, ``c``,
``N``...) are ordinary variable names far too often to be taken by default;
the panel's *short unit names* switch (``{"method": "short", "on": true}``)
adds them for the document, and a session keeps the switch.

The panel shows, for the selection, its **dimension** in the SI system, and
**checks** every sum, relation, exponent and function argument under it:
terms whose dimensions disagree with the rest of their sum are listed (and
outlined in the formula), as are dimensioned exponents and arguments of
``sin``/``exp``/``log``.  **Convert** (``convert_to``) rewrites the selection
in the units typed, **SI base units** in metres, kilograms, seconds...,
**Simplify units** (``quantity_simplify``) merges the units of a product -
each a step of the history.

Methods: ``inspect`` (a query: the dimension and the problems under
``path``), ``convert`` (``path``, ``target``), ``si``, ``simplify`` (``path``)
and ``short`` (``on``).  Nothing beyond SymPy is needed.
"""

from __future__ import annotations

from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sympy
from sympy import (Abs, Add, Basic, Derivative, Function, Integral, Max, Min, Mul, Pow, Rational, Sum,
                   conjugate, im, re)
from sympy.core.function import AppliedUndef
from sympy.core.relational import Relational
from sympy.functions.elementary.integers import ceiling, floor
import sympy.physics.units as _u
from sympy.physics.units import Dimension, Quantity, convert_to
from sympy.physics.units.systems.si import SI, dimsys_SI
from sympy.physics.units.util import quantity_simplify

from sympy_editor.addons import Addon
from sympy_editor.ops import make_op
from sympy_editor.printer import extract_range, format_path, parse_path, view_parts

__all__ = ["UnitsAddon", "ADDON", "ADDON_SHORT", "dimension_of", "check", "unit_names",
           "to_si", "simplify_units", "DimensionInfo"]

STATIC = Path(__file__).parent / "static"

# -- the names ------------------------------------------------------------------

#: Every quantity of ``sympy.physics.units`` by every name it has there.
_ALL: Dict[str, Quantity] = {k: v for k, v in sorted(vars(_u).items()) if isinstance(v, Quantity)}
#: The names the quantities give themselves - what ``srepr`` and ``str``
#: write (``kPa`` is ``kilopascal``, which the module has under no name of
#: its own): always in scope, or a saved session could not be read back.
_OWN: Dict[str, Quantity] = {str(q.name): q for q in _ALL.values()}
#: Abbreviations taken by default: nobody calls a variable ``km``.
DEFAULT_ABBREVIATIONS = ("km", "cm", "mm", "nm", "kg", "mg", "Hz", "eV", "kPa", "Pa", "mol", "atm",
                         "Wb", "Bq", "Gy", "mmHg")
_LONG: Dict[str, Quantity] = dict(_OWN)
for _k, _v in _ALL.items():
    if len(_k) >= 4 or _k in DEFAULT_ABBREVIATIONS:
        _LONG.setdefault(_k, _v)
#: What the *short unit names* switch adds: ``m``, ``s``, ``g``, ``c``,
#: ``N``, ``ms``, ``deg``...
_SHORT: Dict[str, Quantity] = {k: v for k, v in _ALL.items() if k not in _LONG}

#: Offered in the panel's target field.
COMMON_TARGETS = ("meter", "km", "cm", "mm", "second", "minute", "hour", "day", "kg", "gram",
                  "meter/second", "km/hour", "meter/second**2", "newton", "joule", "kJ", "electronvolt",
                  "watt", "pascal", "kPa", "bar", "atmosphere", "kelvin", "coulomb", "volt", "ohm",
                  "liter", "hertz", "mole", "kg*meter**2/second**2")
COMMON_TARGETS = tuple(t for t in COMMON_TARGETS if all(n in _LONG for n in t.replace("*", " ").replace("/", " ").split()
                                                         if not n.isdigit()))


def unit_names(short: bool = False) -> Dict[str, Quantity]:
    """The names the add-on puts in scope: the units' own names, their long
    aliases and a few unmistakable abbreviations; with ``short`` every
    abbreviation SymPy has, one-letter ones included."""
    out = dict(_LONG)
    if short:
        out.update(_SHORT)
    return out


# -- dimensions ------------------------------------------------------------------

#: Base dimensions, as dimensional analysis writes them.
BASE_SYMBOLS = {"mass": "M", "length": "L", "time": "T", "current": "I", "temperature": r"\Theta",
                "amount_of_substance": "N", "luminous_intensity": "J", "information": "B"}
_BASE_ORDER = list(BASE_SYMBOLS)

Deps = Tuple[Tuple[str, Fraction], ...]
DIMENSIONLESS: Deps = ()


def _deps(dim: Dimension) -> Deps:
    raw = dimsys_SI.get_dimensional_dependencies(dim)
    out = {}
    for k, v in raw.items():
        name = str(getattr(k, "name", k))
        out[name] = out.get(name, Fraction(0)) + Fraction(str(v))
    return tuple(sorted(((k, v) for k, v in out.items() if v),
                        key=lambda kv: (_BASE_ORDER.index(kv[0]) if kv[0] in _BASE_ORDER else 99, kv[0])))


def _combine(a: Optional[Deps], b: Optional[Deps], factor=1) -> Optional[Deps]:
    if a is None or b is None:
        return None
    out = dict(a)
    for k, v in b:
        out[k] = out.get(k, Fraction(0)) + v * factor
    return tuple(sorted(((k, v) for k, v in out.items() if v),
                        key=lambda kv: (_BASE_ORDER.index(kv[0]) if kv[0] in _BASE_ORDER else 99, kv[0])))


def _scale(a: Optional[Deps], factor: Fraction) -> Optional[Deps]:
    if a is None:
        return None
    return tuple((k, v * factor) for k, v in a if v * factor)


#: Named dimensions (velocity, force...), by their dependencies; the first
#: name wins where two agree (velocity before speed).
_NAMED: Dict[Deps, str] = {}
for _name in ("length", "mass", "time", "current", "temperature", "amount_of_substance", "luminous_intensity",
              "information", "velocity", "acceleration", "momentum", "force", "energy", "power", "pressure",
              "frequency", "area", "volume", "action", "charge", "voltage", "impedance", "conductance",
              "capacitance", "inductance", "magnetic_flux", "magnetic_density"):
    _dim = getattr(_u, _name, None)
    if isinstance(_dim, Dimension):
        try:
            _NAMED.setdefault(_deps(_dim), _name.replace("_", " "))
        except Exception:
            pass


def _quantity_deps(q: Quantity) -> Optional[Deps]:
    try:
        return _deps(SI.get_quantity_dimension(q))
    except Exception:
        return None


def _exp_tex(v: Fraction) -> str:
    if v == 1:
        return ""
    if v.denominator == 1:
        return "^{%d}" % v.numerator
    return "^{%d/%d}" % (v.numerator, v.denominator)


def deps_latex(d: Optional[Deps]) -> str:
    """``M L T^{-2}`` (as LaTeX); ``1`` for a dimensionless quantity."""
    if d is None:
        return r"?"
    if not d:
        return "1"
    parts = []
    for k, v in d:
        sym = BASE_SYMBOLS.get(k)
        sym = r"\mathsf{%s}" % sym if sym else r"\mathsf{%s}" % k.replace("_", r"\_")
        parts.append(sym + _exp_tex(v))
    return r"\,".join(parts)


def deps_name(d: Optional[Deps]) -> str:
    if d is None:
        return "unknown"
    if not d:
        return "dimensionless"
    return _NAMED.get(d, "")


class DimensionInfo(dict):
    """``{"latex", "name", "known"}`` - JSON for the panel."""

    @classmethod
    def of(cls, d: Optional[Deps]) -> "DimensionInfo":
        return cls(latex=deps_latex(d), name=deps_name(d), known=d is not None)


# The functions whose value has the dimension of their argument.
_SAME_AS_ARG = (Abs, re, im, conjugate, floor, ceiling)
_LIKE_A_SUM = (Max, Min)


class _Checker:
    """Walks the *view tree* (the paths the editor selects with) computing
    every node's dimension and noting what does not add up."""

    def __init__(self, settings=None, limit: int = 40):
        self.settings = settings
        self.limit = limit
        self.problems: List[Dict[str, Any]] = []

    def note(self, path, node, kind: str, got: Optional[Deps], want: Optional[Deps], message: str) -> None:
        if len(self.problems) >= self.limit:
            return
        self.problems.append({"path": format_path(path), "src": str(node), "kind": kind,
                              "dim": DimensionInfo.of(got), "expected": DimensionInfo.of(want),
                              "message": message})

    def dims(self, node: Basic, path: Tuple) -> Optional[Deps]:
        if isinstance(node, Quantity):
            return _quantity_deps(node)
        if not isinstance(node, Basic) or not node.atoms(Quantity):
            # SymPy's convention: a symbol (a number...) is dimensionless
            return DIMENSIONLESS
        parts = None
        try:
            parts = view_parts(node, self.settings)
        except Exception:
            parts = None
        if parts:
            got = {name: self.dims(child, path + (name,)) for name, child in parts}
            if "neg" in got:
                return got["neg"]
            if "n" in got and "d" in got:
                return _combine(got["n"], got["d"], -1)
            return None
        if isinstance(node, (Add,) + _LIKE_A_SUM) or isinstance(node, Relational):
            return self._sum(node, path)
        if isinstance(node, Mul):
            out: Optional[Deps] = DIMENSIONLESS
            for i, a in enumerate(node.args):
                out = _combine(out, self.dims(a, path + (i,)))
            return out
        if isinstance(node, Pow):
            base = self.dims(node.base, path + (0,))
            ex = self.dims(node.exp, path + (1,))
            if ex:
                self.note(path + (1,), node.exp, "exponent", ex, DIMENSIONLESS,
                          "an exponent must be dimensionless")
                return None
            if base == DIMENSIONLESS:
                return DIMENSIONLESS
            if node.exp.is_Rational:
                return _scale(base, Fraction(int(node.exp.p), int(node.exp.q)))
            return None
        if isinstance(node, _SAME_AS_ARG):
            return self.dims(node.args[0], path + (0,))
        if isinstance(node, Derivative):
            out = self.dims(node.expr, path + (0,))
            for v, n in node.variable_count:
                if n.is_Integer:
                    out = _combine(out, _scale(self._plain(v), Fraction(int(n))), -1)
                else:
                    return None
            return out
        if isinstance(node, Integral):
            out = self.dims(node.function, path + (0,))
            for lim in node.limits:
                out = _combine(out, self._plain(lim[0]))
            return out
        if isinstance(node, Sum):
            return self.dims(node.function, path + (0,))
        if isinstance(node, sympy.Piecewise):
            return None
        if isinstance(node, AppliedUndef):
            for i, a in enumerate(node.args):
                self.dims(a, path + (i,))
            return None
        if isinstance(node, Function):
            # sin, exp, log...: the argument must be a pure number
            for i, a in enumerate(node.args):
                d = self.dims(a, path + (i,))
                if d:
                    self.note(path + (i,), a, "argument", d, DIMENSIONLESS,
                              f"the argument of {type(node).__name__} must be dimensionless")
            return DIMENSIONLESS
        return None

    def _plain(self, node: Basic) -> Optional[Deps]:
        """A node's dimension where it has no place of its own in the view
        (a derivative's variable): nothing is noted."""
        return _Checker(self.settings).dims(node, ())

    def _sum(self, node: Basic, path: Tuple) -> Optional[Deps]:
        terms = [(i, a, self.dims(a, path + (i,))) for i, a in enumerate(node.args)]
        known = [(i, a, d) for i, a, d in terms if d is not None]
        if not known:
            return None
        if isinstance(node, Add):
            # in the order the formula draws them: a tie goes to the term
            # read first, and the one after it is the odd one
            try:
                drawn = {t: k for k, t in enumerate(node.as_ordered_terms())}
                known.sort(key=lambda t: drawn.get(t[1], len(drawn)))
            except Exception:
                pass
        # What the sum should be: the commonest dimension among the terms
        # that carry units (x + 5 m flags the x, not the metres); a tie goes
        # to the first such term.
        dimensioned = [d for _, a, d in known if a.atoms(Quantity)] or [d for _, _, d in known]
        counts = Counter(dimensioned)
        best = max(counts.values())
        want = next(d for d in dimensioned if counts[d] == best)
        if any(d != want for _, _, d in known):
            what = "side" if isinstance(node, Relational) else "term"
            for i, a, d in known:
                if d != want:
                    self.note(path + (i,), a, what, d, want,
                              f"this {what} is {deps_name(d) or deps_latex(d)}, the others are "
                              f"{deps_name(want) or deps_latex(want)}")
        if isinstance(node, Relational):
            return DIMENSIONLESS
        return want


def dimension_of(expr: Basic, settings=None) -> Optional[Deps]:
    """The SI dimension of ``expr`` as base-dimension exponents
    (``(("length", 1), ("time", -2))``), ``()`` when dimensionless, None when
    it cannot be told (a symbolic power of a unit, an undefined function)."""
    return _Checker(settings).dims(expr, ())


def check(expr: Basic, path=(), settings=None, limit: int = 40) -> Tuple[Optional[Deps], List[Dict[str, Any]]]:
    """The dimension of ``expr`` and what does not add up in it: terms of a
    sum (sides of a relation) whose dimension differs from the rest,
    dimensioned exponents and function arguments - each with the view path
    (``path`` being ``expr``'s own) the editor selects it by."""
    c = _Checker(settings, limit)
    d = c.dims(expr, tuple(path))
    return d, c.problems


# -- transformations ---------------------------------------------------------------

SI_BASE = (_u.meter, _u.kilogram, _u.second, _u.ampere, _u.kelvin, _u.mole, _u.candela)


def to_si(expr: Basic) -> Basic:
    """``expr`` in SI base units (metre, kilogram, second, ampere, kelvin,
    mole, candela)."""
    return convert_to(expr, list(SI_BASE), "SI")


def simplify_units(expr: Basic) -> Basic:
    """The units of ``expr`` merged (``quantity_simplify``): prefixes taken
    in, a unit over itself cancelled, a product of units of another
    dimension named (``newton*meter`` is ``joule``)."""
    try:
        return quantity_simplify(expr, across_dimensions=True, unit_system="SI")
    except TypeError:          # an older quantity_simplify without the keywords
        return quantity_simplify(expr)


def _units_of(target: Basic) -> List[Basic]:
    if isinstance(target, (sympy.Tuple, list, tuple, sympy.FiniteSet)):
        return list(target)
    return [target]


def _has_units(expr) -> bool:
    return isinstance(expr, Basic) and bool(expr.atoms(Quantity))


# -- the add-on --------------------------------------------------------------------


class UnitsAddon(Addon):
    name = "units"
    label = "Units"
    requires = ()

    ops = (
        make_op("units_si", lambda e: to_si(e), label="SI base units",
                doc="Rewrite in metres, kilograms, seconds, amperes, kelvins, moles and candelas (units add-on)."),
        make_op("units_simplify", lambda e: simplify_units(e), label="Simplify units",
                doc="Merge the units of a product: prefixes in, a unit over itself cancelled (units add-on)."),
    )

    js = (STATIC / "units.js").read_text(encoding="utf-8")
    css = (STATIC / "units.css").read_text(encoding="utf-8")

    def __init__(self, short: bool = False) -> None:
        #: Whether the one-letter abbreviations (m, s, g...) are names too.
        #: Each document's switch picks one of the two instances
        #: (:data:`ADDON`, :data:`ADDON_SHORT`): the namespace is asked
        #: without the document.
        self.short = bool(short)

    # -- the tree ---------------------------------------------------------

    def namespace(self) -> Dict[str, Any]:
        return unit_names(self.short)

    # -- the switch -------------------------------------------------------

    @staticmethod
    def _state(doc) -> Dict[str, Any]:
        return doc.addon_state.setdefault("units", {})

    def _apply_switch(self, doc) -> "UnitsAddon":
        """Put the instance matching the document's switch in its place.
        The namespace is asked without a document, so the switch is which
        of the two instances the document holds."""
        want = bool(self._state(doc).get("short"))
        inst = ADDON_SHORT if want else ADDON
        if doc.addons.get(self.name) is not inst and self.name in doc.addons:
            doc.addons[self.name] = inst
        return inst

    def export_state(self, doc) -> Any:
        return {"short": True} if self._state(doc).get("short") else None

    def restore_state(self, doc, data: Any) -> None:
        if isinstance(data, dict):
            self._state(doc)["short"] = data.get("short") is True
            self._apply_switch(doc)

    # -- data for the panel -----------------------------------------------

    def client_options(self) -> Dict[str, Any]:
        return {"targets": list(COMMON_TARGETS),
                "short": sorted(_SHORT, key=lambda n: (len(n), n.lower()))[:40]}

    def contribute(self, doc, snap: Dict[str, Any], expr: Basic) -> None:
        self._apply_switch(doc)
        out: Dict[str, Any] = {"short": bool(self._state(doc).get("short")), "has_units": _has_units(expr)}
        if out["has_units"]:
            try:
                _, problems = check(expr, (), doc.printer_settings, limit=20)
            except Exception:
                problems = []
            out["problems"] = [{"path": p["path"], "message": p["message"]} for p in problems]
        snap["units"] = out

    # -- methods ----------------------------------------------------------

    def _target(self, doc, payload) -> Tuple[Tuple, Basic, Any]:
        path = parse_path(payload.get("path") or "/")
        children = payload.get("children")
        if children:
            node = extract_range(doc.expr, path, children, doc.printer_settings)
        else:
            node = doc.get(path)
        return path, node, children

    def _commit(self, doc, path, children, node, new) -> None:
        if new == node:
            raise ValueError("Nothing to change: the units are already so")
        doc.replace(format_path(path), new, children=children or None)

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "short":
            on = bool(payload.get("on"))
            self._state(doc)["short"] = on
            self._apply_switch(doc)
            return {"short": on}
        if method == "inspect":
            path, node, children = self._target(doc, payload)
            d, problems = check(node, () if children else path, doc.printer_settings)
            if children:
                problems = []        # a range's own paths are not the formula's
            info = DimensionInfo.of(d)
            si = None
            if _has_units(node) and d is not None:
                try:
                    si = sympy.latex(to_si(node))
                except Exception:
                    si = None
            return {"path": format_path(path), "src": str(node), "has_units": _has_units(node),
                    "dimension": info, "problems": problems, "si": si}
        if method == "convert":
            text = str(payload.get("target") or "").strip()
            if not text:
                raise ValueError("Type the units to convert to (km/hour, joule, kg*m**2/s**2...)")
            path, node, children = self._target(doc, payload)
            target = doc.parse(text)
            units = _units_of(target)
            if not all(_has_units(u) for u in units):
                raise ValueError(f"{text} has no units in it")
            if len(units) == 1:
                d_node, d_target = dimension_of(node), dimension_of(units[0])
                if d_node is not None and d_target is not None and d_node != d_target:
                    raise ValueError(f"Cannot convert: the selection is {deps_name(d_node) or 'of dimension'} "
                                     f"{_plain_deps(d_node)}, {text} is {deps_name(d_target) or 'of dimension'} "
                                     f"{_plain_deps(d_target)}")
            new = convert_to(node, units if len(units) > 1 else units[0], "SI")
            self._commit(doc, path, children, node, new)
            return None
        if method == "si":
            path, node, children = self._target(doc, payload)
            self._commit(doc, path, children, node, to_si(node))
            return None
        if method == "simplify":
            path, node, children = self._target(doc, payload)
            self._commit(doc, path, children, node, simplify_units(node))
            return None
        raise ValueError(f"The units add-on has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]) -> Optional[str]:
        if method == "convert":
            return f"Units: convert to {str(payload.get('target') or '').strip()}"
        if method == "si":
            return "Units: SI base units"
        if method == "simplify":
            return "Units: simplify"
        return None


def _plain_deps(d: Deps) -> str:
    if not d:
        return "1"
    out = []
    for k, v in d:
        sym = {"temperature": "Θ"}.get(k, BASE_SYMBOLS.get(k, k))
        out.append(sym + ("" if v == 1 else "^" + str(v)))
    return " ".join(out)


ADDON = UnitsAddon()
#: The same add-on with the one-letter abbreviations in scope (a document's
#: *short unit names* switch).
ADDON_SHORT = UnitsAddon(short=True)

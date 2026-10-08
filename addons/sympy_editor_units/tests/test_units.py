import json
import sys
from pathlib import Path

import pytest
from sympy import Symbol, latex, srepr
from sympy.physics.units import Quantity, hour, joule, kilogram, kilometer, meter, newton, second, speed_of_light

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor.printer import strip_annotations  # noqa: E402
from sympy_editor_units import ADDON, ADDON_SHORT, check, dimension_of, unit_names  # noqa: E402

x = Symbol("x")


def _doc(expr="x", **kw):
    return Document(expr, addons=[ADDON], **kw)


def _call(doc, method, **payload):
    return doc.handle(dict(action="addon", addon="units", method=method, **payload))


def _set(doc, src):
    snap = doc.handle({"action": "set", "src": src})
    assert not snap.get("error"), snap.get("error")
    return snap


def test_unit_names_are_typed_and_stay_units_from_one_edit_to_the_next():
    doc = _doc()
    _set(doc, "5 meter/second + 3 km/hour")
    assert doc.expr == 5 * meter / second + 3 * kilometer / hour
    # The formula now holds the units; typing them again must still give
    # units - a Quantity's arguments are Symbols, which once became the
    # formula's own names and shadowed the add-on's.
    _set(doc, "2 meter + x")
    assert doc.expr.atoms(Quantity) == {meter}
    _set(doc, "speed_of_light*gravitational_constant")
    assert speed_of_light in doc.expr.atoms(Quantity)
    # ...and the Symbols panel does not list hundreds of unit names
    assert [s["name"] for s in doc.snapshot()["symbols"]] == []


def test_single_letters_stay_variables_until_the_switch():
    doc = _doc()
    _set(doc, "5 m/s")
    assert not doc.expr.atoms(Quantity)                    # m and s: variables
    assert "m" not in unit_names() and "km" in unit_names() and "m" in unit_names(short=True)
    assert _call(doc, "short", on=True)["query"]["result"] == {"short": True}
    assert doc.addons["units"] is ADDON_SHORT and doc.snapshot()["units"]["short"] is True
    _set(doc, "3 g")                                       # a name the formula does not use: now a unit
    assert doc.expr.atoms(Quantity)
    other = _doc()                                         # the switch is per document
    _set(other, "3 g")
    assert not other.expr.atoms(Quantity)
    _call(doc, "short", on=False)
    assert doc.addons["units"] is ADDON


def test_units_render_as_their_symbols_and_the_annotation_is_transparent():
    doc = _doc()
    snap = _set(doc, "5 meter/second**2 + x*kilogram*meter/(joule*second**2)")
    assert r"\text{m}" in snap["latex_plain"] and r"\text{s}" in snap["latex_plain"]
    assert strip_annotations(snap["latex"]) == latex(doc.expr)


def test_dimensions():
    assert dict(dimension_of(5 * meter / second)) == {"length": 1, "time": -1}
    assert dimension_of(x) == ()
    assert dimension_of(newton * meter) == dimension_of(joule)
    doc = _doc()
    _set(doc, "kg*meter/second**2")
    res = _call(doc, "inspect", path="/")["query"]["result"]
    assert res["dimension"]["name"] == "force" and res["dimension"]["known"]
    assert res["dimension"]["latex"] == r"\mathsf{M}\,\mathsf{L}\,\mathsf{T}^{-2}"
    assert res["problems"] == [] and res["has_units"]


def test_a_sum_whose_terms_disagree_is_flagged_term_by_term():
    doc = _doc()
    snap = _set(doc, "5 meter + 2 second + 3 meter*x")
    problems = snap["units"]["problems"]
    flagged = {doc.get(p["path"]) for p in problems}
    assert flagged == {2 * second}
    for p in problems:
        assert p["path"] in snap["nodes"]                  # a path the editor selects with
    # a relation, a function's argument, an exponent
    snap = _set(doc, "Eq(5 joule, 3 newton)")
    assert [doc.get(p["path"]) for p in snap["units"]["problems"]] == [3 * newton]
    snap = _set(doc, "sin(3 meter) + exp(x)")
    assert [doc.get(p["path"]) for p in snap["units"]["problems"]] == [3 * meter]
    snap = _set(doc, "x**(2 second)*meter")
    assert [p["path"] in snap["nodes"] for p in snap["units"]["problems"]] == [True]
    # under a fraction and a minus sign the paths are the view's
    snap = _set(doc, "x/(2*second) - 3*meter/second**2")
    (p,) = snap["units"]["problems"]
    assert p["path"] in snap["nodes"]
    # inspect lists only what is under the selection
    snap = _set(doc, "(meter + second)*(x + 1)")
    path = next(p for p, n in snap["nodes"].items() if n["src"] == "meter + second")
    assert len(_call(doc, "inspect", path=path)["query"]["result"]["problems"]) == 1
    other = next(p for p, n in snap["nodes"].items() if n["src"] == "x + 1")
    assert _call(doc, "inspect", path=other)["query"]["result"]["problems"] == []


def test_a_tie_flags_the_term_read_second():
    _, problems = check(5 * meter + 2 * second)
    assert [p["src"] for p in problems] == ["2*second"]


def test_a_plain_variable_against_units_is_the_odd_one_out():
    _, problems = check(x + 5 * meter)
    assert [p["src"] for p in problems] == ["x"]


def test_convert_si_and_simplify_are_undoable_steps():
    doc = _doc()
    _set(doc, "36 km/hour")
    _call(doc, "convert", path="/", target="meter/second")
    assert doc.expr == 10 * meter / second
    assert doc.history_labels()["actions"][-1] == "Units: convert to meter/second"
    doc.undo()
    assert doc.expr == 36 * kilometer / hour
    _call(doc, "si", path="/")
    assert doc.expr == 10 * meter / second and doc.history_labels()["actions"][-1] == "Units: SI base units"
    _set(doc, "newton*meter + x")
    _call(doc, "simplify", path="/0" if doc.expr.args[0] != x else "/1")
    assert joule in doc.expr.atoms(Quantity)
    # several target units
    _set(doc, "joule")
    _call(doc, "convert", path="/", target="kg, meter, second")
    assert doc.expr == kilogram * meter ** 2 / second ** 2


def test_convert_refuses_what_cannot_be():
    doc = _doc()
    _set(doc, "5 meter")
    err = _call(doc, "convert", path="/", target="second")["query"]["error"]
    assert "Cannot convert" in err and "length" in err and "time" in err
    assert "no units" in _call(doc, "convert", path="/", target="x")["query"]["error"]
    assert _call(doc, "convert", path="/", target="")["query"]["error"]
    assert "Nothing to change" in _call(doc, "convert", path="/", target="meter")["query"]["error"]
    assert doc.expr == 5 * meter and len(doc.history_labels()["actions"]) == 2


def test_a_range_converts_too():
    doc = _doc()
    _set(doc, "1 km + 1 meter + x")
    parent_children = [i for i, a in enumerate(doc.expr.args) if a.atoms(Quantity)]
    _call(doc, "convert", path="/", children=parent_children, target="meter")
    assert doc.expr == 1001 * meter + x


def test_ops_in_the_transform_menu():
    doc = _doc()
    names = [op["name"] for op in doc.snapshot()["ops"]]
    assert "units_si" in names and "units_simplify" in names
    _set(doc, "2 km")
    doc.apply("/", "units_si")
    assert doc.expr == 2000 * meter


def test_a_session_with_units_is_saved_and_opened_again():
    doc = _doc()
    _call(doc, "short", on=True)
    _set(doc, "5 m/s + 36 kPa*x")
    _call(doc, "si", path="/")
    state = json.loads(json.dumps(doc.export()))
    assert state["addon_state"] == {"units": {"short": True}}
    again = Document(0, addons=["sympy_editor_units"], **state)
    assert again.expr == doc.expr and srepr(again.expr) == srepr(doc.expr)
    assert again.expr.atoms(Quantity)
    assert [str(e) for e in again._history] == [str(e) for e in doc._history]
    assert again.addons["units"].short                      # the switch came back
    again.undo()
    assert again.expr.atoms(Quantity)
    # and as a file
    text = doc.save_text()
    fresh = _doc()
    fresh.open_text(text)
    assert fresh.expr == doc.expr and fresh.addons["units"].short


def test_no_units_no_check():
    doc = _doc()
    snap = _set(doc, "x + 1")
    assert snap["units"] == {"short": False, "has_units": False}
    res = _call(doc, "inspect", path="/")["query"]["result"]
    assert res["has_units"] is False and res["dimension"]["name"] == "dimensionless"


def test_the_package_goes_into_a_pyodide_page():
    files = ADDON.python_sources()
    assert "__init__.py" in files and "static/units.js" in files and "static/units.css" in files


@pytest.mark.parametrize("name", ["meter", "kilopascal", "speed_of_light", "electronvolt", "degree"])
def test_every_name_srepr_writes_is_read_back(name):
    q = unit_names()[name]
    assert str(q.name) in unit_names()
    doc = _doc()
    doc.set(5 * q)
    state = doc.export()
    assert Document(0, addons=[ADDON], **state).expr == 5 * q

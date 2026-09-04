"""The add-on contract (sympy_editor.addons): what a Document, a page and
the widget do with an Addon.  The add-on here is a small one written in
place; the real ones live in ``addons/`` at the root of the repository, each
with tests of its own."""
import json

import pytest
from sympy import Basic, Function, Integer, cos, sin, symbols

from sympy_editor import Addon, Document, load_addon, make_op, to_html
from sympy_editor.addons import load_addons
from sympy_editor.html import build_config
from sympy_editor.ops import KINDS, node_kind
from sympy_editor.printer import REBUILDERS, rebuild

x, y = symbols("x y")


class Boxed(Basic):
    """A node from "another library": a value in a box, printed as such."""

    def __new__(cls, value):
        return Basic.__new__(cls, value)

    def _latex(self, printer):
        return r"\boxed{%s}" % printer._print(self.args[0])

    def _sympystr(self, printer):
        return "Box(%s)" % printer._print(self.args[0])


class DemoAddon(Addon):
    name = "demo"
    label = "Demo"
    kinds = {"box": (Boxed,)}
    kind_labels = {"box": "Box"}
    ops = (
        make_op("unbox", lambda b: b.args[0], label="Take out of the box", kinds=("box",)),
        make_op("count_terms", lambda e, doc=None: Integer(len(e.args) + len(doc.addons)), label="Count", context=True),
    )
    rebuilders = {Boxed: lambda node, args: Boxed(args[0])}
    js = 'SympyEditor.registerAddon("demo", {mount: function (api) { return {}; }});'
    css = ".se-addon-demo { color: red; }"

    def namespace(self):
        # Both what a user types and what srepr writes (the class name).
        return {"Box": Boxed, "Boxed": Boxed}

    def make_symbol(self, name):
        return Function(name)(x) if name.startswith("f") else None

    def contribute(self, doc, snap, expr):
        snap["demo"] = {"boxes": sum(1 for n in expr.atoms(Boxed)) + sum(1 for n in expr.args if isinstance(n, Boxed)),
                        "src": str(expr)}

    def handle(self, doc, method, payload):
        if method == "count":
            return {"n": len(doc.expr.args)}
        if method == "box_it":
            return Boxed(doc.expr)
        if method == "box_at":
            doc.replace(payload["path"], Boxed(doc.get(payload["path"])))
            return None
        raise ValueError("no such method: " + method)

    def describe(self, method, payload):
        return "Demo did " + method


ADDON = DemoAddon()


def test_activation_adds_the_kind_and_the_ops():
    doc = Document(Boxed(x + y), addons=[ADDON])
    # the kind is the document's, not the process's
    assert "box" in doc.kinds and list(doc.kinds).index("box") < list(doc.kinds).index("scalar")
    assert "box" not in KINDS and doc.kind_labels["box"] == "Box"
    assert node_kind(doc.expr, doc.kinds) == "box" and node_kind(doc.expr) == "other"
    snap = doc.snapshot()
    assert snap["addons"] == ["demo"]
    # (only the demo entry: the environment may have add-ons installed too)
    assert [a for a in snap["addons_available"] if a["name"] == "demo"] == [{"name": "demo", "label": "Demo", "on": True, "requires": []}]
    names = [op["name"] for op in snap["ops"]]
    assert "unbox" in names and "count_terms" in names and "simplify" in names
    assert snap["nodes"]["/"]["kind"] == "box" and snap["nodes"]["/0"]["src"] == "x + y"
    assert r"\boxed" in snap["latex"]
    assert snap["demo"] == {"boxes": 1, "src": "Box(x + y)"}


def test_typed_input_uses_the_namespace_and_make_symbol():
    doc = Document(x, addons=[ADDON])
    doc.replace("/", "Box(fun)")
    assert isinstance(doc.expr, Boxed)
    assert doc.expr.args[0] == Function("fun")(x)   # a new name starting with f is a function of x here
    doc.replace("/", "g + 1")
    assert doc.expr == symbols("g") + 1              # other names are symbols, as always


def test_srepr_round_trips_through_the_namespace():
    doc = Document(Boxed(sin(x)), addons=[ADDON])
    again = Document(doc.export()["history"][-1], addons=[ADDON])
    assert again.expr == doc.expr
    # without the add-on the name is unknown: sympify reads an undefined function
    assert not isinstance(Document(doc.export()["history"][-1]).expr, Boxed)


def test_editing_inside_the_foreign_node_uses_the_rebuilder():
    doc = Document(Boxed(sin(x)), addons=[ADDON])
    assert Boxed in REBUILDERS
    doc.replace("/0", "cos(x)")
    assert doc.expr == Boxed(cos(x))
    assert rebuild(Boxed(x), [y]) == Boxed(y)


def test_ops_of_the_addon_and_the_context_one():
    doc = Document(Boxed(x + y), addons=[ADDON])
    doc.apply("/", "count_terms")
    assert doc.expr == Integer(2)                   # one argument, one add-on
    doc.undo()
    doc.apply("/", "unbox")
    assert doc.expr == x + y


def test_methods_query_change_and_error():
    doc = Document(x + y, addons=[ADDON])
    snap = doc.handle({"action": "addon", "addon": "demo", "method": "count"})
    assert snap["query"] == {"addon": "demo", "method": "count", "result": {"n": 2}}
    assert not doc.can_undo                          # a query commits nothing
    snap = doc.handle({"action": "addon", "addon": "demo", "method": "box_it"})
    assert doc.expr == Boxed(x + y) and snap["addon"] == {"name": "demo", "method": "box_it"}
    assert doc.history_labels()["actions"][-1] == "Demo did box_it"
    snap = doc.handle({"action": "addon", "addon": "demo", "method": "box_at", "path": "/0"})
    assert doc.expr == Boxed(Boxed(x + y)) and not snap["error"]
    snap = doc.handle({"action": "addon", "addon": "demo", "method": "nope"})
    assert "no such method" in snap["error"] and doc.expr == Boxed(Boxed(x + y))
    snap = doc.handle({"action": "addon", "addon": "other", "method": "count"})
    assert "No add-on 'other'" in snap["error"]


def test_loading_by_name_and_by_module():
    assert load_addon(ADDON) is ADDON
    with pytest.raises(ValueError):
        load_addon("no_such_addon_module_anywhere")
    with pytest.raises(TypeError):
        load_addon(42)
    with pytest.raises(ValueError):
        load_addons([ADDON, ADDON])

    class Bad(Addon):
        name = "Not-Valid"
    with pytest.raises(ValueError):
        load_addon(Bad())


def test_the_page_and_the_config_carry_the_front_end():
    doc = Document(x, addons=[ADDON])
    cfg = build_config(doc)
    assert cfg["addons"] == [{"name": "demo", "label": "Demo", "js": ADDON.js, "css": ADDON.css, "options": {}}]
    assert cfg["document"]["addons"] == ["tests"] or cfg["document"]["addons"] == [ADDON.module]
    assert "packages" in cfg and "micropip" in cfg
    html = to_html(x, addons=[ADDON])
    assert "registerAddon" in html and "addons.py" in json.dumps(list(cfg["sources"]))
    cfg = build_config(doc, backend="http")
    assert cfg["addons"][0]["name"] == "demo" and "packages" not in cfg


def test_the_widget_passes_the_front_end():
    anywidget = pytest.importorskip("anywidget")   # noqa: F841
    from sympy_editor.widget import SympyEditorWidget
    w = SympyEditorWidget(x + y, addons=[ADDON])
    assert w.options["addons"][0]["name"] == "demo"
    w._on_msg(w, {"action": "addon", "addon": "demo", "method": "count", "_req": 7}, [])
    w.wait(5)
    snap = json.loads(w.snapshot)
    assert snap["query"]["result"] == {"n": 2} and snap["_req"] == 7


def test_installed_lists_entry_points_and_specs_name_objects(tmp_path, monkeypatch):
    """An add-on is an external package: the loader reads the entry points
    of whatever is installed, and a ``module:object`` spec names an object
    under any name."""
    from sympy_editor import installed_addons
    from sympy_editor import addons as mod
    assert isinstance(installed_addons(), dict)
    # a module with the add-on under a name of its own
    pkg = tmp_path / "somebody_elses_addon.py"
    pkg.write_text("from sympy_editor import Addon\nclass A(Addon):\n    name = 'elsewhere'\nTHING = A()\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert load_addon("somebody_elses_addon:THING").name == "elsewhere"
    with pytest.raises(ValueError, match="defines no ADDON"):
        load_addon("somebody_elses_addon")
    # a contract from the future is refused with a message
    class Future(Addon):
        name = "future"
        api_version = mod.API_VERSION + 1
    with pytest.raises(ValueError, match="API version"):
        load_addon(Future())


def test_switching_on_and_off_at_run_time():
    doc = Document(Boxed(x + y), available=[ADDON])           # known, off
    snap = doc.snapshot()
    assert snap["addons"] == [] and snap["addons_available"][0]["on"] is False
    assert snap["nodes"]["/"]["kind"] == "other" and "unbox" not in [op["name"] for op in snap["ops"]]
    assert "demo" not in snap
    snap = doc.handle({"action": "addons", "enable": ["demo"]})
    assert not snap["error"] and snap["addons"] == ["demo"] and snap["addons_available"][0]["on"] is True
    assert snap["nodes"]["/"]["kind"] == "box" and "unbox" in [op["name"] for op in snap["ops"]]
    assert snap["addon_clients"][0]["name"] == "demo" and snap["demo"]["boxes"] == 1
    assert not doc.can_undo                                   # a switch is not a step
    doc.addon_state["demo"]["kept"] = 1
    snap = doc.handle({"action": "addons", "disable": ["demo"]})
    assert snap["addons"] == [] and snap["nodes"]["/"]["kind"] == "other"
    assert "unbox" not in doc.ops and "box" not in doc.kinds and "demo" not in snap
    snap = doc.handle({"action": "addon", "addon": "demo", "method": "count"})
    assert "No add-on 'demo'" in snap["error"]
    doc.enable("demo")
    assert doc.addon_state["demo"] == {"kept": 1}             # state survives being off
    doc.disable("demo"); doc.disable("never")                 # idempotent, unknown is fine
    assert doc.addons == {}


def test_an_addon_that_cannot_load_is_listed_with_its_error():
    class Broken(Addon):
        name = "broken"

        def activate(self):
            raise ImportError("pip install something")
    doc = Document(x, available=[Broken()])
    snap = doc.handle({"action": "addons", "enable": ["broken"]})
    assert "pip install something" in snap["error"] and doc.addons == {}
    assert doc.available_addons()[0]["on"] is False
    snap = doc.handle({"action": "addons", "enable": ["no_such_addon_anywhere"]})
    assert "No add-on" in snap["error"]
    snap = doc.handle({"action": "addons", "enable": ["no_such_addon_anywhere"]})   # the failure is remembered
    assert "No add-on" in snap["error"]


def test_the_page_carries_what_can_be_switched_on():
    doc = Document(x, available=[ADDON])
    cfg = build_config(doc)
    assert cfg["addons"] == [] and cfg["document"]["available"] == [ADDON.module] and "addons" not in cfg["document"]
    assert ADDON.module in cfg["packages"]


def test_addon_state_travels_with_a_session():
    """What an add-on keeps about a document goes with export() and comes
    back through restore_state when the session is opened again."""
    class Keeper(Addon):
        name = "keeper"

        def export_state(self, doc):
            return {"notes": list(doc.addon_state["keeper"].get("notes", []))}

        def restore_state(self, doc, data):
            doc.addon_state["keeper"]["notes"] = list(data.get("notes", []))

    doc = Document(x + y, addons=[Keeper()])
    assert "addon_state" not in doc.export() or doc.export()["addon_state"] == {"keeper": {"notes": []}}
    doc.addon_state["keeper"]["notes"] = ["a", "b"]
    state = doc.export()
    assert state["addon_state"] == {"keeper": {"notes": ["a", "b"]}}
    again = Document(x, addons=[Keeper()], **state)
    assert again.addon_state["keeper"]["notes"] == ["a", "b"] and again.expr == x + y
    # given to an add-on switched on later, too
    later = Document(x, available=[Keeper()], addon_state=state["addon_state"])
    assert "keeper" not in later.addon_state
    later.enable("keeper")
    assert later.addon_state["keeper"]["notes"] == ["a", "b"]
    # an add-on with nothing to say exports nothing (the default)
    assert "addon_state" not in Document(x, addons=[ADDON]).export()

import pytest
from sympy import Function, Integer, Matrix, Symbol, cos, sin, symbols, sqrt

from sympy_editor import Document, register_op

x, y = symbols("x y")


def test_replace_parses_in_context_and_keeps_assumptions():
    p = Symbol("p", positive=True)
    doc = Document(p**2 + 1)
    path = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "p**2")
    doc.replace(path, "p**3")
    assert doc.expr == p**3 + 1
    assert doc.expr.free_symbols == {p}
    assert sqrt(doc.expr.atoms(Symbol).pop() ** 2) == p  # assumption preserved


def test_replace_reuses_undefined_functions():
    f = Function("f")
    doc = Document(f(x) + y)
    path = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "f(x)")
    doc.replace(path, "f(y)")
    assert doc.expr == f(y) + y
    assert doc.expr.atoms(Function).pop().func is f


def test_root_replacement_and_history():
    doc = Document(x + 1)
    doc.replace("/", "x*y")
    assert doc.expr == x * y
    assert doc.can_undo and not doc.can_redo
    doc.undo()
    assert doc.expr == x + 1 and doc.can_redo
    doc.redo()
    assert doc.expr == x * y
    doc.undo()
    doc.replace("/", "2")
    assert doc.expr == Integer(2)
    assert not doc.can_redo


def test_delete_and_apply():
    doc = Document(x + y + 1)
    doc.delete("/0")
    assert len(doc.expr.args) == 2
    doc = Document((x + 1) ** 2)
    doc.apply("/", "expand")
    assert doc.expr == x**2 + 2 * x + 1
    doc.apply("/", "factor")
    assert doc.expr == (x + 1) ** 2
    doc.apply("/", lambda e: e + 1)
    assert doc.expr == (x + 1) ** 2 + 1


def test_handle_reports_errors_without_changing_state():
    doc = Document(x + y)
    snap = doc.handle({"action": "replace", "path": "/0", "src": "x +"})
    assert snap["error"] and "parse" in snap["error"].lower()
    assert doc.expr == x + y
    snap = doc.handle({"action": "apply", "path": "/", "op": "nope"})
    assert "Unknown operation" in snap["error"]
    snap = doc.handle({"action": "delete", "path": "/"})
    assert snap["error"]
    snap = doc.handle({"action": "bogus"})
    assert "Unknown action" in snap["error"]
    snap = doc.handle({"action": "replace", "path": "/9", "src": "1"})
    assert snap["error"]


def test_snapshot_shape():
    doc = Document(sin(x) / cos(y))
    snap = doc.snapshot()
    for key in ("seq", "latex", "latex_plain", "src", "srepr", "nodes", "can_undo", "can_redo", "ops", "error"):
        assert key in snap
    assert snap["nodes"]["/"]["src"] == str(doc.expr)
    assert snap["nodes"]["/"]["type"] == "Mul"
    assert {op["name"] for op in snap["ops"]} >= {"simplify", "expand", "factor"}
    assert snap["error"] is None
    assert doc.snapshot()["seq"] == snap["seq"] + 1


def test_handle_sequence():
    doc = Document(x**2)
    snap = doc.handle({"action": "replace", "path": "/1", "src": "3"})
    assert snap["error"] is None and doc.expr == x**3
    snap = doc.handle({"action": "undo"})
    assert doc.expr == x**2 and snap["can_redo"]
    doc.handle({"action": "set", "src": "y"})
    assert doc.expr == y


def test_matrix_and_string_input():
    doc = Document(Matrix([[x, 1], [2, y]]))
    doc.replace("/2/1", "7")
    assert doc.expr[0, 1] == 7
    doc2 = Document(doc.snapshot()["srepr"])
    assert doc2.expr == doc.expr


def test_implicit_parser():
    doc = Document(x + y, parser="implicit")
    doc.replace("/", "2x + 3y")
    assert doc.expr == 2 * x + 3 * y
    with pytest.raises(ValueError):
        Document(x, parser="weird")


def test_listeners_and_custom_ops():
    seen = []
    doc = Document(x)
    doc.on_change(seen.append)
    doc.replace("/", "y")
    assert seen == [y]

    @register_op("double", label="Double")
    def double(e):
        return 2 * e

    doc = Document(x)
    assert "double" in doc.ops
    doc.apply("/", "double")
    assert doc.expr == 2 * x


def test_rejects_non_sympy():
    with pytest.raises(TypeError):
        Document([1, 2])

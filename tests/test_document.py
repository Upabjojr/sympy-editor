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


# -- denominators, matrix context, symbol types, op kinds ---------------------

def test_denominator_power_is_selectable_and_edits_as_denominator():
    from sympy import symbols
    x, y = symbols("x y")
    doc = Document(x / (x + 1) ** 2)
    nodes = doc.snapshot()["nodes"]
    assert {k: nodes["/1"][k] for k in ("src", "type", "kind", "reciprocal")} == {
        "src": "(x + 1)**2", "type": "Pow", "kind": "scalar", "reciprocal": True}
    assert "reciprocal" not in nodes["/1/0"]              # the base is a real node
    doc.handle({"action": "replace", "path": "/1", "src": "y**3", "reciprocal": True})
    assert doc.expr == x / y ** 3
    doc.undo()
    doc.replace("/1/0", "y")                              # editing the base still works
    assert doc.expr == x / y ** 2


def test_new_names_in_a_matrix_slot_are_matrix_symbols():
    from sympy import MatrixSymbol, Symbol, Transpose, symbols
    A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
    x, y = symbols("x y")
    doc = Document(A * B + 2 * A.T)
    doc.replace("/1/1", "C.T")
    C = MatrixSymbol("C", 2, 2)
    assert doc.expr == A * C.T + 2 * A.T
    assert doc.namespace()["C"] == C
    # the whole expression, set from a string, keeps the matrix context
    doc.handle({"action": "set", "src": "D*A + C.I"})
    assert doc.namespace()["D"] == MatrixSymbol("D", 2, 2)
    # a scalar slot still gets scalars; sin() and pi are not turned into matrices
    doc2 = Document(x + y)
    doc2.replace("/0", "z*sin(w) + pi")
    assert doc2.namespace()["z"] == Symbol("z")
    assert doc._new_names("C.T*sin(x) + D_1 + pi", {"x": x}) == ["C", "D_1"]


def test_symbols_panel_info_and_retype():
    from sympy import MatAdd, MatrixSymbol, Matrix, symbols
    x, y = symbols("x y")
    p = symbols("p", positive=True)
    doc = Document(x * y + y ** 2 + p)
    symbols_info = doc.snapshot()["symbols"]
    assert [s["name"] for s in symbols_info] == ["p", "x", "y"]
    assert symbols_info[1] == {"name": "x", "used": True, "type": "Symbol", "assumptions": []}
    assert "positive" in symbols_info[0]["assumptions"] and "commutative" not in symbols_info[0]["assumptions"]
    doc.handle({"action": "retype", "name": "y", "type": "MatrixSymbol", "rows": "2", "cols": "n"})
    Y = MatrixSymbol("y", 2, symbols("n"))
    assert doc.namespace()["y"] == Y
    assert [s for s in doc.symbol_info() if s["name"] == "y"] == [
        {"name": "y", "used": True, "type": "MatrixSymbol", "shape": ["2", "n"]}]
    doc.undo()
    doc.retype("y", "Matrix", 2, 2)
    assert isinstance(doc.expr.args[0], Matrix) or doc.expr.has(Matrix) or "y[0, 0]" in str(doc.expr)
    # a matrix under a transpose cannot become a scalar: refused, state unchanged
    doc3 = Document(MatrixSymbol("A", 2, 2).T)
    snap = doc3.handle({"action": "retype", "name": "A", "type": "Symbol"})
    assert snap["error"] and "cannot become" in snap["error"]
    assert doc3.expr == MatrixSymbol("A", 2, 2).T
    assert "No symbol" in doc3.handle({"action": "retype", "name": "Q", "type": "Symbol"})["error"]


def test_ops_have_kinds_and_matrix_ops_apply_to_matrices():
    from sympy import Determinant, MatrixSymbol, Matrix, Trace, symbols
    x, y = symbols("x y")
    A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
    doc = Document(A * B + x * A)
    snap = doc.snapshot()
    kinds = {n["src"]: n["kind"] for n in snap["nodes"].values()}
    assert snap["nodes"]["/"]["kind"] == "matrix" and kinds["A*B"] == "matrix" and kinds["x"] == "scalar"
    by_name = {op["name"]: op for op in snap["ops"]}
    assert by_name["simplify"]["kinds"] is None
    assert by_name["transpose"]["kinds"] == ["matrix"]
    doc = Document(A * B)
    doc.apply("/", "transpose")
    assert doc.expr == (A * B).T
    doc.apply("/", "trace")
    assert doc.expr == Trace((A * B).T)
    doc = Document(Matrix([[x, 1], [2, y]]))
    doc.apply("/", "determinant")
    assert doc.expr == x * y - 2
    doc = Document(A)
    doc.apply("/", "as_explicit")
    assert doc.snapshot()["nodes"]["/"]["kind"] == "matrix" and doc.expr.shape == (2, 2)
    import pytest
    from sympy_editor.ops import register_op
    with pytest.raises(ValueError):
        register_op("bad", lambda e: e, kinds=("nonsense",))


def test_srepr_strings_keep_matrix_symbol_names():
    # SymPy < 1.14 does not export Str; a Document built from srepr text (the
    # Pyodide front end does that) must still get MatrixSymbol("A"), not an
    # undefined function Str(A) whose name prints as "Str".
    from sympy import MatrixSymbol, srepr
    A = MatrixSymbol("A", 2, 2)
    doc = Document(srepr(A * A.T))
    assert doc.namespace()["A"] == A
    assert doc.snapshot()["src"] == "A*A.T"
    assert Document("MatrixSymbol(Str('B'), Integer(2), Integer(3))").expr == MatrixSymbol("B", 2, 3)


# -- declared symbols and insertion ------------------------------------------

def test_declare_before_use_and_undeclare():
    from sympy import Function, MatrixSymbol, Symbol, symbols
    x = symbols("x")
    doc = Document(x + 1)
    assert [s["name"] for s in doc.symbol_info()] == ["x"]
    doc.handle({"action": "declare", "name": "M", "type": "MatrixSymbol", "rows": "2", "cols": "n"})
    M = MatrixSymbol("M", 2, symbols("n"))
    assert doc.namespace()["M"] == M and doc.declared["M"] == M
    info = {s["name"]: s for s in doc.symbol_info()}
    assert info["M"] == {"name": "M", "used": False, "type": "MatrixSymbol", "shape": ["2", "n"]}
    assert info["x"]["used"] is True
    doc.replace("/", "M*x + M.T")                       # the declared name resolves
    assert doc.expr == M * x + M.T
    assert "occurs" in doc.handle({"action": "undeclare", "name": "M"})["error"]
    doc.undo()
    doc.handle({"action": "undeclare", "name": "M"})
    assert "M" not in doc.namespace()
    assert "No declared" in doc.handle({"action": "undeclare", "name": "M"})["error"]
    # assumptions and functions
    doc.declare("p", "Symbol", assumptions="positive, integer")
    assert doc.declared["p"] == Symbol("p", positive=True, integer=True)
    doc.declare("g", "Function")
    assert doc.declared["g"] == Function("g")
    doc.replace("/", "g(p) + x")
    assert doc.expr.atoms(Symbol) == {Symbol("p", positive=True, integer=True), x}
    assert "Invalid symbol name" in doc.handle({"action": "declare", "name": "2bad"})["error"]
    assert "Unknown symbol type" in doc.handle({"action": "declare", "name": "q", "type": "Tensor"})["error"]


def test_declare_existing_name_retypes_and_assumptions_propagate():
    from sympy import MatrixSymbol, Symbol, symbols
    x, y = symbols("x y")
    doc = Document(x * y + y)
    doc.declare("y", "Symbol", assumptions=["positive"])
    yp = Symbol("y", positive=True)
    assert doc.expr == x * yp + yp
    assert doc.namespace()["y"] is not None and doc.namespace()["y"].is_positive
    doc.handle({"action": "retype", "name": "x", "type": "Symbol", "assumptions": ["real"]})
    assert all(s.is_real for s in doc.expr.atoms(Symbol) if s.name == "x")
    # a declared-but-unused name can be retyped freely, even to a Function
    doc.declare("h", "Symbol")
    doc.retype("h", "Function")
    assert doc.declared["h"].__name__ == "h"
    # but a used symbol cannot become a function
    assert "remove those uses" in doc.handle({"action": "retype", "name": "x", "type": "Function"})["error"]


def test_document_symbols_kwarg_and_srepr_strings():
    from sympy import MatrixSymbol, srepr, symbols
    x = symbols("x")
    A = MatrixSymbol("A", 3, 3)
    doc = Document(x, symbols=[A, srepr(MatrixSymbol("B", 3, 3)), "Function('f')"])
    assert doc.namespace()["A"] == A and doc.namespace()["B"] == MatrixSymbol("B", 3, 3)
    assert doc.namespace()["f"].__name__ == "f"
    doc.replace("/", "A*B + f(x)*A")
    assert doc.expr.shape == (3, 3)
    from sympy_editor.html import build_config
    cfg = build_config(doc, backend="pyodide")
    assert len(cfg["document"]["symbols"]) == 3
    doc2 = Document(cfg["srepr"], symbols=cfg["document"]["symbols"])
    assert doc2.expr == doc.expr and set(doc2.declared) == {"A", "B", "f"}


def test_insert_terms_factors_and_arguments():
    from sympy import Function, MatrixSymbol, symbols
    x, y, z = symbols("x y z")
    f = Function("f")
    doc = Document(x + y)
    nodes = doc.snapshot()["nodes"]
    assert nodes["/"]["insertable"] is True and nodes["/"]["nargs"] == 2
    assert nodes["/0"]["insertable"] is False
    doc.handle({"action": "insert", "path": "/", "index": 2, "src": "z**2"})
    assert doc.expr == x + y + z**2
    doc = Document(x * f(y))
    path = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "f(y)")
    doc.insert(path, 1, "z")                           # f(y) -> f(y, z)
    assert doc.expr == x * f(y, z)
    doc.insert("/", 0, "2")
    assert doc.expr == 2 * x * f(y, z)
    A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
    doc = Document(A * B)
    doc.insert("/", 1, "C")                            # new name in a matrix product is a matrix
    C = MatrixSymbol("C", 2, 2)
    assert doc.expr == A * C * B                       # order matters for MatMul
    assert "Invalid insertion index" in doc.handle({"action": "insert", "path": "/", "index": 9, "src": "x"})["error"]
    assert doc.handle({"action": "insert", "path": "/0", "index": 0, "src": "x"})["error"]  # a MatrixSymbol is not insertable


# -- ranges of adjacent arguments ---------------------------------------------

def test_ranges_replace_delete_apply():
    from sympy import MatrixSymbol, factor, symbols
    a, b, c, d = symbols("a b c d")
    doc = Document(a + b + c + d)
    args = doc.expr.args                                # canonical order: (a, b, c, d)
    snap = doc.snapshot()
    assert snap["nodes"]["/"]["rangeable"] is True and snap["nodes"]["/0"]["rangeable"] is False
    doc.handle({"action": "replace", "path": "/", "children": [1, 2], "src": "z"})
    assert doc.expr == a + d + symbols("z")
    doc.undo()
    doc.handle({"action": "delete", "path": "/", "children": [0, 3]})
    assert doc.expr == b + c
    doc.undo()
    doc = Document(a * b + a * c + d)
    i, j = [k for k, arg in enumerate(doc.expr.args) if arg != d]
    doc.handle({"action": "apply", "path": "/", "children": [i, j], "op": "factor"})
    assert doc.expr == a * (b + c) + d
    # a non-commutative product keeps the position of the range
    A, B, C = (MatrixSymbol(n, 2, 2) for n in "ABC")
    doc = Document(A * B * C)
    doc.replace("/", "B.T", children=[0, 1])
    assert doc.expr == B.T * C
    doc = Document(A * B * C)
    doc.apply("/", "transpose", children=[1, 2])
    assert doc.expr == A * (B * C).T
    # errors: bad indices, non-rangeable target
    assert "Invalid argument range" in doc.handle({"action": "delete", "path": "/", "children": [7]})["error"]
    assert "Invalid argument range" in doc.handle({"action": "delete", "path": "/", "children": []})["error"]


def test_insert_honours_a_leading_operator():
    from sympy import MatrixSymbol, symbols
    x, y, z = symbols("x y z")
    A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
    doc = Document(A * B)
    doc.handle({"action": "insert", "path": "/", "index": 2, "src": "+ B * A"})   # caret after B, in the product
    assert doc.expr == A * B + B * A
    doc = Document(A * B + A)
    mul = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "A*B")
    doc.handle({"action": "insert", "path": mul, "index": 2, "src": "- B*A"})
    assert doc.expr == A * B + A - B * A
    doc = Document(x * z + 1)
    mul = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x*z")
    doc.insert(mul, 2, "+ y")
    assert doc.expr == x * z + y + 1
    mul = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x*z")
    doc.insert(mul, 0, "* 2")                         # "* 2" in the product: a factor
    assert doc.expr == 2 * x * z + y + 1
    doc = Document(x + y)
    doc.insert("/", 2, "* 2")                         # "* 2" in a sum: multiplies it
    assert doc.expr == 2 * (x + y)
    doc = Document(x + y)
    doc.insert("/", 2, "- z")                         # in a sum, a signed term is just a term
    assert doc.expr == x + y - z

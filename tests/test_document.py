import pytest
from sympy import (Function, Integer, Integral, Matrix, MatrixSymbol, Symbol, atan2, cos, log,
                   sin, symbols, sqrt)
from sympy.matrices import MatrixBase
from sympy.tensor.array import NDimArray
from sympy.tensor.array.expressions import ArraySymbol

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

def test_denominator_is_a_view_part_and_edits_as_denominator():
    from sympy import symbols
    x, y = symbols("x y")
    doc = Document(x / (x + 1) ** 2)
    nodes = doc.snapshot()["nodes"]
    assert nodes["/"]["parts"] == ["n", "d"] and not nodes["/"]["insertable"] and not nodes["/"]["rangeable"]
    assert {k: nodes["/d"][k] for k in ("src", "type", "kind")} == {"src": "(x + 1)**2", "type": "Pow", "kind": "scalar"}
    assert "parts" not in nodes["/d"] and "/1" not in nodes  # the tree's Pow(x + 1, -2) is not what is shown
    doc.handle({"action": "replace", "path": "/d", "src": "y**3"})
    assert doc.expr == x / y ** 3
    doc.undo()
    doc.replace("/d/0", "y")                              # the base
    assert doc.expr == x / y ** 2
    doc.undo()
    doc.replace("/d/1", "3")                              # the exponent, as shown (the tree's is -2)
    assert doc.expr == x / (x + 1) ** 3


def test_view_parts_are_editable():
    from sympy import E, symbols
    x, y, z, n = symbols("x y z n")
    doc = Document(1 / n)
    nodes = doc.snapshot()["nodes"]
    assert nodes["/n"]["src"] == "1" and nodes["/d"]["src"] == "n" and nodes["/"]["parts"] == ["n", "d"]
    assert doc.handle({"action": "replace", "path": "/n", "src": "x"})["src"] == "x/n"
    doc.set(1 / (2 * E))
    nodes = doc.snapshot()["nodes"]
    assert nodes["/d"]["src"] == "2*E" and nodes["/d"]["insertable"] and nodes["/d/0"]["src"] == "2" and nodes["/d/1"]["src"] == "E"
    assert doc.handle({"action": "replace", "path": "/d/0", "src": "3"})["src"] == "exp(-1)/3"
    assert doc.handle({"action": "replace", "path": "/n", "src": "x"})["src"] == "x*exp(-1)/3"
    assert doc.handle({"action": "insert", "path": "/d", "index": 2, "src": "*y"})["src"] == "x*exp(-1)/(3*y)"
    doc.set(x - 2 * y)
    nodes = doc.snapshot()["nodes"]
    assert nodes["/1"]["parts"] == ["neg"] and nodes["/1/neg"]["src"] == "2*y" and nodes["/1/neg/0"]["src"] == "2"
    assert doc.handle({"action": "replace", "path": "/1/neg/0", "src": "3"})["src"] == "x - 3*y"
    assert doc.handle({"action": "delete", "path": "/1/neg/1"})["src"] == "x - 3"
    assert "Invalid path" in doc.handle({"action": "delete", "path": "/1/neg"})["error"]    # x has no sign part
    doc.undo()
    assert doc.handle({"action": "delete", "path": "/1/neg"})["src"] == "x"      # the signed term goes
    doc.set(-2 * x * y)
    assert doc.handle({"action": "replace", "path": "/neg/0", "src": "5"})["src"] == "-5*x*y"
    assert doc.handle({"action": "unwrap", "path": "/"})["src"] == "5*x*y"        # the natural part to keep
    doc.set(x * y / z)
    assert doc.handle({"action": "delete", "path": "/n/0"})["src"] == "y/z"
    assert "select the numerator" in doc.handle({"action": "unwrap", "path": "/"})["error"]
    assert doc.handle({"action": "unwrap", "path": "/", "keep": "d"})["src"] == "z"
    assert doc.handle({"action": "unwrap", "path": "/", "keep": "n"})["error"]     # z has no parts


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
    # ... and leaves no trace: the panel still shows a matrix symbol, and a
    # typed A is the one in the expression (a stale declaration used to win)
    assert "A" not in doc3.declared
    assert doc3.symbol_info() == [{"name": "A", "used": True, "type": "MatrixSymbol", "shape": ["2", "2"]}]
    assert doc3.parse("A*A") == MatrixSymbol("A", 2, 2) ** 2
    assert "No symbol" in doc3.handle({"action": "retype", "name": "Q", "type": "Symbol"})["error"]
    # the same for a function turned into a symbol
    from sympy import Function
    doc4 = Document(Function("f")(x) + x)
    assert "remove those uses" in doc4.handle({"action": "retype", "name": "f", "type": "Symbol"})["error"]
    assert "f" not in doc4.declared and doc4.parse("f(y)") == Function("f")(y)


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


def test_kinds_and_type_specific_ops():
    from sympy import Eq, Integral, Matrix, MatrixSymbol, Sum, Array, oo, symbols
    x, n = symbols("x n")
    doc = Document(Integral(x**2, (x, 0, 1)))
    snap = doc.snapshot()
    assert snap["nodes"]["/"]["kinds"] == ["integral", "scalar"] and snap["nodes"]["/"]["kind"] == "integral"
    assert snap["kind_labels"]["integral"] == "Integral" and snap["kind_labels"]["relational"] == "Equation"
    by_name = {op["name"]: op for op in snap["ops"]}
    assert "integral" in by_name["evaluate"]["kinds"] and by_name["transpose"]["kinds"] == ["matrix"]
    doc.apply("/", "evaluate")
    assert doc.expr == symbols("x").integrate((x, 0, 1)) if False else str(doc.expr) == "1/3"
    doc = Document(Sum((x + 1)**2, (x, 0, n)))
    doc.apply("/", "expand_inside")
    assert doc.expr == Sum(x**2 + 2 * x + 1, (x, 0, n))
    doc = Document(Eq(x + 1, 2 * x))
    assert doc.snapshot()["nodes"]["/"]["kinds"] == ["relational"]
    doc.apply("/", "swap_sides")
    assert doc.expr == Eq(2 * x, x + 1)
    doc.apply("/", "to_left")
    assert doc.expr == Eq(x - 1, 0)
    doc = Document(Array([[x, 1], [2, 3]]))
    doc.apply("/", "tomatrix")
    assert doc.expr == Matrix([[x, 1], [2, 3]])
    assert Document(x).snapshot()["nodes"]["/"]["kinds"] == ["scalar"]
    assert Document(MatrixSymbol("A", 2, 2)).snapshot()["nodes"]["/"]["kinds"] == ["matrix", "scalar"]


def test_extend_types_next_to_a_node():
    from sympy import Matrix, MatrixSymbol, sin, symbols
    x, y = symbols("x y")
    doc = Document(Matrix([[x, y], [1, 2]]))
    entry = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x")
    doc.handle({"action": "extend", "path": entry, "side": "after", "src": "+ 1"})
    assert doc.expr[0, 0] == x + 1
    doc.handle({"action": "extend", "path": entry, "side": "before", "src": "2"})     # juxtaposition multiplies
    assert doc.expr[0, 0] == 2 * (x + 1)
    doc.handle({"action": "extend", "path": entry, "side": "after", "src": "y"})
    assert (doc.expr[0, 0] - 2 * y * (x + 1)).expand() == 0
    doc = Document(sin(x) ** 2)
    base = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x")
    doc.extend(base, "after", "- 1")
    assert doc.expr == sin(x - 1) ** 2
    A = MatrixSymbol("A", 2, 2)
    doc = Document(A.T)
    doc.extend("/0", "after", "B")                                               # a new name in a matrix slot is a matrix
    assert doc.expr.doit() == (A * MatrixSymbol("B", 2, 2)).T
    assert "Empty" in doc.handle({"action": "extend", "path": "/0", "side": "after", "src": " "})["error"]


def test_insert_juxtaposition_and_junction_operators():
    from sympy import Function, cos, symbols
    x, y, t = symbols("x y t")
    f = Function("f")
    def ins(expr, src, **kw):
        d = Document(expr); d.handle(dict({"action": "insert", "path": "/", "index": 1, "src": src}, **kw)); return d.expr
    assert ins(x + y, "cos(t)", left=0, right=1) == x * cos(t) + y          # x cos(t): juxtaposition multiplies
    assert ins(x + y, "+ cos(t)", left=0, right=1) == x + y + cos(t)         # "+": a term
    assert ins(x + y, "^2", left=0) == x**2 + y
    assert ins(x + y, "/2", left=0) == x / 2 + y
    assert ins(x + y, "* 3", left=0) == 3 * x + y
    assert ins(x + y, "2", right=1) == x + 2 * y                              # before y
    assert ins(x + y, "t -", right=1) == x + t - y                            # "t -" before y
    assert ins(f(x), ", y", left=0) == f(x, y)                                # ",": a new argument
    assert ins(f(x), "y", left=0) == f(x * y)
    d = Document(x + y); d.insert("/", 2, "t")                                # no neighbours given: plain insertion
    assert d.expr == x + y + t


def test_unwrap_keeps_an_argument():
    from sympy import Integral, cos, sqrt, symbols
    x, y, t = symbols("x y t")
    doc = Document(cos(t) + y)
    c = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "cos(t)")
    doc.handle({"action": "unwrap", "path": c})
    assert doc.expr == t + y
    doc = Document(Integral(x**2, (x, 0, 1)))
    doc.unwrap("/")
    assert doc.expr == x**2
    doc.unwrap("/")                                   # x**2 -> x (the base)
    assert doc.expr == x
    assert "nothing inside" in doc.handle({"action": "unwrap", "path": "/"})["error"]
    doc = Document(sqrt(x) * 3)
    doc.unwrap(next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "sqrt(x)"))
    assert doc.expr == 3 * x
    doc = Document(x + y)
    assert "select the one to keep" in doc.handle({"action": "unwrap", "path": "/"})["error"]
    doc.handle({"action": "unwrap", "path": "/", "keep": 1})
    assert doc.expr in (x, y)
    doc = Document(Integral(x, (x, 0, 1)))
    assert "not an expression" in doc.handle({"action": "unwrap", "path": "/", "keep": 1})["error"]



def test_matrix_and_array_conversions_and_array_tools():
    """Matrices and arrays convert both ways, and an array has its own tools."""
    from sympy import Array

    doc = Document(Matrix([[1, 2], [3, 4]]))
    assert [o for o in doc.snapshot()["ops"] if o["name"] == "to_array"][0]["kinds"] == ["matrix"]
    doc.apply("/", "to_array")
    assert isinstance(doc.expr, NDimArray) and doc.expr == Array([[1, 2], [3, 4]])
    doc.apply("/", "tomatrix")
    assert isinstance(doc.expr, MatrixBase) and doc.expr == Matrix([[1, 2], [3, 4]])
    doc.undo(); doc.undo()
    assert doc.expr == Matrix([[1, 2], [3, 4]])

    # a MatrixSymbol keeps its entries implicit: it becomes an array symbol
    A = MatrixSymbol("A", 2, 3)
    assert Document(A).apply("/", "to_array") == ArraySymbol("A", (2, 3))
    assert Document(ArraySymbol("A", (2, 3))).apply("/", "tomatrix") == A

    # the tools that take axes
    a = Array([[1, 2], [3, 4]])
    assert Document(a).apply("/", "permutedims", args=["(1, 0)"]) == Array([[1, 3], [2, 4]])
    assert Document(a).apply("/", "contraction", args=["(0, 1)"]) == Integer(5)
    assert Document(a).apply("/", "diagonal", args=["(0, 1)"]) == Array([1, 4])
    assert Document(a).apply("/", "array_rank") == Integer(2)

    # a rank-3 array: permutations of three axes, and it is not a matrix
    cube = Array([[[1, 2], [3, 4]], [[5, 6], [7, 8]]])
    assert Document(cube).apply("/", "permutedims", args=["(2, 0, 1)"]).shape == (2, 2, 2)
    assert "rank-2" in Document(cube).handle({"action": "apply", "path": "/", "op": "tomatrix"})["error"]

    # the parameters are declared, so a front end can ask for them
    ops = {o["name"]: o for o in Document(a).snapshot()["ops"]}
    assert [p["name"] for p in ops["permutedims"]["params"]] == ["permutation, e.g. (1, 0)"]
    assert ops["contraction"]["kinds"] == ["array"] and ops["contraction"]["doc"]
    assert ops["transpose"]["params"] == []

    # and applying without them says what is missing, rather than failing obscurely
    assert "needs permutation" in Document(a).handle({"action": "apply", "path": "/", "op": "permutedims"})["error"]
    assert "not an index" in Document(a).handle(
        {"action": "apply", "path": "/", "op": "contraction", "args": ["(x, 1)"]})["error"]
    assert Document(a).handle(
        {"action": "apply", "path": "/", "op": "permutedims", "args": ["(1,0)"]})["src"] == "[[1, 3], [2, 4]]"


def test_array_symbols_have_the_array_tools():
    """An ArraySymbol - and anything built from one - is an array to the editor,
    with the same tools, and its results stay component-implicit."""
    from sympy import Array
    from sympy.tensor.array.expressions import (ArrayContraction, ArrayDiagonal, ArraySymbol,
                                                PermuteDims, Reshape)

    S = ArraySymbol("S", (2, 3, 4))
    doc = Document(S)
    assert doc.snapshot()["nodes"]["/"]["kind"] == "array"       # not "scalar"
    offered = {o["name"] for o in doc.snapshot()["ops"] if o["kinds"] == ["array"]}
    assert {"permutedims", "contraction", "diagonal", "reshape_array",
            "tomatrix", "array_rank", "array_as_explicit"} <= offered

    assert Document(S).apply("/", "permutedims", args=["(1, 0, 2)"]) == PermuteDims(S, (1, 0, 2))
    assert Document(ArraySymbol("T", (3, 3))).apply("/", "contraction", args=["(0, 1)"]) \
        == ArrayContraction(ArraySymbol("T", (3, 3)), (0, 1))
    assert Document(ArraySymbol("T", (3, 3, 2))).apply("/", "diagonal", args=["(0, 1)"]) \
        == ArrayDiagonal(ArraySymbol("T", (3, 3, 2)), (0, 1))
    assert Document(S).apply("/", "array_rank") == Integer(3)

    # the result of a tool is an array too, so they chain
    doc = Document(S)
    doc.apply("/", "permutedims", args=["(1, 0, 2)"])
    assert doc.snapshot()["nodes"]["/"]["kind"] == "array"

    # component-explicit when asked for
    assert Document(ArraySymbol("S", (2, 2))).apply("/", "array_as_explicit") \
        == ArraySymbol("S", (2, 2)).as_explicit()


def test_reshape_and_derive_by_array():
    from sympy import Array
    from sympy.tensor.array.expressions import ArraySymbol, Reshape

    # reshape: a matrix stays a matrix on two dimensions, becomes an array otherwise
    assert Document(Matrix([[1, 2], [3, 4]])).apply("/", "reshape", args=["(1, 4)"]) == Matrix([[1, 2, 3, 4]])
    assert Document(Matrix([[1, 2], [3, 4]])).apply("/", "reshape", args=["(4,)"]) == Array([1, 2, 3, 4])
    assert Document(Array([[1, 2], [3, 4]])).apply("/", "reshape_array", args=["(4, 1)"]) == Array([[1], [2], [3], [4]])
    assert Document(MatrixSymbol("A", 2, 3)).apply("/", "reshape", args=["(3, 2)"]) \
        == Reshape(ArraySymbol("A", (2, 3)), (3, 2))
    assert "Cannot reshape" in Document(Matrix([[1, 2], [3, 4]])).handle(
        {"action": "apply", "path": "/", "op": "reshape", "args": ["(3, 3)"]})["error"]

    # derive by array, for an expression, a matrix and an array - explicit
    assert Document(x ** 2 * y).apply("/", "derive_by_array", args=["[x, y]"]) == Array([2 * x * y, x ** 2])
    assert Document(Matrix([[x, x * y]])).apply("/", "derive_by_array", args=["x"]) == Array([[Integer(1), y]])
    assert Document(Array([x * y, x])).apply("/", "derive_by_array", args=["[x, y]"]) == Array([[y, Integer(1)], [x, Integer(0)]])

    # ... and component-implicit: differentiating a matrix symbol gives an array expression
    A = MatrixSymbol("A", 2, 2)
    result = Document(A).apply("/", "derive_by_array", args=["A"])
    assert Document(result).snapshot()["nodes"]["/"]["kind"] == "array"

    ops = {o["name"]: o for o in Document(Matrix([[1, 2], [3, 4]])).snapshot()["ops"]}
    assert [p["name"] for p in ops["reshape"]["params"]] == ["new shape, e.g. (3, 2)"]
    assert "needs by" in Document(x).handle({"action": "apply", "path": "/", "op": "derive_by_array"})["error"]


def test_wrap_puts_a_node_inside_a_function():
    """wrap is the inverse of unwrap: the node becomes the argument."""
    z = Symbol("z")
    doc = Document(x + y)
    assert str(doc.wrap("/0", "cos")) == "y + cos(x)"           # SymPy's own ordering
    assert doc.expr == y + cos(x)
    doc.undo()
    assert doc.expr == x + y

    # a function of the expression, an undefined one, one with more arguments
    assert str(Document(x * y).wrap("/", "exp")) == "exp(x*y)"
    assert str(Document(x).wrap("/", "f")) == "f(x)"             # unknown name: an undefined Function
    assert str(Document(x * y).wrap("/", "Integral", "x")) == "Integral(x*y, x)"
    assert str(Document(x * y).wrap("/", "Integral(x)")) == "Integral(x*y, x)"   # or written as a call
    assert str(Document(x).wrap("/", "Sum", "(x, 1, 10)")) == "Sum(x, (x, 1, 10))"

    # wrapping builds, it does not compute
    assert str(Document(Integer(4)).wrap("/", "sqrt")) == "sqrt(4)"
    assert str(Document(Integer(0)).wrap("/", "cos")) == "cos(0)"

    # a range of a sum, like the other range operations
    doc = Document(x + y + z)
    doc.wrap("/", "cos", children=[0, 1])
    assert doc.expr == cos(x + y) + z or doc.expr == cos(y + z) + x   # whichever two SymPy orders first

    # wrap then unwrap is the identity
    doc = Document(x + y)
    doc.wrap("/1", "sin")
    doc.unwrap(next(k for k, v in doc.snapshot()["nodes"].items() if v["src"].startswith("sin")))
    assert doc.expr == x + y

    # messages and errors
    assert Document(x).handle({"action": "wrap", "path": "/", "func": "tan"})["src"] == "tan(x)"
    assert "No function" in Document(x).handle({"action": "wrap", "path": "/", "func": ""})["error"]
    assert "Not a function name" in Document(x).handle({"action": "wrap", "path": "/", "func": "2 +"})["error"]
    assert "Cannot wrap" in Document(x).handle({"action": "wrap", "path": "/", "func": "atan2"})["error"]


def test_unwrap_offers_the_arguments_to_keep():
    """A node with more than one argument that could stand alone publishes the
    choice, so the front end asks instead of keeping the first silently."""
    z = Symbol("z")

    def choices(expr, path="/"):
        info = Document(expr).snapshot()["nodes"][path]
        return [(c["key"], c["src"]) for c in info.get("keep_choices", [])]

    assert choices(x ** 2) == [(0, "x"), (1, "2")]          # the base or the exponent
    assert choices(atan2(y, x)) == [(0, "y"), (1, "x")]
    assert choices(log(x, 2)) == [("n", "log(x)"), ("d", "log(2)")]   # printed as a quotient
    assert choices(x + y + z) == [(0, "x"), (1, "y"), (2, "z")]
    assert choices(x / y) == [("n", "x"), ("d", "y")]        # the parts, not the arguments
    assert choices(cos(x)) == []                       # one argument: no question
    assert choices(Integral(cos(x), (x, 0, 1))) == []   # the limits are a Tuple
    assert choices(Symbol("q")) == []

    # every published choice is one unwrap accepts, and it keeps what it says
    for expr in (x ** 2, x / y, x + y + z):
        for key, src in choices(expr):
            doc = Document(expr)
            assert doc.handle({"action": "unwrap", "path": "/", "keep": key})["src"] == src

def test_insert_splices_between_both_neighbours():
    from sympy import MatrixSymbol, cos, symbols
    x, y, z, t = symbols("x y z t")
    def ins(expr, src, path="/", **kw):
        d = Document(expr); d.handle(dict({"action": "insert", "path": path, "index": 1, "src": src}, **kw)); return d.expr
    assert ins(x * z, "+y+", left=0, right=1) == x + y + z                     # a product split at the caret
    assert ins(x + z, "+y+", left=0, right=1) == x + y + z
    # An operator at either end takes the neighbour on that side, whichever
    # side the caret is attached to (a dangling "*" used to be a parse error).
    assert ins(x * z, "*y*", left=0, right=1) == x * y * z
    assert ins(x * z, "*y*", left=0, right=1, attach="right") == x * y * z
    assert ins(x + z, "*y*", left=0, right=1) == x * y * z
    assert ins(x * z, "y*", left=0, right=1) == x * y * z
    assert ins(x * z, "*y", left=0, right=1, attach="right") == x * y * z
    assert ins(x * z, "/y*", left=0, right=1) == x / y * z
    assert ins(x + z, "+y*", left=0, right=1) == x + y * z
    assert ins(cos(x + z), "*y*", path="/0", left=0, right=1) == cos(x * y * z)
    assert ins(cos(x + z), "y", path="/0", left=0, right=1) == cos(x * y + z)   # no operator: only the attached side
    assert ins(x + z, "cos(t)", left=0, right=1, attach="left") == x * cos(t) + z
    assert ins(x + z, "cos(t)", left=0, right=1, attach="right") == x + cos(t) * z
    assert ins(x + z, "cos(t)", left=0, right=1) == x * cos(t) + z             # on the operator: the left neighbour
    assert ins(x * y * z, "^2", left=1, right=2, attach="left") == x * y**2 * z  # ^ binds to the factor, not the half
    A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
    assert ins(A * B, "+ B*A", left=1, attach="left") == A * B + B * A          # + at the sum level: the whole product
    assert ins(A * B, "C", left=0, right=1, attach="left") == A * MatrixSymbol("C", 2, 2) * B


def test_isolate():
    from sympy import cos, symbols
    x, y, t = symbols("x y t")
    doc = Document(x * cos(t) + y)
    c = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "cos(t)")
    doc.handle({"action": "isolate", "path": c})
    assert doc.expr == cos(t)
    doc.undo()
    assert doc.expr == x * cos(t) + y
    doc.handle({"action": "isolate", "path": "/", "children": [0, 1]})   # a range
    assert doc.expr == doc.expr and len(doc.expr.args) == 2


def test_function_signatures_for_prompts():
    from sympy import Matrix, symbols, sin, cos
    from sympy_editor.document import function_signature
    x, y = symbols("x y")
    solve = function_signature("solve")
    assert solve["params"][0]["kind"] == "symbol" and solve["params"][0]["optional"] is False
    assert all(p["optional"] for p in function_signature("factor")["params"])          # applies without a prompt
    series = function_signature("series")
    assert [p["name"] for p in series["params"]][:2] == ["variable", "x0"] and series["params"][1]["default"] == "0"
    assert function_signature(".T", Matrix([[x]]))["callable"] is False
    doc = Document(sin(x) * cos(y))
    snap = doc.handle({"action": "signature", "path": "/", "name": "solve"})
    assert snap["signature"]["name"] == "solve" and snap["nodes"]["/"]["free"] == ["x", "y"]
    assert "Unknown" in doc.handle({"action": "signature", "path": "/", "name": "nope"})["error"]
    doc.call("/", "solve(y)")
    assert all(not e.has(y) for e in doc.expr)                                            # solved for y


def test_names_versus_sympy_functions():
    from sympy import E, Symbol, gamma, sin, symbols
    x = symbols("x")
    doc = Document(x + 1)
    # an undeclared name that SymPy knows is SymPy's, with a hint
    from sympy import I
    snap = doc.handle({"action": "set", "src": "sin(x) + E + I"})
    assert doc.expr == sin(x) + E + I
    assert "E, I" in snap["note"] and "backticks" in snap["note"]
    # backticks make a variable of it, once
    snap = doc.handle({"action": "set", "src": "`sin` * x + `E`"})
    assert doc.expr == Symbol("sin") * x + Symbol("E") and "note" not in snap
    # a declared symbol wins over SymPy's name from then on
    doc.declare("gamma", "Symbol", assumptions=["positive"])
    snap = doc.handle({"action": "set", "src": "gamma * x"})
    assert doc.expr == Symbol("gamma", positive=True) * x and "note" not in snap
    # a name in the expression wins too, and calls are always functions
    snap = doc.handle({"action": "set", "src": "sin(gamma)"})
    assert doc.expr == sin(Symbol("gamma", positive=True)) and "note" not in snap
    assert "not" not in (doc.handle({"action": "set", "src": "x + 2"}).get("note") or "")


def test_preview_renders_without_committing():
    from sympy import symbols
    x, y = symbols("x y")
    doc = Document(x + 1)
    snap = doc.handle({"action": "preview", "src": "x*y"})
    assert snap["preview"] is True and snap["src"] == "x*y" and snap["error"] is None
    assert "/" in snap["nodes"] and snap["nodes"]["/"]["src"] == "x*y"
    assert doc.expr == x + 1 and not doc.can_undo                   # nothing committed
    bad = doc.handle({"action": "preview", "src": "x*("})
    assert bad["preview"] is True and "Could not parse" in bad["error"] and bad["src"] == "x + 1"
    noted = doc.handle({"action": "preview", "src": "E*x"})
    assert "E" in noted["note"] and noted["error"] is None


def test_export_and_restore_history():
    from sympy import symbols, MatrixSymbol
    x = symbols("x")
    doc = Document(x, symbols=[MatrixSymbol("M", 2, 2)])
    doc.set("x + 1")
    doc.set("x**2")
    doc.undo()
    state = doc.export()
    assert state["index"] == 1 and len(state["history"]) == 3 and state["symbols"] == ["MatrixSymbol(Str('M'), Integer(2), Integer(2))"]
    snap = doc.handle({"action": "export"})
    assert snap["export"] == state and snap["src"] == "x + 1"
    doc2 = Document(None, **state)
    assert doc2.expr == x + 1 and doc2.can_undo and doc2.can_redo and doc2.declared["M"] == MatrixSymbol("M", 2, 2)
    doc2.redo()
    assert doc2.expr == x**2
    assert Document("x", history=[], index=None).expr == x           # an empty history: the expression
    assert Document(None, history=["Symbol('y')"], index=7).expr == symbols("y")   # index clamped
    assert Document(x + 1).snapshot()["declared"] == []


def test_goto_history_step_and_labels():
    from sympy import symbols
    x = symbols("x")
    doc = Document(x)
    doc.set("x + 1")
    doc.set("x**2")
    labels = doc.history_labels()
    assert labels["labels"] == ["x", "x + 1", "x**2"] and labels["index"] == 2 and len(labels["steps"]) == 3
    assert doc.goto(0) == x and doc.can_redo and not doc.can_undo
    snap = doc.handle({"action": "goto", "index": 1})
    assert snap["src"] == "x + 1" and snap["can_undo"] and snap["can_redo"]
    assert "No history step" in doc.handle({"action": "goto", "index": 5})["error"]
    hist = doc.handle({"action": "export"})["history"]
    assert hist["labels"] == ["x", "x + 1", "x**2"] and hist["index"] == 1
    assert [s["nodes"]["/"]["src"] for s in hist["steps"]] == ["x", "x + 1", "x**2"]
    assert "\\htmlData{path=/}" in hist["steps"][2]["latex"] and hist["steps"][2]["nodes"]["/1"]["type"] == "Integer"
    assert doc.history_labels()["steps"][0] is doc.history_labels()["steps"][0]     # cached per expression
    doc.set("x + 3")                      # a new edit from the middle drops the later steps
    assert doc.history_labels()["labels"] == ["x", "x + 1", "x + 3"]


def test_rational_parts_are_editable():
    from sympy import Rational, symbols
    x, y = symbols("x y")
    doc = Document(x - Rational(1, 2))
    nodes = doc.snapshot()["nodes"]
    assert nodes["/0/n"]["src"] == "1" and nodes["/0/d"]["src"] == "2" and not nodes["/0/n"]["insertable"]
    assert doc.handle({"action": "replace", "path": "/0/d", "src": "3"})["src"] == "x - 1/3"
    assert doc.handle({"action": "replace", "path": "/0/n", "src": "y"})["src"] == "x - y/3"   # the shown 1 is replaced; the sign stays
    doc.set(Rational(1, 2))
    assert doc.handle({"action": "extend", "path": "/n", "side": "after", "src": "+ 2"})["src"] == "3/2"
    assert doc.handle({"action": "delete", "path": "/n"})["src"] == "1/2"            # a removed numerator leaves 1
    assert doc.handle({"action": "delete", "path": "/d"})["src"] == "1"
    doc.set(Rational(3, 2))
    assert "nothing inside" in doc.handle({"action": "unwrap", "path": "/n"})["error"]
    assert doc.handle({"action": "unwrap", "path": "/", "keep": "n"})["src"] == "3"


def test_unevaluated_transformations_and_calls():
    from sympy import (Derivative, Determinant, Integral, Inverse, Matrix, MatrixSymbol, Rational, Subs, Transpose,
                       Trace, sin, symbols, sympify)
    x, y = symbols("x y")
    M = Matrix([[1, 2], [3, 4]])
    doc = Document(M)
    assert next(o for o in doc.snapshot()["ops"] if o["name"] == "determinant")["lazy"]
    assert not next(o for o in doc.snapshot()["ops"] if o["name"] == "simplify")["lazy"]
    assert doc.handle({"action": "apply", "path": "/", "op": "determinant", "lazy": True})["src"].startswith("Determinant(Matrix(")
    assert isinstance(doc.expr, Determinant)
    assert doc.handle({"action": "apply", "path": "/", "op": "doit"})["src"] == "-2"     # evaluated later
    assert doc.export()["labels"][1] == "Transform: Determinant (unevaluated)"
    doc.set(M)
    assert doc.handle({"action": "apply", "path": "/", "op": "determinant"})["src"] == "-2"
    for op, cls in (("inverse", Inverse), ("transpose", Transpose), ("trace", Trace)):
        doc.set(M)
        assert isinstance(doc.apply("/", op, lazy=True), cls)
    A = MatrixSymbol("A", 2, 2)
    doc = Document(A)
    assert doc.call("/", "det()", lazy=True) == Determinant(A)
    doc.set(A)
    assert doc.call("/", ".T", lazy=True) == Transpose(A)
    doc = Document(x**2 * sin(y))
    assert doc.call("/", "diff(x)", lazy=True) == Derivative(x**2 * sin(y), x)
    assert doc.call("/", "doit()") == 2 * x * sin(y)
    doc.set(x**2)
    assert doc.call("/", "integrate(x)", lazy=True) == Integral(x**2, x)
    doc.set(x**2)
    assert doc.call("/", "integrate(x)") == x**3 / 3
    doc.set(x)
    assert doc.call("/", "subs(x, 1)", lazy=True) == Subs(x, x, 1)
    doc.set(sympify(0))
    r = doc.call("/", "sin", lazy=True)
    assert isinstance(r, sin) and r.args == (0,) and doc.call("/", "sin") == 0       # sin(0) stays sin(0)
    doc.set(sympify(4))
    r = doc.call("/", "sqrt", lazy=True)
    assert r.is_Pow and r.args == (4, Rational(1, 2))
    doc.set(sympify(4))
    assert doc.call("/", "sqrt") == 2
    doc.set((x + 1) ** 2)
    snap = doc.handle({"action": "call", "path": "/", "func": "expand", "lazy": True})    # no unevaluated form: applied, and said so
    assert snap["src"] == "x**2 + 2*x + 1" and "no unevaluated form" in snap["note"]
    assert doc.export()["labels"][-1] == "SymPy: expand (unevaluated)"
    snap = doc.handle({"action": "apply", "path": "/", "op": "factor", "lazy": True})
    assert snap["src"] == "(x + 1)**2" and "no unevaluated form" in snap["note"]


def test_python_script_rebuilds_the_history():
    from sympy import Function, MatrixSymbol, Rational, Symbol, sin, symbols
    x, y = symbols("x y")
    p = Symbol("p", positive=True)
    f = Function("f")
    doc = Document(x + sin(y) + Rational(1, 2))
    doc.handle({"action": "replace", "path": "/2", "src": "cos(y)"})
    doc.handle({"action": "call", "path": "/", "func": "diff(y)"})
    doc.set(f(p) ** 2 + 3)                                          # p is positive here
    doc.handle({"action": "set", "src": "3"})
    doc.undo()
    script = doc.python_script("My session")
    assert script.startswith('"""My session') and "from sympy import *" in script
    assert "p = Symbol('p', positive=True)" in script and "f = Function('f')" in script and "x = Symbol('x')" in script
    assert "Rational(1, 2)" in script and "1/2" not in script.replace("Rational(1, 2)", "")
    assert "# Step 1: start\n" in script and "# Step 2: Edit: sin(y) → cos(y)\n" in script
    assert "\n# Step 4: " in script and "  (current)\nexpr = f(p)**2 + 3\n" in script and "expr = Integer(3)" in script
    ns = {}
    exec(script, ns)                                                 # runs with SymPy alone
    assert ns["steps"] == [Document(s).expr for s in doc.export()["history"]]
    assert ns["steps"][3] == f(p) ** 2 + 3 and ns["steps"][0] == x + sin(y) + Rational(1, 2) and ns["p"].is_positive
    assert doc.handle({"action": "script", "title": "T"})["script"].startswith('"""T')
    A = MatrixSymbol("A", 2, 2)
    doc = Document(A * A.T + 2 * A)
    ns = {}
    exec(doc.python_script(), ns)
    assert ns["steps"][0] == A * A.T + 2 * A and "MatrixSymbol('A', 2, 2)" in doc.python_script()


def test_history_records_what_produced_each_step():
    from sympy import symbols, sin
    x, y = symbols("x y")
    doc = Document(x + sin(y))
    doc.handle({"action": "replace", "path": "/1", "src": "cos(y)"})
    doc.handle({"action": "apply", "path": "/", "op": "expand"})            # no change: not a step
    doc.handle({"action": "call", "path": "/", "func": "diff(y)"})
    doc.handle({"action": "set", "src": "x**2"})
    doc.handle({"action": "insert", "path": "/", "index": 0, "src": "+ 1"})
    labels = doc.export()["labels"]
    assert labels[0] is None
    assert labels[1] == "Edit: sin(y) → cos(y)" and labels[2] == "Transform: Expand"      # expand did commit (same value)
    assert labels[3] == "SymPy: diff(y)" and labels[4] == "Type the whole expression: x**2"
    assert labels[5].startswith('Insert "+ 1" in')
    assert doc.history_labels()["actions"] == labels
    # undo then a new edit drops the later labels with the later steps
    doc.undo(); doc.undo()
    doc.handle({"action": "delete", "path": "/0"})
    assert doc.export()["labels"][-1].startswith("Delete ") and len(doc.export()["labels"]) == len(doc.export()["history"])
    # restored with a session
    state = doc.export()
    doc2 = Document(None, **state)
    assert doc2.history_labels()["actions"] == state["labels"]
    assert Document(None, history=state["history"], index=state["index"]).history_labels()["actions"] == [None] * len(state["history"])
    # edits from Python carry no label
    doc.replace("/", "y")
    assert doc.export()["labels"][-1] is None


def test_the_operator_between_two_arguments_can_be_changed():
    from sympy import And, Eq, Lt, MatrixSymbol, Or, symbols, sympify
    x, y, z = symbols("x y z")

    def op(expr, o, left=0, right=1, path="/", **kw):
        d = Document(expr)
        snap = d.handle(dict({"action": "operator", "path": path, "left": left, "right": right, "op": o}, **kw))
        assert not snap["error"], snap["error"]
        return d.expr

    assert op(x + y, "*") == x * y                       # "+" turned into "*"
    assert op(x + y, "") == x * y                        # deleted: juxtaposition multiplies
    assert op(x + y, "-") == x - y
    assert op(x - y, "*") == x * y                       # the "-" of a negative term is the operator
    assert op(x - y, "+") == x + y
    assert op(x - y, "") == x * y
    assert op(x + y + z, "*", left=0, right=1) == x * y + z   # only the pair binds
    assert op(x * y * z, "+", left=1, right=2) == x * y + z   # "+" splits the product at the operator
    assert op(x * y * z, "-", left=0, right=1) == x - y * z
    assert op(x * y * z, "/", left=1, right=2) == x * y / z
    assert op(x + y, "^") == x ** y
    assert op(x + y, "=") == Eq(x, y)
    assert op(Eq(x, y), "<") == Lt(x, y)
    assert op(Eq(x, y), "+") == x + y
    assert op(x ** y, "*") == x * y
    assert op(And(x > 0, y > 0), "|") == Or(x > 0, y > 0)
    A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
    assert op(A + B, "*") == A * B
    assert op(A * B, "+") == A + B
    # No-ops do not touch the history; unevaluated keeps 2*3 unevaluated.
    d = Document(x + y)
    d.handle({"action": "operator", "path": "/", "left": 0, "right": 1, "op": "+"})
    assert not d.can_undo
    assert op(sympify("2 + 3", evaluate=False), "*") == 6
    assert str(op(sympify("2 + 3", evaluate=False), "*", lazy=True)) == "2*3"
    # "=" needs the two arguments to be the whole node.
    snap = Document(x + y + z).handle({"action": "operator", "path": "/", "left": 0, "right": 1, "op": "="})
    assert "two sides" in snap["error"]
    # A lone operator typed at a caret between two arguments does the same.
    d = Document(x * z)
    d.handle({"action": "insert", "path": "/", "index": 1, "src": "+", "left": 0, "right": 1})
    assert d.expr == x + z


def test_methods_action_lists_the_type_methods():
    from sympy import Matrix
    d = Document(Matrix([[1, 2], [3, 4]]))
    snap = d.handle({"action": "methods", "path": "/"})
    entries = snap["methods"]["ImmutableDenseMatrix"]
    names = [e["name"] for e in entries]
    assert "det" in names and "transpose" in names and "rref" in names
    assert next(e for e in entries if e["name"] == "T")["property"] is True
    assert next(e for e in entries if e["name"] == "det")["property"] is False
    # no assumption queries, dunders or structural plumbing
    assert all(not n.startswith("is_") and not n.startswith("_") for n in names)
    assert "args" not in names and "sort_key" not in names and "func" not in names
    # a picked method goes through the existing call flow
    d.handle({"action": "call", "path": "/", "func": ".det()"})
    assert d.expr == -2
    # scalar expressions have their own list
    x, y = symbols("x y")
    snap = Document(x**2 + y).handle({"action": "methods", "path": "/"})
    names = [e["name"] for e in snap["methods"]["Add"]]
    assert "diff" in names and "simplify" in names and "as_poly" in names
    # snapshots piggyback the lists of the types they introduce, once each
    d2 = Document(x**2 + y)
    first = d2.snapshot()
    assert "Add" in first["methods"] and "Symbol" in first["methods"] and "Pow" in first["methods"]
    assert d2.snapshot()["methods"] == {}                     # nothing new the second time


def test_a_lambda_is_applied_to_its_arguments():
    """Lambda is itself a function.  `__call__` is a dunder, so it appears in
    no listing of methods and there was no way to evaluate one at a point."""
    from sympy import Lambda
    from sympy_editor.document import function_signature, type_methods
    x, y = symbols("x y")
    d = Document(Lambda(x, x**2))
    assert d.call("/", "(3)") == 9
    d = Document(Lambda(x, x**2))
    assert d.call("/", ".__call__(y + 1)") == (y + 1) ** 2
    # inside a larger expression, on the node that is the Lambda
    d = Document(Lambda(x, x**2) + y)
    i = [isinstance(a, Lambda) for a in d.expr.args].index(True)
    d.handle({"action": "call", "path": f"/{i}", "func": "(2)"})
    assert d.expr == y + 4
    # the methods menu offers it, first and under a readable label
    entry = type_methods(Lambda)[0]
    assert entry["name"] == "__call__" and entry["label"] == "( ) apply"
    assert [e["name"] for e in type_methods((x + 1).func) if e["name"].startswith("_")] == []
    # ... and asks for the arguments rather than applying it to none
    sig = function_signature(".__call__", Lambda(x, x**2))
    assert [p["name"] for p in sig["params"]] == ["arguments"]
    assert sig["params"][0]["optional"] is False
    # anything else says so plainly
    with pytest.raises(ValueError, match="not a function"):
        Document(x + 1).call("/", "(3)")
    with pytest.raises(ValueError, match="Not a function call"):
        Document(x + 1).call("/", "3 +")


def test_wrapping_in_a_container_builds_the_container():
    """Matrix(x) is an error in SymPy - a matrix wants its contents, not an
    argument - so wrapping an expression in Matrix did nothing at all.  It
    builds the 1x1 matrix holding it."""
    from sympy import Array, FiniteSet, Matrix, Tuple, cos

    x, y = symbols("x y")
    d = Document(x + y)
    d.wrap("/", "Matrix")
    assert d.expr == Matrix([[x + y]]) and d.expr.shape == (1, 1)
    d = Document(x + y)
    d.handle({"action": "wrap", "path": "/", "func": "ImmutableMatrix"})
    assert d.expr == Matrix([[x + y]])
    # the other containers, and the ordinary functions, are untouched
    d = Document(x + y)
    d.wrap("/", "FiniteSet")
    assert d.expr == FiniteSet(x + y)
    d = Document(x + y)
    d.wrap("/", "Tuple")
    assert d.expr == Tuple(x + y)
    d = Document(x + y)
    d.wrap("/", "cos")
    assert d.expr == cos(x + y)
    # and what really cannot be wrapped still says so
    with pytest.raises(ValueError, match="Cannot wrap in Integral"):
        Document(x + y).wrap("/", "Integral")


def test_applying_a_container_takes_the_expression_as_its_contents():
    """The function box goes through `call`, not `wrap`: typing Matrix there
    failed the same way, since Matrix(x) is an error in SymPy."""
    from sympy import Array, FiniteSet, ImmutableMatrix, Matrix

    x, y = symbols("x y")
    d = Document(x + y)
    d.call("/", "Matrix")
    assert d.expr == Matrix([[x + y]])
    d = Document(x + y)
    d.handle({"action": "call", "path": "/", "func": "ImmutableMatrix"})
    assert d.expr == ImmutableMatrix([[x + y]])
    d = Document(x + y)
    d.call("/", "Matrix", lazy=True)                 # unevaluated asks for the same thing
    assert d.expr == Matrix([[x + y]])
    # ordinary functions are untouched, and a real error is still an error
    d = Document(x + y)
    d.call("/", "expand")
    assert d.expr == x + y
    with pytest.raises(ValueError, match="Unknown SymPy function"):
        Document(x + y).call("/", "no_such_function_at_all")

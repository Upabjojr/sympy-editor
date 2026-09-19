"""Expressions SymPy refuses to build: refused by a document that does not
allow them, kept as invalid nodes by one that does."""

import pytest
from sympy import Add, MatrixSymbol, Pow, S, Symbol, latex, sin, srepr, symbols

from sympy_editor import Document
from sympy_editor.invalid import Invalid, InvalidExpr, first_problem, invalid, tolerate
from sympy_editor.printer import annotate, annotate_str

x, y = symbols("x y")
A = MatrixSymbol("A", 3, 3)
B = MatrixSymbol("B", 2, 2)
C = MatrixSymbol("C", 3, 2)
D = MatrixSymbol("D", 3, 3)


def test_printers():
    node = invalid("MatMul")(A, B)
    assert str(node) == "Invalid(MatMul, A, B)"
    assert latex(node) == r"\textcolor{red}{\mathtt{MatMul}}\left[A,\ B\right]"
    text, spans = annotate_str(node)
    assert text == str(node) and spans["/0"] == (16, 17) and spans["/1"] == (19, 20)
    tex, nodes = annotate(Add(x, invalid("sin")(x, y), evaluate=False))
    assert nodes[(1,)] == invalid("sin")(x, y) and nodes[(1, 1)] == y
    assert r"\htmlData{path=/1/1}{y}" in tex


def test_srepr_and_str_read_back():
    node = invalid("MatMul")(A, B)
    doc = Document(x, symbols=[A, B], allow_invalid=True)
    assert doc._coerce(srepr(node)) == node
    assert doc.parse(str(node)) == node
    assert Invalid("MatMul", A, D) == A*D                     # valid by now: the expression itself


def test_problems_the_constructors_let_through():
    assert first_problem(Add(2, B)) is not None               # 2 + B: the operator refuses it
    assert first_problem(Pow(C*B, S.Half, evaluate=False)) is not None
    assert first_problem(sin(x) + A[0, 0]) is None
    assert first_problem(A*D + A) is None
    kept = tolerate(Add(x, Pow(C*B, S.Half, evaluate=False), evaluate=False))
    assert isinstance(kept, Add) and invalid("Pow")(C*B, S.Half) in kept.args


@pytest.mark.parametrize("message", [
    {"action": "set", "src": "A*B"},
    {"action": "set", "src": "sin(x, y)"},
    {"action": "replace", "path": "/1", "src": "B"},
    {"action": "insert", "path": "/", "index": 1, "src": "B"},
    {"action": "call", "path": "/", "func": "sqrt", "lazy": True},
    {"action": "wrap", "path": "/", "func": "Inverse"},
])
def test_refused_when_not_allowed(message):
    doc = Document(A*C, symbols=[B])
    snap = doc.handle(message)
    assert snap["error"] and doc.expr == A*C and not doc.can_undo


def test_an_operator_between_a_scalar_and_a_matrix_is_refused():
    # Add(2, B) builds without an error: it used to be committed, and then
    # neither transformed nor read back from a saved session
    doc = Document(B**2)
    assert doc.handle({"action": "operator", "path": "/", "left": 0, "right": 1, "op": "+"})["error"]
    assert doc.expr == B**2


def test_kept_when_allowed_and_healed_by_an_edit():
    doc = Document(A*C, symbols=[B, D], allow_invalid=True)
    snap = doc.handle({"action": "replace", "path": "/1", "src": "B"})
    assert snap["error"] is None and snap["allow_invalid"] is True
    assert doc.expr == invalid("MatMul")(A, B) and isinstance(doc.expr, InvalidExpr)
    assert snap["nodes"]["/"]["invalid"] == "MatMul" and snap["nodes"]["/1"]["src"] == "B"
    assert snap["src"] == "Invalid(MatMul, A, B)"
    doc.handle({"action": "replace", "path": "/1", "src": "D"})
    assert doc.expr == A*D


@pytest.mark.parametrize("src, expected", [
    ("A*B", invalid("MatMul")(A, B)),
    ("A*B + D", invalid("MatAdd")(invalid("MatMul")(A, B), D)),
    ("A - B", invalid("MatAdd")(A, -B)),
    ("sin(x, y)", invalid("sin")(x, y)),
    ("x + sin(x, y)", x + invalid("sin")(x, y)),
])
def test_typed_invalid_input(src, expected):
    doc = Document(x, symbols=[A, B, D], allow_invalid=True)
    assert doc.handle({"action": "set", "src": src})["error"] is None
    assert doc.expr == expected
    assert doc.handle({"action": "preview", "src": src})["error"] is None


def test_input_that_does_not_read_is_still_an_error():
    doc = Document(x, allow_invalid=True)
    assert doc.handle({"action": "set", "src": "x +"})["error"]
    assert doc.handle({"action": "set", "src": "x.nosuchmethod()"})["error"]
    assert doc.expr == x


def test_the_switch_and_the_session():
    doc = Document(A*C, symbols=[B])
    assert doc.handle({"action": "settings", "allow_invalid": True})["allow_invalid"] is True
    doc.handle({"action": "replace", "path": "/1", "src": "B"})
    state = doc.export()
    assert state["allow_invalid"] is True
    again = Document(0, **state)
    assert again.expr == invalid("MatMul")(A, B) and again.allow_invalid
    doc.handle({"action": "settings", "allow_invalid": False})
    assert doc.handle({"action": "insert", "path": "/", "index": 2, "src": "B"})["error"]
    assert doc.handle({"action": "undo"})["error"] is None and doc.expr == A*C
    script = doc.python_script()
    assert "from sympy_editor.invalid import Invalid" in script
    namespace = {}
    exec(script, namespace)
    assert namespace["steps"][1] == invalid("MatMul")(A, B)


def test_a_session_saved_with_a_step_sympy_refuses_opens():
    # saved before invalid expressions were checked: the text does not read
    # back as it is, and the session could not be opened at all
    saved = srepr(Pow(C*B, S.Half, evaluate=False))
    doc = Document(0, history=[srepr(C*B), saved])
    assert doc.expr == invalid("Pow")(C*B, S.Half)
    assert doc.handle({"action": "export"})["error"] is None
    assert doc.handle({"action": "undo"})["error"] is None and doc.expr == C*B


def test_symbols_named_like_the_helper_are_not_shadowed():
    doc = Document(Symbol("Invalid") + x)
    assert doc.handle({"action": "replace", "path": "/1", "src": "y"})["error"] is None


def test_a_named_object_is_not_kept_invalid():
    doc = Document(A*D, allow_invalid=True)
    assert doc.handle({"action": "replace", "path": "/0/1", "src": "x + A"})["error"]
    assert doc.expr == A*D


def test_the_expression_given_is_kept():
    # a document re-created from its last state after an interruption
    doc = Document(srepr(invalid("MatMul")(A, B)))
    assert doc.expr == invalid("MatMul")(A, B)
    assert Document(Add(2, B)).expr == invalid("Add")(2, B)

"""Matrices, matrix expressions, block matrices and N-dimensional arrays."""
import pytest
from sympy import (
    Array, BlockMatrix, Determinant, Matrix, MatrixSymbol, SparseMatrix, Trace, eye, latex,
    sin, symbols, sympify,
)
from sympy.tensor.array import ImmutableSparseNDimArray, derive_by_array, tensorproduct

from sympy_editor import Document, annotate, get_at, strip_annotations

x, y, z, t = symbols("x y z t")
A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
M = Matrix([[x, y], [z, t]])

CASES = [
    M,
    SparseMatrix([[x, 0], [0, y]]),
    A * B + 2 * A.T - A.I,
    (A + B) * M,
    BlockMatrix([[A, B], [B, A]]),
    Determinant(M) + Trace(A),
    M.T * M + x * eye(2),
    Array([[x, y], [z, t]]),
    Array([[[x, 1], [y, 2]], [[z, 3], [t, 4]]]),
    ImmutableSparseNDimArray({0: x, 3: y}, (2, 2)),
    tensorproduct(Array([x, y]), Array([1, z])),
    derive_by_array(x**2 * y, [x, y]),
]


@pytest.mark.parametrize("expr", CASES, ids=lambda e: type(sympify(e)).__name__)
def test_special_types_annotate(expr):
    expr = sympify(expr)
    tex, nodes = annotate(expr)
    assert strip_annotations(tex) == latex(expr)
    assert nodes[()] == expr
    for path, node in nodes.items():
        assert get_at(expr, path) == node


def _path_of(doc, src):
    return next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == src)


def test_edit_matrix_element():
    doc = Document(M)
    doc.replace(_path_of(doc, "y"), "y**2")
    assert doc.expr == Matrix([[x, y**2], [z, t]])
    doc.replace("/", "Matrix([[1, 2, 3]])")   # reshape by replacing the whole thing
    assert doc.expr.shape == (1, 3)


def test_edit_sparse_matrix_element():
    doc = Document(SparseMatrix([[x, 0], [0, y]]))
    doc.replace(_path_of(doc, "y"), "z")
    assert doc.expr == SparseMatrix([[x, 0], [0, z]])


def test_edit_matrix_expression():
    doc = Document(A * B + 2 * A.T)
    doc.replace(_path_of(doc, "B"), "A")
    assert doc.expr == A * A + 2 * A.T
    assert doc.namespace()["A"] == A                      # MatrixSymbols are in scope
    assert "B" not in doc.namespace()                     # ...only while they occur in the expression
    doc.replace("/", "A*A.T + A")
    assert doc.expr == A * A.T + A


def test_edit_block_matrix():
    doc = Document(BlockMatrix([[A, B], [B, A]]))
    doc.replace(_path_of(doc, "B"), "A")
    assert doc.expr == BlockMatrix([[A, A], [B, A]])
    assert isinstance(doc.expr, BlockMatrix)


def test_edit_ndim_arrays():
    doc = Document(Array([[x, y], [z, t]]))
    doc.replace(_path_of(doc, "y"), "sin(y)")
    assert doc.expr == Array([[x, sin(y)], [z, t]])
    doc = Document(Array([[[x, 1], [y, 2]], [[z, 3], [t, 4]]]))
    doc.replace(_path_of(doc, "3"), "w")
    assert doc.expr[1, 0, 1] == symbols("w")
    assert doc.expr.shape == (2, 2, 2)
    doc.apply(_path_of(doc, "x"), "negate")
    assert doc.expr[0, 0, 0] == -x


def test_ops_on_matrix_selection():
    doc = Document(Matrix([[(x + 1) ** 2, 0], [0, x]]))
    doc.apply(_path_of(doc, "(x + 1)**2"), "expand")
    assert doc.expr[0, 0] == x**2 + 2 * x + 1
    doc.apply("/", "doit")
    assert doc.expr[1, 1] == x

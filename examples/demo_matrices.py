"""Matrices, matrix expressions and N-dimensional arrays in sympy-editor.

    python examples/demo_matrices.py   # writes examples/demo_matrices.html

The page holds several independent editors (one per example); each is a
self-contained Pyodide-backed fragment from ``to_html(..., full_page=False)``.
"""

import html
from pathlib import Path

from sympy import Array, BlockMatrix, Determinant, Matrix, MatrixSymbol, Trace, eye, sin, cos, symbols
from sympy.tensor.array import derive_by_array, tensorproduct

from sympy_editor import to_html

x, y, z, t, theta = symbols("x y z t theta")
A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
R = Matrix([[cos(theta), -sin(theta)], [sin(theta), cos(theta)]])

EXAMPLES = [
    ("Dense matrix", "Click any entry and type to replace it; select an entry and pick an operation in a menu.",
     Matrix([[x, y], [z, t]])),
    ("Rotation matrix times a vector", "Select the whole product (click, then ↑ until the root) and pick 'Evaluate (doit)' in the Transform menu.",
     R * Matrix([x, y])),
    ("Matrix expressions (MatrixSymbol algebra)", "A and B are 2x2 MatrixSymbols; editing rebuilds the MatAdd/MatMul tree.",
     A * B + 2 * A.T - A.I),
    ("Block matrix", "Blocks are editable; the BlockMatrix is rebuilt from its rows.",
     BlockMatrix([[A, B], [B, A]])),
    ("Determinant and trace", "Select the determinant and pick 'Evaluate' in its Integral/Matrix-style type menu to compute it.",
     Determinant(Matrix([[x, y], [z, t]])) + Trace(A) + x * Determinant(eye(2))),
    ("2-D array (ImmutableDenseNDimArray)", "Arrays behave like matrices for editing.",
     Array([[x, y], [z, t]])),
    ("3-D array", "Nested rendering; every scalar entry is addressable.",
     Array([[[x, 1], [y, 2]], [[z, 3], [t, 4]]])),
    ("Tensor product", "tensorproduct([x, y], [1, z])",
     tensorproduct(Array([x, y]), Array([1, z]))),
    ("Gradient via derive_by_array", "derive_by_array(x**2*y + sin(z), [x, y, z])",
     derive_by_array(x**2 * y + sin(z), [x, y, z])),
]

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>sympy-editor: matrices and arrays</title>
<style>
  body { margin: 2rem auto; max-width: 60rem; font-family: system-ui, sans-serif; background: #fff; color: #1f2328; }
  @media (prefers-color-scheme: dark) { body { background: #1e1e1e; color: #e6e6e6; } }
  h1 { font-size: 1.4rem; } h2 { font-size: 1.05rem; margin: 2rem 0 .3rem; } p { margin: 0 0 .6rem; opacity: .8; }
  .sympy-editor-host > .sympy-editor { display: block; }
</style></head><body>
<h1>sympy-editor: matrices, matrix expressions and N-dim arrays</h1>
<p>Each block is an independent editor.  Edits run in your browser (Pyodide loads on the first edit).</p>
%s
</body></html>
"""

sections = []
for title, blurb, expr in EXAMPLES:
    sections.append(f"<h2>{html.escape(title)}</h2>\n<p>{html.escape(blurb)}</p>\n" + to_html(expr, full_page=False))

out = Path(__file__).with_name("demo_matrices.html")
out.write_text(PAGE % "\n".join(sections), encoding="utf-8")
print("Wrote", out)

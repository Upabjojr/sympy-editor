"""The export add-on's Python: every format, its options, what a printer
cannot translate, and what it refuses - through the document's message, as
the panel asks."""
import sys
from pathlib import Path

import pytest
from sympy import Eq, Integral, Matrix, besselj, cos, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_export import ADDON, FORMATS, export  # noqa: E402

x, y = symbols("x y")


def ask(doc, fmt, path="/", options=None, children=None):
    msg = {"action": "addon", "addon": "export", "method": "export", "path": path, "format": fmt}
    if options is not None:
        msg["options"] = options
    if children is not None:
        msg["children"] = children
    snap = doc.handle(msg)
    assert "query" in snap, snap.get("error")
    return snap["query"]["result"]


def code(res):
    assert res["error"] is None, res["error"]
    return res["files"][0]["code"]


def test_every_format_is_offered_with_its_options():
    keys = [f["key"] for f in FORMATS]
    assert keys == ["latex", "mathml", "python", "c", "fortran", "javascript", "octave", "julia", "rust", "function"]
    snap = Document(x, addons=[ADDON]).snapshot()
    client = [a for a in snap.get("addon_clients", []) if a["name"] == "export"] or None
    opts = ADDON.client_options()["formats"]
    assert [f["key"] for f in opts] == keys and all("fn" not in f for f in opts)
    assert client is None or client[0]["options"]["formats"] == opts


def test_the_whole_formula_a_node_and_a_range():
    doc = Document(sin(x) + cos(y) + x**2, addons=[ADDON])
    assert code(ask(doc, "c")) == "pow(x, 2) + sin(x) + cos(y)"
    terms = doc.snapshot()["nodes"]
    sin_path = next(p for p, n in terms.items() if n["src"] == "sin(x)")
    assert code(ask(doc, "latex", sin_path)) == r"\sin{\left(x \right)}"
    a = doc.expr.args
    res = ask(doc, "python", "/", children=[0, 1])        # a range: two of the three terms
    assert code(res) == code(export(a[0] + a[1], "python"))
    assert code(res) != code(export(doc.expr, "python"))
    assert not doc.can_undo                                # a query: nothing changed


def test_latex_and_mathml_options():
    doc = Document(x**2, addons=[ADDON])
    assert code(ask(doc, "latex")) == "x^{2}"
    assert code(ask(doc, "latex", options={"mode": "equation*"})) == "\\begin{equation*}x^{2}\\end{equation*}"
    pres = code(ask(doc, "mathml"))
    assert pres.startswith('<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">') and "<msup>" in pres
    content = code(ask(doc, "mathml", options={"printer": "content", "wrap": False}))
    assert content.startswith("<apply>") and "<power/>" in content
    assert ask(doc, "mathml")["files"][0]["name"] == "formula.mml"


def test_python_flavours():
    doc = Document(sin(x) / y, addons=[ADDON])
    assert code(ask(doc, "python")) == "import math\n\nmath.sin(x)/y"
    assert code(ask(doc, "python", options={"module": "numpy"})) == "import numpy\n\nnumpy.sin(x)/y"
    assert "mpmath.sin(x)" in code(ask(doc, "python", options={"module": "mpmath"}))
    src = code(ask(doc, "python", options={"module": "sympy"}))
    assert "x = Symbol('x')" in src and "e = sin(x)/y" in src


def test_c_fortran_and_the_rest():
    doc = Document(x**2 + sin(y), addons=[ADDON])
    assert code(ask(doc, "c", options={"standard": "C89"})) == "pow(x, 2) + sin(y)"
    assert code(ask(doc, "c", options={"assign": "r"})) == "r = pow(x, 2) + sin(y);"
    assert code(ask(doc, "fortran")) == "x**2 + sin(y)"
    fixed = code(ask(doc, "fortran", options={"source_format": "fixed", "standard": "77"}))
    assert fixed.startswith("      ")                    # column 7
    assert code(ask(doc, "javascript")) == "Math.pow(x, 2) + Math.sin(y)"
    assert code(ask(doc, "octave")) == "x.^2 + sin(y)"
    assert code(ask(doc, "julia")) == "x .^ 2 + sin(y)"
    assert code(ask(doc, "rust")) == "x.powi(2) + y.sin()"
    names = {fmt: ask(doc, fmt)["files"][0]["name"] for fmt in ("c", "fortran", "javascript", "octave", "julia", "rust")}
    assert names == {"c": "formula.c", "fortran": "formula.f90", "javascript": "formula.js",
                     "octave": "formula.m", "julia": "formula.jl", "rust": "formula.rs"}


def test_a_matrix_is_assigned_where_the_language_needs_it():
    doc = Document(Matrix([[x, y], [1, x * y]]), addons=[ADDON])
    assert code(ask(doc, "c")) == "M[0] = x;\nM[1] = y;\nM[2] = 1;\nM[3] = x*y;"
    assert code(ask(doc, "javascript", options={"assign": "A"})).startswith("A[0] = x;")
    assert code(ask(doc, "octave")) == "[x y; 1 x.*y]"
    res = ask(doc, "rust")                                  # SymPy's Rust printer refuses matrices
    assert res["files"] == [] and res["error"].startswith("Rust cannot write this:") and "Matrix" in res["error"]


def test_what_a_language_lacks_is_commented_and_noted():
    doc = Document(besselj(1, x) + sin(x), addons=[ADDON])
    res = ask(doc, "c")
    assert code(res).startswith("/* Not supported in C: */\n/* besselj */")
    assert res["notes"] and "besselj" in res["notes"][0]
    res = ask(doc, "octave")                                # Octave has besselj
    assert res["notes"] == []
    res = ask(Document(Integral(x, x), addons=[ADDON]), "julia")
    assert "# Not supported in Julia:" in code(res) and "Integral" in res["notes"][0]


def test_a_whole_function_with_codegen():
    doc = Document(sin(x) / y, addons=[ADDON])
    res = ask(doc, "function")
    assert [f["name"] for f in res["files"]] == ["f.c", "f.h"]
    assert "double f(double x, double y) {" in res["files"][0]["code"]
    res = ask(doc, "function", options={"name": "ratio", "header": False, "language": "C89"})
    assert [f["name"] for f in res["files"]] == ["ratio.c"] and "double ratio(double x, double y)" in code(res)
    for language, ext in (("F95", "f90"), ("Octave", "m"), ("Julia", "jl"), ("Rust", "rs")):
        res = ask(doc, "function", options={"name": "g", "language": language, "header": False})
        assert res["files"][0]["name"] == "g." + ext, (language, res)
    res = ask(Document(Matrix([x, y]), addons=[ADDON]), "function")
    assert "void f(double x, double y, double *out)" in code(res)
    res = ask(Document(Eq(x, y**2), addons=[ADDON]), "function")
    assert "void f(double *x, double y)" in code(res)
    res = ask(Document(besselj(1, x), addons=[ADDON]), "function")
    assert code(res).startswith("/* Not supported in C: */") and "besselj" in res["notes"][0]


def test_bad_options_are_said_in_words():
    doc = Document(x, addons=[ADDON])
    assert "plain name" in ask(doc, "c", options={"assign": "1a"})["error"]
    assert "plain name" in ask(doc, "function", options={"name": "for"})["error"]
    assert "No C standard" in ask(doc, "c", options={"standard": "C42"})["error"]
    assert "No export format" in ask(doc, "cobol")["error"]
    assert "Nothing to export" in ask(doc, "c", path="/7/3")["error"]
    with pytest.raises(ValueError):
        ADDON.handle(doc, "nope", {})


def test_export_is_a_plain_function_too():
    res = export(x + 1, "latex")
    assert res["files"] == [{"name": "formula.tex", "code": "x + 1", "mime": "application/x-tex"}]

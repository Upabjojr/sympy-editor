"""The handwriting add-on: the LaTeX the model's tokens become, insertion, a
missing model said rather than crashed on, and - where math-ocr and
onnxruntime are here - the model reading handwriting it never saw."""
import json
import sys
from pathlib import Path

import pytest
from sympy import symbols

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "sympy_editor_latex"))

from sympy_editor import Document  # noqa: E402
from sympy_editor_handwriting import ADDON, HandwritingAddon, StrokeRecognizer, functions_as_commands, sized_delimiters, with_braces  # noqa: E402

x, y, M, r = symbols("x y M r")


def test_letters_spelling_a_function_become_its_command():
    """The training labels write sin, log, lim as letters, and the model has no
    command for them: the LaTeX reader would read a product of letters."""
    assert functions_as_commands(list("sinx")) == ["\\sin", "x"]
    assert functions_as_commands(list("sinhx")) == ["\\sinh", "x"]              # the longest name
    assert functions_as_commands(["\\frac", "1", "{"] + list("logn") + ["}"]) == ["\\frac", "1", "{", "\\log", "n", "}"]
    assert functions_as_commands(list("abc")) == list("abc")
    assert functions_as_commands(["\\sin", "x"]) == ["\\sin", "x"]


def test_every_argument_gets_its_braces_back():
    assert with_braces(["\\frac", "1", "2"]) == ["\\frac", "{", "1", "}", "{", "2", "}"]
    assert with_braces(["x", "^", "2"]) == ["x", "^", "{", "2", "}"]
    assert with_braces(["\\frac", "{", "t", "^", "2", "}", "q"]) == ["\\frac", "{", "t", "^", "{", "2", "}", "}", "{", "q", "}"]
    assert with_braces(["\\sqrt", "[", "3", "]", "x"]) == ["\\sqrt", "[", "3", "]", "{", "x", "}"]
    assert with_braces(["e", "^", "\\frac", "1", "2"]) == ["e", "^", "{", "\\frac", "{", "1", "}", "{", "2", "}", "}"]



def test_matching_delimiters_are_sized_for_display():
    r"""\left and \right on every pair, so that a parenthesis is as tall as
    what it holds; never where LaTeX would refuse them, nor on a lone bar."""
    def sized(text):
        return " ".join(sized_delimiters(text.split()))
    assert sized(r"( \frac { 1 } { 2 } )") == r"\left ( \frac { 1 } { 2 } \right )"
    assert sized(r"( [ a ] + \{ b \} )") == r"\left ( \left [ a \right ] + \left \{ b \right \} \right )"
    assert sized(r"[ 0 , 1 )") == r"\left [ 0 , 1 \right )"                               # an interval
    assert sized(r"| a | + \| b \|") == r"\left | a \right | + \left \| b \right \|"
    assert sized(r"P ( A | B )") == r"P \left ( A | B \right )"                          # a lone bar stays
    assert sized(r"\langle x \rangle \lfloor y \rfloor") == r"\left \langle x \right \rangle \left \lfloor y \right \rfloor"
    for untouched in (r"\sqrt [ 3 ] { x }", r"( a", r"a )", r"{ ( } )", r"\begin{matrix} ( a & b ) \end{matrix}",
                      r"\begin{matrix} ( a \\ b ) \end{matrix}"):
        assert sized(untouched) == untouched, untouched


def test_insert_replaces_a_range_or_the_whole_expression():
    doc = Document(x**3 + 2*x**2 + x, addons=[ADDON])
    children = [i for i, arg in enumerate(doc.expr.args) if arg in (2*x**2, x)]
    snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "insert", "latex": "y", "path": "/", "children": children})
    assert not snap.get("error") and doc.expr == x**3 + y
    doc.handle({"action": "addon", "addon": "handwriting", "method": "insert", "latex": "\\frac{1}{2}", "path": "/"})
    assert str(doc.expr) == "1/2"
    assert doc.history_labels()["actions"][-1] == "Handwriting: \\frac{1}{2}"



def test_a_reading_carries_its_options_and_an_insert_follows_the_picks():
    """The bottom sheet of the full screen offers what the LaTeX panel does:
    each ambiguity a menu, each constant name a switch; what goes in is the
    reading with the options picked."""
    doc = Document(x, addons=[ADDON])
    call = lambda method, **payload: doc.handle(dict(payload, action="addon", addon="handwriting", method=method))
    latex = r"\sin x \cos y + \pi"
    reading = call("read", latex=latex)["query"]["result"]["reading"]
    assert reading["ok"] and reading["src"] == "sin(x)*cos(y) + pi"
    point = next(a for a in reading["ambiguities"] if a["fragment"] == r"\sin x \cos y")
    other = next(i for i, o in enumerate(point["options"]) if o["src"] == "sin(x*cos(y)) + pi")
    assert [c["name"] for c in reading["constants"]] == ["pi"] and reading["constants"][0]["on"]
    picks = {"choices": dict(reading["choices"], **{point["key"]: other}), "constants": {"pi": False}}
    picked = call("read", latex=latex, **picks)["query"]["result"]["reading"]
    assert picked["src"] == "pi + sin(x*cos(y))"
    call("insert", latex=latex, path="/", **picks)
    assert str(doc.expr) == "pi + sin(x*cos(y))" and "pi" not in [str(a) for a in doc.expr.atoms() if a.is_number]



def test_a_reading_goes_after_the_formula_when_nothing_is_selected():
    doc = Document(x, addons=[ADDON])
    doc.handle({"action": "addon", "addon": "handwriting", "method": "insert", "latex": "+ y", "end": True})
    assert doc.expr == x + y


def test_a_model_notice_is_text_for_the_guide():
    from sympy_editor_handwriting.recognizer import read_notice
    assert read_notice(b"  Terms of the model.\n") == "Terms of the model."
    assert read_notice(b"") is None and read_notice(None) is None


def test_a_missing_model_is_said_not_crashed_on(tmp_path):
    missing = StrokeRecognizer(mathocr=tmp_path)
    status = missing.status()
    assert status["available"] is False and "math-ocr was not found" in status["reason"]
    doc = Document(x, addons=[HandwritingAddon(missing)])                     # switched on all the same
    snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "recognize", "strokes": [[[0, 0, 0], [1, 1, 5]]]})
    assert "math-ocr was not found" in snap["query"]["error"] and doc.expr == x


STATUS = StrokeRecognizer().status()
needs_model = pytest.mark.skipif(not STATUS["available"], reason=STATUS.get("reason", ""))


def _test_inks(recognizer, count=None):
    root = recognizer.root
    index = root / "data" / "index_test.jsonl"
    if not index.is_file():
        pytest.skip("math-ocr's test split is not here")
    inkml = recognizer.load()[3]
    records = [json.loads(line) for line in index.open(encoding="utf-8")]
    return inkml, records if count is None else records[:count], root / "data" / "handwriting-inks"


@needs_model
def test_the_model_reads_handwriting_it_never_saw():
    """math-ocr's held-out test split: the stroke model reads about two formulas in
    five exactly.  Far fewer means the features, the graphs or the decoding
    went wrong somewhere between here and math-ocr."""
    rec = StrokeRecognizer()
    inkml, records, base = _test_inks(rec, 20)
    tokenizer = rec.load()[4]
    exact = 0
    for item in records:
        ink = inkml.parse_inkml(base / item["p"])
        res = rec.recognize([s.tolist() for s in ink.strokes])
        assert res["candidates"] and res["strokes"] == len(ink.strokes)
        exact += tokenizer.normalize(res["candidates"][0]["raw"]) == tokenizer.normalize(ink.label)
    assert exact >= 5, exact


@needs_model
def test_recognizing_in_a_document_answers_with_what_sympy_gets():
    rec = ADDON.recognizer
    inkml, records, base = _test_inks(rec)
    item = next(i for i in records if i["l"] == "\\frac{1}{2M-r}")
    ink = inkml.parse_inkml(base / item["p"])
    doc = Document(x, addons=[ADDON])
    snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "recognize", "strokes": [s.tolist() for s in ink.strokes]})
    best = snap["query"]["result"]["candidates"][0]
    assert best["latex"] == "\\frac{1}{2M-r}" and best["reading"]["ok"]
    assert best["reading"]["src"] == str(1 / (2*M - r))
    assert doc.expr == x                                              # a query: nothing changed


class _Answers:
    """A recognizer that answers with one LaTeX and keeps what it was given."""

    def __init__(self, latex):
        self.latex, self.got = latex, None

    def status(self):
        return {"available": True}

    def warm(self, background=True):
        return True


class _BoxReader(_Answers):
    def recognize(self, strokes, beam=4, limit=5, context=None):
        self.got = (strokes, context)
        return {"candidates": [{"latex": self.latex}], "ms": 1.0, "stand_in": "\\ctx"}


class _InkReader(_Answers):
    def recognize(self, strokes, beam=4, limit=5):
        self.got = (strokes, None)
        return {"candidates": [{"latex": self.latex}], "ms": 1.0}


def _write(rec, box):
    from sympy import sin
    doc = Document(sin(x), addons=[HandwritingAddon(rec)])
    snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "write",
                       "strokes": [[[0, 30, 0], [20, 30, 5]], [[8, 40, 9], [12, 44, 12]]],
                       "context": box, "nest": "", "beam": 1})
    return snap["query"]["result"]["candidates"][0]


def test_a_recognizer_that_takes_the_piece_gets_its_box():
    rec = _BoxReader(r"\frac{\ctx}{x}")
    best = _write(rec, [0, 0, 20, 20])
    assert rec.got[1] == [0, 0, 20, 20] and len(rec.got[0]) == 2       # the ink as written
    assert best["nested"] and best["reading"]["src"] == "sin(x)/x"


def test_a_recognizer_that_does_not_gets_the_triangle():
    rec = _InkReader(r"\frac{\Delta}{x}")
    best = _write(rec, [0, 0, 20, 20])
    assert len(rec.got[0]) == 3                                         # the triangle first
    tri = rec.got[0][0]
    assert tri[-1][2] < rec.got[0][1][0][2] and max(p[1] for p in tri) <= 20
    assert best["nested"] and best["reading"]["src"] == "sin(x)/x"


z = symbols("z")


class _SiblingReader(_BoxReader):
    """A model with a token per printed sibling."""
    def box_tokens(self):
        return ["\\ctx", "\\ctxb", "\\ctxc", "\\ctxd", "\\ctxe", "\\ctxf"]

    def recognize(self, strokes, beam=4, limit=5, context=None):
        self.got = (strokes, context)
        return {"candidates": [{"latex": t} for t in self.latex.split("|")], "ms": 1.0, "stand_in": "\\ctx"}


def _among_siblings(latex, nest):
    """sin(x) cos(y) tan(z), ink written by the factor at ``nest``, read as
    ``latex`` (candidates split by |), the best one put in."""
    from sympy import cos, sin, tan
    rec = _SiblingReader(latex)
    doc = Document(sin(x) * cos(y) * tan(z), addons=[HandwritingAddon(rec)])
    sibs = []
    for k in range(3):   # printed left to right: sin, cos, tan - whatever their order in args
        arg = [sin(x), cos(y), tan(z)][k]
        sibs.append({"path": str(doc.expr.args.index(arg)), "box": [30 * k, 0, 30 * k + 25, 20]})
    nest_path = str(doc.expr.args.index(nest))
    snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "write",
                       "strokes": [[[30, 25, 0], [55, 25, 5]]], "context": [0, 0, 1, 1], "nest": nest_path,
                       "siblings": sibs, "beam": 1})
    cands = snap["query"]["result"]["candidates"]
    best = cands[0]
    if best["nested"]:
        doc.handle({"action": "addon", "addon": "handwriting", "method": "insert", "latex": best["latex"],
                    "path": best["nest"], "children": best["children"], "nest": best["nest"],
                    "display": best["display"]})
    return rec, doc, cands


def test_ink_among_siblings_goes_with_the_one_it_names():
    from sympy import cos, sin, tan
    rec, doc, cands = _among_siblings(r"\frac{\ctxb}{\theta}", cos(y))
    assert len(rec.got[1]) == 3 and rec.got[1][1] == [30, 0, 55, 20]      # every sibling's box, in order
    assert cands[0]["nested"] and cands[0]["reading"]["ok"]
    assert doc.expr == sin(x) * cos(y) / __import__("sympy").Symbol("theta") * tan(z)
    assert r"\cos" in cands[0]["display"]


def test_the_model_may_name_another_sibling_than_the_guess():
    from sympy import Symbol, cos, sin, tan
    _, doc, _ = _among_siblings(r"\ctxc^{2}", cos(y))                    # guessed cos, written by tan
    assert doc.expr == sin(x) * cos(y) * tan(z) ** 2
    _, doc, _ = _among_siblings(r"\frac{\ctxb\ctxc}{t}", sin(x))       # one bar under two of them
    assert doc.expr == sin(x) * cos(y) * tan(z) / Symbol("t")


def test_a_reading_that_names_no_run_is_the_ink_alone():
    from sympy import cos
    _, _, cands = _among_siblings(r"\ctx+\ctxc|x", cos(y))
    assert not cands[0]["nested"] and not cands[1]["nested"]


def _nest_and_insert(expr, latex, reader=_BoxReader):
    """Ink written by the whole of ``expr``, read as ``latex``, put in."""
    doc = Document(expr, addons=[HandwritingAddon(reader(latex))])
    snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "write",
                       "strokes": [[[0, 30, 0], [20, 30, 5]]], "context": [0, 0, 20, 20], "nest": "/", "beam": 1})
    best = snap["query"]["result"]["candidates"][0]
    assert best["nested"] and best["reading"]["ok"], best
    doc.handle({"action": "addon", "addon": "handwriting", "method": "insert", "latex": best["latex"],
                "path": "/", "nest": best.get("nest"), "display": best["display"]})
    return doc.expr, best


@pytest.mark.parametrize("make", [
    lambda: __import__("sympy").Function("f")(x),
    lambda: __import__("sympy").Derivative(__import__("sympy").Function("f")(x), x),
    lambda: __import__("sympy").Function("f")(x, y),
    lambda: __import__("sympy").Symbol("x_1"),
    lambda: __import__("sympy").Symbol("lamda"),
    lambda: __import__("sympy").Symbol("xy"),
    lambda: __import__("sympy").Symbol("e"),
    lambda: __import__("sympy").Symbol("i") + 1,
])
@pytest.mark.parametrize("reader", [_BoxReader, _InkReader])
def test_the_piece_written_by_goes_in_as_itself(make, reader):
    # Its LaTeX read back is another expression: f(x) a product, x_1 the symbol
    # x_{1}, e Euler's number...  The piece itself takes the stand-in's place.
    piece = make()
    stand = r"\ctx" if reader is _BoxReader else r"\Delta"
    got, best = _nest_and_insert(piece, stand + " + 1", reader)
    assert got == piece + 1
    assert best["reading"]["src"] == str(piece + 1)
    got, _ = _nest_and_insert(piece, stand + "^{2}", reader)
    assert got == piece ** 2
    import sympy
    assert sympy.latex(piece) in best["display"]                   # shown as the piece's LaTeX


def test_strokes_without_an_engine_after_the_host_was_chosen_go_to_the_model():
    rec = _InkReader(r"x + 1")
    addon = HandwritingAddon(rec)
    doc = Document(y, addons=[addon])
    doc.handle({"action": "addon", "addon": "handwriting", "method": "engine", "name": "host"})
    snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "write",
                       "strokes": [[[0, 30, 0], [20, 30, 5]]], "beam": 1})
    assert snap["query"]["result"]["candidates"][0]["reading"]["src"] == "x + 1"
    assert snap["query"]["result"]["engine"] == "math-ocr"
    # and "recognize" asks the engine that reads here, not a fixed one
    other = _InkReader(r"y")
    from sympy_editor_handwriting import Engine
    addon2 = HandwritingAddon(engines=[Engine("a", "A", rec), Engine("b", "B", other)], engine="b")
    doc2 = Document(y, addons=[addon2])
    snap = doc2.handle({"action": "addon", "addon": "handwriting", "method": "recognize",
                        "strokes": [[[0, 30, 0], [20, 30, 5]]], "beam": 1})
    assert snap["query"]["result"]["candidates"][0]["latex"] == "y" and other.got is not None


class _Says:
    """A recognizer that reads any ink as the LaTeX it is given, best first."""
    def __init__(self, *latex):
        self.latex = latex

    def status(self):
        return {"available": True}

    def warm(self, background=True):
        return True

    def recognize(self, strokes, beam=4, limit=5, context=None):
        return {"candidates": [{"latex": t, "raw": t, "score": -i} for i, t in enumerate(self.latex)], "ms": 1.0}


def test_ink_over_a_selected_operator_is_read_as_an_operator():
    """Written over the = of an equation, the ink is the operator that takes
    its place: readings that are no operator go, each operator once, and
    nothing is read together with a piece."""
    from sympy import Eq
    from sympy_editor_handwriting import operator_of
    doc = Document(Eq(x, y), addons=[HandwritingAddon(_Says(r"\leq", "x", r"\le", "=", r"\neq"))])
    snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "write", "operator": True,
                       "strokes": [[[0, 0, 0], [5, 5, 10]]]})
    got = snap["query"]["result"]["candidates"]
    assert [c["reading"]["operator"] for c in got] == ["<=", "=", "!="]
    assert all(c["reading"]["ok"] and not c["nested"] for c in got)
    assert got[0]["display"] == r"\le"
    # nothing that is an operator: said so, not read as a formula
    doc = Document(Eq(x, y), addons=[HandwritingAddon(_Says("x^2"))])
    snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "write", "operator": True,
                       "strokes": [[[0, 0, 0]]]})
    [only] = snap["query"]["result"]["candidates"]
    assert not only["reading"]["ok"] and "Not an operator" in only["reading"]["error"]
    assert operator_of(" {\\geq} ") == ">=" and operator_of(r"\cdot") == "*" and operator_of("xy") is None

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


def test_ink_beside_a_piece_in_a_denominator_multiplies_it():
    r"""Bug: a d written right of the a of 1/a read "\mathit{nestedpiece} d",
    which the LaTeX reader refused ("could not be read ... near 'd'"): a named
    symbol could not be followed by a factor.  It is the product a*d."""
    a, d = symbols("a d")
    doc = Document(1 / a, addons=[HandwritingAddon(_InkReader(r"\Delta d"))])
    snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "write",
                       "strokes": [[[20, 30, 0], [26, 40, 5]]], "context": [0, 20, 16, 44], "nest": "/d", "beam": 1})
    best = snap["query"]["result"]["candidates"][0]
    assert best["nested"] and best["reading"]["ok"], best["reading"]
    assert best["reading"]["src"] == "a*d"


def test_ink_at_the_foot_of_a_piece_is_its_subscript():
    r"""Bug: a d written at the foot of the a of 1/a read
    "\mathit{nestedpiece}_{d}", which the LaTeX reader refused.  The piece's
    own name takes the subscript: a_d (the LaTeX reader names it as SymPy does).  A piece with no name says so."""
    a, x = symbols("a x")
    def write(expr, nest):
        doc = Document(expr, addons=[HandwritingAddon(_InkReader(r"\Delta_{d}"))])
        snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "write",
                           "strokes": [[[20, 40, 0], [24, 48, 5]]], "context": [0, 20, 16, 44], "nest": nest, "beam": 1})
        return snap["query"]["result"]["candidates"][0]["reading"]
    reading = write(1 / a, "/d")
    assert reading["ok"] and reading["src"] == "a_d", reading
    reading = write(1 / (x + 1), "/d")
    assert not reading["ok"] and "subscript goes on a name" in reading["error"]


def test_the_pieces_to_read_with_come_as_latex():
    """latex_of: the LaTeX of each piece the page offers to read the ink with;
    a path that is gone is left out."""
    from sympy import sin
    doc = Document(sin(x) + 1 / y, addons=[HandwritingAddon(_InkReader("x"))])
    paths = [p for p, n in doc.snapshot()["nodes"].items() if n["src"] in ("sin(x)", "1/y")]
    snap = doc.handle({"action": "addon", "addon": "handwriting", "method": "latex_of", "paths": paths + ["/9/9"]})
    got = snap["query"]["result"]["latex"]
    assert sorted(got.values()) == sorted([r"\sin{\left(x \right)}", r"\frac{1}{y}"]) and "/9/9" not in got


# ---- what a page is asked to install, and what it is told -------------------

def _checkout(tmp_path, meta='{"mode": "stroke"}'):
    """A folder that passes for a math-ocr checkout with an exported model:
    the files ``status()`` looks for, all of them empty - it loads nothing."""
    root = tmp_path / "somebody" / "checkouts" / "math-ocr"
    (root / "mathocr" / "data").mkdir(parents=True)
    (root / "mathocr" / "tokenizer.py").write_text("")
    (root / "mathocr" / "data" / "inkml.py").write_text("")
    model = root / "export" / "a_stroke_model"
    model.mkdir(parents=True)
    for name in ("encoder.onnx", "decoder_step.onnx"):
        (model / name).write_bytes(b"")
    (model / "meta.json").write_text(meta)
    return root, model


@pytest.fixture
def onnxruntime_here(monkeypatch):
    """onnxruntime as far as ``status()`` asks - that it can be found -
    where it is not installed."""
    import importlib.machinery
    import importlib.util
    import types
    if importlib.util.find_spec("onnxruntime") is None:
        stand = types.ModuleType("onnxruntime")
        stand.__spec__ = importlib.machinery.ModuleSpec("onnxruntime", None)
        monkeypatch.setitem(sys.modules, "onnxruntime", stand)


def test_a_page_that_runs_its_own_python_is_asked_to_install_nothing_for_the_model(monkeypatch):
    """Bug: the add-on asked a Pyodide page for onnxruntime, which has no
    wheel there; asked for in one ``micropip.install`` with every other
    add-on's requirements, it failed them all - a page whose catalogue listed
    handwriting had no ``lark``, and its LaTeX add-on could not read.  The
    model cannot run in such a page: nothing is installed for it, and its
    status says so rather than "pip install onnxruntime"."""
    from sympy_editor.html import pyodide_requirements
    from sympy_editor_latex import ADDON as LATEX
    assert HandwritingAddon(_InkReader("x")).pyodide_packages() == []
    asked = pyodide_requirements(Document(x + 1, addons=[LATEX], available=[ADDON]))
    assert not [pkg for pkg in asked if pkg.startswith(("onnxruntime", "numpy"))]
    assert [pkg for pkg in asked if pkg.startswith("lark")]                  # the LaTeX add-on's, still asked for
    monkeypatch.setattr(sys, "platform", "emscripten")
    status = StrokeRecognizer().status()
    assert status["available"] is False
    assert "runs its own Python" in status["reason"] and "pip install" not in status["reason"]


def test_a_meta_json_that_holds_no_object_is_read_as_an_empty_one(tmp_path, onnxruntime_here):
    """Bug: a ``meta.json`` that is valid JSON but no object - ``[1, 2]`` -
    raised "'list' object has no attribute 'get'" out of ``status()``, which
    switching the add-on on and building any page both call."""
    root, model = _checkout(tmp_path, meta="[1, 2]")
    rec = StrokeRecognizer(mathocr=root, model="export/a_stroke_model")
    assert rec.status()["available"] is True
    addon = HandwritingAddon(rec)
    assert addon.client_options()["status"]["available"] is True
    (model / "meta.json").write_text('"image"')
    assert rec.status()["available"] is True
    (model / "meta.json").write_text('{"mode": "image"}')            # an object is read as before
    assert "image model" in rec.status()["reason"]


def test_a_page_is_not_told_where_the_model_is(tmp_path, onnxruntime_here):
    """Bug: the model's folder and the checkout's went, as absolute paths,
    into every page built - a saved page passed on carried the layout of its
    maker's home directory.  A page is told whether the model reads, why not,
    its name and its notice; the paths stay with this Python."""
    import json as _json

    from sympy_editor import to_html
    root, model = _checkout(tmp_path)
    (model / "NOTICE").write_text("The terms of the model.")
    here, home = str(tmp_path), str(tmp_path / "somebody")
    rec = StrokeRecognizer(mathocr=root, model="export/a_stroke_model")
    assert rec.status()["model"] == str(model)                           # Python's own: load() goes by it
    missing = StrokeRecognizer(mathocr=root, model="export/not_there")
    nowhere = StrokeRecognizer(mathocr=tmp_path / "somebody" / "elsewhere")
    for r, said in ((rec, None), (missing, "No exported model in not_there"), (nowhere, "math-ocr was not found")):
        addon = HandwritingAddon(r)
        doc = Document(x, addons=[addon])
        told = [addon.client_options(), addon.client(), doc.snapshot().get("handwriting")]
        # ... nor by what it answers: a model that cannot be read (these
        # files are empty) is refused by onnxruntime with the file's path
        for method, payload in (("status", {}), ("engines", {}), ("engine", {"name": "math-ocr"}),
                                ("recognize", {"strokes": [[[0, 0, 0], [1, 1, 5]]]}),
                                ("write", {"strokes": [[[0, 0, 0], [1, 1, 5]]]})):
            told.append(doc.handle(dict(payload, action="addon", addon="handwriting", method=method))["query"])
        assert "error" in told[-1]
        text = _json.dumps([{k: v for k, v in t.items() if k not in ("js", "css")} for t in told if t])
        assert here not in text and home not in text, text
        assert said is None or said in text
        page = to_html(doc)
        assert here not in page and home not in page
    status = HandwritingAddon(rec).client_options()["status"]
    assert status == {"available": True, "model": "a_stroke_model", "notice": "The terms of the model."}


def test_the_engine_chosen_is_the_document_s_own():
    """Bug: the engine chosen was kept on the add-on, which every document
    shares: one page chose the device's own reader, and every page built
    after it was given "host" - on a device with no reader, a Pen that was
    off.  The choice is the document's, and travels with its session."""
    addon = HandwritingAddon(_InkReader("x + 1"))
    a, b = Document(x, addons=[addon]), Document(y, addons=[addon])
    ask = lambda doc, method, **kw: doc.handle(dict(kw, action="addon", addon="handwriting", method=method))
    assert ask(a, "engine", name="host")["query"]["result"]["engine"] == "host"
    assert addon.engine == "math-ocr" and addon.client_options()["engine"] == "math-ocr"
    assert a.snapshot()["handwriting"] == {"engine": "host"}
    assert b.snapshot()["handwriting"] == {"engine": "math-ocr"}
    assert ask(b, "engines")["query"]["result"]["engine"] == "math-ocr"
    assert Document(x, addons=[addon]).snapshot()["handwriting"] == {"engine": "math-ocr"}   # a page built after
    # with the session: kept, and given back
    state = a.export()
    assert state["addon_state"] == {"handwriting": {"engine": "host"}}
    assert "addon_state" not in b.export()
    again = Document(x, addons=[addon], history=state["history"], index=state["index"],
                     addon_state=state["addon_state"])
    assert again.snapshot()["handwriting"] == {"engine": "host"}
    # an engine this add-on has not is no choice at all
    other = Document(x, addons=[addon], addon_state={"handwriting": {"engine": "gone"}})
    assert other.snapshot()["handwriting"] == {"engine": "math-ocr"}


# ---- payloads that are not what the page sends --------------------------------

class _Counting(_InkReader):
    """Keeps count of what it was asked to read."""
    asked = 0

    def recognize(self, strokes, beam=4, limit=5):
        self.asked += 1
        return super().recognize(strokes, beam=beam, limit=limit)


class _Unread(list):
    """Points that say so when they are read."""
    def __iter__(self):
        raise AssertionError("the points were read before they were counted")


LINE = [[10 + i, 20, i * 8] for i in range(30)]


@pytest.mark.parametrize("method, payload, said", [
    ("write", {"strokes": 5}, "list of strokes"),
    ("write", {"strokes": "hello"}, "list of strokes"),
    ("write", {"strokes": [1, 2, 3]}, "list of strokes"),
    ("write", {"strokes": [[{"x": 1, "y": 2}]]}, "list of strokes"),                 # KeyError: 0
    ("recognize", {"strokes": [[{"x": 1, "y": 2}]]}, "list of strokes"),
    ("recognize", {"strokes": 5}, "list of strokes"),                              # 'int' object is not iterable
    ("write", {"strokes": [_Unread([[0, 0, 0]] * 1_000_000)]}, "Too much ink"),
    ("write", {"strokes": [LINE], "beam": "many"}, "whole number"),
    ("write", {"strokes": [LINE], "context": "abcd", "nest": "/"}, "four numbers"),
    ("write", {"strokes": [LINE], "context": [1, 2, 3], "nest": "/"}, "four numbers"),
    ("write", {"strokes": [LINE], "context": {"a": 1}, "nest": "/"}, "four numbers"),
    ("write", {"strokes": [LINE], "context": [float("nan")] * 4, "nest": "/"}, "four numbers"),
    ("write", {"strokes": [LINE], "context": [0, 0, 9, 9], "nest": "/0", "siblings": "zz"}, "its path and its box"),
    ("write", {"strokes": [LINE], "context": [0, 0, 9, 9], "nest": "/0",
               "siblings": [{"path": "/0"}, {"path": "/1"}]}, "four numbers"),   # KeyError: 'box'
    ("write", {"candidates": "abc"}, "each with its LaTeX"),
    ("write", {"candidates": [1, 2]}, "each with its LaTeX"),
    ("write", {"candidates": [{"latex": 5}]}, "is text"),
    ("read", {"latex": 5}, "is text"),
    ("read", {"latex": "x", "nest": "/", "children": "ab"}, "whole numbers"),
    ("latex_of", {"paths": 5}, "list of paths"),
    ("insert", {"latex": "x", "path": "/", "children": ["a"]}, "whole numbers"),
    ("insert", {"latex": "x", "caret": "zz"}, "cursor"),
])
def test_a_payload_of_the_wrong_shape_is_refused_in_words(method, payload, said):
    """Bug: nothing malformed escaped ``handle``, but what came back was the
    text of whatever line tripped on it - "KeyError: 0" for points given as
    objects, "'int' object is not iterable", "KeyError: 'latex'" - and a
    million points were all read before the limit refused them.  The shape
    is looked at first, the ink counted before it is read, and the refusal
    is one a user can read; nothing is asked of the model."""
    rec = _Counting("z")
    rec.box_tokens = lambda: ["\\ctx", "\\ctxb"]
    doc = Document(x + y, addons=[HandwritingAddon(rec)])
    snap = doc.handle(dict(payload, action="addon", addon="handwriting", method=method))
    error = (snap.get("query") or {}).get("error") or ""
    assert said in error, snap.get("query")
    for raw in ("KeyError", "TypeError", "AttributeError", "OverflowError", "not iterable", "has no attribute"):
        assert raw not in error, error
    assert rec.asked == 0 and doc.expr == x + y


def test_a_reading_that_says_nothing_is_no_reading():
    """Bug: ``latex: null`` was read as the text "None" - the product
    ``E*N*n*o`` - and a host's reading with no ``latex`` raised
    "KeyError: 'latex'".  Neither is a reading."""
    doc = Document(x + y, addons=[HandwritingAddon(_InkReader("z"))])
    ask = lambda method, **kw: doc.handle(dict(kw, action="addon", addon="handwriting", method=method))["query"]
    reading = ask("read", latex=None)["result"]["reading"]
    assert not reading["ok"] and not reading["src"]
    got = ask("write", candidates=[{"raw": "x"}, {"latex": None}, {"latex": "  "}, {"latex": " x^2 "}])["result"]
    assert [c["latex"] for c in got["candidates"]] == ["x^2"]
    assert got["candidates"][0]["reading"]["src"] == "x**2"
    got = ask("write", candidates=[{"latex": None}], operator=True)["result"]
    assert got["candidates"] == []
    assert "error" in ask("insert", latex=None, path="/") and doc.expr == x + y


def test_ink_farther_than_anything_is_drawn_is_refused():
    """Bug: coordinates of 1e30 came back as "OverflowError: cannot convert
    float infinity to integer", from inside the features.  What is no number
    at all is still dropped, as it was."""
    pytest.importorskip("numpy")
    from sympy_editor_handwriting.recognizer import _strokes
    with pytest.raises(ValueError, match="farther than anything"):
        _strokes([[[1e30, 5, 0], [2e30, 1e30, 1]]])
    kept = _strokes([[[float("nan"), 1, 0], [3, 4, 5], ["a", 2, 3], [5, 6]], [[float("inf"), 2, 1]]])
    assert [s.tolist() for s in kept] == [[[3.0, 4.0, 5.0], [5.0, 6.0, 1.0]]]


def test_a_reading_whose_piece_is_not_there_is_refused():
    r"""Bug: a nested reading whose piece could not be found - arguments the
    node has not, a path that is gone - was read all the same, and answered
    ``ok`` with the placeholder's own name: ``nestedpiece**2``.  So was one
    that read the placeholder into a name (``\hat{\mathit{nestedpiece}}``).
    Both are refused, and nothing of the kind goes into the formula."""
    doc = Document(x + y, addons=[HandwritingAddon(_InkReader("z"))])
    ask = lambda method, **kw: doc.handle(dict(kw, action="addon", addon="handwriting", method=method))["query"]
    tex = r"\mathit{nestedpiece}^2"
    for where in ({"nest": "/", "children": [0, 7]}, {"nest": "/", "children": [0, 0]},
                  {"nest": "/", "children": [-1]}, {"nest": "/9"}, {}):
        reading = ask("read", latex=tex, **where)["result"]["reading"]
        assert not reading["ok"] and "nestedpiece" not in str(reading["src"]), (where, reading)
        assert "not in the formula" in reading["error"]
        assert "not in the formula" in ask("insert", latex=tex, path="/", **where)["error"]
        assert doc.expr == x + y
    reading = ask("read", latex=r"\hat{\mathit{nestedpiece}}", nest="/0")["result"]["reading"]
    assert not reading["ok"] and "nestedpiece" not in str(reading["src"])
    # and a piece that is there is read with it, as before
    reading = ask("read", latex=tex, nest="/", children=[0, 1])["result"]["reading"]
    assert reading["ok"] and reading["src"] == "(x + y)**2"


def test_keep_and_undo_wear_the_colours_of_the_change():
    """Keep is the green of what came, Undo the red of what went - the
    editor's own diff colours, so they follow the theme - and the red is
    the weaker of the two: its border and its words, no fill."""
    import re
    from pathlib import Path
    import sympy_editor_handwriting
    css = (Path(sympy_editor_handwriting.__file__).parent / "static" / "handwriting.css").read_text(encoding="utf-8")
    rule = lambda sel: re.search(re.escape(sel) + r"\s*\{([^}]*)\}", css).group(1)
    # under the row's own selector: `.hw-applied-ask button` sets the plain
    # look, and a bare `.hw-keep` would lose to it
    keep, back = rule(".hw-applied-ask .hw-keep"), rule(".hw-applied-ask .hw-back")
    assert "background: rgba(var(--se-added-rgb)" in keep and "color: rgb(var(--se-added-rgb))" in keep
    assert "color: rgb(var(--se-removed-rgb))" in back and "border-color: rgba(var(--se-removed-rgb)" in back
    assert "background" not in back

def test_the_ios_session_speaks_bytes_to_the_apps_module(monkeypatch):
    """In the iOS app ONNX Runtime is a module built into the interpreter that
    knows nothing of numpy: tensors cross as (type code, shape, buffer), and
    the session turns them into arrays on the way back, in the order asked."""
    import sys
    import types
    np = pytest.importorskip("numpy")
    from sympy_editor_handwriting import recognizer

    seen = {}

    class Session:
        output_names = ["memory", "mask"]

        def __init__(self, model, threads):
            seen["made"] = (model, threads)

        def run(self, feeds):
            seen["feeds"] = {name: (code, shape, bytes(memoryview(data))) for name, (code, shape, data) in feeds.items()}
            return [("f", (1, 2), np.array([[1.5, 2.5]], dtype=np.float32).tobytes()),
                    ("?", (1, 2), np.array([[True, False]]).tobytes())]

    monkeypatch.setitem(sys.modules, recognizer.IOS_MODULE, types.SimpleNamespace(Session=Session))
    session = recognizer._NativeSession(b"model", threads=0)
    assert seen["made"] == (b"model", 1)
    src = np.arange(6, dtype=np.float64).reshape(1, 3, 2)[:, ::-1]          # not float32, not contiguous
    mask, memory = session.run(["mask", "memory"], {"src": src, "src_len": np.array([3], dtype=np.int64),
                                                   "pad": np.array([True, False])})
    assert memory.dtype == np.float32 and memory.tolist() == [[1.5, 2.5]]
    assert mask.dtype == np.bool_ and mask.tolist() == [[True, False]]
    code, shape, data = seen["feeds"]["src"]
    assert (code, shape) == ("f", (1, 3, 2))
    assert np.frombuffer(data, dtype=np.float32).tolist() == [4, 5, 2, 3, 0, 1]
    assert seen["feeds"]["src_len"][:2] == ("q", (1,)) and seen["feeds"]["pad"][:2] == ("?", (2,))
    assert [a.tolist() for a in session.run(None, {})] == [[[1.5, 2.5]], [[True, False]]]

    # only the iOS app's interpreter has the module: nowhere else is it "in an app"
    assert not recognizer._on_ios()

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
from sympy_editor_ink import ADDON, InkAddon, StrokeRecognizer, functions_as_commands, with_braces  # noqa: E402

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


def test_insert_replaces_a_range_or_the_whole_expression():
    doc = Document(x**3 + 2*x**2 + x, addons=[ADDON])
    children = [i for i, arg in enumerate(doc.expr.args) if arg in (2*x**2, x)]
    snap = doc.handle({"action": "addon", "addon": "ink", "method": "insert", "latex": "y", "path": "/", "children": children})
    assert not snap.get("error") and doc.expr == x**3 + y
    doc.handle({"action": "addon", "addon": "ink", "method": "insert", "latex": "\\frac{1}{2}", "path": "/"})
    assert str(doc.expr) == "1/2"
    assert doc.history_labels()["actions"][-1] == "Handwriting: \\frac{1}{2}"


def test_a_missing_model_is_said_not_crashed_on(tmp_path):
    missing = StrokeRecognizer(mathocr=tmp_path)
    status = missing.status()
    assert status["available"] is False and "math-ocr was not found" in status["reason"]
    doc = Document(x, addons=[InkAddon(missing)])                     # switched on all the same
    snap = doc.handle({"action": "addon", "addon": "ink", "method": "recognize", "strokes": [[[0, 0, 0], [1, 1, 5]]]})
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
    snap = doc.handle({"action": "addon", "addon": "ink", "method": "recognize", "strokes": [s.tolist() for s in ink.strokes]})
    best = snap["query"]["result"]["candidates"][0]
    assert best["latex"] == "\\frac{1}{2M-r}" and best["reading"]["ok"]
    assert best["reading"]["src"] == str(1 / (2*M - r))
    assert doc.expr == x                                              # a query: nothing changed

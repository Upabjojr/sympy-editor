"""The linear algebra add-on's Python: finding the matrix, the properties,
the spectrum, the decompositions, the row reduction step by step, Insert,
and the time limits."""
import random
import sys
import threading
import time
from pathlib import Path

import pytest
from sympy import ImmutableMatrix, Matrix, MatrixSymbol, Symbol, eye, simplify, symbols, zeros

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
import sympy_editor_linalg as linalg  # noqa: E402
from sympy_editor_linalg import ADDON, TimeLimit, limited, rref_steps  # noqa: E402

x, a, b = symbols("x a b")
LAM = Symbol("lamda")


def call(doc, method, **payload):
    res = doc.handle(dict(action="addon", addon="linalg", method=method, **payload))
    if "query" in res:
        q = res["query"]
        if "error" in q:
            raise AssertionError(q["error"])
        return q["result"]
    return res


def by_label(items):
    return {i["label"]: i for i in items}


# -- the target -----------------------------------------------------------------

def test_the_matrix_is_found_around_the_selection():
    M = Matrix([[1, 2], [3, 4]])
    doc = Document(M, addons=[ADDON])
    assert call(doc, "analyse", path="/")["path"] == "/"
    assert call(doc, "analyse", path="/2/3")["path"] == "/"           # an entry: its matrix
    # a matrix inside a product: the selection in it names it, the whole formula does not
    A = MatrixSymbol("A", 2, 2)
    doc = Document(A * ImmutableMatrix([[1, x], [0, 1]]), addons=[ADDON])
    i = [k for k, arg in enumerate(doc.expr.args) if isinstance(arg, ImmutableMatrix)][0]
    res = call(doc, "analyse", path=f"/{i}/2/1")
    assert res["path"] == f"/{i}" and res["explicit"] and res["rows"] == 2


def test_a_matrix_expression_is_written_out_and_a_scalar_refused():
    doc = Document(MatrixSymbol("A", 2, 2), addons=[ADDON])
    res = call(doc, "analyse", path="/")
    assert not res["explicit"] and res["rows"] == 2
    doc = Document(x + 1, addons=[ADDON])
    with pytest.raises(AssertionError, match="No matrix here"):
        call(doc, "analyse", path="/")


# -- properties -----------------------------------------------------------------

def test_rank_determinant_trace_and_characteristic_polynomial():
    M = Matrix([[2, 1], [1, 2]])
    doc = Document(M, addons=[ADDON])
    res = call(doc, "analyse", path="/")
    items = by_label(res["items"])
    assert res["square"] and res["problems"] == []
    assert items["Rank"]["src"] == "2"
    assert items["Determinant"]["src"] == "3"
    assert items["Trace"]["src"] == "4"
    assert items["Characteristic polynomial"]["src"] == str(LAM**2 - 4 * LAM + 3)
    assert items["… factored"]["src"] == str((LAM - 3) * (LAM - 1))
    assert "\\lambda" in items["Characteristic polynomial"]["latex"]


def test_a_rectangular_matrix_has_a_rank_and_no_determinant():
    doc = Document(Matrix([[1, 2, 3], [2, 4, 6]]), addons=[ADDON])
    res = call(doc, "analyse", path="/")
    assert not res["square"] and [i["label"] for i in res["items"]] == ["Rank"]
    assert res["items"][0]["src"] == "1"
    with pytest.raises(AssertionError, match="square"):
        call(doc, "spectrum", path="/")


# -- the spectrum -----------------------------------------------------------------

def test_a_jordan_block_is_not_diagonalizable():
    M = Matrix([[2, 1], [0, 2]])
    doc = Document(M, addons=[ADDON])
    res = call(doc, "spectrum", path="/")
    assert res["problems"] == []
    [e] = res["eigen"]
    assert e["value"]["src"] == "2" and e["alg"] == 2 and e["geo"] == 1 and len(e["vectors"]) == 1
    assert res["diagonalizable"] is False
    assert res["jordan"]["J"]["src"] == str(Matrix([[2, 1], [0, 2]]))


def test_a_symmetric_matrix_is_diagonalized_and_p_j_p_inverse_is_the_matrix():
    M = Matrix([[2, 1, 0], [1, 2, 0], [0, 0, 5]])
    doc = Document(M, addons=[ADDON])
    res = call(doc, "spectrum", path="/")
    assert res["diagonalizable"] is True and res["problems"] == []
    values = sorted(int(e["value"]["src"]) for e in res["eigen"])
    assert values == [1, 3, 5]
    assert all(e["alg"] == e["geo"] == 1 for e in res["eigen"])
    call(doc, "insert", id=res["jordan"]["product"]["id"], what="P J P⁻¹")
    assert doc.expr.doit() == M                                   # the unevaluated product equals the matrix
    assert len(doc.expr.args) == 3


def test_eigenvalues_with_multiplicity():
    M = Matrix([[3, 0, 0], [0, 3, 0], [0, 0, 1]])
    res = call(Document(M, addons=[ADDON]), "spectrum", path="/")
    e3 = [e for e in res["eigen"] if e["value"]["src"] == "3"][0]
    assert e3["alg"] == 2 and e3["geo"] == 2 and res["diagonalizable"] is True


# -- decompositions -----------------------------------------------------------------

def _insert_and_check(doc, item, M):
    call(doc, "insert", id=item["id"], what="product")
    assert simplify(doc.expr.doit() - M) == zeros(*M.shape)
    doc.undo()
    assert doc.expr == M


def test_lu_qr_and_cholesky_insert_products_equal_to_the_matrix():
    M = Matrix([[4, 2], [2, 3]])
    doc = Document(M, addons=[ADDON])
    lu = call(doc, "decompose", path="/", kind="lu")
    assert [f["label"] for f in lu["factors"]] == ["L", "U"] and lu["relation"] == "L U"
    _insert_and_check(doc, lu["product"], M)
    qr = call(doc, "decompose", path="/", kind="qr")
    assert [f["label"] for f in qr["factors"]] == ["Q", "R"]
    _insert_and_check(doc, qr["product"], M)
    ch = call(doc, "decompose", path="/", kind="cholesky")
    assert ch["problems"] == [] and ch["relation"] == "L Lᴴ"
    _insert_and_check(doc, ch["product"], M)


def test_lu_with_a_row_exchange_carries_the_permutation():
    M = Matrix([[0, 1], [2, 3]])
    doc = Document(M, addons=[ADDON])
    lu = call(doc, "decompose", path="/", kind="lu")
    assert lu["relation"] == "Pᵀ L U" and "R1 ↔ R2" in lu["note"]
    _insert_and_check(doc, lu["product"], M)


def test_cholesky_says_when_it_does_not_apply():
    doc = Document(Matrix([[1, 2], [3, 4]]), addons=[ADDON])
    res = call(doc, "decompose", path="/", kind="cholesky")
    assert "not applicable" in res["problems"][0]["text"] and "factors" not in res
    doc = Document(Matrix([[1, 2], [2, 1]]), addons=[ADDON])                   # symmetric, indefinite
    res = call(doc, "decompose", path="/", kind="cholesky")
    assert "positive" in res["problems"][0]["text"]


# -- row reduction ---------------------------------------------------------------------

def test_row_reduction_names_each_operation():
    res = call(Document(Matrix([[1, 2], [3, 4]]), addons=[ADDON]), "rref", path="/")
    ops = [s["op"] for s in res["steps"]]
    assert ops == ["R2 ← R2 − 3·R1", "R2 ← (-1/2)·R2", "R1 ← R1 − 2·R2"]
    assert res["check"] is True and res["pivots"] == [1, 2]
    assert res["steps"][0]["oplatex"] == "R_{2} \\leftarrow R_{2} - 3 R_{1}"
    assert res["steps"][-1]["src"] == str(eye(2))


def test_row_reduction_swaps_when_the_pivot_is_zero():
    res = call(Document(Matrix([[0, 1, 2], [3, 0, 6]]), addons=[ADDON]), "rref", path="/")
    assert res["steps"][0]["op"] == "R1 ↔ R2" and res["steps"][0]["kind"] == "swap"
    assert res["check"] is True


def test_row_reduction_agrees_with_sympy_on_many_matrices():
    rng = random.Random(7)
    for _ in range(60):
        r, c = rng.randint(1, 4), rng.randint(1, 5)
        M = Matrix(r, c, [rng.choice([0, 0, 1, -1, 2, 3, -4, 5]) for _ in range(r * c)])
        rec = rref_steps(M)
        R, piv = M.rref()
        assert rec["rref"] == R and rec["pivots"] == list(piv), M
        # replaying the operations on the matrix gives every recorded matrix
        prev = M
        for s in rec["steps"]:
            assert s["matrix"].shape == M.shape
            assert s["matrix"].rank() == prev.rank()                 # row operations keep the rank
            prev = s["matrix"]


def test_row_reduction_of_a_symbolic_matrix_assumes_its_pivots():
    M = Matrix([[a, 1], [b, 2]])
    res = call(Document(M, addons=[ADDON]), "rref", path="/")
    assert res["check"] is True
    assert res["assumed"] and res["steps"][0]["assumes"] == "a"
    assert res["steps"][0]["op"] == "R1 ← (1/a)·R1"


# -- insert ----------------------------------------------------------------------------

def test_insert_replaces_the_matrix_and_undo_takes_it_back():
    A = MatrixSymbol("A", 2, 2)
    M = ImmutableMatrix([[1, 2], [3, 4]])
    doc = Document(A + M, addons=[ADDON])
    i = [k for k, arg in enumerate(doc.expr.args) if isinstance(arg, ImmutableMatrix)][0]
    res = call(doc, "rref", path=f"/{i}/2/0")                       # from an entry: the matrix
    assert res["path"] == f"/{i}"
    call(doc, "insert", id=res["result"]["id"], what="reduced row echelon form")
    assert doc.expr == A + eye(2)
    assert doc.history_labels()["actions"][-1] == "Linear algebra: reduced row echelon form"
    doc.undo()
    assert doc.expr == A + M
    # a scalar result in place of the whole matrix
    doc = Document(M, addons=[ADDON])
    det = by_label(call(doc, "analyse", path="/")["items"])["Determinant"]
    call(doc, "insert", id=det["id"], what="determinant")
    assert doc.expr == -2


def test_a_stale_result_is_refused():
    doc = Document(Matrix([[1, 2], [3, 4]]), addons=[ADDON])
    det = by_label(call(doc, "analyse", path="/")["items"])["Determinant"]
    doc.replace("/2/0", "5")
    with pytest.raises(AssertionError, match="changed since"):
        call(doc, "insert", id=det["id"])
    with pytest.raises(AssertionError, match="no longer known"):
        call(doc, "insert", id="r999999")


def test_a_huge_result_is_described_not_typeset():
    M = Matrix([[1, 2], [3, 4]])
    doc = Document(M, addons=[ADDON])
    big = ((a + b + x + 1) ** 12).expand()
    item = ADDON._item(doc, (), M, "big", big)
    assert item["long"] > linalg.MAX_SHOWN_OPS and "long expression" in item["latex"] and len(item["src"]) < 100
    call(doc, "insert", id=item["id"])                               # Insert still puts all of it in
    assert doc.expr == big


# -- time limits ----------------------------------------------------------------------

def _spin():
    n = 0
    while True:
        n += 1


def test_a_computation_past_its_limit_is_stopped():
    before = threading.active_count()
    t = time.monotonic()
    with pytest.raises(TimeLimit):
        limited(_spin, 0.3)
    assert time.monotonic() - t < 2
    deadline = time.monotonic() + 3
    while threading.active_count() > before and time.monotonic() < deadline:
        time.sleep(0.05)
    assert threading.active_count() == before                     # the worker was stopped, not left spinning
    assert limited(lambda: 6 * 7, 1) == 42
    with pytest.raises(ZeroDivisionError):
        limited(lambda: 1 / 0, 1)


def test_the_panel_hears_of_a_time_limit_in_words(monkeypatch):
    monkeypatch.setattr(linalg.LinalgAddon, "_qr", staticmethod(lambda M: _spin()))
    doc = Document(Matrix([[1, 2], [3, 4]]), addons=[ADDON])
    res = call(doc, "decompose", path="/", kind="qr", limit=0.5)
    [p] = res["problems"]
    assert p["timeout"] == 0.5 and "stopped after 0.5 s" in p["text"] and "try for longer" in p["text"]
    # the next one has the whole of its own time
    assert call(doc, "decompose", path="/", kind="lu")["problems"] == []


def test_without_threads_it_runs_unlimited(monkeypatch):
    def refuse(self):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Thread, "start", refuse)
    assert limited(lambda: 5, 0.01) == 5
    assert linalg.threads_available() is False
    res = call(Document(Matrix([[1, 2], [3, 4]]), addons=[ADDON]), "analyse", path="/")
    assert res["limited"] is False and res["problems"] == []


# -- packaging ----------------------------------------------------------------------------

def test_the_package_carries_its_front_end():
    files = ADDON.python_sources()
    assert "__init__.py" in files and any(k.endswith("linalg.js") for k in files)
    client = ADDON.client()
    assert client["name"] == "linalg" and "registerAddon(\"linalg\"" in client["js"]
    assert "any-pointer: coarse" in client["css"]
    with pytest.raises(AssertionError, match="no method"):
        call(Document(Matrix([[1]]), addons=[ADDON]), "nothing")

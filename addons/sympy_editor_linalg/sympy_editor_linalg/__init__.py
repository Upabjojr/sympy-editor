"""sympy-editor add-on: a linear algebra workbench for the selected matrix.

Every method but ``insert`` is a *query* - nothing in the formula changes:

* ``analyse`` ``{path}``: the explicit matrix at ``path`` or around it (the
  whole formula when nothing is selected) - its shape, rank, determinant,
  trace and characteristic polynomial;
* ``spectrum`` ``{path}``: eigenvalues with their algebraic and geometric
  multiplicities and eigenvectors, whether the matrix is diagonalizable, and
  its Jordan form;
* ``decompose`` ``{path, kind}``: ``lu``, ``qr`` or ``cholesky``;
* ``rref`` ``{path}``: Gauss-Jordan elimination step by step - every
  elementary row operation in words ("R2 ← R2 − 3·R1") and the matrix after
  it - checked against ``Matrix.rref()``.

Each result shown carries an ``id``; ``insert`` ``{id}`` puts that result in
place of the matrix it was computed from, as one step of the history (so
Undo takes it back).  A factorisation is inserted as the unevaluated product
of its factors, which equals the matrix.

Symbolic linear algebra can take for ever (eigenvectors of a symbolic 4×4,
say), so every computation runs under a time limit where Python has threads
(the kernel, the server, the apps): past it the computation is stopped and
the answer says so in words.  Pyodide has no threads; there the editor's own
Interrupt button is the limit.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import sympy
from sympy import Basic, ImmutableMatrix, MatMul, Symbol, cancel, eye, factor, latex, simplify
from sympy.matrices import MatrixBase
from sympy.matrices.expressions import MatrixExpr

from sympy_editor.addons import Addon
from sympy_editor.printer import format_path, parse_path

__all__ = ["LinalgAddon", "ADDON", "TimeLimit", "limited", "rref_steps", "find_matrix"]

STATIC = Path(__file__).parent / "static"

#: Seconds each kind of computation may take before it is stopped.  The
#: panel can ask for a longer one ("Try for longer"), up to MAX_LIMIT.
BASIC_LIMIT = 4.0
SPECTRUM_LIMIT = 8.0
DECOMPOSE_LIMIT = 8.0
MAX_LIMIT = 120.0
#: How many results ``insert`` remembers per document.
KEEP_RESULTS = 400
#: A result bigger than this (in operations) is described, not typeset.
MAX_SHOWN_OPS = 600
#: The variable of the characteristic polynomial (printed λ).
LAMBDA = Symbol("lamda")


class TimeLimit(Exception):
    """A computation was stopped after its time limit."""

    def __init__(self, seconds: float):
        super().__init__(f"stopped after {seconds:g} s")
        self.seconds = seconds


def _stop(ident: int) -> None:
    """Raise an exception in the thread ``ident`` (SymPy is pure Python: it
    arrives at the next bytecode)."""
    try:
        from sympy_editor.document import interrupt_thread
        interrupt_thread(ident)
    except Exception:
        pass


def limited(fn: Callable[[], Any], seconds: float) -> Any:
    """``fn()``, stopped with :class:`TimeLimit` after ``seconds``.

    The work runs on a thread of its own, joined in short slices so that an
    Interrupt aimed at the calling thread still lands; past the limit (or on
    any exception here) the worker is stopped too.  Where threads cannot be
    started (Pyodide) ``fn`` runs directly, with no limit."""
    box: Dict[str, Any] = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:          # the stop itself lands here too
            box["error"] = exc

    worker = threading.Thread(target=run, name="linalg", daemon=True)
    try:
        worker.start()
    except RuntimeError:                      # no threads (Pyodide)
        return fn()
    deadline = time.monotonic() + seconds
    try:
        while worker.is_alive() and time.monotonic() < deadline:
            worker.join(0.05)
    finally:
        if worker.is_alive():
            _stop(worker.ident)
    if worker.is_alive() or ("value" not in box and "error" not in box):
        raise TimeLimit(seconds)
    if "error" in box:
        raise box["error"]
    return box["value"]


def threads_available() -> bool:
    """Whether :func:`limited` can enforce a limit here."""
    try:
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()
        return True
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
# finding the matrix
# ---------------------------------------------------------------------------

def _explicit(node: Basic) -> Optional[ImmutableMatrix]:
    """``node`` as an explicit matrix, or None."""
    if isinstance(node, MatrixBase):
        return ImmutableMatrix(node)
    if isinstance(node, MatrixExpr):
        try:
            return ImmutableMatrix(node.as_explicit())
        except Exception:
            return None
    return None


def find_matrix(doc, path) -> Tuple[Tuple, Basic, ImmutableMatrix]:
    """The matrix to work on for a selection at ``path``: the innermost
    explicit matrix at the path or above it, else the innermost matrix
    expression that can be written out (a transpose of an explicit matrix, a
    matrix symbol of a numeric shape).  ``(path, node, explicit matrix)``."""
    p = parse_path(path) if isinstance(path, str) else tuple(path or ())
    nodes = []
    for k in range(len(p), -1, -1):
        try:
            nodes.append((p[:k], doc.get(p[:k])))
        except Exception:
            continue
    for q, node in nodes:
        if isinstance(node, MatrixBase):
            return q, node, ImmutableMatrix(node)
    for q, node in nodes:
        m = _explicit(node)
        if m is not None:
            return q, node, m
    raise ValueError("No matrix here: select an explicit matrix, an entry of one, or a formula that is one")


# ---------------------------------------------------------------------------
# row reduction, step by step
# ---------------------------------------------------------------------------

def _simp(e):
    try:
        return cancel(e)
    except Exception:
        return e


def _is_zero(e) -> Optional[bool]:
    """True, False, or None when it cannot be told (a symbol): such an entry
    is taken for non-zero, as ``Matrix.rref`` does, and the step says so."""
    z = e.is_zero
    if z is None:
        try:
            z = simplify(e).is_zero
        except Exception:
            z = None
    return z


def _row(i: int) -> Tuple[str, str]:
    return f"R{i + 1}", f"R_{{{i + 1}}}"


def _coef(c) -> Tuple[str, str]:
    """A factor in front of a row: ``3·``, ``(a + 1)·``, nothing for 1."""
    if c == 1:
        return "", ""
    text, tex = str(c), latex(c)
    if not (c.is_Integer and c > 0 or c.is_Symbol):
        text = f"({text})"
    if c.is_Add or c.could_extract_minus_sign():
        tex = f"\\left({tex}\\right)"
    return text + "·", tex + " "


def rref_steps(M) -> Dict[str, Any]:
    """Gauss-Jordan elimination of ``M``, recorded: ``{"steps": [{"op",
    "latex", "kind", "matrix", "assumes"}], "rref", "pivots", "assumed"}``.

    For each column in turn: the first row at or below the current one with
    an entry that is not zero there becomes the pivot row (swapped up when it
    is lower), is divided by its pivot, and the pivot's column is cleared in
    every other row.  An entry that cannot be decided zero or not (a symbol)
    is taken for non-zero - as ``Matrix.rref`` does - and listed in
    ``assumed`` and on the step that divides by it."""
    A = sympy.Matrix(M).applyfunc(_simp)
    rows, cols = A.shape
    steps: List[Dict[str, Any]] = []
    pivots: List[int] = []
    assumed: List[Basic] = []
    r = 0
    for c in range(cols):
        if r >= rows:
            break
        piv, unsure = None, False
        for i in range(r, rows):
            z = _is_zero(A[i, c])
            if z is False or z is None:
                piv, unsure = i, z is None
                break
        if piv is None:
            continue
        if piv != r:
            A.row_swap(r, piv)
            (a, at), (b, bt) = _row(r), _row(piv)
            steps.append({"kind": "swap", "op": f"{a} ↔ {b}", "latex": f"{at} \\leftrightarrow {bt}",
                          "matrix": ImmutableMatrix(A), "assumes": None})
        p = A[r, c]
        assumes = None
        if unsure:
            assumed.append(p)
            assumes = p
        if p != 1:
            A[r, :] = A[r, :].applyfunc(lambda e: _simp(e / p))
            a, at = _row(r)
            ct, ctex = _coef(_simp(1 / p))
            text, tex = f"{a} ← {ct}{a}", f"{at} \\leftarrow {ctex}{at}"
            steps.append({"kind": "scale", "op": text, "latex": tex, "matrix": ImmutableMatrix(A), "assumes": assumes})
        for i in range(rows):
            if i == r:
                continue
            f = A[i, c]
            if _is_zero(f) is True:
                continue
            A[i, :] = (A[i, :] - f * A[r, :]).applyfunc(_simp)
            a, at = _row(i)
            b, bt = _row(r)
            if f.could_extract_minus_sign():
                sign, g = "+", -f
            else:
                sign, g = "−", f
            ct, ctex = _coef(g)
            tsign = "+" if sign == "+" else "-"
            steps.append({"kind": "add", "op": f"{a} ← {a} {sign} {ct}{b}",
                          "latex": f"{at} \\leftarrow {at} {tsign} {ctex}{bt}",
                          "matrix": ImmutableMatrix(A), "assumes": None})
        pivots.append(c)
        r += 1
    return {"steps": steps, "rref": ImmutableMatrix(A), "pivots": pivots, "assumed": assumed}


def same_matrix(A, B) -> bool:
    """Whether two matrices are equal entry by entry (after simplifying the
    differences)."""
    if A.shape != B.shape:
        return False
    return all(simplify(a - b) == 0 for a, b in zip(A, B))


# ---------------------------------------------------------------------------
# the add-on
# ---------------------------------------------------------------------------

class Budget:
    """The time one method may take, shared by the computations it makes:
    a determinant that took three of the four seconds leaves one for the
    characteristic polynomial."""

    def __init__(self, seconds: float):
        self.total = seconds
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        return self.end - time.monotonic()


def _budget(payload: Dict[str, Any], default: float) -> Budget:
    """The method's time: ``limit`` from the panel ("try for longer"), or
    ``default``; between half a second and :data:`MAX_LIMIT`."""
    try:
        s = float(payload.get("limit") or default)
    except (TypeError, ValueError):
        s = default
    return Budget(max(0.5, min(s, MAX_LIMIT)))


def _size(value) -> int:
    """How big a result is, in SymPy's operations (summed over a matrix's
    entries and a product's factors)."""
    try:
        if isinstance(value, MatrixBase):
            return sum(sympy.count_ops(e) for e in value)
        if isinstance(value, MatMul):
            return sum(_size(f) for f in value.args)
        return int(sympy.count_ops(value))
    except Exception:
        return 0


def _unevaluated_product(*factors) -> Basic:
    return MatMul(*[ImmutableMatrix(f) for f in factors], evaluate=False)


class LinalgAddon(Addon):
    name = "linalg"
    label = "Linear algebra"
    requires = ()
    js = (STATIC / "linalg.js").read_text(encoding="utf-8")
    css = (STATIC / "linalg.css").read_text(encoding="utf-8")

    def client_options(self) -> Dict[str, Any]:
        return {"limits": {"basic": BASIC_LIMIT, "spectrum": SPECTRUM_LIMIT, "decompose": DECOMPOSE_LIMIT,
                           "max": MAX_LIMIT}}

    # -- results the panel can insert ----------------------------------------

    def _state(self, doc) -> Dict[str, Any]:
        st = doc.addon_state.setdefault(self.name, {})
        st.setdefault("results", {})
        st.setdefault("next", 1)
        return st

    def _item(self, doc, where: Tuple, node: Basic, label: str, value: Basic, insert: Optional[Basic] = None) -> Dict[str, Any]:
        """A result as the panel shows it: its LaTeX and text, and an id that
        ``insert`` takes (``insert`` is what goes in the formula, ``value``
        when not given)."""
        st = self._state(doc)
        rid = f"r{st['next']}"
        st["next"] += 1
        results = st["results"]
        results[rid] = (where, node, value if insert is None else insert, label)
        while len(results) > KEEP_RESULTS:
            results.pop(next(iter(results)))
        size = _size(value)
        if size > MAX_SHOWN_OPS:
            # The eigenvectors of a symbolic 4×4 are 70,000 characters each:
            # said, not shown - Insert still puts the whole of it in.
            return {"id": rid, "label": label, "long": size,
                    "latex": f"\\text{{a long expression ({size} operations)}}",
                    "src": f"a long expression ({size} operations)"}
        return {"id": rid, "label": label, "latex": latex(value), "src": str(value)}

    def _timed(self, fn, budget: Budget, what: str) -> Tuple[Any, Optional[Dict[str, Any]]]:
        """``(value, None)``, or ``(None, problem)`` when ``fn`` failed or was
        stopped - the problem in words for the panel (``timeout``: the
        method's whole time, which "try for longer" multiplies)."""
        left = budget.left()
        if left < 0.05:
            return None, {"what": what, "timeout": budget.total,
                          "text": f"{what}: not computed - the {budget.total:g} s were used up by what comes before it."}
        try:
            return limited(fn, left), None
        except TimeLimit:
            return None, {"what": what, "timeout": budget.total,
                          "text": f"{what}: stopped after {budget.total:g} s - SymPy did not finish in time "
                                  f"(symbolic entries can make this take very long). Simplify the entries, "
                                  f"or try for longer."}
        except Exception as exc:
            return None, {"what": what, "text": f"{what}: {exc}"}

    def _target(self, doc, payload):
        where, node, M = find_matrix(doc, payload.get("path") or "/")
        return where, node, M

    def _head(self, where, node, M) -> Dict[str, Any]:
        rows, cols = M.shape
        return {"path": format_path(where), "rows": int(rows), "cols": int(cols), "square": rows == cols,
                "explicit": isinstance(node, MatrixBase), "latex": latex(M), "src": str(node),
                "limited": threads_available()}

    # -- the methods -----------------------------------------------------------

    def analyse(self, doc, payload) -> Dict[str, Any]:
        where, node, M = self._target(doc, payload)
        out = self._head(where, node, M)
        secs = _budget(payload, BASIC_LIMIT)
        items, problems = [], []

        def add(label, fn, insert=True):
            value, problem = self._timed(fn, secs, label)
            if problem:
                problems.append(problem)
                return None
            item = self._item(doc, where, node, label, value)
            item["insertable"] = insert
            items.append(item)
            return value

        add("Rank", lambda: sympy.Integer(M.rank(simplify=True)))
        if M.rows == M.cols:
            add("Determinant", lambda: M.det())
            add("Trace", lambda: M.trace())
            poly = add("Characteristic polynomial", lambda: M.charpoly(LAMBDA).as_expr())
            if poly is not None:
                factored, problem = self._timed(lambda: factor(poly), secs, "Factored")
                if factored is not None and factored != poly:
                    item = self._item(doc, where, node, "… factored", factored)
                    item["insertable"] = True
                    items.append(item)
        out["items"] = items
        out["problems"] = problems
        return out

    def spectrum(self, doc, payload) -> Dict[str, Any]:
        where, node, M = self._target(doc, payload)
        out = self._head(where, node, M)
        if M.rows != M.cols:
            raise ValueError("Eigenvalues need a square matrix")
        secs = _budget(payload, SPECTRUM_LIMIT)
        problems = []
        # The eigenvalues first (the roots of the characteristic polynomial):
        # when the vectors do not come in time, the values still show.
        vals, problem = self._timed(lambda: M.eigenvals(), secs, "Eigenvalues")
        vects = None
        if problem:
            problems.append(problem)
        else:
            vects, problem = self._timed(lambda: M.eigenvects(), secs, "Eigenvectors")
            if problem:
                problems.append(problem)
        eigen = []
        if vects is None:
            for val, alg in (vals or {}).items():
                eigen.append({"value": self._item(doc, where, node, "Eigenvalue", val), "alg": int(alg),
                              "geo": None, "vectors": []})
        else:
            for val, alg, vecs in vects:
                eigen.append({"value": self._item(doc, where, node, "Eigenvalue", val), "alg": int(alg),
                              "geo": len(vecs),
                              "vectors": [self._item(doc, where, node, "Eigenvector", ImmutableMatrix(v)) for v in vecs]})
        out["eigen"] = eigen
        out["diagonalizable"] = (bool(eigen) and all(e["alg"] == e["geo"] for e in eigen)) if vects is not None else None
        if out["diagonalizable"]:
            # The Jordan form is the diagonal of the eigenvalues and P the
            # eigenvectors side by side: no need for jordan_form, which
            # recomputes all of it (and took seconds for a cubic's roots).
            def diagonal():
                cols = [v for val, alg, vecs in vects for v in vecs]
                P = ImmutableMatrix(sympy.Matrix.hstack(*cols))
                J = ImmutableMatrix(sympy.diag(*[val for val, alg, vecs in vects for v in vecs]))
                return P, J
            work = diagonal
        else:
            work = lambda: self._jordan(M)                     # noqa: E731
        jordan, problem = self._timed(work, secs, "Jordan form")
        if problem:
            problems.append(problem)
        else:
            P, J = jordan
            out["jordan"] = {"J": self._item(doc, where, node, "Jordan form J", J),
                             "P": self._item(doc, where, node, "Change of basis P", P)}
            # the inverse is what is slow with radicals in P: on its own, so
            # that J and P come anyway
            Pinv, problem = self._timed(lambda: ImmutableMatrix(P.inv()), secs, "P⁻¹ (for M = P J P⁻¹)")
            if problem:
                problems.append(problem)
            else:
                out["jordan"]["product"] = self._item(doc, where, node, "P J P⁻¹", _unevaluated_product(P, J, Pinv))
            if out["diagonalizable"] is None:
                out["diagonalizable"] = J.is_diagonal()
        out["problems"] = problems
        return out

    @staticmethod
    def _jordan(M):
        P, J = M.jordan_form()
        return ImmutableMatrix(P), ImmutableMatrix(J)

    def decompose(self, doc, payload) -> Dict[str, Any]:
        where, node, M = self._target(doc, payload)
        out = self._head(where, node, M)
        kind = str(payload.get("kind") or "")
        secs = _budget(payload, DECOMPOSE_LIMIT)
        fn = {"lu": self._lu, "qr": self._qr, "cholesky": self._cholesky}.get(kind)
        if fn is None:
            raise ValueError(f"No decomposition {kind!r} (lu, qr, cholesky)")
        res, problem = self._timed(lambda: fn(M), secs, {"lu": "LU", "qr": "QR", "cholesky": "Cholesky"}[kind])
        out["kind"] = kind
        if problem:
            out["problems"] = [problem]
            return out
        factors, relation, note = res
        out["factors"] = [self._item(doc, where, node, name, f) for name, f in factors]
        out["relation"] = relation
        out["note"] = note
        out["product"] = self._item(doc, where, node, relation, _unevaluated_product(*[f for name, f in factors]))
        out["problems"] = []
        return out

    @staticmethod
    def _lu(M):
        L, U, perm = M.LUdecomposition()
        if not perm:
            return [("L", L), ("U", U)], "L U", "L is lower triangular with ones on its diagonal, U upper triangular."
        P = eye(M.rows).as_mutable()
        for i, j in perm:
            P.row_swap(i, j)
        # P M = L U, so M = Pᵀ L U
        swaps = ", ".join(f"R{i + 1} ↔ R{j + 1}" for i, j in perm)
        return ([("Pᵀ", ImmutableMatrix(P.T)), ("L", L), ("U", U)], "Pᵀ L U",
                f"Rows had to be exchanged ({swaps}): P M = L U, with P the permutation.")

    @staticmethod
    def _qr(M):
        Q, R = M.QRdecomposition()
        return [("Q", Q), ("R", R)], "Q R", "Q has orthonormal columns, R is upper triangular."

    @staticmethod
    def _cholesky(M):
        if M.rows != M.cols:
            raise ValueError("not applicable: the matrix is not square")
        herm = M.is_hermitian
        if herm is False:
            if M.is_symmetric() is not True:
                raise ValueError("not applicable: the matrix is not Hermitian (nor symmetric)")
        if herm is True:
            L = M.cholesky(hermitian=True)
            return [("L", L), ("Lᴴ", ImmutableMatrix(L.H))], "L Lᴴ", "M = L Lᴴ, L lower triangular."
        # symbolic, symmetric: the real-symmetric form, under the assumption
        # that every pivot is positive
        L = M.cholesky(hermitian=False)
        return ([("L", L), ("Lᵀ", ImmutableMatrix(L.T))], "L Lᵀ",
                "The entries are symbolic: this is the symmetric form, valid where the matrix is positive definite.")

    def rref(self, doc, payload) -> Dict[str, Any]:
        where, node, M = self._target(doc, payload)
        out = self._head(where, node, M)
        secs = _budget(payload, DECOMPOSE_LIMIT)

        def work():
            rec = rref_steps(M)
            R, piv = M.rref(simplify=True)
            rec["check"] = same_matrix(rec["rref"], R) and list(piv) == rec["pivots"]
            return rec

        rec, problem = self._timed(work, secs, "Row reduction")
        if problem:
            out["problems"] = [problem]
            return out
        out["steps"] = []
        for s in rec["steps"]:
            item = self._item(doc, where, node, s["op"], s["matrix"])
            item.update({"op": s["op"], "oplatex": s["latex"], "kind": s["kind"],
                         "assumes": None if s["assumes"] is None else latex(s["assumes"])})
            out["steps"].append(item)
        out["result"] = self._item(doc, where, node, "Reduced row echelon form", rec["rref"])
        out["pivots"] = [c + 1 for c in rec["pivots"]]
        out["assumed"] = [latex(a) for a in rec["assumed"]]
        out["check"] = rec["check"]
        out["problems"] = []
        return out

    def insert(self, doc, payload) -> Basic:
        rid = str(payload.get("id") or "")
        st = self._state(doc)
        if rid not in st["results"]:
            raise ValueError("That result is no longer known: compute it again")
        where, node, value, label = st["results"][rid]
        try:
            current = doc.get(where)
        except Exception:
            current = None
        if current != node:
            raise ValueError("The matrix has changed since this was computed: compute it again")
        replace = getattr(doc, "_replace_at", None)
        if replace is not None:
            return replace(doc.expr, where, value)
        from sympy_editor.printer import replace_at
        return replace_at(doc.expr, where, value, getattr(doc, "printer_settings", None))

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method in ("analyse", "spectrum", "decompose", "rref", "insert"):
            return getattr(self, method)(doc, payload)
        raise ValueError(f"The linear algebra add-on has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]):
        if method == "insert":
            what = str(payload.get("what") or "result")
            return f"Linear algebra: {what}"
        return None


ADDON = LinalgAddon()

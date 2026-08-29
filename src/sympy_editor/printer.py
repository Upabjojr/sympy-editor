"""Annotated LaTeX printer.

:class:`AnnotatedLatexPrinter` is a :class:`sympy.printing.latex.LatexPrinter`
that wraps the LaTeX of every printed sub-expression in an annotation
(``\\htmlData{path=/0/1}{...}`` by default).  KaTeX turns the annotation into a
``<span data-path="/0/1">`` element, so the rendered HTML carries, for every
visible piece, the position of the corresponding node in the SymPy tree.

Paths are tuples of ``args`` indices: ``()`` is the root, ``(1, 0)`` is
``expr.args[1].args[0]``.  Their string form is ``"/"``, ``"/1/0"``.

How the mapping is found
------------------------
The SymPy printer does not always print the tree it is given: ``x - y`` is
``Add(x, Mul(-1, y))`` but the printer prints ``-y`` as ``- y`` by negating the
term, ``x/y**2`` is ``Mul(x, Pow(y, -2))`` but the printer synthesises
``Pow(y, 2)`` for the denominator, and so on.  Instead of tracking ``args``
indices while printing, the printer keeps a stack of *frames* (the real tree
nodes currently being printed) and, each time ``_print`` is called, searches
the sub-tree of the innermost frame (breadth-first, up to ``max_depth``) for an
unclaimed node structurally equal to the object being printed.  Synthesised
objects that are not found are printed unannotated, but their children are
still located relative to the enclosing real frame - with one exception: a
denominator ``Pow(b, n)`` synthesised from the tree's ``Pow(b, -n)`` is
annotated with the path of that real node, so a denominator raised to a power
can be selected and edited as a whole (the document treats such a node as the
reciprocal of what is printed; see ``Document.snapshot``).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple
from typing import Union as TUnion

from sympy import Integer, Rational, sympify
from sympy.core.basic import Basic
from sympy.core.containers import Tuple as SymTuple
from sympy.core.power import Pow
from sympy.functions.elementary.piecewise import ExprCondPair
from sympy.matrices.expressions.blockmatrix import BlockMatrix
from sympy.matrices.expressions.matadd import MatAdd
from sympy.matrices.expressions.matmul import MatMul
from sympy.core.function import AppliedUndef
from sympy.core.operations import AssocOp, LatticeOp
from sympy.sets.sets import FiniteSet, Intersection, Union
from sympy.printing.latex import LatexPrinter, latex
from sympy.printing.str import StrPrinter

#: Path elements are ``args`` indices; ``"n"`` and ``"d"`` are the numerator
#: and denominator of a ``Rational`` (an atom in SymPy, printed as a fraction).
Path = Tuple[TUnion[int, str], ...]
RATIONAL_PARTS = ("n", "d")

__all__ = [
    "AnnotatedLatexPrinter",
    "AnnotatedStrPrinter",
    "annotate",
    "annotate_str",
    "latex_spans",
    "spans_from_marked",
    "strip_annotations",
    "format_path",
    "parse_path",
    "get_at",
    "replace_at",
    "delete_at",
    "insert_at",
    "is_insertable",
    "is_rangeable",
    "extract_range",
    "replace_range",
    "delete_range",
    "rebuild",
]


# --------------------------------------------------------------------------
# Path helpers
# --------------------------------------------------------------------------

def format_path(path: Path) -> str:
    """``()`` -> ``"/"``, ``(0, 1)`` -> ``"/0/1"``."""
    return "/" + "/".join(str(i) for i in path)


def parse_path(text: str) -> Path:
    """Inverse of :func:`format_path`."""
    text = (text or "").strip()
    if text in ("", "/"):
        return ()
    try:
        return tuple(part if part in RATIONAL_PARTS else int(part) for part in text.strip("/").split("/"))
    except ValueError:
        raise ValueError(f"Invalid path: {text!r}") from None


def _is_fraction(node) -> bool:
    """A ``Rational`` that prints as a fraction (an ``Integer`` is a ``Rational`` too)."""
    return isinstance(node, Rational) and node.q != 1


def get_at(expr: Basic, path: Path) -> Basic:
    """Return the sub-expression of ``expr`` at ``path``."""
    node = expr
    for i in path:
        if i in RATIONAL_PARTS:
            if not _is_fraction(node):
                raise ValueError(f"Invalid path {format_path(path)} for {expr}")
            node = Integer(node.p if i == "n" else node.q)
            continue
        try:
            node = node.args[i]
        except (IndexError, AttributeError, TypeError):
            raise ValueError(f"Invalid path {format_path(path)} for {expr}") from None
    return node


def rebuild(expr: Basic, args) -> Basic:
    """``expr`` reconstructed with new ``args`` (``expr.func(*args)``, with
    special cases for classes whose constructor does not accept their own
    ``args``)."""
    args = list(args)
    if isinstance(expr, BlockMatrix):
        # BlockMatrix stores a matrix of blocks but is constructed from rows.
        return BlockMatrix(args[0].tolist())
    if isinstance(expr, (MatMul, MatAdd)):
        # Matrix arithmetic is canonicalised by the operators (A*A -> A**2),
        # not by the constructors; doit(deep=False) does the same at this level.
        return expr.func(*args).doit(deep=False)
    return expr.func(*args)


def replace_at(expr: Basic, path: Path, new: Basic) -> Basic:
    """Return ``expr`` with the node at ``path`` replaced by ``new``.

    Ancestors are rebuilt with :func:`rebuild` (normally ``node.func(*args)``),
    so SymPy's automatic evaluation applies (``x + (-x)`` becomes ``0``, etc.).
    """
    if not path:
        return new
    i = path[0]
    if i in RATIONAL_PARTS:
        # The numerator or denominator of a number: the number is rebuilt
        # around the new part (x over 2 for a numerator x, say).
        if len(path) != 1 or not _is_fraction(expr):
            raise ValueError(f"Invalid path {format_path(path)} for {expr}")
        return sympify(new) / Integer(expr.q) if i == "n" else Integer(expr.p) / sympify(new)
    args = list(expr.args)
    if not isinstance(i, int) or not 0 <= i < len(args):
        raise ValueError(f"Invalid path {format_path(path)} for {expr}")
    args[i] = replace_at(args[i], path[1:], new)
    return rebuild(expr, args)


def insert_at(expr: Basic, path: Path, index: int, new: Basic) -> Basic:
    """Return ``expr`` with ``new`` inserted at position ``index`` of the
    arguments of the node at ``path``."""
    parent = get_at(expr, path)
    args = list(parent.args)
    if not 0 <= index <= len(args):
        raise ValueError(f"Invalid insertion index {index} for {parent}")
    args.insert(index, new)
    return replace_at(expr, path, rebuild(parent, args))


#: Node types whose argument list can be extended by the editor ("insert a
#: term here").  Commutative ones (Add, Mul, ...) re-order their arguments.
INSERTABLE = (AssocOp, LatticeOp, MatAdd, MatMul, FiniteSet, Union, Intersection, AppliedUndef)


def is_insertable(node: Basic) -> bool:
    return isinstance(node, INSERTABLE)


#: Node types in which a *range* of adjacent arguments can be selected and
#: acted on as a sub-expression (``b + c`` inside ``a + b + c + d``).
RANGEABLE = (AssocOp, LatticeOp)


def is_rangeable(node: Basic) -> bool:
    return isinstance(node, RANGEABLE)


def _range_indices(parent: Basic, indices) -> List[int]:
    idx = sorted(set(int(i) for i in indices))
    if not idx or idx[0] < 0 or idx[-1] >= len(parent.args):
        raise ValueError(f"Invalid argument range {list(indices)} for {parent}")
    return idx


def extract_range(expr: Basic, path: Path, indices) -> Basic:
    """The sub-expression formed by arguments ``indices`` of the node at
    ``path`` (``Add(b, c)`` for a range of two terms)."""
    parent = get_at(expr, path)
    idx = _range_indices(parent, indices)
    if len(idx) == 1:
        return parent.args[idx[0]]
    return rebuild(parent, [parent.args[i] for i in idx])


def replace_range(expr: Basic, path: Path, indices, new: Basic) -> Basic:
    """``expr`` with arguments ``indices`` of the node at ``path`` replaced by
    the single argument ``new`` (at the position of the first of them)."""
    parent = get_at(expr, path)
    idx = _range_indices(parent, indices)
    args = [a for i, a in enumerate(parent.args) if i not in idx]
    args.insert(idx[0], new)
    return replace_at(expr, path, rebuild(parent, args))


def delete_range(expr: Basic, path: Path, indices) -> Basic:
    """``expr`` without arguments ``indices`` of the node at ``path``."""
    parent = get_at(expr, path)
    idx = _range_indices(parent, indices)
    return replace_at(expr, path, rebuild(parent, [a for i, a in enumerate(parent.args) if i not in idx]))


def delete_at(expr: Basic, path: Path) -> Basic:
    """Return ``expr`` with the node at ``path`` removed from its parent's args."""
    if not path:
        raise ValueError("Cannot delete the root expression")
    if path[-1] in RATIONAL_PARTS:
        raise ValueError("The numerator or denominator of a number cannot be removed: edit it, or delete the number")
    parent = get_at(expr, path[:-1])
    args = list(parent.args)
    del args[path[-1]]
    return replace_at(expr, path[:-1], rebuild(parent, args))


# --------------------------------------------------------------------------
# Printer
# --------------------------------------------------------------------------

#: Containers whose children the SymPy printer accesses directly (integration
#: limits, matrix elements, piecewise branches...).  They do not count towards
#: the search depth and their children are searched before ordinary siblings.
TRANSPARENT = (SymTuple, ExprCondPair)


class _Frame:
    """A real tree node currently being printed, with a lazily built index of
    its descendants (up to ``max_depth``) keyed by structural equality, each
    entry listing ``(path, node)`` pairs in search order."""

    __slots__ = ("expr", "path", "is_root", "_index")

    def __init__(self, expr: Basic, path: Path, is_root: bool = False):
        self.expr = expr
        self.path = path
        self.is_root = is_root
        self._index: Optional[Dict[Basic, List[Tuple[Path, Basic]]]] = None

    @staticmethod
    def _expand(path: Path, node, out: List[Tuple[Path, Basic]]) -> None:
        transparent, plain = [], []
        for i, arg in enumerate(getattr(node, "args", ())):
            (transparent if isinstance(arg, TRANSPARENT) else plain).append((path + (i,), arg))
        for p, arg in transparent:
            out.append((p, arg))
            _Frame._expand(p, arg, out)
        out.extend(plain)

    def candidates(self, expr: Basic, max_depth: int) -> List[Tuple[Path, Basic]]:
        if self._index is None:
            index: Dict[Basic, List[Tuple[Path, Basic]]] = {}
            level: List[Tuple[Path, Basic]] = [(self.path, self.expr)]
            entries: List[Tuple[Path, Basic]] = list(level) if self.is_root else []
            for _ in range(max_depth):
                nxt: List[Tuple[Path, Basic]] = []
                for path, node in level:
                    self._expand(path, node, nxt)
                entries.extend(nxt)
                level = [(p, n) for p, n in nxt if not isinstance(n, TRANSPARENT)]
            for path, node in entries:
                try:
                    index.setdefault(node, []).append((path, node))
                except TypeError:  # unhashable (e.g. mutable matrix as root)
                    pass
            self._index = index
        return self._index.get(expr, [])


class _AnnotatingMixin:
    """Shared by the annotated LaTeX and str printers: locating the tree node
    being printed and wrapping its output (``wrap``).

    Use :meth:`annotate` rather than ``doprint``; without a prior call to
    ``annotate`` the printer behaves exactly like its SymPy base class.
    """

    #: How deep below the innermost real frame to look for the printed object.
    max_depth = 3

    #: ``(path_string, tex) -> annotated tex``.  The default emits KaTeX's
    #: ``\htmlData`` (requires the ``trust`` option in KaTeX).
    wrap: Callable[[str, str], str] = staticmethod(
        lambda path, tex: r"\htmlData{path=%s}{%s}" % (path, tex)
    )

    def __init__(self, settings=None):
        super().__init__(settings)
        self._stack: Optional[List[_Frame]] = None
        self._claimed: set = set()
        self._nodes: Dict[Path, Basic] = {}

    # -- public API ---------------------------------------------------------

    def annotate(self, expr: Basic) -> Tuple[str, Dict[Path, Basic]]:
        """Return ``(latex, nodes)`` where ``nodes`` maps each annotated path
        to the corresponding sub-expression."""
        self._stack = [_Frame(expr, (), is_root=True)]
        self._claimed = set()
        self._nodes = {}
        try:
            tex = self.doprint(expr)
            return tex, dict(self._nodes)
        finally:
            self._stack = None
            self._claimed = set()
            self._nodes = {}

    # -- internals ----------------------------------------------------------

    def _locate(self, expr: Basic) -> Optional[Tuple[Path, Basic]]:
        """The ``(path, node)`` of the unclaimed tree node that ``expr`` prints,
        or None.  ``node`` is ``expr`` itself except for a denominator, where
        the printer builds ``Pow(b, n)`` to print the tree's ``Pow(b, -n)``
        under the fraction bar: that real node is claimed for it, and it is
        the real node whose children are then searched, so the printed
        exponent ``n`` is not mistaken for the tree's ``-n``."""
        if not self._stack:
            return None
        frame = self._stack[-1]
        for path, node in frame.candidates(expr, self.max_depth):
            if path in self._claimed:
                continue
            if node is expr or (type(node) is type(expr) and node == expr):
                self._claimed.add(path)
                return path, node
        if isinstance(expr, Pow):
            inverse = Pow(expr.base, -expr.exp, evaluate=False)
            for path, node in frame.candidates(inverse, self.max_depth):
                if path in self._claimed:
                    continue
                if isinstance(node, Pow) and node.base == expr.base and node.exp == -expr.exp:
                    self._claimed.add(path)
                    return path, node
        return None

    def _annotated(self, expr: Basic, path: Path, tex: str) -> str:
        self._nodes[path] = expr
        return self.wrap(format_path(path), tex)

    def _rational_parts(self, expr):
        """``(node, path)`` when ``expr`` is the fraction printed for the
        innermost frame's node (that node, or its negation - a sum prints a
        negative term as ``- p/q``), else ``(None, None)``.  The parts are
        annotated once per node."""
        frame = self._stack[-1] if self._stack else None
        if frame is None or not _is_fraction(expr) or not _is_fraction(frame.expr):
            return None, None
        node = frame.expr
        if node.q != expr.q or abs(node.p) != abs(expr.p) or frame.path + ("n",) in self._nodes:
            return None, None
        return node, frame.path

    def _print(self, expr, **kwargs):
        if self._stack is None or not isinstance(expr, Basic):
            return super()._print(expr, **kwargs)
        found = self._locate(expr)
        if found is None:
            return super()._print(expr, **kwargs)
        path, node = found
        self._stack.append(_Frame(node, path))
        try:
            tex = super()._print(expr, **kwargs)
        finally:
            self._stack.pop()
        return self._annotated(expr, path, tex)

class AnnotatedLatexPrinter(_AnnotatingMixin, LatexPrinter):
    """LaTeX printer that annotates every printed sub-expression with its
    path (KaTeX ``\\htmlData``)."""

    def _print_Add(self, expr, order=None):
        # Same as LatexPrinter._print_Add, except that a term printed as
        # ``- (negated term)`` is annotated with the path of the original
        # (negative) term, sign included.
        terms = self._as_ordered_terms(expr, order=order)
        tex = ""
        for i, term in enumerate(terms):
            if i == 0:
                tex += self._print_add_term(term)
            elif term.could_extract_minus_sign():
                found = self._locate(term) if self._stack is not None else None
                if found is None:
                    tex += " - " + self._print_add_term(-term)
                else:
                    path, node = found
                    self._stack.append(_Frame(node, path))
                    try:
                        inner = self._print_add_term(-term)
                    finally:
                        self._stack.pop()
                    tex += " " + self._annotated(term, path, "- " + inner)
            else:
                tex += " + " + self._print_add_term(term)
        return tex

    def _print_add_term(self, term):
        term_tex = self._print(term)
        if self._needs_add_brackets(term):
            term_tex = r"\left(%s\right)" % term_tex
        return term_tex

    def _print_Rational(self, expr):
        # LatexPrinter._print_Rational, with the numerator and denominator
        # annotated (paths "n" and "d" under the number's own path).
        node, path = self._rational_parts(expr)
        if node is None:
            return super()._print_Rational(expr)
        sign, p = ("- ", -expr.p) if expr.p < 0 else ("", expr.p)
        num = self._annotated(Integer(node.p), path + ("n",), str(p))
        den = self._annotated(Integer(node.q), path + ("d",), str(expr.q))
        if self._settings["fold_short_frac"]:
            return r"%s%s / %s" % (sign, num, den)
        return r"%s\frac{%s}{%s}" % (sign, num, den)


MARK_START, MARK_SEP, MARK_END = "\x01", "\x02", "\x03"


class AnnotatedStrPrinter(_AnnotatingMixin, StrPrinter):
    """``str()`` printer whose output carries markers around every printed
    sub-expression, from which :func:`spans_from_marked` computes character
    spans (used to link the source line to the rendering)."""

    wrap = staticmethod(lambda path, text: MARK_START + path + MARK_SEP + text + MARK_END)

    def _print_Rational(self, expr):
        node, path = self._rational_parts(expr)
        if node is None or self._settings.get("sympy_integers", False):
            return super()._print_Rational(expr)
        num = self._annotated(Integer(node.p), path + ("n",), str(expr.p))
        den = self._annotated(Integer(node.q), path + ("d",), str(expr.q))
        return num + "/" + den

    @staticmethod
    def _strip_minus(text: str):
        """``(text without its leading "-", True)`` when the printed term
        starts with a minus - possibly inside (nested) wrappers, as for the
        numerator of an annotated ``-1/2`` - else ``(text, False)``."""
        i = 0
        while text.startswith(MARK_START, i):
            i = text.index(MARK_SEP, i) + 1
        if i < len(text) and text[i] == "-":
            return text[:i] + text[i + 1:], True
        return text, False

    @staticmethod
    def _unwrap(text: str):
        """``(path, inner)`` when ``text`` is exactly one marker pair, else None."""
        if not (text.startswith(MARK_START) and text.endswith(MARK_END)):
            return None
        sep = text.find(MARK_SEP)
        if sep < 0:
            return None
        inner = text[sep + 1:-1]
        depth = 0
        for ch in inner:            # the closing marker must be the outer one
            if ch == MARK_START:
                depth += 1
            elif ch == MARK_END:
                depth -= 1
                if depth < 0:
                    return None
        return text[1:sep], inner

    def _print_Add(self, expr, order=None):
        # StrPrinter._print_Add, with the sign taken from inside the wrapper.
        from sympy.core.expr import UnevaluatedExpr
        from sympy.printing.precedence import precedence
        terms = self._as_ordered_terms(expr, order=order)
        is_add = lambda e: e.is_Add or (isinstance(e, UnevaluatedExpr) and e.args[0].is_Add)
        prec = precedence(expr)
        parts = []
        for term in terms:
            t = self._print(term)
            sign = "+"
            if not is_add(term):
                t, negative = self._strip_minus(t)
                if negative:
                    sign = "-"
            if precedence(term) < prec or is_add(term):
                parts.extend([sign, "(%s)" % t])
            else:
                parts.extend([sign, t])
        sign = parts.pop(0)
        if sign == "+":
            sign = ""
        return sign + " ".join(parts)


def _annotated_matrix_str(printer, expr) -> str:
    """``str()`` of a matrix is ``DenseMatrix.__str__``: ``Matrix(<list of
    rows>)`` on one line (not the printer's aligned table), reproduced here
    with annotated elements."""
    from sympy import S
    if S.Zero in expr.shape:
        return "Matrix(%s, %s, [])" % (expr.rows, expr.cols)
    rows = ["[" + ", ".join(printer._print(expr[i, j]) for j in range(expr.cols)) + "]" for i in range(expr.rows)]
    return "Matrix([%s])" % ", ".join(rows)


AnnotatedStrPrinter._print_MatrixBase = lambda self, expr: _annotated_matrix_str(self, expr)


def spans_from_marked(marked: str) -> Tuple[str, Dict[str, Tuple[int, int]]]:
    """Strip the markers of :class:`AnnotatedStrPrinter` output; return the
    plain text and ``{path string: (start, end)}`` character spans."""
    out: List[str] = []
    length = 0
    stack: List[Tuple[str, int]] = []
    spans: Dict[str, Tuple[int, int]] = {}
    i, n = 0, len(marked)
    while i < n:
        ch = marked[i]
        if ch == MARK_START:
            j = marked.index(MARK_SEP, i)
            stack.append((marked[i + 1:j], length))
            i = j + 1
            continue
        if ch == MARK_END:
            path, start = stack.pop()
            spans.setdefault(path, (start, length))
            i += 1
            continue
        out.append(ch)
        length += 1
        i += 1
    return "".join(out), spans


def latex_spans(expr: Basic, **settings) -> Tuple[str, Dict[str, Tuple[int, int]]]:
    """``(latex(expr), spans)``: the character span of every sub-expression in
    the LaTeX string, keyed by path string - the same keys as
    :func:`annotate_str`, so LaTeX and Python source correspond node by node."""
    if not isinstance(expr, Basic):
        expr = sympify(expr)
    printer = AnnotatedLatexPrinter(dict(settings, mode="plain"))
    printer.wrap = AnnotatedStrPrinter.wrap
    marked, _nodes = printer.annotate(expr)
    text, spans = spans_from_marked(marked)
    plain = latex(expr, **dict(settings, mode="plain"))
    return plain, (spans if text == plain else {})


def annotate_str(expr: Basic) -> Tuple[str, Dict[str, Tuple[int, int]]]:
    """``(str(expr), spans)``: the character span of every sub-expression in
    ``str(expr)``.  Spans are empty when the annotated output would not
    match ``str(expr)`` exactly (never seen, but the guarantee is checked)."""
    if not isinstance(expr, Basic):
        expr = sympify(expr)
    marked, _nodes = AnnotatedStrPrinter().annotate(expr)
    text, spans = spans_from_marked(marked)
    plain = str(expr)
    return plain, (spans if text == plain else {})


def annotate(expr: Basic, **settings) -> Tuple[str, Dict[Path, Basic]]:
    """Return ``(latex, nodes)`` for ``expr``.

    ``settings`` are :func:`sympy.latex` settings (``mul_symbol``, ``order``,
    ...); ``mode`` is forced to ``"plain"``.
    """
    settings = dict(settings, mode="plain")
    if not isinstance(expr, Basic):
        expr = sympify(expr)
    return AnnotatedLatexPrinter(settings).annotate(expr)


def plain_latex(expr: Basic, **settings) -> str:
    """The un-annotated LaTeX for ``expr`` with the same settings."""
    settings = dict(settings, mode="plain")
    return latex(expr, **settings)


def strip_annotations(tex: str, command: str = r"\htmlData") -> str:
    """Remove ``\\htmlData{...}{body}`` wrappers, keeping ``body``."""
    out = []
    i = 0
    n = len(tex)
    while i < n:
        j = tex.find(command + "{", i)
        if j < 0:
            out.append(tex[i:])
            break
        out.append(tex[i:j])
        k = _match_brace(tex, j + len(command))       # end of first argument
        if k < 0 or k + 1 >= n or tex[k + 1] != "{":
            out.append(tex[j:j + len(command)])
            i = j + len(command)
            continue
        end = _match_brace(tex, k + 1)                # end of body
        if end < 0:
            out.append(tex[j:])
            break
        out.append(strip_annotations(tex[k + 2:end], command))
        i = end + 1
    return "".join(out)


def _match_brace(tex: str, start: int) -> int:
    """Index of the ``}`` matching the ``{`` at ``start`` (or -1)."""
    depth = 0
    i = start
    while i < len(tex):
        c = tex[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1

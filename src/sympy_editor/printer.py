"""Annotated LaTeX printer.

:class:`AnnotatedLatexPrinter` is a :class:`sympy.printing.latex.LatexPrinter`
that wraps the LaTeX of every printed sub-expression in an annotation
(``\\htmlData{path=/0/1}{...}`` by default).  KaTeX turns the annotation into a
``<span data-path="/0/1">`` element, so the rendered HTML carries, for every
visible piece, the position of the corresponding node in the SymPy tree.

Paths are tuples of steps: ``()`` is the root, ``(1, 0)`` is
``expr.args[1].args[0]``.  Their string form is ``"/"``, ``"/1/0"``.

The view tree
-------------
The SymPy printer does not always print the tree it is given: ``1/n`` is
``Pow(n, -1)`` but is shown as a fraction with a numerator ``1`` that exists
nowhere in the tree, ``1/(2e)`` is ``Mul(1/2, exp(-1))`` but is shown as
``1`` over ``2 e``, ``x - y`` is ``Add(x, Mul(-1, y))`` but the printer shows
``- y`` by negating the term.  The editor works on what is shown, so paths
address the *view tree*: the SymPy tree with, wherever the printer shows
something else than a node's arguments, *virtual parts* in place of those
arguments (:func:`view_parts`):

``n``, ``d``
    the numerator and denominator of whatever is shown as a fraction - a
    ``Rational``, a ``Mul`` with negative powers or a rational coefficient, a
    ``Pow`` with a negative exponent (the split is the printer's, via
    :func:`sympy.simplify.radsimp.fraction`);
``neg``
    the negated product shown after the leading minus of a ``Mul`` (or
    ``MatMul``) that ``could_extract_minus_sign``.

Parts are values computed from the node (``1`` and ``n`` for ``1/n``), so
:func:`get_at` reads them and :func:`replace_at` writes them back by rebuilding
the node around the new part (a numerator ``x`` in place of ``1`` gives
``x/n``).  The virtual parts of a node hide its real arguments, which are still
addressable for printers that do print them (the source-line printer prints
``exp(-1)/2`` for ``1/(2e)``).

How the mapping is found
------------------------
Instead of tracking the tree while printing, the printer keeps a stack of
*frames* (the view-tree nodes currently being printed) and, each time
``_print`` is called, searches the view sub-tree of the innermost frame
(breadth-first, up to ``max_depth``; virtual parts before real arguments) for
an unclaimed node structurally equal to the object being printed.  Synthesised
objects that are not found are printed unannotated, but their children are
still located relative to the enclosing frame.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple
from typing import Union as TUnion

from sympy import Integer, Mul, Rational, S, sympify
from sympy.core.basic import Basic
from sympy.core.containers import Tuple as SymTuple
from sympy.core.numbers import Number
from sympy.core.power import Pow
from sympy.simplify.radsimp import fraction
from sympy.functions.elementary.piecewise import ExprCondPair
from sympy.matrices.expressions.blockmatrix import BlockMatrix
from sympy.matrices.expressions.matadd import MatAdd
from sympy.matrices.expressions.matmul import MatMul
from sympy.core.function import AppliedUndef
from sympy.core.operations import AssocOp, LatticeOp
from sympy.sets.sets import FiniteSet, Intersection, Union
from sympy.printing.latex import LatexPrinter, latex
from sympy.printing.str import StrPrinter

#: Path steps are ``args`` indices or the names of virtual parts (see
#: :func:`view_parts`): ``"n"``/``"d"`` are the numerator and denominator of
#: what is shown as a fraction, ``"neg"`` the product after a leading minus.
Path = Tuple[TUnion[int, str], ...]
PARTS = ("n", "d", "neg")
RATIONAL_PARTS = ("n", "d")
Settings = Optional[Dict[str, Any]]

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
    "view_parts",
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
        return tuple(part if part in PARTS else int(part) for part in text.strip("/").split("/"))
    except ValueError:
        raise ValueError(f"Invalid path: {text!r}") from None


def _is_fraction(node) -> bool:
    """A ``Rational`` that prints as a fraction (an ``Integer`` is a ``Rational`` too)."""
    return isinstance(node, Rational) and node.q != 1


def _pow_as_fraction(node: Pow, settings: Dict[str, Any]) -> bool:
    """Whether the LaTeX printer shows ``node`` as ``1`` over something (the
    cases in which ``LatexPrinter._print_Pow`` defers to ``_print_Mul``)."""
    e = node.exp
    if not e.is_Rational:
        return False
    if abs(e.p) == 1 and e.q != 1 and settings.get("root_notation", True):
        return False                                    # 1 over a root: the base is printed as is
    if settings.get("fold_frac_powers", False) and e.q != 1:
        return False
    if not (e.is_negative and node.base.is_commutative):
        return False
    b = node.base
    if b == 1 or (b.is_Rational and b.p * b.q == abs(b.q)):
        return False                                    # printed literally
    return True


def view_parts(node: Basic, settings: Settings = None) -> Optional[List[Tuple[str, Basic]]]:
    """The virtual parts of ``node`` - ``[(name, value), ...]`` in display
    order - when the LaTeX printer shows it as something else than its
    arguments, else None.  ``settings`` are the printer settings that affect
    the decision (``root_notation``, ``fold_frac_powers``).

    * a ``Rational`` ``p/q``: ``n`` = ``|p|``, ``d`` = ``q`` (the sign is
      shown in front of the fraction);
    * a ``Mul``/``MatMul`` shown with a leading minus: ``neg`` = the negated
      product, whose own parts or arguments are what is shown after the sign;
    * a ``Mul`` or a ``Pow`` shown as a fraction: ``n`` and ``d``, the
      printer's numerator and denominator.
    """
    settings = settings or {}
    if isinstance(node, Rational):
        return [("n", Integer(abs(node.p))), ("d", Integer(node.q))] if node.q != 1 else None
    if isinstance(node, MatMul):
        return [("neg", -node)] if node.could_extract_minus_sign() else None
    if isinstance(node, Mul):
        args = node.args
        if args[0] is S.One or any(isinstance(a, Number) for a in args[1:]):
            return None                                 # unevaluated product: printed as is
    elif isinstance(node, Pow):
        if not _pow_as_fraction(node, settings):
            return None
    else:
        return None
    if node.could_extract_minus_sign():
        return [("neg", -node)]
    numer, denom = fraction(node, exact=True)
    if denom is S.One:
        return None
    return [("n", numer), ("d", denom)]


def _part(expr: Basic, name: str, path: Path, settings: Settings) -> Basic:
    for part, value in view_parts(expr, settings) or ():
        if part == name:
            return value
    raise ValueError(f"Invalid path {format_path(path)} for {expr}")


def _evaluated(node: Basic) -> Basic:
    """``fraction`` builds unevaluated products: evaluate them before they
    are combined with something else."""
    return Mul(*node.args) if type(node) is Mul else node


def _replace_part(expr: Basic, name: str, new: Basic, path: Path, settings: Settings) -> Basic:
    """``expr`` rebuilt around ``new`` in place of its part ``name``."""
    parts = dict(view_parts(expr, settings) or ())
    if name not in parts:
        raise ValueError(f"Invalid path {format_path(path)} for {expr}")
    new = sympify(new)
    if isinstance(expr, Rational):
        if name == "n":
            return (-new if expr.p < 0 else new) / Integer(expr.q)
        return Integer(expr.p) / new
    if name == "neg":
        return -new
    if name == "n":
        return new / _evaluated(parts["d"])
    return _evaluated(parts["n"]) / new


def get_at(expr: Basic, path: Path, settings: Settings = None) -> Basic:
    """Return the sub-expression of ``expr`` at ``path`` (the value of a
    virtual part for a named step)."""
    node = expr
    for i in path:
        if isinstance(i, str):
            node = _part(node, i, path, settings)
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


def replace_at(expr: Basic, path: Path, new: Basic, settings: Settings = None) -> Basic:
    """Return ``expr`` with the node at ``path`` replaced by ``new``.

    Ancestors are rebuilt with :func:`rebuild` (normally ``node.func(*args)``),
    so SymPy's automatic evaluation applies (``x + (-x)`` becomes ``0``, etc.).
    """
    if not path:
        return new
    i = path[0]
    if isinstance(i, str):
        # A virtual part: the node is rebuilt around the new part (x over n
        # for a numerator x in place of the 1 of 1/n, say).
        inner = replace_at(_part(expr, i, path, settings), path[1:], new, settings)
        return _replace_part(expr, i, inner, path, settings)
    args = list(expr.args)
    if not isinstance(i, int) or not 0 <= i < len(args):
        raise ValueError(f"Invalid path {format_path(path)} for {expr}")
    args[i] = replace_at(args[i], path[1:], new, settings)
    return rebuild(expr, args)


def insert_at(expr: Basic, path: Path, index: int, new: Basic, settings: Settings = None) -> Basic:
    """Return ``expr`` with ``new`` inserted at position ``index`` of the
    arguments of the node at ``path``."""
    parent = get_at(expr, path, settings)
    args = list(parent.args)
    if not 0 <= index <= len(args):
        raise ValueError(f"Invalid insertion index {index} for {parent}")
    args.insert(index, new)
    return replace_at(expr, path, rebuild(parent, args), settings)


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


def extract_range(expr: Basic, path: Path, indices, settings: Settings = None) -> Basic:
    """The sub-expression formed by arguments ``indices`` of the node at
    ``path`` (``Add(b, c)`` for a range of two terms)."""
    parent = get_at(expr, path, settings)
    idx = _range_indices(parent, indices)
    if len(idx) == 1:
        return parent.args[idx[0]]
    return rebuild(parent, [parent.args[i] for i in idx])


def replace_range(expr: Basic, path: Path, indices, new: Basic, settings: Settings = None) -> Basic:
    """``expr`` with arguments ``indices`` of the node at ``path`` replaced by
    the single argument ``new`` (at the position of the first of them)."""
    parent = get_at(expr, path, settings)
    idx = _range_indices(parent, indices)
    args = [a for i, a in enumerate(parent.args) if i not in idx]
    args.insert(idx[0], new)
    return replace_at(expr, path, rebuild(parent, args), settings)


def delete_range(expr: Basic, path: Path, indices, settings: Settings = None) -> Basic:
    """``expr`` without arguments ``indices`` of the node at ``path``."""
    parent = get_at(expr, path, settings)
    idx = _range_indices(parent, indices)
    return replace_at(expr, path, rebuild(parent, [a for i, a in enumerate(parent.args) if i not in idx]), settings)


def delete_at(expr: Basic, path: Path, settings: Settings = None) -> Basic:
    """Return ``expr`` with the node at ``path`` removed from its parent's
    args.  Removing a numerator or denominator leaves ``1`` in its place;
    removing the product after a minus sign removes the signed product."""
    if not path:
        raise ValueError("Cannot delete the root expression")
    last = path[-1]
    if last == "neg":
        _part(get_at(expr, path[:-1], settings), last, path, settings)      # must exist
        return delete_at(expr, path[:-1], settings)
    if isinstance(last, str):
        return replace_at(expr, path, S.One, settings)
    parent = get_at(expr, path[:-1], settings)
    args = list(parent.args)
    del args[last]
    return replace_at(expr, path[:-1], rebuild(parent, args), settings)


# --------------------------------------------------------------------------
# Printer
# --------------------------------------------------------------------------

def _patch_number_separator() -> None:
    """SymPy 1.15+ decides whether two factors of a product are numbers
    (``2 \\cdot 3`` rather than ``2 3``) from the LaTeX of each factor; an
    annotated number must still count as one.  The module-level patterns are
    replaced by ones that skip the annotation wrappers (``\\htmlData{..}{``
    and the markers of :func:`latex_spans`), which never occur in plain
    output, so unannotated printing is unaffected."""
    import re
    import sys
    mod = sys.modules["sympy.printing.latex"]
    pair = getattr(mod, "_between_two_numbers_p", None)
    if not pair or getattr(mod, "_sympy_editor_patched", False):
        return
    first, _second = pair
    wrapper = r"(?:\\htmlData\{[^{}]*\}\{|\x01[^\x02]*\x02)*"
    mod._between_two_numbers_p = (
        re.compile(first.pattern.replace("[} ]", "[} \x03]")),
        re.compile(wrapper + r"(?:\d|\\frac\{" + wrapper + r"\d+[}\x03]*\{" + wrapper + r"\d+)"),
    )
    mod._sympy_editor_patched = True


_patch_number_separator()

#: Containers whose children the SymPy printer accesses directly (integration
#: limits, matrix elements, piecewise branches...).  They do not count towards
#: the search depth and their children are searched before ordinary siblings.
TRANSPARENT = (SymTuple, ExprCondPair)


class _Frame:
    """A view-tree node currently being printed, with a lazily built index of
    its descendants (up to ``max_depth``) keyed by structural equality, each
    entry listing ``(path, node)`` pairs in search order.  ``parts`` gives
    the virtual parts of a node (:func:`view_parts` with the printer's
    settings); they and their contents are searched before the node's real
    arguments, at the same level."""

    __slots__ = ("expr", "path", "is_root", "parts", "_index")

    def __init__(self, expr: Basic, path: Path, parts: Callable[[Basic], Optional[List[Tuple[str, Basic]]]],
                 is_root: bool = False):
        self.expr = expr
        self.path = path
        self.parts = parts
        self.is_root = is_root
        self._index: Optional[Dict[Basic, List[Tuple[Path, Basic]]]] = None

    def _expand(self, path: Path, node, out: List[Tuple[Path, Basic, bool]]) -> None:
        """Append the children of ``node`` to ``out`` as ``(path, node,
        expand)``: ``expand`` is False for the entries whose own children
        are appended here as well (virtual parts, transparent containers)."""
        for name, value in self.parts(node) or ():
            out.append((path + (name,), value, False))
            self._expand(path + (name,), value, out)
        transparent, plain = [], []
        for i, arg in enumerate(getattr(node, "args", ())):
            (transparent if isinstance(arg, TRANSPARENT) else plain).append((path + (i,), arg))
        for p, arg in transparent:
            out.append((p, arg, False))
            self._expand(p, arg, out)
        out.extend((p, arg, True) for p, arg in plain)

    def candidates(self, expr: Basic, max_depth: int) -> List[Tuple[Path, Basic]]:
        if self._index is None:
            index: Dict[Basic, List[Tuple[Path, Basic]]] = {}
            level: List[Tuple[Path, Basic]] = [(self.path, self.expr)]
            entries: List[Tuple[Path, Basic]] = list(level) if self.is_root else []
            for _ in range(max_depth):
                nxt: List[Tuple[Path, Basic, bool]] = []
                for path, node in level:
                    self._expand(path, node, nxt)
                entries.extend((p, n) for p, n, _e in nxt)
                level = [(p, n) for p, n, expand in nxt if expand]
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
        self._stack = [_Frame(expr, (), self._view_parts, is_root=True)]
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

    def _view_parts(self, node: Basic) -> Optional[List[Tuple[str, Basic]]]:
        return view_parts(node, self._settings)

    def _locate(self, expr: Basic) -> Optional[Tuple[Path, Basic]]:
        """The ``(path, node)`` of the unclaimed view-tree node that ``expr``
        prints, or None."""
        if not self._stack:
            return None
        frame = self._stack[-1]
        for path, node in frame.candidates(expr, self.max_depth):
            if path in self._claimed:
                continue
            if node is expr or (type(node) is type(expr) and node == expr):
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
        annotated once per node, with the values of :func:`view_parts`
        (``|p|`` and ``q``: the sign is shown in front of the fraction)."""
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
        self._stack.append(_Frame(node, path, self._view_parts))
        try:
            tex = super()._print(expr, **kwargs)
        finally:
            self._stack.pop()
        return self._annotated(expr, path, tex)

def _is_block_matrix(mat) -> bool:
    from sympy.matrices.expressions.blockmatrix import BlockMatrix
    return isinstance(mat, BlockMatrix)


class AnnotatedLatexPrinter(_AnnotatingMixin, LatexPrinter):
    """LaTeX printer that annotates every printed sub-expression with its
    path (KaTeX ``\\htmlData``)."""

    def _print_Limit(self, expr):
        # LatexPrinter writes the direction of a one-sided limit as "0^+",
        # with no braces: fine while "+" is one character, but the annotation
        # makes it \htmlData{path=/3}{+}, and a superscript then takes only
        # the first token of it.  The braces are all that is missing.
        e, z, z0, direction = expr.args
        tex = r"\lim_{%s \to " % self._print(z)
        if str(direction) == "+-" or z0 in (S.Infinity, S.NegativeInfinity):
            tex += r"%s}" % self._print(z0)
        else:
            tex += r"%s^{%s}}" % (self._print(z0), self._print(direction))
        from sympy.core.operations import AssocOp
        if isinstance(e, AssocOp):
            return r"%s\left(%s\right)" % (tex, self._print(e))
        return r"%s %s" % (tex, self._print(e))

    def _print_Determinant(self, expr):
        # LatexPrinter prints the determinant of an explicit matrix by
        # reaching for its contents (``_print_matrix_contents``) instead of
        # printing the matrix, so the matrix node never went through
        # ``_print`` and got no annotation: the view tree jumped from the
        # Determinant straight to the entries, and ↑/↓ skipped the matrix.
        # Print it properly, with its own delimiters off - the bars are the
        # determinant's.
        mat = expr.arg
        if getattr(mat, "is_MatrixExpr", False) and not _is_block_matrix(mat):
            return r"\left|{%s}\right|" % self._print(mat)
        delim = self._settings["mat_delim"]
        self._settings["mat_delim"] = ""
        try:
            return r"\left|{%s}\right|" % self._print(mat)
        finally:
            self._settings["mat_delim"] = delim

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
                    self._stack.append(_Frame(node, path, self._view_parts))
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

    def _print_Mul(self, expr):
        # The printer prints the factors of a numerator or denominator, and
        # of the product after a minus sign, directly: those parts get their
        # spans here, around the LaTeX of their factors, so that the shown
        # hierarchy (2 -> 2e -> 1/(2e)) can be walked and edited.
        tex = super()._print_Mul(expr)
        frame = self._stack[-1] if self._stack else None
        if frame is None or not (type(frame.expr) is type(expr) and frame.expr == expr):
            return tex                                  # a synthesised product: no node of its own
        parts = self._view_parts(frame.expr)
        if not parts:
            return tex
        if parts[0][0] == "neg":
            if not tex.startswith("- "):
                return tex
            path, node = frame.path + ("neg",), parts[0][1]
            body = self._annotate_fraction(path, node, tex[2:])
            if path not in self._nodes:
                body = self._annotated(node, path, body)
            return "- " + body
        return self._annotate_fraction(frame.path, frame.expr, tex)

    def _annotate_fraction(self, path: Path, node: Basic, tex: str) -> str:
        """``tex`` (``\\frac{numerator}{denominator}`` printed for ``node``)
        with the numerator and denominator annotated as the parts ``n`` and
        ``d`` of ``node``, unless they were annotated while being printed."""
        parts = dict(self._view_parts(node) or ())
        if "n" not in parts or not tex.startswith(r"\frac{"):
            return tex
        k = _match_brace(tex, 5)
        if k < 0 or k + 1 >= len(tex) or tex[k + 1] != "{" or _match_brace(tex, k + 1) != len(tex) - 1:
            return tex
        num, den = tex[6:k], tex[k + 2:-1]
        if path + ("n",) not in self._nodes:
            num = self._annotated(parts["n"], path + ("n",), num)
        if path + ("d",) not in self._nodes:
            den = self._annotated(parts["d"], path + ("d",), den)
        return r"\frac{%s}{%s}" % (num, den)

    def _print_Pow(self, expr):
        # A power shown as 1 over something is printed through _print_Mul (as
        # SymPy 1.14 does; later versions print the 1 literally), so that the
        # numerator 1 and the denominator are printed - and annotated - as
        # the view tree's parts.
        if self._stack is not None and _pow_as_fraction(expr, self._settings):
            return self._print_Mul(expr)
        return super()._print_Pow(expr)

    def _print_Rational(self, expr):
        # LatexPrinter._print_Rational, with the numerator and denominator
        # annotated (paths "n" and "d" under the number's own path).
        node, path = self._rational_parts(expr)
        if node is None:
            return super()._print_Rational(expr)
        sign, p = ("- ", -expr.p) if expr.p < 0 else ("", expr.p)
        num = self._annotated(Integer(p), path + ("n",), str(p))
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
        num = self._annotated(Integer(abs(node.p)), path + ("n",), str(expr.p))
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
                t = "(%s)" % t
            parts.append((sign, t))
        # A minus belongs to the term it negates - the rendering shows
        # "- sin(x)" as one term, sign included - so it goes inside that
        # term's marker and the source span covers it too.  A plus does not:
        # there it is the operator between two terms, selectable on its own.
        # The printed characters are unchanged either way.
        def signed(prefix: str, text: str) -> str:
            pair = self._unwrap(text)
            if not pair:
                return prefix + text
            path, inner = pair
            return MARK_START + path + MARK_SEP + prefix + inner + MARK_END

        first_sign, first = parts[0]
        out = signed("-", first) if first_sign == "-" else first
        for sign, text in parts[1:]:
            out += " " + (signed("- ", text) if sign == "-" else sign + " " + text)
        return out


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

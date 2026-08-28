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
still located relative to the enclosing real frame.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from sympy import sympify
from sympy.core.basic import Basic
from sympy.core.containers import Tuple as SymTuple
from sympy.functions.elementary.piecewise import ExprCondPair
from sympy.printing.latex import LatexPrinter, latex

Path = Tuple[int, ...]

__all__ = [
    "AnnotatedLatexPrinter",
    "annotate",
    "strip_annotations",
    "format_path",
    "parse_path",
    "get_at",
    "replace_at",
    "delete_at",
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
        return tuple(int(part) for part in text.strip("/").split("/"))
    except ValueError:
        raise ValueError(f"Invalid path: {text!r}") from None


def get_at(expr: Basic, path: Path) -> Basic:
    """Return the sub-expression of ``expr`` at ``path``."""
    node = expr
    for i in path:
        try:
            node = node.args[i]
        except (IndexError, AttributeError):
            raise ValueError(f"Invalid path {format_path(path)} for {expr}") from None
    return node


def replace_at(expr: Basic, path: Path, new: Basic) -> Basic:
    """Return ``expr`` with the node at ``path`` replaced by ``new``.

    Ancestors are rebuilt with ``node.func(*args)``, so SymPy's automatic
    evaluation applies (``x + (-x)`` becomes ``0``, etc.).
    """
    if not path:
        return new
    i = path[0]
    args = list(expr.args)
    if not 0 <= i < len(args):
        raise ValueError(f"Invalid path {format_path(path)} for {expr}")
    args[i] = replace_at(args[i], path[1:], new)
    return expr.func(*args)


def delete_at(expr: Basic, path: Path) -> Basic:
    """Return ``expr`` with the node at ``path`` removed from its parent's args."""
    if not path:
        raise ValueError("Cannot delete the root expression")
    parent = get_at(expr, path[:-1])
    args = list(parent.args)
    del args[path[-1]]
    return replace_at(expr, path[:-1], parent.func(*args))


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


class AnnotatedLatexPrinter(LatexPrinter):
    """LaTeX printer that annotates every printed sub-expression with its path.

    Use :meth:`annotate` (or the module-level :func:`annotate`) rather than
    :meth:`doprint`; without a prior call to ``annotate`` the printer behaves
    exactly like :class:`~sympy.printing.latex.LatexPrinter`.
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

    def _locate(self, expr: Basic) -> Optional[Path]:
        if not self._stack:
            return None
        frame = self._stack[-1]
        for path, node in frame.candidates(expr, self.max_depth):
            if path in self._claimed:
                continue
            if node is expr or (type(node) is type(expr) and node == expr):
                self._claimed.add(path)
                return path
        return None

    def _annotated(self, expr: Basic, path: Path, tex: str) -> str:
        self._nodes[path] = expr
        return self.wrap(format_path(path), tex)

    def _print(self, expr, **kwargs):
        if self._stack is None or not isinstance(expr, Basic):
            return super()._print(expr, **kwargs)
        path = self._locate(expr)
        if path is None:
            return super()._print(expr, **kwargs)
        self._stack.append(_Frame(expr, path))
        try:
            tex = super()._print(expr, **kwargs)
        finally:
            self._stack.pop()
        return self._annotated(expr, path, tex)

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
                path = self._locate(term) if self._stack is not None else None
                if path is None:
                    tex += " - " + self._print_add_term(-term)
                else:
                    self._stack.append(_Frame(term, path))
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

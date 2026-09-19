"""Expressions SymPy refuses to build, kept as they were written.

``A*B`` with ``A`` a 3x3 and ``B`` a 2x2 matrix is an error: ``MatMul``'s
constructor raises.  So is ``sin(x, y)``, ``Inverse`` of a non-square
matrix, ``1 + M``...  A document that allows invalid expressions (see
``Document(allow_invalid=True)``) keeps such a node instead of refusing the
edit: an :class:`InvalidExpr` holding the name of the constructor that
refused (its *head*) and the arguments it was given, each of them a valid
SymPy expression or itself an invalid node - an S-expression
``[MatMul, A, B]`` wherever SymPy would not build one.

The node prints as ``Invalid(MatMul, A, B)`` (which reads back), and in
LaTeX as the head in red followed by the arguments in square brackets.  Its
arguments are its children in the view tree, so they are edited as any
others; whenever the node is rebuilt with new arguments (an edit inside it)
the head is tried again, and the node becomes the expression SymPy builds
once it builds one - replacing ``B`` by a 3x3 matrix gives back ``A*B``.

A document that does not allow them refuses every commit that holds one,
whether an operator raised (typing ``A*B``) or a constructor let through
what its operator would have refused (``Add(2, M)``, ``Pow(M, 1/2)`` of a
non-square ``M``, built unevaluated): :func:`first_problem` finds those.
"""

from __future__ import annotations

import ast
import contextlib
import operator
import re
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import sympy
from sympy import Add, Basic, Expr, Function, sympify
from sympy.core.function import UndefinedFunction
from sympy.core.numbers import Integer
from sympy.core.symbol import Str
from sympy.matrices import MatrixBase
from sympy.matrices.expressions import MatAdd, MatrixExpr
from sympy.parsing.sympy_parser import stringify_expr

from .printer import register_rebuild, rebuild, rebuild_fallback

__all__ = ["InvalidExpr", "Invalid", "invalid", "build", "node_problem", "first_problem", "tolerate",
           "allowing_invalid", "tolerant_parse", "tolerant_locals", "has_invalid"]


class InvalidExpr(Expr):
    """A node SymPy would not build: ``head`` (a class attribute: the name
    of the constructor that refused) applied to ``args``.  One subclass per
    head (:func:`invalid`), so that ``node.func(*args)`` is the same kind of
    node, as for any SymPy class."""

    head: str = ""
    is_commutative = False          # matrix products keep their order

    def __new__(cls, *args):
        return Basic.__new__(cls, *[sympify(a) for a in args])

    def _sympystr(self, printer) -> str:
        return "Invalid(%s)" % ", ".join([self.head] + [printer._print(a) for a in self.args])

    def _sympyrepr(self, printer) -> str:
        return "Invalid(%s)" % ", ".join([repr(self.head)] + [printer._print(a) for a in self.args])

    def _latex(self, printer) -> str:
        head = self.head.replace("_", r"\_")
        return r"\textcolor{red}{\mathtt{%s}}\left[%s\right]" % (
            head, r",\ ".join(printer._print(a) for a in self.args))

    def _pythoncode(self, printer) -> str:
        return self._sympyrepr(printer)


_CLASSES: Dict[str, type] = {}


def invalid(head: str) -> type:
    """The :class:`InvalidExpr` subclass for ``head`` (``InvalidMatMul``)."""
    cls = _CLASSES.get(head)
    if cls is None:
        name = "Invalid" + re.sub(r"\W", "_", head)
        cls = _CLASSES[head] = type(name, (InvalidExpr,), {"head": head, "__module__": __name__})
    return cls


def _head_name(head: Any) -> str:
    if isinstance(head, str):
        return head
    if isinstance(head, Basic):             # Invalid(f, x, y) read back: f is a symbol by then
        return str(head)
    return getattr(head, "__name__", None) or str(head)


def _constructor(head: str) -> Callable:
    fn = getattr(sympy, head, None)
    return fn if callable(fn) else Function(head)


def build(head: Any, args) -> Basic:
    """``head(*args)`` if SymPy builds it and the node is well formed, else
    the invalid node keeping them.  ``head`` is a name or a callable."""
    args = [sympify(a) for a in args]
    name = _head_name(head)
    fn = head if callable(head) and not isinstance(head, (str, Basic)) else _constructor(name)
    try:
        result = sympify(fn(*args))
    except Exception:
        return invalid(name)(*args)
    if isinstance(result, Basic) and node_problem(result) is not None:
        return invalid(name)(*args)
    return result


def Invalid(head: Any, *args) -> Basic:
    """``Invalid(MatMul, A, B)``: the node as printed, read back - or the
    expression itself when SymPy builds it by now."""
    return build(head, args)


register_rebuild(InvalidExpr, lambda expr, args: build(expr.head, args))


# -- validity ------------------------------------------------------------------

def _matrixish(a) -> bool:
    return isinstance(a, (MatrixBase, MatrixExpr))


def _unusual(a) -> bool:
    """An argument that constructors check only when they evaluate: a
    matrix, or something that is not a scalar expression (``Eq`` given to
    ``conjugate``)."""
    return _matrixish(a) or not isinstance(a, Expr)


def node_problem(node: Basic) -> Optional[str]:
    """Why SymPy would not have built ``node`` from its arguments (the
    constructor's error), or None.  Only this node is looked at, not its
    arguments.  The constructor is run unevaluated, and evaluated as well
    when an argument is a matrix or not a scalar - the checks of ``Pow`` and
    of the functions only run then, and the construction is cheap there,
    whereas evaluating ``factorial(10**6)`` is not."""
    if isinstance(node, InvalidExpr):
        return "SymPy does not build %s of these arguments" % node.head
    args = getattr(node, "args", ())
    if not args or not isinstance(node, Basic):
        return None
    try:
        if isinstance(node, Add) and not isinstance(node, MatAdd) and any(_matrixish(a) for a in args):
            raise TypeError("a matrix cannot be added to a scalar")
        if isinstance(node, sympy.Pow) and _matrixish(node.exp):
            raise TypeError("a matrix cannot be an exponent")
        with sympy.evaluate(False):
            rebuild(node, list(args))
        if any(_unusual(a) for a in args):
            rebuild(node, list(args))
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _post_order(expr: Basic) -> Iterator[Basic]:
    stack: List[Tuple[Basic, bool]] = [(expr, False)]
    seen = set()
    while stack:
        node, done = stack.pop()
        if done:
            yield node
            continue
        try:
            if node in seen:
                continue
            seen.add(node)
        except TypeError:
            pass
        stack.append((node, True))
        stack.extend((a, False) for a in reversed(getattr(node, "args", ())) if isinstance(a, Basic))


def first_problem(expr: Basic) -> Optional[Tuple[Basic, str]]:
    """The innermost node of ``expr`` SymPy would not have built, with the
    reason, or None when every node is valid."""
    for node in _post_order(expr):
        problem = node_problem(node)
        if problem is not None:
            return node, problem
    return None


def has_invalid(expr: Basic) -> bool:
    return any(isinstance(n, InvalidExpr) for n in _post_order(expr))


def tolerate(expr: Basic) -> Basic:
    """``expr`` with every node SymPy would not have built made an
    :class:`InvalidExpr` of the same head and arguments.  Valid nodes are
    kept as they are (an unevaluated form stays unevaluated)."""
    if not isinstance(expr, Basic) or not expr.args or isinstance(expr, MatrixBase):
        return expr
    args = [tolerate(a) if isinstance(a, Basic) else a for a in expr.args]
    node = expr
    if any(a is not b for a, b in zip(args, expr.args)):
        try:
            with sympy.evaluate(False):
                node = rebuild(expr, args)
        except Exception:
            if not _keepable(expr):
                raise
            return invalid(type(expr).__name__)(*args)
    if isinstance(node, InvalidExpr):
        return node
    problem = node_problem(node)
    if problem is None:
        return node
    if not _keepable(node):
        raise ValueError(f"{node} is not a valid expression ({problem})")
    return invalid(type(node).__name__)(*node.args)


# -- building and reading with invalid nodes allowed -----------------------------

def _keepable(expr: Basic) -> bool:
    """Whether a node refused by SymPy may stay as an invalid one: not a
    named object (a ``MatrixSymbol`` given an expression for its name or
    its shape is not an expression waiting to be fixed, it is no object)."""
    return not any(isinstance(a, Str) for a in expr.args)


def _fallback(expr: Basic, args, exc: Exception) -> Basic:
    if not _keepable(expr):
        raise exc
    return invalid(type(expr).__name__)(*args)


@contextlib.contextmanager
def allowing_invalid(allow: bool = True):
    """While active, a tree edit whose rebuilt node SymPy refuses gives an
    invalid node instead of the error (see ``printer.rebuild``)."""
    token = rebuild_fallback.set(_fallback if allow else None)
    try:
        yield
    finally:
        rebuild_fallback.reset(token)


def _is_constructor(fn: Any) -> bool:
    """A SymPy class or function, or an undefined function: what an invalid
    node may be headed by (a method call that fails is an error)."""
    if isinstance(fn, UndefinedFunction):
        return True
    if isinstance(fn, type):
        return issubclass(fn, Basic)
    name = getattr(fn, "__name__", None)
    return bool(name) and getattr(sympy, name, None) is fn


def _basic(a) -> Basic:
    if isinstance(a, int) and not isinstance(a, bool):
        return Integer(a)
    if not isinstance(a, Basic):
        raise TypeError(f"{type(a).__name__} is not a SymPy expression")
    return a


def _heads(op: str, a: Basic, b: Basic) -> str:
    matrix = _matrixish(a) or _matrixish(b)
    if op in ("add", "sub"):
        return "MatAdd" if matrix else "Add"
    if op in ("mul", "truediv", "matmul"):
        return "MatMul" if matrix else "Mul"
    return "MatPow" if _matrixish(a) else "Pow"


_BINARY = {ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "truediv", ast.Pow: "pow",
           ast.MatMult: "matmul"}


def _se_binop(op: str, a, b):
    try:
        return getattr(operator, op)(a, b)
    except Exception:
        a, b = _basic(a), _basic(b)
        if op == "sub":
            b = _se_unop(b)
        elif op == "truediv":
            b = _se_binop("pow", b, Integer(-1))
        return invalid(_heads(op, a, b))(a, b)


def _se_unop(a):
    try:
        return -a
    except Exception:
        a = _basic(a)
        return invalid("MatMul" if _matrixish(a) else "Mul")(Integer(-1), a)


def _se_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        if kwargs or not _is_constructor(fn) or not all(isinstance(a, (Basic, int)) for a in args):
            raise
        return invalid(fn.__name__)(*[_basic(a) for a in args])


class _Tolerant(ast.NodeTransformer):
    """Operators and calls go through ``_se_binop``, ``_se_unop`` and
    ``_se_call``, which keep what SymPy refuses as an invalid node."""

    def visit_BinOp(self, node):
        self.generic_visit(node)
        op = _BINARY.get(type(node.op))
        if op is None:
            return node
        return ast.Call(ast.Name("_se_binop", ast.Load()), [ast.Constant(op), node.left, node.right], [])

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if not isinstance(node.op, ast.USub):
            return node
        return ast.Call(ast.Name("_se_unop", ast.Load()), [node.operand], [])

    def visit_Call(self, node):
        self.generic_visit(node)
        return ast.Call(ast.Name("_se_call", ast.Load()), [node.func] + node.args, node.keywords)


def tolerant_parse(src: str, local_dict: Dict[str, Any], transformations) -> Basic:
    """``parse_expr(src, local_dict, transformations)`` where what SymPy
    refuses to build is kept as an invalid node."""
    from sympy.parsing.sympy_parser import null
    global_dict: Dict[str, Any] = {}
    exec("from sympy import *", global_dict)
    global_dict.update(max=sympy.Max, min=sympy.Min, Invalid=Invalid,
                       _se_binop=_se_binop, _se_unop=_se_unop, _se_call=_se_call)
    local_dict = dict(local_dict)
    code = stringify_expr(src, local_dict, global_dict, transformations)
    tree = ast.fix_missing_locations(_Tolerant().visit(ast.parse(code, mode="eval")))
    try:
        return sympify(eval(compile(tree, "<input>", "eval"), global_dict, local_dict))
    finally:
        local_dict.pop(null, None)


def tolerant_locals(text: str) -> Dict[str, Any]:
    """Names for reading back ``srepr`` text in which a node SymPy refuses
    (a step saved before this module existed, or under an older SymPy)
    becomes an invalid node instead of making the whole text unreadable."""
    local: Dict[str, Any] = {"Invalid": Invalid}
    for name in set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", text)):
        fn = getattr(sympy, name, None)
        if name in local or not callable(fn) or not _is_constructor(fn):
            continue
        local[name] = _tolerant_constructor(fn)
    return local


def _tolerant_constructor(fn):
    def construct(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            if kwargs or not all(isinstance(a, (Basic, int)) for a in args):
                raise
            return invalid(fn.__name__)(*[_basic(a) for a in args])
    return construct

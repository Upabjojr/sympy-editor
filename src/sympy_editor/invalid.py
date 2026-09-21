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
from sympy.matrices.expressions import MatAdd, MatMul, MatrixExpr
from sympy.parsing.sympy_parser import stringify_expr

from .printer import register_rebuild, rebuild, rebuild_fallback

__all__ = ["InvalidExpr", "Invalid", "invalid", "build", "node_problem", "first_problem", "tolerate",
           "allowing_invalid", "tolerant_parse", "tolerant_locals", "has_invalid", "read_srepr", "read_source",
           "UnsafeText"]


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
    if fn is None:
        fn = _sympy_class(head)     # a class SymPy does not export: ExprCondPair
    return fn if callable(fn) else Function(head)


def build(head: Any, args) -> Basic:
    """``head(*args)`` if SymPy builds it and the node is well formed, else
    the invalid node keeping them.  ``head`` is a name or a callable."""
    args = [sympify(a) for a in args]
    name = _head_name(head)
    fn = head if callable(head) and not isinstance(head, (str, Basic)) else _constructor(name)
    try:
        # evaluated, even while a saved step is read unevaluated: the head
        # is tried as SymPy's operators would try it, which is what refused it
        with sympy.evaluate(True):
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


def _rebuild_unevaluated(node: Basic, args) -> Basic:
    """``rebuild(node, args)`` under ``evaluate(False)``.  A matrix sum or
    product is built by its constructor alone: ``rebuild`` canonicalises
    those (``doit``), which unevaluated recurses without end on an explicit
    matrix term (``Matrix([[1, 2], [3, 4]]) + B``)."""
    with sympy.evaluate(False):
        if isinstance(node, (MatAdd, MatMul)):
            return node.func(*args)
        return rebuild(node, list(args))


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
        if isinstance(node, (sympy.Pow, sympy.MatPow)) and _matrixish(node.exp):
            raise TypeError("a matrix cannot be an exponent")
        _rebuild_unevaluated(node, list(args))
        if any(_unusual(a) for a in args):
            # evaluated even while the caller reads unevaluated (a saved
            # step): these checks only run when the constructor evaluates
            with sympy.evaluate(True):
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
            node = _rebuild_unevaluated(expr, args)
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


# -- reading saved data without running it ------------------------------------------
# A saved formula (a .sympy file, a session a page kept) arrives from a mail,
# a file manager, a page's storage: it is data, and reading it must never run
# code.  ``sympify``/``eval`` of its text would - ``(open(...).write(...),
# Symbol('x'))[1]`` is a valid srepr to them.  What follows reads the two
# things such data holds - ``srepr`` text and a line of SymPy source - by
# walking the syntax tree, with nothing but SymPy's constructors to call.

class UnsafeText(ValueError):
    """Text the restricted reader will not read (an attribute, a subscript,
    a name it does not know as SymPy's...)."""


class _NotSrepr(UnsafeText):
    """Text that is not ``srepr`` but may be SymPy source (an operator, a
    name that is not SymPy's)."""


#: Where the SymPy functions a saved formula may call live: the ones that
#: build expressions.  (``lambdify``, ``preview``, ``sympify`` and their kind
#: live elsewhere or are named in ``_UNSAFE_FUNCTIONS``.)
_SAFE_MODULES = ("sympy.core.", "sympy.functions.", "sympy.sets.", "sympy.logic.", "sympy.concrete.",
                 "sympy.integrals.", "sympy.series.", "sympy.matrices.", "sympy.tensor.array.",
                 "sympy.calculus.", "sympy.polys.polytools", "sympy.simplify.")
_UNSAFE_FUNCTIONS = frozenset({"sympify", "_sympify", "S", "var", "symbols", "evaluate", "lambdify", "parse_expr",
                               "srepr", "sstr", "pprint", "preview", "init_printing", "init_session"})
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")


_CLASS_CACHE: Dict[str, Any] = {}


def _sympy_class(name: str) -> Optional[type]:
    """One of SymPy's expression, matrix or array classes by name, those
    the ``sympy`` namespace does not export included (``srepr`` writes
    ``ExprCondPair``...).  The classes are listed again when a name is not
    found, in case a module defining it was imported since."""
    if name in _CLASS_CACHE or name in _CLASS_CACHE.get("", {}).get("missing", ()):
        return _CLASS_CACHE.get(name)
    from sympy.tensor.array import NDimArray
    seen = set()
    stack = [Basic, MatrixBase, NDimArray]
    while stack:
        cls = stack.pop()
        try:
            subs = cls.__subclasses__()
        except TypeError:              # a metaclass
            subs = type.__subclasses__(cls)
        for sub in subs:
            if sub not in seen:
                seen.add(sub)
                stack.append(sub)
    for cls in seen:
        if (cls.__module__ or "").startswith("sympy.") and not cls.__name__.startswith("_"):
            _CLASS_CACHE.setdefault(cls.__name__, cls)
    if name not in _CLASS_CACHE:
        missing = _CLASS_CACHE.setdefault("", {}).setdefault("missing", set())
        if len(missing) < 1000:
            missing.add(name)
    return _CLASS_CACHE.get(name)


def _name_taker(fn: Any) -> bool:
    """A constructor whose string arguments are names (``Symbol('x')``,
    ``MatrixSymbol('A', 2, 2)``, ``Function('f')``, ``Float('1.5')``),
    never text it would parse."""
    from sympy import Dummy, Float, IndexedBase, MatrixSymbol, Symbol, Wild
    from sympy.tensor.array.expressions import ArraySymbol
    if fn is Invalid or fn is Function or fn is Float:
        return True
    return isinstance(fn, type) and issubclass(fn, (Symbol, Str, Dummy, Wild, IndexedBase, MatrixSymbol, ArraySymbol))


def _constructor_ok(fn: Any) -> bool:
    """Whether a saved formula may call ``fn``: a SymPy class (an
    expression, a matrix, an array), an undefined function, or a SymPy
    function that builds expressions."""
    if isinstance(fn, type):
        from sympy.tensor.array import NDimArray
        return issubclass(fn, (Basic, MatrixBase, NDimArray))
    name = getattr(fn, "__name__", None)
    module = getattr(fn, "__module__", None) or ""
    if not name or name.startswith("_") or name in _UNSAFE_FUNCTIONS or getattr(sympy, name, None) is not fn:
        return False
    return module.startswith(_SAFE_MODULES)


def _check_strings(value: Any, top_ok: bool) -> None:
    """Strings handed to a constructor that is not a name taker must be
    plain names: some constructors ``sympify`` a string, which parses it."""
    if isinstance(value, str):
        if not top_ok and (not _NAME_RE.match(value) or value.startswith("__")):
            raise UnsafeText(f"text {value!r} where an expression belongs")
    elif isinstance(value, (list, tuple)):
        for v in value:
            _check_strings(v, False)
    elif isinstance(value, dict):
        for k, v in value.items():
            _check_strings(k, False)
            _check_strings(v, False)


_SOURCE_BINARY = {ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "truediv", ast.Pow: "pow",
                  ast.MatMult: "matmul", ast.Mod: "mod", ast.BitAnd: "and_", ast.BitOr: "or_", ast.BitXor: "xor"}
_COMPARE = {ast.Lt: "Lt", ast.Gt: "Gt", ast.LtE: "Le", ast.GtE: "Ge"}


class _Reader:
    """The interpreter behind :func:`read_srepr` and :func:`read_source`."""

    def __init__(self, text: str, names: Optional[Dict[str, Any]], source: bool, python_numbers: bool = False,
                 new_name: Optional[Callable[[str], Any]] = None):
        self.text = text
        self.names = dict(names or {})
        self.names.setdefault("Invalid", Invalid)
        self.names.setdefault("Str", Str)
        self.source = source
        self.python_numbers = python_numbers
        self.new_name = new_name

    def fail(self, what: str):
        raise (UnsafeText if self.source else _NotSrepr)(what)

    def lookup(self, name: str, called: bool) -> Any:
        if name.startswith("__"):
            raise UnsafeText(f"the name {name!r} is not allowed")
        if name in self.names:
            return self.names[name]
        obj = getattr(sympy, name, None)
        if obj is None:
            obj = _sympy_class(name)        # srepr names classes SymPy does not export: ExprCondPair
        if obj is not None:
            if called and _constructor_ok(obj):
                return obj
            if not called and isinstance(obj, Basic):
                return obj
            if not called and not self.source and _constructor_ok(obj):
                return obj                      # a head named as a value: Invalid(MatMul, A, B)
        if not self.source:
            raise _NotSrepr(f"unknown name {name!r}")
        if called:
            if obj is not None:
                raise UnsafeText(f"{name} cannot be called in a saved formula")
            return Function(name)
        if self.new_name is not None:
            made = self.new_name(name)
            if made is not None:
                return made
        from sympy import Symbol
        return Symbol(name)

    def number(self, node: ast.Constant) -> Any:
        value = node.value
        if not self.source or self.python_numbers:
            return value
        from sympy import Float
        if isinstance(value, bool):
            return sympy.true if value else sympy.false
        if isinstance(value, int):
            return Integer(value)
        if isinstance(value, float):
            segment = ast.get_source_segment(self.text, node)
            return Float(segment if segment else repr(value))
        return value

    def eval(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return self.eval(node.body)
        if isinstance(node, ast.Constant):
            if node.value is None or isinstance(node.value, (bool, int, float, str)):
                return self.number(node)
            raise UnsafeText(f"a {type(node.value).__name__} literal")
        if isinstance(node, ast.Name):
            return self.lookup(node.id, called=False)
        if isinstance(node, (ast.Tuple, ast.List)):
            items = [self.eval(e) for e in node.elts]
            return tuple(items) if isinstance(node, ast.Tuple) else items
        if isinstance(node, ast.Dict):
            if any(k is None for k in node.keys):
                raise UnsafeText("** in a dict")
            return {self.eval(k): self.eval(v) for k, v in zip(node.keys, node.values)}
        if isinstance(node, ast.UnaryOp):
            operand = self.eval(node.operand)
            if isinstance(node.op, ast.USub):
                if not self.source and not isinstance(operand, (int, float)):
                    self.fail("a minus sign")
                if isinstance(operand, (str, list, tuple, dict)) or operand is None:
                    self.fail("a minus sign before something that is not a number")
                return -operand if isinstance(operand, (int, float)) else _se_unop(operand)
            if isinstance(node.op, ast.UAdd) and self.source:
                return operand
            if isinstance(node.op, ast.Invert) and self.source:
                return ~operand
            self.fail("this operator")
        if isinstance(node, ast.Call):
            return self.call(node)
        if not self.source:
            raise _NotSrepr(type(node).__name__)
        if isinstance(node, ast.BinOp):
            op = _SOURCE_BINARY.get(type(node.op))
            if op is None:
                raise UnsafeText(f"the operator {type(node.op).__name__}")
            a, b = self.eval(node.left), self.eval(node.right)
            for v in (a, b):
                if isinstance(v, (str, list, tuple, dict)) or v is None:
                    raise UnsafeText("an operator on something that is not an expression")
            if op == "pow" and not self.python_numbers and isinstance(a, Integer) and isinstance(b, Integer) \
                    and abs(int(b)) > 10000:
                return sympy.Pow(a, b, evaluate=False)         # no huge powers computed while reading
            if op in ("add", "sub", "mul", "truediv", "pow", "matmul"):
                return _se_binop(op, a, b)
            return getattr(operator, op)(a, b)
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or type(node.ops[0]) not in _COMPARE:
                raise UnsafeText("this comparison")
            a, b = self.eval(node.left), self.eval(node.comparators[0])
            return getattr(sympy, _COMPARE[type(node.ops[0])])(a, b)
        raise UnsafeText(f"{type(node).__name__} is not allowed in a saved formula")

    def call(self, node: ast.Call) -> Any:
        if isinstance(node.func, ast.Name):
            fn = self.lookup(node.func.id, called=True)
        elif isinstance(node.func, ast.Call):
            fn = self.call(node.func)           # Function('f')(x)
        else:
            raise UnsafeText(f"a call of {type(node.func).__name__}")
        from_names = isinstance(node.func, ast.Name) and node.func.id in self.names
        if not (callable(fn) and (_constructor_ok(fn) or fn is Invalid or (from_names and not isinstance(fn, Basic)))):
            raise UnsafeText(f"{getattr(fn, '__name__', fn)!s} cannot be called in a saved formula")
        args = []
        for i, a in enumerate(node.args):
            if isinstance(a, ast.Starred):
                raise UnsafeText("* in a call")
            if fn is Invalid and i == 0 and isinstance(a, ast.Name):
                args.append(a.id)               # the head, named: Invalid(MatMul, A, B)
                continue
            args.append(self.eval(a))
        kwargs = {}
        for kw in node.keywords:
            if kw.arg is None or kw.arg.startswith("__"):
                raise UnsafeText("** in a call")
            kwargs[kw.arg] = self.eval(kw.value)
        takes_names = _name_taker(fn)
        for v in args:
            _check_strings(v, takes_names)
        for v in kwargs.values():
            _check_strings(v, takes_names)
        try:
            if _builds_as_evaluated(fn):
                with sympy.evaluate(True):
                    return fn(*args, **kwargs)
            return fn(*args, **kwargs)
        except Exception:
            # a node SymPy refuses (a step saved by an older version, which
            # let it through) reads back as an invalid node
            if kwargs or fn is Invalid or not _is_constructor(fn) or not all(isinstance(a, (Basic, int)) for a in args) \
                    or isinstance(fn, UndefinedFunction):
                raise
            return invalid(fn.__name__)(*[_basic(a) for a in args])


def _builds_as_evaluated(fn: Any) -> bool:
    """Constructors that compute nothing, but that unevaluated leave marks
    of their own: ``Integral(x, (x, 0, 1))`` built under ``evaluate(False)``
    holds ``1*x``.  A saved step is read with them evaluating."""
    from sympy.concrete.expr_with_limits import ExprWithLimits
    return isinstance(fn, type) and issubclass(fn, ExprWithLimits)


def _parse(text: str) -> ast.AST:
    try:
        return ast.parse(text.strip(), mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise UnsafeText(f"not a formula: {exc}") from None


def read_srepr(text: str, names: Optional[Dict[str, Any]] = None, evaluate: bool = False) -> Any:
    """``srepr`` text read back without running it, *unevaluated* unless
    ``evaluate`` - the node as it was saved (``sqrt(-3/4)`` stays itself
    rather than becoming ``sqrt(3)*I/2``).  Only calls of SymPy's
    constructors (and of ``names``: ``Placeholder``, the add-ons' node
    types) with literal, container or nested-call arguments are read;
    anything else raises :class:`UnsafeText`.  A node SymPy refuses is read
    as an invalid one."""
    tree = _parse(text)
    try:
        with sympy.evaluate(bool(evaluate)):
            return _Reader(text, names, source=False).eval(tree)
    except RecursionError:
        raise UnsafeText("the formula is nested too deeply") from None


def read_source(text: str, names: Optional[Dict[str, Any]] = None, *, python_numbers: bool = False,
                new_name: Optional[Callable[[str], Any]] = None) -> Any:
    """A line of SymPy source (``x**2 + sin(y)``, ``Matrix([[1, 2]])``) read
    without running it: operators, numbers, names and calls of SymPy's
    constructors, evaluated as SymPy would.  A name that is neither in
    ``names`` nor SymPy's is a new ``Symbol`` (``new_name(name)`` may say
    otherwise), a new name called is an undefined function.  With
    ``python_numbers`` numbers stay Python's (``1/2`` is ``0.5``), as they
    would in a script."""
    tree = _parse(text)
    try:
        return _Reader(text, names, source=True, python_numbers=python_numbers, new_name=new_name).eval(tree)
    except RecursionError:
        raise UnsafeText("the formula is nested too deeply") from None

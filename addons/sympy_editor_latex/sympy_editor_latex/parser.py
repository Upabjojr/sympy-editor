"""Reading LaTeX into SymPy, ambiguities and all.

SymPy's Lark-based LaTeX parser (``sympy.parsing.latex.lark``) parses with an
Earley parser that keeps *every* reading of an ambiguous string - ``f(x)`` is
a function applied or a product, ``\\sin x \\cos y`` is ``sin(x) cos(y)`` or
``sin(x cos(y))`` - and hands back a forest.  This module walks that forest:

* every ambiguous node is a **choice point**, named by its rule and the span
  of the text it covers (``"_expression_mul@0-13"``);
* a first reading picks an alternative at each point by a few conventions
  (:func:`tree_cost`: a function without parentheses takes the product that
  follows but not a sum nor another function, ``f``/``g``/``h`` and the
  document's own functions are applied while other letters multiply...);
* the caller's ``choices`` (``{point: alternative index}``) override those,
  and the answer lists the points with the whole expression under each of
  their alternatives, so a user can pick another reading;
* names that usually stand for constants (``\\pi``, ``e``, ``i``...) are read
  as symbols by the grammar and turned into SymPy's constants here, each one
  on or off (:data:`CONSTANTS`, ``constants={"pi": False}``).

The grammar is the add-on's own copy of SymPy's (``static/grammar``), with
``\\pi`` and the other letters SymPy's grammar leaves out.

A reading is asked for at every pause in the typing, with the editor waiting
for it, so it is bounded on every side: the text by its length and by the
time its reading may take (:data:`MAX_LENGTH`, :data:`MAX_SECONDS`), the numbers
by their size (a power or a factorial too large to write out is kept as
written, :func:`heavy_power`), the choices by how many are worked out
(:data:`MAX_POINTS`, :data:`MAX_ALTERNATIVES`, :data:`MAX_TRIALS`).  And it
keeps nothing on the reader between two calls: one reader serves every
document, from any thread.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sympy
from sympy import Basic, Symbol

GRAMMAR_DIR = Path(__file__).parent / "static" / "grammar"

#: Names the grammar reads as symbols that usually mean a constant: name ->
#: (the constant, whether it is applied unless the user says otherwise, a label).
CONSTANTS: Dict[str, Tuple[Basic, bool, str]] = {
    "pi": (sympy.pi, True, "π, the circle constant"),
    "e": (sympy.E, True, "Euler's number (the base of exp)"),
    "i": (sympy.I, True, "the imaginary unit"),
    "gamma": (sympy.EulerGamma, False, "the Euler–Mascheroni constant"),
    "E": (sympy.E, False, "Euler's number (the base of exp)"),
    "I": (sympy.I, False, "the imaginary unit"),
    "j": (sympy.I, False, "the imaginary unit (engineering)"),
}

#: Function rules whose argument is *not* delimited by the rule itself (``\sin x``):
#: how far the argument reaches is the ambiguity these conventions settle.
BARE_FUNCTIONS = frozenset("""sin cos tan csc sec cot sin_power cos_power tan_power csc_power sec_power cot_power
    arcsin arccos arctan arccsc arcsec arccot sinh cosh tanh coth sech csch asinh acosh atanh acoth asech acsch exponential log
    determinant trace adjugate""".split())
#: Every rule that applies a function (the delimited ones included).
FUNCTIONS = BARE_FUNCTIONS | frozenset("function_applied function_power abs floor ceil square_root conjugate min max".split())
#: Letters that name a function when followed by parentheses, by convention.
FUNCTION_LETTERS = frozenset("f g h F G H".split())
#: Greek letters that do: what SymPy writes its own functions with
#: (``\Gamma\left(x\right)``), by the number of arguments.
GREEK_FUNCTIONS: Dict[Tuple[str, int], Any] = {
    ("Gamma", 1): sympy.gamma, ("Gamma", 2): sympy.uppergamma, ("gamma", 2): sympy.lowergamma,
    ("zeta", 1): sympy.zeta, ("zeta", 2): sympy.zeta,
}
#: ... and the ones among them applied by convention, as ``f`` is.
GREEK_FUNCTION_LETTERS = frozenset(("Gamma", "zeta"))

#: What an unfinished text is told: it stops in the middle of an expression
#: (``\frac{x``, ``x +``), or of a command (``\fr``) - being typed, not wrong.
INCOMPLETE = "Not finished yet: the LaTeX stops in the middle of an expression"

#: The longest text read, in characters.  The parser's work grows faster than
#: the text does, and the editor waits for the answer.
MAX_LENGTH = 1000
#: ... and the time a reading may take, in seconds of the processor's: a
#: text within the length that still asks for more (a run of three hundred
#: letters is one product nested three hundred deep, which the parser goes
#: through again at every letter) is refused when they are spent.  An
#: ordinary formula takes a tenth of one.
MAX_SECONDS = 3.0
#: How many ambiguous parts are offered as choices (the first ones in the
#: text), how many readings of one part are shown - the chosen one always -
#: and how many readings are worked out to find them.
MAX_POINTS = 12
MAX_ALTERNATIVES = 8
MAX_TRIALS = 160

#: A power of numbers is computed only when its result can be written out:
#: Python refuses to print a whole number of more than 4300 digits, which is
#: about 14000 bits.  The rule is the one the editor reads saved formulas by
#: (``sympy_editor.invalid``: a whole exponent above 10000, or the exponent
#: times the bits of the base above a limit), with the limit brought down to
#: what can be shown.  A function is not evaluated at a whole number above
#: :data:`LARGE_ARGUMENT` (``1000!`` has 2568 digits, ``2000!`` 5736).
LARGE_EXPONENT = 10000
LARGE_BITS = 13000
LARGE_ARGUMENT = 1000


@dataclass
class Point:
    """A choice point: the alternatives of one ambiguous node."""
    key: str
    rule: str
    start: int
    end: int
    count: int
    fragment: str = ""
    choice: int = 0
    options: List[Dict[str, Any]] = field(default_factory=list)   # {"src", "latex"} per alternative


class TooLong(Exception):
    """The text asks for more work than a reading may take."""


class Unreadable(ValueError):
    """The text parses, and what it says cannot be an expression; the
    message is for the user."""


def _lark():
    try:
        import lark  # noqa: F401
    except ImportError:  # pragma: no cover - the add-on refuses to activate before this
        raise ImportError("The LaTeX add-on needs the lark package: pip install lark") from None
    import lark as _l
    return _l


def _name(data) -> str:
    return data if isinstance(data, str) else str(getattr(data, "value", data))


# -- names ------------------------------------------------------------------------

#: The decorations of the grammar (``CMD_DECORATION``) as SymPy names them: a
#: symbol called ``vhat`` prints as ``\hat{v}``, ``Abold`` as a bold A.
DECORATIONS = {"vec": "vec", "hat": "hat", "widehat": "hat", "bar": "bar", "tilde": "tilde", "widetilde": "tilde",
               "dot": "dot", "ddot": "ddot", "mathbf": "bold", "boldsymbol": "bold",
               "check": "check", "breve": "breve", "acute": "acute", "grave": "grave"}

_TOKEN_NAME = re.compile(r"\\?([A-Za-z]+)('*)(?:_\{?\\?([A-Za-z0-9]+)('*)\}?)?$")


def symbol_name(base: str, sub: str = "", primes: int = 0, decoration: str = "") -> str:
    """The name of a symbol read from LaTeX, as SymPy's own printer expects
    it: ``x_1`` for ``x_{1}``, ``a_ij``, ``vhat`` for ``\\hat{v}``,
    ``xprime`` for ``x'`` - names that print as the LaTeX they were read from
    *and* can be typed in the editor's source line.  (They used to be the
    LaTeX itself - ``x_{1}``, ``\\hat{v}`` - which the source line could not
    read back: one such name in the formula, and no edit went through it.)"""
    return base + decoration + "prime" * primes + ("_" + sub if sub else "")


def decorated_name(name: str, decoration: str) -> str:
    """``name`` under a decoration: it goes on the letter, before a
    subscript (``\\hat{x_1}`` is ``xhat_1``)."""
    base, sep, sub = name.partition("_")
    return base + decoration + sep + sub


def token_name(text: str) -> Optional[str]:
    """The name of the symbol a token of the grammar spells (``x``, ``x'``,
    ``\\alpha_{i}``, ``x_{10}``...), None when it spells none."""
    m = _TOKEN_NAME.match(str(text).strip())
    if not m:
        return None
    base, primes, sub, sub_primes = m.groups()
    return symbol_name(base, (sub or "") + "prime" * len(sub_primes or ""), len(primes))


def sympy_function(name: str, nargs: Optional[int] = None):
    """The function of SymPy called ``name`` (``asin``, ``re``, ``sinc``:
    what ``\\operatorname{name}`` applies), None when there is none.  Only
    classes of functions: nothing else of SymPy's is called by a text."""
    from sympy.core.function import AppliedUndef, FunctionClass, UndefinedFunction
    obj = getattr(sympy, name, None) if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name or "") else None
    if not isinstance(obj, FunctionClass) or isinstance(obj, UndefinedFunction):
        return None
    if obj in (sympy.Function, sympy.WildFunction, AppliedUndef) or issubclass(obj, (sympy.Piecewise, sympy.WildFunction)):
        return None
    return obj


# -- large numbers ------------------------------------------------------------------

def _whole(a: Any) -> Optional[int]:
    """``a`` as a Python int when it is a whole number, else None."""
    if isinstance(a, bool):
        return None
    if isinstance(a, (int, sympy.Integer)):
        return int(a)
    return None


def heavy_power(base: Any, exponent: Any) -> bool:
    """Whether ``base ** exponent`` is a number too large to compute while
    reading - and to write out afterwards: whole or rational base, whole
    exponent (see :data:`LARGE_BITS`)."""
    n = _whole(exponent)
    if n is None or isinstance(base, bool) or not isinstance(base, (int, sympy.Rational)):
        return False
    base = sympy.sympify(base)
    if base.q == 1 and abs(base.p) <= 1:
        return False                                     # 0, 1 and -1: whatever the exponent
    bits = max(int(base.p).bit_length(), int(base.q).bit_length())
    return abs(n) > LARGE_EXPONENT or abs(n) * bits > LARGE_BITS


#: Where SymPy keeps the functions whose value at a whole number is a whole
#: number, worked out to the last digit: the factorials, the binomial
#: coefficient, Fibonacci's numbers, the gamma function.
_COMPUTING = ("sympy.functions.combinatorial", "sympy.functions.special.gamma_functions", "sympy.ntheory")


def heavy_call(fn: Any, args) -> bool:
    """Whether calling ``fn`` would compute with a large whole number:
    ``factorial``, ``binomial``, ``fibonacci``... given one above
    :data:`LARGE_ARGUMENT`.  The other functions keep a number they are
    given (``sin(2000)``), and the ones the text only names (``f``) compute
    nothing."""
    if fn is sympy.Pow:
        return len(args) == 2 and heavy_power(args[0], args[1])
    if not str(getattr(fn, "__module__", "")).startswith(_COMPUTING):
        return False
    return any(abs(n) > LARGE_ARGUMENT for n in map(_whole, args) if n is not None)


def _large(value: Any) -> bool:
    return isinstance(value, sympy.Rational) and max(int(value.p).bit_length(), int(value.q).bit_length()) > LARGE_BITS // 2


def large_numbers(expr: Any) -> bool:
    """Whether ``expr`` holds a number kept as written because it is too
    large to compute (``2**20000``, ``factorial(20000)``), or one large
    enough that what is built on it should not be computed either: a sum or
    a product made with it is made unevaluated."""
    if not isinstance(expr, Basic):
        return False
    for node in sympy.preorder_traversal(expr):
        if _large(node):
            return True
        if isinstance(node, sympy.Pow) and heavy_power(node.base, node.exp):
            return True
        if isinstance(node, sympy.Function) and heavy_call(node.func, node.args):
            return True
    return False


# -- differentials --------------------------------------------------------------------

class _Differential:
    """``d`` followed by something (``dx``, ``d x^2``, ``dx\\,dy``): the
    variable of a derivative under a fraction bar, and the product ``d*x``
    anywhere else - which of the two is known only to what holds it."""
    __slots__ = ("of",)

    def __init__(self, of):
        self.of = of


class _Beside:
    """A factor with a differential after it (the ``x dy`` of ``dx\\,dy``)."""
    __slots__ = ("left", "right")

    def __init__(self, left, right):
        self.left, self.right = left, right


class _Operator:
    """``\\frac{d}{dx}``: a derivative waiting for what it differentiates."""
    __slots__ = ("wrt",)

    def __init__(self, wrt):
        self.wrt = wrt


#: The rules that know what to do with a differential; any other gets the
#: product.
DIFFERENTIAL_RULES = frozenset(("fraction", "adjacent_expressions", "group_curly_parentheses"))


# -- the conventions ------------------------------------------------------------------

def _parenthesised(tree) -> bool:
    """Whether ``tree`` is a pair of parentheses: ``(x)``, or ``{\\left(x
    \\right)}`` as SymPy writes the argument of a function."""
    lark = _lark()
    if not isinstance(tree, lark.Tree):
        return False
    kind = _name(tree.data)
    if kind == "group_round_parentheses":
        return True
    if kind == "group_curly_parentheses":
        inner = [c for c in tree.children if isinstance(c, lark.Tree)]
        return len(inner) == 1 and _name(inner[0].data) == "group_round_parentheses"
    return False


def _names_function(head, known_functions=()) -> bool:
    """Whether what stands before a pair of parentheses is a function by
    convention: ``f``, ``g``, ``h`` (primes and all: ``f'``), a function the
    document uses, ``\\Gamma``, a name under ``\\operatorname`` or one SymPy
    has a function for."""
    lark = _lark()
    if isinstance(head, lark.Tree):
        kind = _name(head.data)
        if kind == "superscript" and head.children:          # f^{2}(x): the f
            return _names_function(head.children[0], known_functions)
        if kind != "multi_letter_symbol" or len(head.children) < 3:
            return False
        command, name = str(head.children[0]), str(head.children[2]).strip().rstrip("'")
        return command == "\\operatorname" or name in known_functions or sympy_function(name) is not None
    if not isinstance(head, lark.Token) or head.type in ("DIGIT", "PARTIAL"):
        return False
    bare = str(head).lstrip("\\").rstrip("'")
    return (bare in FUNCTION_LETTERS or bare in GREEK_FUNCTION_LETTERS or bare in known_functions
            or token_name(str(head)) in known_functions)


def _local_cost(node, known_functions, contains_function) -> int:
    """What one node of a parse tree adds to :func:`tree_cost`."""
    lark = _lark()
    kind = _name(node.data)
    kids = [c for c in node.children if isinstance(c, lark.Tree)]
    total = 0
    if kind in BARE_FUNCTIONS and kids:
        arg = kids[-1]
        akind = _name(arg.data)
        if akind in ("add", "sub"):
            total += 2                                   # the argument swallowed a sum
        elif contains_function(arg):
            total += 2                                   # ... or another function
        elif akind in ("adjacent_expressions", "superscript"):
            first = [c for c in arg.children if isinstance(c, lark.Tree)]
            if first and _parenthesised(first[0]):
                total += 2                               # (x) y: the parentheses ended the argument
    elif kind == "adjacent_expressions" and node.children:
        if len(kids) >= 2:
            left, right = kids[0], kids[-1]
            if _name(left.data) in BARE_FUNCTIONS and not contains_function(right):
                total += 1                               # the function stopped short of its product
        first, last = node.children[0], node.children[-1]
        if isinstance(first, lark.Tree) and _name(first.data) == "div":
            total += 1                                   # a/b c: the divisor takes the product after it
        if len(node.children) == 2 and _parenthesised(last) and _names_function(first, known_functions):
            total += 1                                   # f (x): a product, where f is a function by convention
    elif kind in ("function_applied", "function_power") and node.children:
        if not _names_function(node.children[0], known_functions):
            total += 1                                   # a(b+c): a product, unless a is a function here
    return total


def tree_cost(tree, known_functions=()) -> int:
    """How far a parse tree strays from the usual conventions: the reading
    with the lowest cost is offered first.

    - a function written without parentheses (``\\sin x``) takes the product
      after it, but not a sum (``\\sin x + 1`` is ``sin(x) + 1``) nor another
      function (``\\sin x \\cos y`` is a product of two sines);
    - when its argument *is* in parentheses, the parentheses end it
      (``\\ln(x) y`` is ``y ln(x)``, ``\\sin(x)^2`` is ``sin(x)^2``);
    - a letter followed by parentheses is a function when it is ``f``, ``g``,
      ``h`` (or their capitals), a function the document already uses, or a
      name that says so (``\\operatorname{sinc}``, ``\\Gamma``), and a factor
      otherwise (``a(b+c)`` is ``a (b + c)``);
    - a divisor takes the product written after it (``a/bc`` is ``a/(bc)``).
    """
    return _Costs(known_functions).of(tree)


class _Costs:
    """:func:`tree_cost`, kept for the trees of one reading: the alternatives
    of a choice share most of their sub-trees, and each is weighed once."""

    def __init__(self, known_functions=()):
        self.known_functions = known_functions
        self._seen: Dict[int, Tuple[Any, int, bool]] = {}      # id -> (the tree, kept alive; its cost; whether it holds a function)

    def _note(self, tree) -> Tuple[Any, int, bool]:
        lark = _lark()
        cost, holds = 0, _name(tree.data) in FUNCTIONS
        for child in tree.children:
            if isinstance(child, lark.Tree):
                _t, c, h = self._seen[id(child)]
                cost += c
                holds = holds or h
        cost += _local_cost(tree, self.known_functions, self.holds_function)
        entry = self._seen[id(tree)] = (tree, cost, holds)
        return entry

    def _entry(self, tree) -> Tuple[Any, int, bool]:
        lark = _lark()
        found = self._seen.get(id(tree))
        if found is not None:
            return found
        stack = [(tree, False)]
        while stack:                                      # children first, and no recursion: trees are deep
            node, done = stack.pop()
            if id(node) in self._seen:
                continue
            if done:
                self._note(node)
                continue
            stack.append((node, True))
            stack.extend((c, False) for c in node.children if isinstance(c, lark.Tree) and id(c) not in self._seen)
        return self._seen[id(tree)]

    def holds_function(self, tree) -> bool:
        return self._entry(tree)[2]

    def of(self, what) -> int:
        lark = _lark()
        if isinstance(what, lark.Tree):
            return self._entry(what)[1]
        if isinstance(what, (list, tuple)):
            return sum(self.of(w) for w in what)
        return 0


_COMMAND = re.compile(r'"\\\\([A-Za-z]+)"')


def guard_commands(grammar: str, others: str = "") -> str:
    r"""The grammar with every command that begins a longer command of it
    kept from matching there: ``"\\sin"`` becomes ``/\\sin(?!h)/`` beside
    ``"\\sinh"``.

    TeX reads all the letters after a backslash as one name, but the reader
    lets a command run into the letters that follow it - ``\sinx`` is
    sin(x), ``\pix`` is pi*x, as people type - and so it also read
    ``\sinh x`` as sin(h*x), even preferring that to sinh(x).  Now a command
    gives way to a longer one the grammar knows, and any other letters after
    it read as before.  ``others``: grammar text (the imported Greek letters)
    whose commands count as longer ones too; it is not rewritten itself."""
    names = set(_COMMAND.findall(grammar)) | set(_COMMAND.findall(others))

    def guarded(m):
        name = m.group(1)
        tails = sorted(other[len(name):] for other in names if other != name and other.startswith(name))
        return "/\\\\%s(?!%s)/" % (name, "|".join(tails)) if tails else m.group(0)

    return _COMMAND.sub(guarded, grammar)


# -- the time a reading may take ------------------------------------------------------------

#: What the reading this thread is making goes by: when it must be over
#: (``until``), and what the parser has predicted in the column it is at
#: (``predicted``, see :class:`_Predictions`).
_work = threading.local()


def _clock() -> float:
    """The processor's time spent by this thread, where it is kept (a busy
    machine takes none of a reading's allowance); the time gone by otherwise -
    in a browser (Pyodide) too, which has one thread and may not keep it."""
    if sys.platform == "emscripten":
        return time.perf_counter()
    try:
        return time.thread_time()
    except (AttributeError, OSError):  # pragma: no cover - a platform without it
        return time.perf_counter()


def _begin() -> None:
    _work.until = _clock() + MAX_SECONDS
    _work.calls = 0


def _end() -> None:
    _work.until = None


def _late() -> bool:
    """Whether the reading this thread is making has had its time."""
    until = getattr(_work, "until", None)
    return until is not None and _clock() > until


def _in_time() -> None:
    """Called wherever a reading does its work: once in a while it looks at
    the clock, and stops the reading that has had its time."""
    if getattr(_work, "until", None) is None:
        return
    _work.calls = calls = getattr(_work, "calls", 0) + 1
    if not calls % 64 and _clock() > _work.until:
        raise TooLong()


def _timed(match):
    """The parser's token matcher, watching the clock: an Earley parse
    cannot be asked how far it is nor told to stop, but it asks this at
    every step, for every item it has open."""
    def timed(*args, **kwargs):
        _in_time()
        return match(*args, **kwargs)
    return timed


class _Predictions(dict):
    """The parser's table of what to expect for each symbol of the grammar,
    answering once for each rule in each column of the parse.

    Where an expression may begin, a hundred rules may: lark makes an item
    for each of them every time something in the column expects one of the
    grammar's symbols, and finds nearly all of them there already - which
    was four fifths of the time a reading took.  What has been handed out
    since the column was begun (:func:`_by_column`) is left out: an item
    made twice is dropped by the parser all the same."""

    def __getitem__(self, symbol):
        rules = dict.__getitem__(self, symbol)
        seen = getattr(_work, "predicted", None)
        if seen is None:
            return rules
        if symbol in seen:
            return ()
        seen.add(symbol)
        fresh = [rule for rule in rules if id(rule) not in seen]
        seen.update(id(rule) for rule in fresh)
        return fresh


def _by_column(step):
    """The parser's step for one column, telling :class:`_Predictions` where
    a column begins and ends.  Outside a step the table answers in full."""
    def stepped(*args, **kwargs):
        _work.predicted = set()
        try:
            return step(*args, **kwargs)
        finally:
            _work.predicted = None
    return stepped


def _run(task):
    """Run ``task``, a generator that yields the generators whose results it
    needs and returns its own: a recursion kept on a list.  A forest is as
    deep as the formula nests - two hundred braces, or a product of two
    hundred letters, each inside the one before - which is deeper than
    Python lets calls nest."""
    stack = [task]
    value, error = None, None
    while stack:
        top = stack[-1]
        try:
            if error is not None:
                raised, error = error, None
                asked = top.throw(raised)
            else:
                asked = top.send(value)
        except StopIteration as stop:
            stack.pop()
            value = stop.value
            continue
        except Exception as exc:  # noqa: BLE001 - handed to whoever asked for this result
            stack.pop()
            if not stack:
                raise
            value, error = None, exc
            continue
        stack.append(asked)
        value = None
    return value


class _Cycle(Exception):
    """A derivation that holds itself: not a reading."""


@dataclass
class _Trial:
    """One alternative tried at one point, everything else as chosen."""
    key: str
    index: int
    start: int
    end: int


class _Forest:
    """The forest of one reading, walked: one tree of it under the choices
    taken, and the same with another alternative at one point.

    At an ambiguous node the alternative `fixed` names is taken; at any
    other the decision is *local*: each alternative is built (this same
    way, inside) and the one that costs least under tree_cost wins - a
    decision that depends on nothing outside the node, made once.  What is
    built is kept by node: trying another alternative at one point builds
    again only what holds that point, not the whole text - which used to be
    walked afresh for every alternative of every point, deciding everything
    inside it again each time."""

    def __init__(self, callbacks, fixed: Dict[str, int], known_functions=()):
        self.callbacks = callbacks
        self.fixed = fixed
        self.decided: Dict[str, int] = {}
        self.costs = _Costs(known_functions)
        self._children: Dict[int, list] = {}
        self._built: Dict[int, Any] = {}
        self._open: set = set()

    def children(self, node) -> list:
        kids = self._children.get(id(node))
        if kids is None:
            kids = self._children[id(node)] = list(node.children)
        return kids

    @staticmethod
    def key(node) -> Tuple[str, str]:
        rule = _name(getattr(node.s, "name", node.s))
        return rule, "%s@%s-%s" % (rule, node.start, node.end)

    def tree(self, root, trial: Optional[_Trial] = None):
        """The tree under the choices taken - with `trial`, under that
        alternative at that point."""
        return _run(self._symbol(root, trial, {} if trial is not None else None))

    def _symbol(self, node, trial, local):
        _in_time()
        # What holds the point tried is built again, for this trial; anything
        # else is as it was built under the choices taken.
        holds = trial is not None and node.start <= trial.start and trial.end <= node.end
        kept = local if holds else self._built
        if id(node) in kept:
            return kept[id(node)]
        if id(node) in self._open:
            raise _Cycle()
        self._open.add(id(node))
        try:
            kids = self.children(node)
            index = 0
            if len(kids) > 1:
                _rule, key = self.key(node)
                if holds and key == trial.key:
                    index = trial.index
                elif key in self.fixed and 0 <= self.fixed[key] < len(kids):
                    index = self.fixed[key]
                elif key in self.decided:
                    index = self.decided[key]
                else:
                    index = yield self._decide(key, kids)
            if not 0 <= index < len(kids):
                raise _Cycle()
            result = yield self._packed(kids[index], trial if holds else None, local if holds else None)
        finally:
            self._open.discard(id(node))
        kept[id(node)] = result
        return result

    def _decide(self, key, kids):
        """The alternative that costs least - of all of them: the
        conventional reading of seven logarithms in a row is the ninth."""
        best, best_cost = 0, None
        for i, kid in enumerate(kids):
            try:
                built = yield self._packed(kid, None, None)
            except TooLong:
                raise
            except Exception:  # noqa: BLE001 - an alternative that cannot be built
                continue
            cost = self.costs.of(built)
            if best_cost is None or cost < best_cost:
                best, best_cost = i, cost
        self.decided[key] = best
        return best

    def _packed(self, packed, trial, local):
        children = []
        for side in (packed.left, packed.right):
            if side is None:
                continue
            token = getattr(side, "token", None)
            if token is not None or not hasattr(side, "children"):
                children.append(token if token is not None else side)
                continue
            got = yield self._symbol(side, trial, local)
            if side is packed.left and getattr(side, "is_intermediate", False) and isinstance(got, list):
                children.extend(got)
            else:
                children.append(got)
        if packed.parent.is_intermediate:
            return children
        tree = self.callbacks[packed.rule](children)
        self.costs.of(tree)
        return tree

    def points(self, root) -> Dict[str, Point]:
        """The choice points of the tree under the choices taken."""
        found: Dict[str, Point] = {}
        seen, stack = set(), [root]
        while stack:
            node = stack.pop()
            if node is None or getattr(node, "token", None) is not None or not hasattr(node, "children") or id(node) in seen:
                continue
            seen.add(id(node))
            kids = self.children(node)
            index = 0
            if len(kids) > 1:
                rule, key = self.key(node)
                index = self.fixed[key] if key in self.fixed and 0 <= self.fixed[key] < len(kids) else self.decided.get(key, 0)
                found.setdefault(key, Point(key, rule, int(node.start), int(node.end), len(kids), choice=index))
            stack.extend((kids[index].right, kids[index].left))
        return found


def _transformer_class():
    """SymPy's transformer, with the rules the grammar adds."""
    from sympy.parsing.latex.lark.transformer import TransformToSymPyExpr
    lark = _lark()
    d = Symbol("d")

    class Transformer(TransformToSymPyExpr):
        """One is made for each reading: what it holds - the choices, the
        text, what it has worked out - is that reading's alone."""

        def __init__(self, text: str = "", choices: Optional[Dict[str, int]] = None, functions: Optional[Dict[str, Any]] = None):
            super().__init__()
            self.text = text
            #: The reading's own choices (the derivative points below) and
            #: the points met.
            self.choices: Dict[str, int] = dict(choices or {})
            self.points: Dict[str, Point] = {}
            #: The document's functions, by name.
            self.functions = dict(functions or {})
            #: The names given with a command (\operatorname{asin}): they may
            #: be SymPy's functions; a letter never is.
            self.named: set = set()
            #: What holds a number kept as written, by id (and kept alive):
            #: whatever is built on it is built unevaluated.
            self.heavy: Dict[int, Any] = {}
            self._values: Dict[int, Tuple[Any, Any]] = {}

        # -- walking a tree ---------------------------------------------------

        def value(self, tree, within: Optional[Tuple[int, int]] = None):
            """``tree`` as SymPy.  Every sub-tree is worked out once: the
            trees of two readings share all but what holds the point they
            differ at.  ``within``: a span of the text whose reading has
            changed with the choices (a derivative point), so that what
            holds it is worked out again."""
            fresh: Dict[int, Tuple[Any, Any]] = {}

            def kept(t):
                if within is None:
                    return self._values
                meta = getattr(t, "meta", None)
                start, end = getattr(meta, "start_pos", None), getattr(meta, "end_pos", None)
                if start is None or end is None or (start <= within[0] and within[1] <= end):
                    return fresh
                return self._values

            stack = [(tree, False)]
            while stack:
                node, done = stack.pop()
                store = kept(node)
                if id(node) in store:
                    continue
                if not done:
                    stack.append((node, True))
                    stack.extend((c, False) for c in node.children if isinstance(c, lark.Tree) and id(c) not in kept(c))
                    continue
                args = [kept(c)[id(c)][1] if isinstance(c, lark.Tree) else self._token(c) for c in node.children]
                store[id(node)] = (node, self._rule(node, args))
            return kept(tree)[id(tree)][1]

        def _token(self, token):
            method = getattr(self, getattr(token, "type", ""), None) if isinstance(token, lark.Token) else None
            return method(token) if method is not None else token

        def _rule(self, node, args):
            rule = _name(node.data)
            if rule not in DIFFERENTIAL_RULES:
                args = [self.plain(a) for a in args]
            method = getattr(self, rule, None)
            if method is None:
                return lark.Tree(node.data, args, node.meta)            # a rule with nothing to say: its children, read
            if any(id(a) in self.heavy for a in args):
                with sympy.evaluate(False):
                    return self._heavy(method(args))
            result = method(args)
            return self._heavy(result) if _large(result) else result

        def _heavy(self, value):
            if isinstance(value, (_Differential, _Beside, _Operator)) or (isinstance(value, Basic) and (value.args or _large(value))):
                self.heavy[id(value)] = value
            return value

        def plain(self, value):
            """``value`` as an expression: a differential no fraction has
            taken for its variable is the product d*x."""
            if isinstance(value, _Operator):
                raise Unreadable("a derivative with nothing after it to differentiate")
            if not isinstance(value, (_Differential, _Beside)):
                return value
            left, right = (d, value.of) if isinstance(value, _Differential) else (value.left, value.right)
            left, right = self.plain(left), self.plain(right)
            if id(value) in self.heavy:
                return self._heavy(sympy.Mul(left, right, evaluate=False))
            return sympy.Mul(left, right)

        # -- names ------------------------------------------------------------
        # SymPy hands lark's Tokens (a str subclass) to Symbol and Function;
        # here the names are plain strings, so they compare equal to the
        # user's own Symbol("f") and print without the Token in srepr - and
        # they are the names SymPy's printer expects (symbol_name).

        def SYMBOL(self, token):
            return Symbol(token_name(str(token)) or str(token))

        GREEK_SYMBOL_WITH_PRIMES = SYMBOL
        LATIN_SYMBOL_WITH_LATIN_SUBSCRIPT = SYMBOL
        LATIN_SYMBOL_WITH_GREEK_SUBSCRIPT = SYMBOL
        GREEK_SYMBOL_WITH_LATIN_SUBSCRIPT = SYMBOL
        GREEK_SYMBOL_WITH_GREEK_SUBSCRIPT = SYMBOL

        def PARTIAL(self, token):
            return d                                      # \partial behaves as the letter d does

        def multi_letter_symbol(self, tokens):
            # words are joined as a subscript is: a name with a space in it
            # cannot be typed back
            text = "_".join(str(tokens[2]).split())
            base = text.rstrip("'")
            primes = len(text) - len(base)
            tail = str(tokens[4]) if len(tokens) == 5 else ""
            sub = ""
            if tail.startswith("_"):                       # a subscript, written as x_d is: ab_d
                sub = tail[1:].strip("{}")
                sub = sub.rstrip("'") + "prime" * (len(sub) - len(sub.rstrip("'")))
            else:
                primes += len(tail)
            self.named.add(base)
            return Symbol(symbol_name(base, sub, primes))

        def decorated_symbol(self, tokens):
            command = str(tokens[0]).lstrip("\\")
            inner = [t for t in tokens[1:] if not (isinstance(t, lark.Token) and t.type in ("L_BRACE", "R_BRACE"))][0]
            name = inner.name if isinstance(inner, Symbol) else str(inner)
            return Symbol(decorated_name(name, DECORATIONS.get(command, command)))

        def symbol_prime(self, tokens):
            base, sep, sub = tokens[0].name.partition("_")
            return Symbol(base + "prime" * len(str(tokens[1])) + sep + sub)

        # -- functions ----------------------------------------------------------

        def list_of_expressions(self, tokens):
            # a list, not SymPy's filter: what is worked out once is read twice
            return [t for t in tokens if not isinstance(t, lark.Token)]

        def _applied(self, head, args):
            name = head.name if isinstance(head, Symbol) else str(head)
            args = list(args)
            fn = self.functions.get(name) or GREEK_FUNCTIONS.get((name, len(args)))
            if fn is None and name in self.named:
                fn = sympy_function(name)
            if fn is None:
                return sympy.Function(name)(*args)
            try:
                return self.call(fn, *args)
            except TypeError:                              # not the arguments SymPy's function takes: one of that name
                return sympy.Function(name)(*args)

        def call(self, fn, *args):
            """``fn(*args)`` - as written, not worked out, where that would
            compute with a number too large to."""
            if heavy_call(fn, args):
                return self._heavy(fn(*args, evaluate=False))
            return fn(*args)

        def function_applied(self, tokens):
            return self._applied(tokens[0], next(t for t in tokens if isinstance(t, list)))

        def function_power(self, tokens):
            return self.power(self._applied(tokens[0], next(t for t in tokens if isinstance(t, list))), tokens[2])

        def power(self, base, exponent):
            if isinstance(base, Basic) and isinstance(exponent, Basic) and heavy_power(base, exponent):
                return self._heavy(sympy.Pow(base, exponent, evaluate=False))
            return sympy.Pow(base, exponent)

        def superscript(self, tokens):
            if len(tokens) == 3 and isinstance(tokens[0], Basic) and isinstance(tokens[2], Basic) \
                    and heavy_power(tokens[0], tokens[2]):
                return self.power(tokens[0], tokens[2])
            return super().superscript(tokens)

        def factorial(self, tokens):
            return self.call(sympy.factorial, tokens[0])

        def binomial(self, tokens):
            return self.call(sympy.binomial, tokens[1], tokens[2])

        # the hyperbolic functions SymPy's grammar and transformer lack
        def coth(self, tokens):
            return sympy.coth(tokens[1])

        def sech(self, tokens):
            return sympy.sech(tokens[1])

        def csch(self, tokens):
            return sympy.csch(tokens[1])

        def acoth(self, tokens):
            return sympy.acoth(tokens[1])

        def asech(self, tokens):
            return sympy.asech(tokens[1])

        def acsch(self, tokens):
            return sympy.acsch(tokens[1])

        # -- matrices -------------------------------------------------------------

        @staticmethod
        def _same_environment(tokens):
            """\\begin{pmatrix} ... \\end{bmatrix} is two halves of two
            matrices: the grammar matches each end by itself."""
            begin = re.search(r"\\begin\{([A-Za-z]+)\}", str(tokens[0]))
            end = re.search(r"\\end\{([A-Za-z]+)\}", str(tokens[-1]))
            if begin and end and begin.group(1) != end.group(1):
                raise Unreadable("\\begin{%s} is closed by \\end{%s}" % (begin.group(1), end.group(1)))

        def matrix(self, tokens):
            self._same_environment(tokens)
            return super().matrix(tokens)

        def determinant(self, tokens):
            if len(tokens) == 3:
                self._same_environment(tokens)
            return super().determinant(tokens)

        # -- differentials and derivatives -------------------------------------------

        def adjacent_expressions(self, tokens):
            left, right = tokens[0], tokens[1]
            if isinstance(left, _Operator):
                return sympy.Derivative(self.plain(right), *left.wrt)
            left = self.plain(left)
            if left == d and isinstance(left, Symbol):
                # a d before something: a differential if a fraction takes
                # it for one, the product d*x if nothing does
                return _Differential(right)
            if isinstance(right, (_Differential, _Beside)):
                return _Beside(left, right)
            right = self.plain(right)                     # x \frac{d}{dx}: an operator with nothing after it
            if "sympy.physics.quantum" in sys.modules:
                # a ket beside a bra is SymPy's to read - when there can be
                # one: asking it means importing the quantum package, which
                # took the first product of a session a second
                return super().adjacent_expressions([left, right])
            return sympy.Mul(left, right)

        @staticmethod
        def _variables(below):
            """What a denominator differentiates by - ``dx``, ``dx^2``,
            ``dx\\,dy``: ``[(x, 1)]``, ``[(x, 2)]``, ``[(x, 1), (y, 1)]`` - or
            None when it is not a row of differentials."""
            found = []
            while isinstance(below, _Differential):
                var, below = below.of, None
                if isinstance(var, _Beside):
                    var, below = var.left, var.right
                order = 1
                if isinstance(var, sympy.Pow) and var.exp.is_Integer and var.exp > 1:
                    var, order = var.base, int(var.exp)
                if not isinstance(var, Symbol):
                    return None
                found.append((var, order))
                if below is None:
                    return found
            return None

        def fraction(self, tokens):
            # SymPy reads \\frac{d}{dx} as an operator awaiting its operand
            # ("derivative", x) and forgets the numerator of \\frac{dy}{dx}.
            # Here a differential over differentials is a derivative
            # (\\frac{dy}{dx}; \\frac{\\partial^2 f}{\\partial x \\partial y}
            # by two variables), and d^n over them (the numerator d**n alone)
            # the operator.  Any other numerator over a differential is a
            # division by d times the variable (\\frac{1}{dx} is 1/(d x)) -
            # except one that is d**n times something else (\\frac{d^2
            # y}{dx^2}, \\frac{b d}{dt}): that is the n-th derivative of the
            # rest or a division, a choice point of the reading (a derivative
            # when the numerator begins with the d, by convention).
            above, below = tokens[1], tokens[2]
            variables = self._variables(below)
            if variables is None:
                return self._handle_division(self.plain(above), self.plain(below))
            order = sum(n for _v, n in variables)
            wrt = [v if n == 1 else (v, n) for v, n in variables]
            if isinstance(above, _Differential) and isinstance(above.of, Basic):
                return sympy.Derivative(above.of, *wrt)
            above, below = self.plain(above), self.plain(below)
            if isinstance(above, Basic) and above.has(d):
                rest = self._without(above, d ** order)
                if rest is not None and not rest.has(d):
                    if rest == 1:
                        return _Operator(wrt)
                    key, leads = self._derivative_point(tokens[0])
                    idx = self.choices.get(key) if key else None
                    if idx not in (0, 1):                  # none, or not one of the two: the convention
                        idx = 0 if leads else 1
                    if key:
                        start, end = (int(v) for v in key.split("@")[1].split("-"))
                        self.points.setdefault(key, Point(key, "derivative", start, end, 2, choice=idx))
                    if idx == 0:
                        return sympy.Derivative(rest, *wrt)
            return self._handle_division(above, below)

        def _without(self, product, factor):
            """``product`` with ``factor`` taken out of it, None when it is
            not one of its factors."""
            if product == factor:
                return sympy.S.One
            if isinstance(product, sympy.Mul) and factor in product.args:
                rest = list(product.args)
                rest.remove(factor)
                return rest[0] if len(rest) == 1 else sympy.Mul(*rest)
            if id(product) in self.heavy or large_numbers(product):
                return None                                 # nothing is worked out on a number kept as written
            return sympy.cancel(product / factor)

        def _derivative_point(self, cmd):
            """The key of the \\frac beginning at ``cmd`` as a choice
            point (its text's span), and whether its numerator begins
            with the d."""
            start, text = getattr(cmd, "start_pos", None), self.text
            if start is None or not text:
                return None, False
            i = start + len(str(cmd))
            spans = []
            for _ in range(2):
                while i < len(text) and text[i].isspace():
                    i += 1
                if i < len(text) and text[i] == "{":
                    depth, j = 0, i
                    while j < len(text):
                        depth += {"{": 1, "}": -1}.get(text[j], 0)
                        if depth == 0:
                            break
                        j += 1
                    spans.append((i + 1, j))
                    i = j + 1
                else:
                    spans.append((i, i + 1))
                    i += 1
            top = text[spans[0][0]:spans[0][1]].lstrip()
            leads = bool(re.match(r"(d|\\partial|\\mathrm\{d\}|\\text\{d\})(?![A-Za-z])", top))
            return "derivative@%d-%d" % (start, min(i, len(text))), leads

    return Transformer


def _misplaced(expr: Basic) -> bool:
    """Whether a differential, a limit or the like stands where a value
    should: a ``Tuple`` as a term, a factor, a base or an argument."""
    from sympy.core.function import Application
    from sympy.core.relational import Relational
    for node in sympy.preorder_traversal(expr):
        if isinstance(node, (sympy.Add, sympy.Mul, sympy.Pow, Application, Relational)) \
                and any(isinstance(a, sympy.Tuple) for a in node.args):
            return True
    return False


def _said(exc: BaseException) -> str:
    """What went wrong, for the user."""
    if isinstance(exc, Unreadable):
        return f"This LaTeX could not be read: {exc}"
    if isinstance(exc, RecursionError):
        return "This LaTeX could not be read: it nests too deep"
    if isinstance(exc, (OverflowError, MemoryError)) or "integer string conversion" in str(exc):
        return "This LaTeX could not be read: it holds a number too large to write out"
    inner = getattr(exc, "orig_exc", None)                  # lark wraps what a rule raises
    if isinstance(inner, BaseException) and inner is not exc:
        return _said(inner)
    lines = str(exc).splitlines()
    return f"This LaTeX could not be turned into an expression: {(lines[0] if lines else type(exc).__name__)[:120]}"


class LatexReader:
    """Reads LaTeX with the add-on's grammar; see the module docstring.
    One instance serves every document (the parsers are built once) and
    every thread: a reading keeps what it works with to itself."""

    def __init__(self, grammar_dir: Path = GRAMMAR_DIR):
        self.grammar_dir = Path(grammar_dir)
        self._forest_parser = None
        self._callbacks = None
        self._transformer = None
        self._lock = threading.Lock()

    # -- the parsers ---------------------------------------------------------

    def _build(self) -> None:
        if self._forest_parser is not None:
            return
        with self._lock:                  # a warm-up thread may be building them this moment: wait for it
            if self._forest_parser is not None:
                return
            lark = _lark()
            grammar = guard_commands((self.grammar_dir / "latex.lark").read_text(encoding="utf-8"),
                                     (self.grammar_dir / "greek_symbols.lark").read_text(encoding="utf-8"))
            common = dict(source_path=str(self.grammar_dir) + "/", parser="earley", start="latex_string", lexer="auto",
                          propagate_positions=True, maybe_placeholders=False, keep_all_tokens=True)
            forest_parser = lark.Lark(grammar, ambiguity="forest", **common)
            self._tune(forest_parser)
            # The forest carries no tree-building callbacks of its own: a twin
            # parser (any ambiguity mode that builds trees) lends its table.
            self._callbacks = lark.Lark(grammar, ambiguity="explicit", **common).parser.parser.callbacks
            self._transformer = _transformer_class()
            self._forest_parser = forest_parser          # last: it is what says the rest is ready

    @staticmethod
    def _tune(parser) -> None:
        """Have the parser watch the clock, so that it can be stopped, and
        spare it the predictions it makes again and again.  Both reach into
        lark: a lark built otherwise is left as it is (the length of the text
        alone bounds it then), and the sparing is taken back if the parser
        does not read with it what it should."""
        try:
            earley = parser.parser.parser
            earley.term_matcher = _timed(earley.term_matcher)
        except AttributeError:  # pragma: no cover
            return
        try:
            table, step = earley.predictions, earley.predict_and_complete
        except AttributeError:  # pragma: no cover
            return
        if type(table) is not dict or not callable(step):  # pragma: no cover
            return
        earley.predictions, earley.predict_and_complete = _Predictions(table), _by_column(step)
        try:
            parser.parse(r"\frac{x y}{2} + \sin x \cos y")
        except Exception:  # noqa: BLE001  # pragma: no cover - not the parser this was written for
            earley.predictions = table
            del earley.predict_and_complete

    @property
    def ready(self) -> bool:
        """Whether the parsers are built."""
        return self._forest_parser is not None

    def warm(self, background: bool = False) -> bool:
        """Build the parsers now rather than at the first reading, which would
        wait for them - half a second on a laptop, seconds on a phone, while
        the user is typing.  ``background``: in a thread of its own, so that
        nothing waits, where there are threads.  Pyodide has none: there
        nothing is built, and the answer is False (True otherwise: built, or
        being built)."""
        if self._forest_parser is not None:
            return True
        if not background:
            self._build()
            return True
        def build():
            try:
                self._build()
            except Exception:  # noqa: BLE001 - the first reading builds them again, and says what is wrong
                pass
        try:
            threading.Thread(target=build, name="latex-grammar", daemon=True).start()
        except RuntimeError:
            return False
        return True

    # -- reading ------------------------------------------------------------------

    def read(self, latex: str, choices: Optional[Dict[str, int]] = None, constants: Optional[Dict[str, bool]] = None,
             known: Optional[Dict[str, Any]] = None, pieces: Optional[Dict[str, Basic]] = None) -> Dict[str, Any]:
        """Read ``latex``.  ``choices`` fixes alternatives at choice points
        (by key), ``constants`` says which constant names are constants
        (``{"pi": True, "e": False}``; the defaults of :data:`CONSTANTS`
        otherwise), ``known`` maps names to the document's own symbols and
        functions (a symbol of the same name is reused, with its assumptions).
        ``pieces`` maps the names of placeholder symbols in the text to the
        objects that take their place as they are (``xreplace``): a piece of
        the formula spliced into a reading keeps its own tree and names,
        which its LaTeX read back would not (``f(x)`` would be ``f*x``).

        Returns ``{"ok": True, "expr", "src", "latex", "ambiguities": [...],
        "constants": [...], "choices": {...}}`` or ``{"ok": False, "error":
        ...}`` - with ``"incomplete": True`` when the text only stops too
        early (it is being typed: :data:`INCOMPLETE`).  Nothing is raised:
        whatever goes wrong is an error to show.  Each ambiguity is ``{"key",
        "fragment", "choice", "options": [{"src", "latex", "index"}]}`` - the
        whole expression under each alternative shown, ``choice`` the place of
        the chosen one among them and ``index`` what ``choices`` takes to
        choose one - and each constant ``{"name", "on", "value", "label"}``.
        ``"more"`` counts the ambiguous parts left out when there are more
        than are offered.
        """
        _begin()
        try:
            return self._read(latex, choices, constants, known, pieces)
        except TooLong:
            return {"ok": False, "error": "This LaTeX takes too long to read in one go: put it in piece by piece"}
        except Exception as exc:  # noqa: BLE001 - a reading answers, whatever the text
            return {"ok": False, "error": _said(exc)}
        finally:
            _end()

    def _read(self, latex, choices, constants, known, pieces) -> Dict[str, Any]:
        if latex is not None and not isinstance(latex, str):
            return {"ok": False, "error": "The LaTeX to read comes as text"}
        latex = (latex or "").strip()
        if not latex:
            return {"ok": False, "error": "Nothing to read"}
        if len(latex) > MAX_LENGTH:
            return {"ok": False, "error": f"This LaTeX is too long to read in one go ({len(latex)} characters, "
                                          f"{MAX_LENGTH} at most): put it in piece by piece"}
        self._build()
        lark = _lark()
        from sympy.core.function import FunctionClass
        known = dict(known or {}) if isinstance(known, dict) else {}
        functions = {str(n): v for n, v in known.items() if isinstance(v, FunctionClass)}
        known_functions = {str(n) for n, v in known.items() if callable(v) and not isinstance(v, Basic)} | set(functions)
        wanted = {str(k): v for k, v in (constants or {}).items() if isinstance(v, bool) or v in (0, 1)} \
            if isinstance(constants, dict) else {}
        pieces = {str(k): v for k, v in (pieces or {}).items() if isinstance(v, Basic)} if isinstance(pieces, dict) else {}
        try:
            forest = self._forest_parser.parse(latex)
        except lark.exceptions.UnexpectedEOF:
            return {"ok": False, "incomplete": True, "error": INCOMPLETE}
        except lark.exceptions.UnexpectedCharacters as exc:
            # a command being typed at the end (\fr on the way to \frac) is
            # unfinished too, not wrong
            tail = re.search(r"\\[A-Za-z]*$", latex)
            if tail and getattr(exc, "pos_in_stream", -1) >= tail.start():
                return {"ok": False, "incomplete": True, "error": INCOMPLETE}
            return self._unreadable(latex, exc)
        except lark.exceptions.UnexpectedInput as exc:
            return self._unreadable(latex, exc)
        except lark.exceptions.LarkError as exc:
            return {"ok": False, "error": f"This LaTeX could not be read: {str(exc).splitlines()[0][:120]}"}

        fixed = self._choices(choices)
        walk = _Forest(self._callbacks, fixed, known_functions)
        tree = walk.tree(forest)
        points = walk.points(forest)
        chosen = dict(fixed)
        # the choice each point was read with: the caller's where it is one of
        # its alternatives, the convention's otherwise (an index out of range
        # was echoed back as if it had been taken)
        chosen.update({k: p.choice for k, p in points.items()})
        reading = self._transformer(latex, chosen, functions)
        raw = self._expression(reading, tree)
        for k, p in dict(reading.points).items():       # the reading's own choice points (a derivative or a division)
            points.setdefault(k, p)
            chosen[k] = p.choice

        finish = self._finisher(reading, wanted, known, pieces)
        expr, consts = finish(raw, True)
        first = {"src": str(expr), "latex": sympy.latex(expr)}
        ambiguities, more = self._describe_points(latex, forest, walk, reading, chosen, points, raw, first, finish)
        # ``choices`` is every decision taken, the user's and the reader's own:
        # sent back with one changed, the others stay as they were, so a pick
        # changes just what was picked (a decision made afresh could flip).
        result = {"ok": True, "expr": expr, "src": first["src"], "latex": first["latex"],
                  "ambiguities": ambiguities, "constants": consts, "choices": dict(chosen)}
        if more:
            result["more"] = more
        return result

    @staticmethod
    def _choices(choices) -> Dict[str, int]:
        """The caller's choices that can be one: a key, and a whole number
        for it (a page sends what it likes: 1e400 is not an alternative)."""
        fixed: Dict[str, int] = {}
        if not isinstance(choices, dict):
            return fixed
        for key, value in choices.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not str(key):
                continue
            if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf")) or value != int(value)):
                continue
            if 0 <= value < 10 ** 6:
                fixed[str(key)] = int(value)
        return fixed

    @staticmethod
    def _unreadable(latex: str, exc) -> Dict[str, Any]:
        col = getattr(exc, "column", None)
        where = f" at position {col}" if isinstance(col, int) and col > 0 else ""
        snippet = latex[max(0, (col or 1) - 1):(col or 1) + 11] if col else ""
        return {"ok": False, "error": f"This LaTeX could not be read{where}" + (f": near {snippet!r}" if snippet else "")}

    @staticmethod
    def _expression(reading, tree, within=None) -> Basic:
        """The tree as SymPy, under the reading's choices."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")          # SymPy's transformer warns on readings it then rejects
            result = reading.plain(reading.value(tree, within))
        if isinstance(result, (tuple, list)):
            raise Unreadable("a differential or a derivative that stands alone")
        if not isinstance(result, Basic):
            heavy = id(result) in reading.heavy
            result = sympy.sympify(result)
            if heavy:
                reading.heavy[id(result)] = result
        if _misplaced(result):
            raise Unreadable("a differential, or the variable of a limit, stands where a value should")
        return result

    def _finisher(self, reading, wanted, known, pieces):
        """What turns the expression read into the one answered: the
        constants, the pieces in the place of their names, the document's own
        symbols.  ``finish(expr, True)`` answers the constants met as well."""
        def finish(expr, listed=False):
            if id(expr) in reading.heavy or large_numbers(expr):
                # nothing is worked out on a number kept as written: a sum
                # made again around 10**(10**8) would compute it
                with sympy.evaluate(False):
                    done, consts = named(expr)
            else:
                done, consts = named(expr)
            return (done, consts) if listed else done

        def named(expr):
            expr, consts = self._apply_constants(expr, wanted)
            swap = {Symbol(k): v for k, v in pieces.items()}
            # A subscript on a placeholder (ink written at the foot of a piece):
            # the piece's own name takes it - a_d for the a of the formula.  A
            # piece with no name to carry it (x + 1) cannot take one.
            for s in expr.free_symbols:
                name = getattr(s, "name", "")
                for k, v in pieces.items():
                    if name.startswith(k + "_"):
                        if not isinstance(v, Symbol):
                            raise Unreadable(f"a subscript goes on a name, and {v} is not one")
                        swap[s] = Symbol(v.name + name[len(k):], **v.assumptions0)
            if swap:
                expr = expr.xreplace(swap)
            return self._reuse_known(expr, known), consts

        return finish

    def _describe_points(self, latex, forest, walk, reading, chosen, points, raw, first, finish):
        """The choice points of the reading, with the whole expression under
        each of their alternatives (``finish`` gives each the reading's
        constants and known names) - points whose alternatives all read the
        same, and points that repeat another's alternatives, left out.
        Returns them and the number of points not looked at: the first
        :data:`MAX_POINTS` that are choices are offered, :data:`MAX_TRIALS`
        readings are worked out at most, and none once the reading has had
        its time."""
        out: List[Dict[str, Any]] = []
        seen_sets = set()
        trials = 0
        ordered = sorted(points.values(), key=lambda p: (p.start, -p.end))
        for at, point in enumerate(ordered):
            if len(out) >= MAX_POINTS or trials >= MAX_TRIALS or _late():
                return out, len(ordered) - at
            mine = chosen.get(point.key, 0)
            options: Dict[int, Optional[Dict[str, Any]]] = {mine: first} if 0 <= mine < point.count else {}
            shown = {first["src"]} if options else set()
            for i in range(point.count):
                if i == mine:
                    continue
                if point.count > MAX_ALTERNATIVES and len(shown) >= MAX_ALTERNATIVES:
                    break
                if trials >= MAX_TRIALS:
                    break
                trials += 1
                option = self._option(forest, walk, reading, chosen, point, i, raw, first, finish)
                if point.count > MAX_ALTERNATIVES:
                    # more readings than are shown: the ones that say
                    # something new, the chosen one among them
                    if option is None or option["src"] in shown:
                        continue
                options[i] = option
                if option is not None:
                    shown.add(option["src"])
            reading.choices = dict(chosen)
            if len({o["src"] for o in options.values() if o is not None}) < 2:
                continue                                  # every alternative reads the same: not a choice
            listed = sorted(options)
            signature = tuple((options[i] or {}).get("src") for i in listed)
            if signature in seen_sets:
                continue
            seen_sets.add(signature)
            out.append({"key": point.key, "fragment": latex[point.start:point.end].strip(),
                        "choice": listed.index(mine) if mine in options else 0,
                        "options": [dict(options[i] or {"src": "(not a reading)", "latex": "", "invalid": True}, index=i)
                                    for i in listed]})
        return out, 0

    def _option(self, forest, walk, reading, chosen, point, index, raw, first, finish) -> Optional[Dict[str, Any]]:
        """The whole expression with alternative ``index`` at ``point``,
        None when that is not a reading."""
        reading.choices = dict(chosen)
        reading.choices[point.key] = index
        try:
            if point.rule == "derivative":
                # the same tree, read otherwise where the fraction is
                other = self._expression(reading, walk.tree(forest), (point.start, point.end))
            else:
                other = self._expression(reading, walk.tree(forest, _Trial(point.key, index, point.start, point.end)))
            if other is raw or other == raw:
                return first                              # the same reading: nothing to print again
            done = finish(other)
            return {"src": str(done), "latex": sympy.latex(done)}
        except Exception:  # noqa: BLE001 - one that ran out of time among them: the reading stands without it
            return None

    def _apply_constants(self, expr: Basic, wanted: Dict[str, bool]):
        """Turn the constant names among the free symbols into constants (on
        by default for some, see :data:`CONSTANTS`; ``wanted`` overrides),
        and list every one that occurs with its state."""
        consts = []
        subs = {}
        free = {s.name: s for s in expr.free_symbols if isinstance(s, Symbol)}
        for name, (value, default, label) in CONSTANTS.items():
            if name not in free:
                continue
            on = bool(wanted.get(name, default))
            consts.append({"name": name, "on": on, "value": str(value), "label": label})
            if on:
                subs[free[name]] = value
        if subs:
            expr = expr.subs(subs)
        return expr, consts

    def _reuse_known(self, expr: Basic, known: Dict[str, Any]) -> Basic:
        """A free symbol named like one the document already has becomes
        that one (its assumptions, or its matrix shape, come along).  Names
        are compared as :func:`symbol_key` has them, since a document may
        hold a name under its LaTeX spelling (``x_{1}``, from a reading made
        before names were SymPy's: ``x_1`` now).  A ``lambda`` the document
        does not have is read as ``lamda`` all the same: ``lambda`` cannot be
        typed back (a keyword)."""
        by_key: Dict[str, Any] = {}
        for name, obj in known.items():
            by_key.setdefault(symbol_key(str(name)), obj)
        subs = {}
        for s in expr.free_symbols:
            name = getattr(s, "name", None)
            if name is None:
                continue
            other = known.get(name)
            if other is None:
                other = by_key.get(symbol_key(name))
            if isinstance(other, Basic) and other != s and (isinstance(other, Symbol) or getattr(other, "is_MatrixExpr", False)):
                subs[s] = other
            elif other is None and isinstance(s, Symbol) and _LAMBDA.search(name):
                subs[s] = Symbol(_LAMBDA.sub(lambda m: m.group(1) + "amda", name))
        return expr.subs(subs) if subs else expr


#: ``lambda`` as a whole name or a part of one (``lambda_1``), capital too.
_LAMBDA = re.compile(r"(?<![A-Za-z])([lL])ambda(?![A-Za-z])")


def symbol_key(name: str) -> str:
    """A symbol's name as it is compared with the document's: the name a
    reading gives (SymPy's: ``x_1``, ``xhat``) and the LaTeX spelling that
    readings used to give, which documents made then still hold, meet here -
    ``x_{1}`` and ``x_1``, ``lambda`` and ``lamda``, ``alpha_{i}`` and
    ``alpha_i``, ``\\hat{x}`` and ``xhat``, ``x'`` and ``xprime``."""
    name = name.strip()
    m = re.fullmatch(r"\\([A-Za-z]+)\{(.+)\}", name)
    if m and m.group(1) in DECORATIONS:
        return decorated_name(symbol_key(m.group(2)), DECORATIONS[m.group(1)])
    prev = None
    while prev != name:                                  # x_{1} -> x_1, nested braces from the inside
        prev, name = name, re.sub(r"([_^])\{([^{}]*)\}", r"\1\2", name)
    primed = re.fullmatch(r"([^_']+)('+)(_.*)?", name)
    if primed:                                           # x'_1 -> xprime_1
        name = primed.group(1) + "prime" * len(primed.group(2)) + (primed.group(3) or "")
    return _LAMBDA.sub(lambda m: m.group(1) + "amda", name)


def read_latex(latex: str, **kwargs) -> Dict[str, Any]:
    """One-off reading with a fresh :class:`LatexReader` (tests, scripts)."""
    return LatexReader().read(latex, **kwargs)

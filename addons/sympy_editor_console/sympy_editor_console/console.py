"""The console itself: a namespace per document, code run in it as IPython
runs a cell or as Python runs a file, and ``editor`` - the formula, from
Python.

Nothing here needs IPython (which the apps and Pyodide do not carry): the
cell is compiled with :mod:`ast`, the value of a last expression is shown as
IPython shows it (``Out[n]``, ``_``, ``__``, ``_n``), and the few things
IPython adds to Python that a phone user reaches for are read by hand -
``obj?`` / ``obj??`` for help and source, and the line magics ``%who``,
``%whos``, ``%reset`` and ``%time``.
"""

from __future__ import annotations

import ast
import builtins
import codeop
import inspect
import io
import keyword
import linecache
import re
import secrets
import sys
import threading
import time
import tokenize
import traceback
import types
import weakref
from typing import Any, Dict, List, Optional, Tuple

import sympy
from sympy import Basic
from sympy.matrices import MatrixBase

from sympy_editor.printer import extract_range, parse_path

__all__ = ["Console", "Editor", "console_of"]

#: What a cell may show before the rest is cut - what it prints and what it
#: displays, the LaTeX counted with the text: a loop printing forever must not
#: send megabytes back to a phone, and neither must one that displays.
MAX_OUTPUT = 200_000
#: How many pieces of output a cell may have (a print and a ``display()``
#: taking turns make one each): the panel draws every one of them.
MAX_ITEMS = 200
#: Where a value's text is cut in an ``Out`` line.
MAX_REPR = 20_000
#: The LaTeX of a value, above which it is left out and the text is shown:
#: nobody reads a formula of that length typeset, and ``expand((x + y + z +
#: 1)**40)`` sent 450,000 characters of it beside 20,000 of text.
MAX_LATEX = 20_000
#: What is said where output stops.
CUT_NOTE = "[… output cut]\n"
#: Completions offered at once.
MAX_COMPLETIONS = 200

_MISSING = object()
_HELP_RE = re.compile(r"^\s*(\?{1,2})?\s*([A-Za-z_][\w.]*(?:\([^()]*\))?)\s*(\?{1,2})?\s*$")
#: A line magic, alone on its line: ``%time factor(x**8 - 1)``.
_MAGIC_LINE_RE = re.compile(r"^([ \t]*)%(\w+)[ \t]*(.*?)[ \t]*$")


class _Shown:
    """What one run shows, in order (a print and a ``display()`` interleave
    as they happened), and how much of it there may be: ``MAX_OUTPUT``
    characters, the LaTeX of what is displayed counted with the text, in
    ``MAX_ITEMS`` pieces.  Where that ends the output says so, once, and the
    rest is dropped - the code runs on."""

    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []
        self.used = 0
        self.cut = False

    def write(self, kind: str, text: str) -> None:
        if self.cut:
            return
        last = self.items[-1] if self.items else None
        joins = last is not None and last["kind"] == kind
        if not joins and len(self.items) >= MAX_ITEMS:
            self._cut()
            return
        room = MAX_OUTPUT - self.used
        fits = text[:room]
        if fits:
            if joins:
                last["text"] += fits
            else:
                self.items.append({"kind": kind, "text": fits})
            self.used += len(fits)
        if len(text) > room:
            self._cut()

    def display(self, value: Any) -> None:
        if self.cut:                       # not even rendered: that is work too
            return
        if len(self.items) >= MAX_ITEMS:
            self._cut()
            return
        item = dict(render(value), kind="display")
        size = len(item["text"]) + len(item.get("latex", ""))
        if size > MAX_OUTPUT - self.used:
            self._cut()
            return
        self.items.append(item)
        self.used += size

    def _cut(self) -> None:
        self.cut = True
        last = self.items[-1] if self.items else None
        if last is not None and last["kind"] in ("stdout", "stderr"):
            last["text"] += "\n" + CUT_NOTE
        else:
            self.items.append({"kind": "stdout", "text": CUT_NOTE})


class _Output(io.TextIOBase):
    """Where a cell's ``print`` goes: into what the run shows (:class:`_Shown`),
    as its standard output or its standard error."""

    def __init__(self, shown: _Shown, kind: str) -> None:
        self.shown = shown
        self.kind = kind

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        text = str(text)
        if text:
            self.shown.write(self.kind, text)
        return len(text)


class _Router(io.TextIOBase):
    """``sys.stdout`` (or ``sys.stderr``) while any console runs code: what
    the thread running a cell writes goes to that cell, what any other
    thread writes goes where it went before.

    ``contextlib.redirect_stdout`` was here, and it swaps a stream the whole
    process shares: two documents running cells on two threads (two widgets
    in a notebook) read each other's output, and the one that finished last
    put back the *other's* capture - every ``print`` of the process was lost
    from then on.  A lock around the runs would have kept them apart too, but
    one long computation would then hold up every other console, and a print
    of the server's own would still land in a cell.  Routing by thread needs
    no thread to exist: in Pyodide there is one, and it is the cell's.

    There is one router per stream for the whole process, put in place when
    the first capture begins and taken away when the last one ends, so
    ``keep = sys.stdout`` in a cell is an object that still writes to the
    right place in the next one."""

    def __init__(self, stream: str) -> None:
        self.stream = stream                       # "stdout" / "stderr"
        self.real: Any = None                      # what was there before the first capture
        self.targets: Dict[int, List[_Output]] = {}

    def _target(self) -> Any:
        stack = self.targets.get(threading.get_ident())
        if stack:
            return stack[-1]
        real = self.real
        if real is None or real is self:
            real = getattr(sys, "__%s__" % self.stream, None)
        return real

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        target = self._target()
        if target is None:                         # a process with no stream of its own
            return len(text)
        return target.write(text)

    def flush(self) -> None:
        target = self._target()
        if target is not None and not isinstance(target, _Output):
            target.flush()


_ROUTERS = {"stdout": _Router("stdout"), "stderr": _Router("stderr")}
_CAPTURES = threading.RLock()


class _Capture:
    """The output of this thread, into ``shown``, from ``with`` to its end.

    :meth:`close` may be called again, and takes away whatever this capture
    and the ones begun inside it left behind: the editor's Interrupt raises
    in the middle of anything, the end of a ``with`` included.  When the last
    capture of the process closes, ``sys.stdout`` and ``sys.stderr`` are what
    they were before the first one began - whatever the code did to them
    meanwhile."""

    def __init__(self, shown: _Shown) -> None:
        self.shown = shown
        self.ident = threading.get_ident()
        self.depth: Optional[int] = None

    def __enter__(self) -> "_Capture":
        with _CAPTURES:
            if not any(r.targets for r in _ROUTERS.values()):
                for name, router in _ROUTERS.items():
                    now = getattr(sys, name, None)
                    if now is not router:          # (left in place by an end that never came)
                        router.real = now
                    setattr(sys, name, router)
            for name, router in _ROUTERS.items():
                stack = router.targets.setdefault(self.ident, [])
                if self.depth is None:
                    self.depth = len(stack)
                stack.append(_Output(self.shown, name))
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.depth is None:
            return
        with _CAPTURES:
            for router in _ROUTERS.values():
                stack = router.targets.get(self.ident)
                if stack is not None:
                    del stack[self.depth:]
                    if not stack:
                        del router.targets[self.ident]
            if not any(r.targets for r in _ROUTERS.values()):
                for name, router in _ROUTERS.items():
                    setattr(sys, name, router.real)


def _is_math(value: Any) -> bool:
    """What is shown typeset: SymPy objects, and lists/tuples/dicts/sets of them."""
    if isinstance(value, (Basic, MatrixBase)):
        return True
    if isinstance(value, dict):
        items = list(value.keys()) + list(value.values())
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        return False
    return 0 < len(items) <= 200 and any(isinstance(v, (Basic, MatrixBase)) for v in items) \
        and all(isinstance(v, (Basic, MatrixBase, int, float, complex, str, bool)) for v in items)


def render(value: Any) -> Dict[str, Any]:
    """A value as the panel shows it: its text (``repr``, as IPython) and,
    for mathematics, its LaTeX - the text cut at ``MAX_REPR``, the LaTeX
    left out above ``MAX_LATEX`` (the panel then shows the text)."""
    try:
        text = repr(value)
    except Exception as exc:  # noqa: BLE001 - a broken __repr__ is the user's
        text = f"<{type(value).__name__}: repr failed: {exc}>"
    whole = len(text) <= MAX_REPR
    if not whole:
        text = text[:MAX_REPR] + " …"
    out: Dict[str, Any] = {"text": text}
    if whole and _is_math(value):          # (a text that was cut is not typeset: that takes longer still)
        try:
            latex = sympy.latex(value)
            if len(latex) <= MAX_LATEX:
                out["latex"] = latex
        except Exception:  # noqa: BLE001 - the text is enough
            pass
    return out


class Editor:
    """The formula, from the console: ``editor`` in its namespace.

    ``editor.expr`` is the whole expression and ``editor.selection`` what is
    selected in the formula when the code runs (the whole expression when
    nothing is); assigning either one changes the formula - a step of its
    history, undone with the editor's Undo like any other edit.  Paths are
    the editor's own (``"/"``, ``"/1/0"``: see ``editor.paths()``)::

        editor.expr                      # sin(x)**2 + cos(x)**2
        editor.expr = simplify(editor.expr)
        editor.selection = expand(editor.selection)
        editor["/1"]                     # the node at a path; editor["/1"] = y replaces it
        editor.apply("factor", "/0")     # one of the editor's transformations
        editor.find(cos(x))              # the paths where cos(x) is drawn
        editor.select("/0")              # select it in the formula after the run
        editor.undo(); editor.redo()
    """

    #: The properties completion reads to look inside them (``editor.expr.``):
    #: they are the console's own and only look at the formula.  Nobody
    #: else's property is read - see :meth:`Console.complete`.
    _READ_BY_COMPLETION = ("doc", "expr", "path", "selection")

    def __init__(self, doc) -> None:
        self._doc = doc
        self._path: Optional[str] = None
        self._children: Optional[List[int]] = None
        self._select: Optional[str] = None

    # -- set by the console around each run --------------------------------------

    def _begin(self, path: Optional[str], children: Optional[List[int]]) -> None:
        self._path = str(path) if path else None
        self._children = [int(i) for i in children] if children else None
        self._select = None

    # -- the formula -------------------------------------------------------------

    @property
    def doc(self):
        """The :class:`sympy_editor.Document` behind the formula, for what
        this object does not cover."""
        return self._doc

    @property
    def expr(self) -> Basic:
        """The whole expression; assign to change it."""
        return self._doc.expr

    @expr.setter
    def expr(self, value) -> None:
        self._doc.set(value)

    @property
    def path(self) -> str:
        """The selected node's path (``"/"`` for none: the whole expression)."""
        return self._path or "/"

    @property
    def selection(self) -> Basic:
        """What is selected in the formula - a node, or a range of the terms
        of a sum or the factors of a product - or the whole expression when
        nothing is.  Assign to replace it."""
        if self._children:
            return extract_range(self._doc.expr, parse_path(self.path), self._children, self._doc.printer_settings)
        return self._doc.get(self.path)

    @selection.setter
    def selection(self, value) -> None:
        if self._children:
            self._doc.replace(self.path, value, children=self._children)
            self._children = None
        elif self.path == "/":
            self._doc.set(value)
        else:
            self._doc.replace(self.path, value)

    def __getitem__(self, path: str) -> Basic:
        return self._doc.get(path)

    def __setitem__(self, path: str, value) -> None:
        self.replace(path, value)

    def get(self, path: str = "/") -> Basic:
        """The node at ``path``."""
        return self._doc.get(path)

    def replace(self, path: str, value) -> Basic:
        """Put ``value`` (a SymPy object, or text read as the editor reads
        typed text) in place of the node at ``path``."""
        if str(path) in ("", "/"):
            return self._doc.set(value)
        return self._doc.replace(path, value)

    def delete(self, path: str) -> Basic:
        """Remove the node at ``path``, as the editor's Delete does."""
        return self._doc.delete(path)

    def apply(self, op: str, path: str = "/", *args) -> Basic:
        """Apply one of the editor's transformations by name (``editor.ops``
        lists them) to the node at ``path``; ``args`` are the values an op
        asks for (axes, variables)."""
        return self._doc.apply(path, op, args=list(args) or None)

    def call(self, func: str, path: str = "/") -> Basic:
        """What the function box does: ``editor.call("diff(x)", "/1")``."""
        return self._doc.call(path, func)

    def parse(self, text: str) -> Basic:
        """Text read as the editor reads what is typed into it: the
        formula's own symbols (with their assumptions) are reused."""
        return self._doc.parse(text)

    def undo(self) -> Basic:
        return self._doc.undo()

    def redo(self) -> Basic:
        return self._doc.redo()

    def select(self, path: str = "/") -> None:
        """Select the node at ``path`` in the formula once the code has run."""
        self._doc.get(path)            # an unknown path fails here, not in the page
        self._select = str(path)

    def paths(self) -> Dict[str, str]:
        """Every path of the formula, with the text of what is there."""
        nodes = self._doc.snapshot().get("nodes") or {}
        return {p: str(info.get("src", "")) for p, info in nodes.items()}

    def find(self, value) -> List[str]:
        """The paths of the nodes equal to ``value`` (text is read first)."""
        if isinstance(value, str):
            value = self._doc.parse(value)
        out = []
        for p in self.paths():
            try:
                if self._doc.get(p) == value:
                    out.append(p)
            except Exception:  # noqa: BLE001 - a part with no object of its own
                pass
        return out

    @property
    def ops(self) -> List[str]:
        """The names :meth:`apply` takes."""
        return [op["name"] for op in self._doc.snapshot().get("ops") or []]

    @property
    def symbols(self) -> Dict[str, Any]:
        """The names the formula uses and declares."""
        return self._doc.namespace()

    def __repr__(self) -> str:
        return f"<editor: {self._doc.expr}>"


def _exit(code: Any = None) -> None:
    """``exit()`` / ``quit()``, which not every Python defines (the site
    module adds them to a terminal's): the console says it stays."""
    raise SystemExit(code)


class UsageError(Exception):
    """Something the console does not do, said without a traceback."""


def _no_input(prompt: str = "") -> str:
    raise UsageError("input() cannot read here: the console has no keyboard of its own to wait on. "
                       "Put the value in the code instead.")


class Console:
    """One document's console: its namespace, its numbered inputs and
    outputs, and what running code in it gives back.  Made by
    :func:`console_of`, kept in ``doc.addon_state["console"]``."""

    def __init__(self, doc) -> None:
        self.doc = doc
        self.editor = Editor(doc)
        self._shown: Optional[_Shown] = None
        self._running_ns: Optional[Dict[str, Any]] = None
        #: Whether ``%reset`` may reset: it is alone in the cell being run.
        self._may_reset = False
        #: Changes to the formula since the last run began (``on_change``).
        self.changed = False
        _listen(doc, self)
        self.reset()

    # -- the namespace -------------------------------------------------------

    def reset(self) -> None:
        """A fresh namespace: SymPy's names, ``editor``, and the formula's symbols."""
        #: Names the namespace, for the panel: another one (a session switch,
        #: a Python started afresh, a reset) means the variables it showed
        #: are gone - and last time's cells, kept as text, are only text.
        self.token = secrets.token_hex(6)
        self.count = 1
        self.inputs: List[str] = [""]
        self.outputs: Dict[int, Any] = {}
        self.ns = self._fresh("__console__")
        self.ns.update(In=self.inputs, Out=self.outputs, _="", __="", ___="")
        self._baseline = set(self.ns)
        #: The formula's names as they were put in ``ns`` (:meth:`_sync_names`):
        #: a name that still holds what was put there is the formula's, any
        #: other value is the user's.  It belongs to this namespace - a
        #: script's has one of its own.
        self._injected: Dict[str, Any] = {}

    def _fresh(self, name: str) -> Dict[str, Any]:
        ns: Dict[str, Any] = {"__name__": name, "__builtins__": builtins}
        exec("from sympy import *", ns)  # noqa: S102 - the console's own start
        ns.update(editor=self.editor, display=self._display, input=_no_input, help=self._help, __magic__=self._magic,
                  exit=_exit, quit=_exit)
        return ns

    def _sync_names(self, ns: Dict[str, Any], injected: Optional[Dict[str, Any]] = None) -> None:
        """The formula's names, where the user has not put something of
        their own: ``x`` is the formula's ``x`` (assumptions and all), and
        follows it when a retype changes it; a SymPy name the formula uses
        as a symbol (``beta``) is the symbol, as it is in the editor.
        ``injected`` remembers what was put in ``ns`` (the console's own
        namespace when it is not given)."""
        if injected is None:
            injected = self._injected
        for name, obj in self.doc.namespace().items():
            if not str(name).isidentifier():
                continue
            cur = ns.get(name, _MISSING)
            if cur is _MISSING or cur is getattr(sympy, name, _MISSING) or cur is injected.get(name, _MISSING):
                ns[name] = obj
                injected[name] = obj

    def user_names(self) -> List[str]:
        """What the user defined (``%who``)."""
        out = []
        for name, value in self.ns.items():
            if name.startswith("_") or name in self._baseline:
                continue
            if value is self._injected.get(name, _MISSING):
                continue
            if re.fullmatch(r"_i?\d+", name):
                continue
            out.append(name)
        return sorted(out)

    # -- running ---------------------------------------------------------------

    def _display(self, *values: Any) -> None:
        """``display(obj)``: show a value where the code is, typeset."""
        shown = getattr(self, "_shown", None)
        for value in values:
            if shown is None:
                print(repr(value))
            else:
                shown.display(value)

    def _help(self, obj: Any = _MISSING) -> None:
        if obj is _MISSING:
            print("help(obj) describes obj; obj? does the same, obj?? shows its source.")
            return
        print(self._describe(obj, source=False))

    @staticmethod
    def _describe(obj: Any, source: bool) -> str:
        lines = [f"Type:      {type(obj).__name__}"]
        try:
            sig = inspect.signature(obj)
            name = getattr(obj, "__name__", "") or type(obj).__name__
            lines.append(f"Signature: {name}{sig}")
        except (TypeError, ValueError):
            pass
        if not callable(obj) or isinstance(obj, Basic):
            text = repr(obj)
            lines.append("Value:     " + (text if len(text) < 400 else text[:399] + "…"))
        if source:
            try:
                lines.append("Source:\n" + inspect.getsource(obj))
            except (TypeError, OSError):
                lines.append("Source:    not available")
        else:
            doc = inspect.getdoc(obj)
            if doc:
                lines.append("Docstring:\n" + doc)
        return "\n".join(lines)

    @staticmethod
    def needs_more(code: str) -> bool:
        """True while ``code`` is an unfinished block, as IPython's Enter
        reads it: ``for i in range(3):`` wants its body, and a body wants
        an empty line before it runs.  Invalid code is complete (it runs, and
        says why it is invalid)."""
        lines = code.split("\n")
        if len(lines) > 1 and not lines[-1].strip():
            return False                   # an empty line ends a block
        try:
            return codeop.compile_command(code, "<input>", "single") is None
        except (SyntaxError, ValueError, OverflowError):
            pass
        try:                               # several statements: only the last one can be unfinished
            return codeop.compile_command(code, "<input>", "exec") is None
        except (SyntaxError, ValueError, OverflowError):
            return False

    def run_cell(self, code: str, path: Optional[str] = None, children: Optional[List[int]] = None) -> Dict[str, Any]:
        """Run one input as IPython does: statements, then the value of a
        last expression shown as ``Out[n]``."""
        n = self.count
        self.count += 1
        self.inputs.append(code)
        self.ns["_i%d" % n] = code
        filename = "<In [%d]>" % n
        result = self._run(code, filename, self.ns, path, children, cell=n)
        result["n"] = n
        return result

    def run_script(self, code: str, name: str = "script.py", path: Optional[str] = None,
                   children: Optional[List[int]] = None) -> Dict[str, Any]:
        """Run a whole file as ``python script.py`` would - its own namespace,
        ``__name__ == "__main__"`` - and then, as IPython's ``%run`` does,
        leave what it defined in the console's namespace."""
        ns = self._fresh("__main__")
        ns["__file__"] = name
        filename = "<%s>" % (name or "script.py")
        # The formula's names as the script was given them: what it left
        # untouched is not something it defined, and copying it back put the
        # formula's t over the t = 5 of the console.  The console has them
        # as it has them at any run - where the user has nothing of their
        # own - and from before the script ran, which may change the formula.
        given: Dict[str, Any] = {}
        self._sync_names(self.ns)
        result = self._run(code, filename, ns, path, children, cell=None, injected=given)
        for key, value in ns.items():
            if not key.startswith("__") and key not in ("editor", "display", "input", "help", "exit", "quit"):
                if value is not getattr(sympy, key, _MISSING) and value is not given.get(key, _MISSING):
                    self.ns[key] = value
        return result

    def _run(self, code: str, filename: str, ns: Dict[str, Any], path, children, cell: Optional[int],
             injected: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.editor._begin(path, children)
        self.changed = False
        self._may_reset = False
        shown = _Shown()
        items = shown.items
        self._shown = shown
        self._running_ns = ns
        out: Dict[str, Any] = {"items": items}
        self._sync_names(ns, injected)
        capture = _Capture(shown)
        started = time.perf_counter()
        try:
            with capture:
                special = self._special(code, ns) if cell is not None else None
                if special is None:
                    value = self._exec(code, filename, ns, cell is not None, top=True)
                    if cell is not None and value is not None:
                        self._remember(cell, value)
                        out["out"] = render(value)
        except SystemExit as exc:
            items.append({"kind": "error", "text": f"SystemExit: {exc.code!r} - the console stays open.\n"})
        except KeyboardInterrupt:
            items.append({"kind": "error", "text": "KeyboardInterrupt\n"})
        except BaseException as exc:  # noqa: BLE001 - the user's code: shown, not raised
            if type(exc).__name__ == "Interrupted":
                items.append({"kind": "error", "text": "Interrupted\n"})
            else:
                items.append({"kind": "error", "text": _format_exception(exc, filename)})
        finally:
            capture.close()                # again: an Interrupt may have landed in the end of the with
            self._shown = None
            self._running_ns = None
            self._may_reset = False
        out["seconds"] = round(time.perf_counter() - started, 4)
        out["changed"] = self.changed
        if self.editor._select:
            out["select"] = self.editor._select
        return out

    def _exec(self, code: str, filename: str, ns: Dict[str, Any], interactive: bool, top: bool = False) -> Any:
        """Compile and run; the value of a last expression statement when
        ``interactive`` (a cell), None otherwise.  ``top``: the code is the
        cell itself, not the statement of a ``%time``."""
        linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
        magics: List[str] = []
        if interactive:
            code, magics = _rewrite_magics(code)
        tree = ast.parse(code, filename, "exec")
        if "reset" in magics:
            # Refused before anything runs: a reset in the middle of a cell left
            # its other lines running in the namespace that was thrown away -
            # zz = 3 that the next input did not know, an Out[7] at In [1].
            if not (top and len(tree.body) == 1 and _is_magic_call(tree.body[0], "reset")):
                raise UsageError(_RESET_ALONE)
            self._may_reset = True
        last = None
        if interactive and tree.body and isinstance(tree.body[-1], ast.Expr):
            last = ast.Expression(tree.body.pop().value)
        if tree.body:
            exec(compile(tree, filename, "exec"), ns)  # noqa: S102 - running code is what a console is for
        if last is not None:
            return eval(compile(last, filename, "eval"), ns)  # noqa: S307
        return None

    def _remember(self, n: int, value: Any) -> None:
        self.outputs[n] = value
        self.ns["___"], self.ns["__"], self.ns["_"] = self.ns.get("__", ""), self.ns.get("_", ""), value
        self.ns["_%d" % n] = value

    # -- what IPython adds to Python -----------------------------------------------

    def _special(self, code: str, ns: Dict[str, Any]) -> Optional[bool]:
        """``obj?``, ``obj??`` and ``!cmd``: handled (True), or
        None for plain Python."""
        stripped = code.strip()
        if "\n" in stripped:
            return None
        m = _HELP_RE.match(stripped)
        if m and (m.group(1) or m.group(3)):
            marks = (m.group(1) or "") + (m.group(3) or "")
            obj = eval(compile(m.group(2), "<help>", "eval"), ns)  # noqa: S307 - the user's own name
            print(self._describe(obj, source=len(marks) >= 2))
            return True
        if stripped.startswith("!"):
            raise UsageError("There is no shell here: !commands do not run (on a phone there is none to run them in).")
        return None

    def _magic(self, name: str, arg: str = "") -> Any:
        """A line magic, called where the line was (``%who`` becomes
        ``__magic__("who", "")``); the value of ``%time``'s statement is
        the line's value."""
        ns = self._running_ns if self._running_ns is not None else self.ns
        arg = arg.strip()
        if name == "who":
            names = self.user_names()
            print("  ".join(names) if names else "Interactive namespace is empty.")
        elif name == "whos":
            names = self.user_names()
            if not names:
                print("Interactive namespace is empty.")
            for key in names:
                text = repr(self.ns[key]).replace("\n", " ")
                print(f"{key:<16}{type(self.ns[key]).__name__:<16}{text[:60]}")
        elif name == "reset":
            if not self._may_reset:        # %time %reset, __magic__("reset") in the middle of something
                raise UsageError(_RESET_ALONE)
            self._may_reset = False
            self.reset()
            print("The namespace is fresh: SymPy, editor and the formula's names.")
        elif name == "time":
            if not arg:
                raise UsageError("%time needs a statement: %time factor(x**8 - 1)")
            start = time.perf_counter()
            value = self._exec(arg, "<%time>", ns, True)
            print(f"Wall time: {_seconds(time.perf_counter() - start)}")
            return value
        else:
            raise UsageError(f"Line magic %{name} is not known here (there are %who, %whos, %reset and %time)")
        return None

    def complete(self, code: str, pos: Optional[int] = None) -> Dict[str, Any]:
        """Names that complete the word before ``pos``: ``{"start", "word",
        "matches", "kinds", "total"}``.

        ``start`` is where the completed word begins in ``code``; ``word``
        may be dotted (``editor.fi``), and then the matches are the
        attributes of the object already in the namespace (``editor.find``).
        ``kinds`` says what each one is (``function``, ``class``, ``module``,
        ``property``, or the type of a value), and the user's own names and
        the formula's come first.  ``total`` counts them all, ``matches``
        stops at ``MAX_COMPLETIONS``.  Inside a string or a comment, or after
        a number, there is nothing to complete.

        Nothing of the user's is run to find them: the menu opens by itself
        after a dot, and typing ``obj.prop.`` must not be what reads ``prop``.
        (``rlcompleter`` was here: it evaluates what is before the last dot.)
        The names are read where they are kept - the object's own, its
        class's - and a dotted word is followed only through what is already
        there: past a property, or anything else that computes what it
        gives, nothing is offered.  ``editor``'s own properties are the
        exception (``editor.expr.``): they are the console's."""
        pos = len(code) if pos is None else max(0, min(int(pos), len(code)))
        head = code[:pos]
        m = re.search(r"[A-Za-z_][\w.]*$|[A-Za-z_]?$", head)
        word = m.group(0) if m else ""
        empty = {"start": pos - len(word), "word": word, "matches": [], "kinds": [], "total": 0}
        line = head[head.rfind("\n") + 1:]
        if not word or _in_string_or_comment(line) or re.search(r"\d\.?$", head[:m.start()]):
            return empty
        self._sync_names(self.ns)
        *base, last = word.split(".")
        if base:
            owner = self._owner(base)
            names = _static_names(owner) if owner is not _MISSING else []
            # the private names only once a "_" is typed, the dunders never
            hidden = "__" if last.startswith("_") else "_"
            names = [name for name in names if not name.startswith(hidden)]
        else:
            soft = [k for k in getattr(keyword, "softkwlist", []) if k != "_"]
            names = [name for name in set(keyword.kwlist + soft) | set(self.ns) | set(vars(builtins))
                     if isinstance(name, str) and not name.startswith("__")]
        found = [".".join(base + [name]) for name in names if name.startswith(last)]
        own = set(self.user_names()) | set(self._injected)
        found.sort(key=lambda s: (s not in own, s.split(".")[-1].startswith("_"), s.lower()))
        matches = found[:MAX_COMPLETIONS]
        return {"start": pos - len(word), "word": word, "matches": matches,
                "kinds": [self._kind_of(name) for name in matches], "total": len(found)}

    def _owner(self, parts: List[str]) -> Any:
        """The object a dotted name stands for, found without running
        anything of its own, or ``_MISSING``: the name is not there, or
        getting it would take reading a property."""
        try:
            if parts[0] in self.ns:
                owner = self.ns[parts[0]]
            else:
                owner = vars(builtins)[parts[0]]
            for part in parts[1:]:
                owner = _peek(owner, part)
                if owner is _MISSING:
                    break
            return owner
        except Exception:  # noqa: BLE001 - nothing to offer, then
            return _MISSING

    def _kind_of(self, dotted: str) -> str:
        """What a completion is, looked at without running anything of its
        own: a property is named, not read."""
        *base, last = dotted.split(".")
        try:
            if base:
                owner = self._owner(base)
                if owner is _MISSING:
                    return ""
                value = inspect.getattr_static(owner, last)
                if isinstance(value, property):
                    return "property"
                if isinstance(value, (staticmethod, classmethod)):
                    return "method"
            elif last in self.ns:
                value = self.ns[last]
            elif keyword.iskeyword(last) or last in getattr(keyword, "softkwlist", ()):
                return "keyword"
            else:
                value = vars(builtins)[last]
        except Exception:  # noqa: BLE001 - it is only a hint
            return ""
        if inspect.ismodule(value):
            return "module"
        if inspect.isclass(value):
            return "class"
        if inspect.isroutine(value) or type(value).__name__ in ("method_descriptor", "wrapper_descriptor"):
            return "method" if base else "function"
        return type(value).__name__


_static_mro = type.__dict__["__mro__"].__get__
_static_class_dict = type.__dict__["__dict__"].__get__
#: What a class holds that gives itself, or binds itself, when it is looked
#: up on an object: nothing of anyone's runs.
_PLAIN_DESCRIPTORS = (types.FunctionType, types.BuiltinFunctionType, types.MethodDescriptorType,
                      types.WrapperDescriptorType, types.ClassMethodDescriptorType, type)


def _static_names(obj: Any) -> List[str]:
    """The attribute names of ``obj`` read where they are kept - its own
    ``__dict__``, its classes' - as ``dir()`` finds them for an ordinary
    object, without the ``__dir__`` or ``__getattr__`` an object may have of
    its own: those are code, and completion runs none."""
    names = set()
    try:
        own = object.__getattribute__(obj, "__dict__")
        if isinstance(own, (dict, types.MappingProxyType)):
            names.update(own)
    except Exception:  # noqa: BLE001 - an object with no __dict__ (slots, a number)
        pass
    for klass in _static_mro(obj if isinstance(obj, type) else type(obj)):
        names.update(_static_class_dict(klass))
    return [name for name in names if isinstance(name, str) and name.isidentifier()]


def _peek(owner: Any, name: str) -> Any:
    """``owner.name`` when it is already there - a value the object holds, a
    slot, a function, a class, a module's name - and ``_MISSING`` when it
    would have to be computed: a property, a ``cached_property``, any
    descriptor written in Python, a ``__getattr__``."""
    found = inspect.getattr_static(owner, name, _MISSING)
    if found is _MISSING:
        return _MISSING
    if isinstance(found, types.MemberDescriptorType):              # a slot (SymPy's objects have them)
        return found.__get__(owner, type(owner))
    if isinstance(found, (staticmethod, classmethod)):
        return found.__func__
    if isinstance(found, property) and type(owner) is Editor and name in Editor._READ_BY_COMPLETION:
        return getattr(owner, name)
    if isinstance(found, _PLAIN_DESCRIPTORS):
        return found
    if inspect.getattr_static(type(found), "__get__", _MISSING) is not _MISSING:
        return _MISSING
    return found


def _in_string_or_comment(line: str) -> bool:
    """Whether the end of a line of code is inside a string or a comment."""
    quote = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\":
                i += 1
            elif line.startswith(quote, i):
                i += len(quote) - 1
                quote = None
        elif c == "#":
            return True
        elif c in "'\"":
            quote = line[i:i + 3] if line[i:i + 3] in ("'''", '"""') else c
            i += len(quote) - 1
        i += 1
    return quote is not None


_RESET_ALONE = ("%reset must be alone in its cell: the lines around it would run in a namespace that is "
                "thrown away. Nothing was run, and nothing was reset.")


def _logical_lines(code: str) -> set:
    """The numbers (from 1) of the lines of ``code`` that begin a statement:
    not the inside of a string that goes over several lines, nor what
    continues a bracket or a backslash.  Where the code stops being Python
    the tokenizer stops too, and what it found until there is what there
    is: the rest is left as written, for the compiler to say what is wrong
    with it."""
    starts = set()
    at_start = True
    skipped = (tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type == tokenize.NEWLINE:
                at_start = True
            elif tok.type not in skipped and at_start:
                starts.add(tok.start[0])
                at_start = False
    except Exception:  # noqa: BLE001 - tokenize.TokenError, SyntaxError: see above
        pass
    return starts


def _rewrite_magics(code: str) -> Tuple[str, List[str]]:
    """``code`` with its line magics as calls (``%who`` becomes
    ``__magic__("who", "")``, in place: the line numbers stay), and the names
    of the magics found.

    Only a line that begins a statement is one.  Read off the text of the
    cell, a line of a string was taken for a magic (``%d items`` inside
    triple quotes came out as ``__magic__('d', 'items')``) and so was the
    ``%3)`` that ends ``a = (10`` on the line before, which is Python."""
    if "%" not in code:
        return code, []
    starts = _logical_lines(code)
    lines = code.split("\n")
    names: List[str] = []
    for number in starts:
        if number > len(lines):
            continue
        m = _MAGIC_LINE_RE.match(lines[number - 1])
        if m:
            lines[number - 1] = "%s__magic__(%r, %r)" % (m.group(1), m.group(2), m.group(3))
            names.append(m.group(2))
    return "\n".join(lines), names


def _is_magic_call(node: ast.AST, name: str) -> bool:
    """Whether a statement is the line magic ``name`` and nothing else."""
    call = node.value if isinstance(node, ast.Expr) else None
    return (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "__magic__"
            and bool(call.args) and isinstance(call.args[0], ast.Constant) and call.args[0].value == name)


def _seconds(s: float) -> str:
    return f"{s * 1e3:.3g} ms" if s < 1 else f"{s:.3g} s"


def _format_exception(exc: BaseException, filename: str) -> str:
    """The traceback of the user's code: the console's own frames - the
    ones that ran it, and the helpers it called (``input``, a magic) - are
    nobody's business."""
    if isinstance(exc, UsageError):
        return f"UsageError: {exc}\n"
    if isinstance(exc, SyntaxError):
        return "".join(traceback.format_exception_only(type(exc), exc))
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename != __file__]
    while frames and not frames[0].filename.startswith("<"):
        frames.pop(0)
    head = "Traceback (most recent call last):\n" + "".join(traceback.format_list(frames)) if frames else ""
    return head + "".join(traceback.format_exception_only(type(exc), exc))


def _listen(doc, console: Console) -> None:
    """Tell ``console`` when the formula changes while it runs code - through
    a listener that does not hold it.

    The document keeps its listeners for as long as it lives, and hands the
    very list to the document that replaces it when a session is opened
    (``server.load_session``): a listener holding its console kept every
    earlier session's namespace, variables and all, and called each one at
    every change.  This one lets its console go with its document, returns
    at once when the console is gone or is not running anything, and the
    ones left by consoles that are gone are taken off the list when the next
    console is made - found in the document's own ``_listeners`` and taken
    off with ``off_change``, since a console that is gone took its callback
    with it; where that list is not to be found they stay, and do nothing."""
    ref = weakref.ref(console)

    def changed(expr) -> None:
        console = ref()
        if console is not None and console._shown is not None:
            console.changed = True

    changed.console = ref                                  # how one of ours is known
    listeners = getattr(doc, "_listeners", None)
    if isinstance(listeners, list):
        def stale(cb) -> bool:
            mark = getattr(cb, "console", None)
            if not isinstance(mark, weakref.ref):
                return False                               # somebody else's
            other = mark()
            return other is None or other.doc is doc       # gone, or the one this console replaces
        for cb in [cb for cb in listeners if stale(cb)]:
            if hasattr(doc, "off_change"):
                doc.off_change(cb)
            else:
                listeners.remove(cb)                      # a sympy_editor from before off_change
    doc.on_change(changed)


def console_of(doc, name: str = "console") -> Console:
    """The console of ``doc``, made on first use."""
    state = doc.addon_state.setdefault(name, {}) if isinstance(getattr(doc, "addon_state", None), dict) else None
    if state is None:
        raise RuntimeError("This document keeps no add-on state")
    console = state.get("console")
    if console is None or console.doc is not doc:
        console = state["console"] = Console(doc)
    return console

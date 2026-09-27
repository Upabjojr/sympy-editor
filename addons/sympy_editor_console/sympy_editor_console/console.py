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
import linecache
import re
import secrets
import time
import traceback
from typing import Any, Dict, List, Optional

import sympy
from sympy import Basic
from sympy.matrices import MatrixBase

from sympy_editor.printer import extract_range, parse_path

__all__ = ["Console", "Editor", "console_of"]

#: What a cell may print before the rest is cut: a loop printing forever must
#: not send megabytes back to a phone.
MAX_OUTPUT = 200_000
#: Where a value's text is cut in an ``Out`` line.
MAX_REPR = 20_000
#: Completions offered at once.
MAX_COMPLETIONS = 200

_MISSING = object()
_HELP_RE = re.compile(r"^\s*(\?{1,2})?\s*([A-Za-z_][\w.]*(?:\([^()]*\))?)\s*(\?{1,2})?\s*$")
#: A line magic, alone on its line: ``%time factor(x**8 - 1)``.
_MAGIC_LINE_RE = re.compile(r"^([ \t]*)%(\w+)[ \t]*(.*?)[ \t]*$", re.M)


class _Output(io.TextIOBase):
    """``sys.stdout`` / ``sys.stderr`` while code runs: what is written goes,
    in order, into the cell's list of outputs (a print and a ``display()``
    interleave as they happened)."""

    def __init__(self, items: List[Dict[str, Any]], kind: str) -> None:
        self.items = items
        self.kind = kind

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        text = str(text)
        if not text:
            return 0
        used = sum(len(i.get("text", "")) for i in self.items)
        if used >= MAX_OUTPUT:
            return len(text)
        text = text[: MAX_OUTPUT - used] + ("\n[… output cut]\n" if used + len(text) > MAX_OUTPUT else "")
        last = self.items[-1] if self.items else None
        if last is not None and last["kind"] == self.kind:
            last["text"] += text
        else:
            self.items.append({"kind": self.kind, "text": text})
        return len(text)


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
    for mathematics, its LaTeX."""
    try:
        text = repr(value)
    except Exception as exc:  # noqa: BLE001 - a broken __repr__ is the user's
        text = f"<{type(value).__name__}: repr failed: {exc}>"
    if len(text) > MAX_REPR:
        text = text[:MAX_REPR] + " …"
    out: Dict[str, Any] = {"text": text}
    if _is_math(value):
        try:
            out["latex"] = sympy.latex(value)
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
        self._items: Optional[List[Dict[str, Any]]] = None
        self._running_ns: Optional[Dict[str, Any]] = None
        #: Changes to the formula since the last run began (``on_change``).
        self.changed = False
        #: Tells the panel it is speaking to another namespace (a session
        #: switch, a page reloaded): a new one is a new console.
        self.token = secrets.token_hex(6)
        doc.on_change(self._on_change)
        self.reset()

    def _on_change(self, expr) -> None:
        self.changed = True

    # -- the namespace -------------------------------------------------------

    def reset(self) -> None:
        """A fresh namespace: SymPy's names, ``editor``, and the formula's symbols."""
        self.count = 1
        self.inputs: List[str] = [""]
        self.outputs: Dict[int, Any] = {}
        self.ns = self._fresh("__console__")
        self.ns.update(In=self.inputs, Out=self.outputs, _="", __="", ___="")
        self._baseline = set(self.ns)

    def _fresh(self, name: str) -> Dict[str, Any]:
        ns: Dict[str, Any] = {"__name__": name, "__builtins__": builtins}
        exec("from sympy import *", ns)  # noqa: S102 - the console's own start
        ns.update(editor=self.editor, display=self._display, input=_no_input, help=self._help, __magic__=self._magic,
                  exit=_exit, quit=_exit)
        self._injected: Dict[str, Any] = {}
        return ns

    def _sync_names(self, ns: Dict[str, Any]) -> None:
        """The formula's names, where the user has not put something of
        their own: ``x`` is the formula's ``x`` (assumptions and all), and
        follows it when a retype changes it; a SymPy name the formula uses
        as a symbol (``beta``) is the symbol, as it is in the editor."""
        for name, obj in self.doc.namespace().items():
            if not str(name).isidentifier():
                continue
            cur = ns.get(name, _MISSING)
            if cur is _MISSING or cur is getattr(sympy, name, _MISSING) or cur is self._injected.get(name, _MISSING):
                ns[name] = obj
                self._injected[name] = obj

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
        items = getattr(self, "_items", None)
        for value in values:
            if items is None:
                print(repr(value))
            else:
                items.append(dict(render(value), kind="display"))

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
        result = self._run(code, filename, ns, path, children, cell=None)
        for key, value in ns.items():
            if not key.startswith("__") and key not in ("editor", "display", "input", "help", "exit", "quit"):
                if value is not getattr(sympy, key, _MISSING):
                    self.ns[key] = value
        return result

    def _run(self, code: str, filename: str, ns: Dict[str, Any], path, children, cell: Optional[int]) -> Dict[str, Any]:
        self.editor._begin(path, children)
        self.changed = False
        items: List[Dict[str, Any]] = []
        self._items = items
        self._running_ns = ns
        out: Dict[str, Any] = {"items": items}
        self._sync_names(ns)
        stdout, stderr = _Output(items, "stdout"), _Output(items, "stderr")
        import contextlib
        started = time.perf_counter()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                special = self._special(code, ns) if cell is not None else None
                if special is None:
                    value = self._exec(code, filename, ns, cell is not None)
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
            self._items = None
            self._running_ns = None
        out["seconds"] = round(time.perf_counter() - started, 4)
        out["changed"] = self.changed
        if self.editor._select:
            out["select"] = self.editor._select
        return out

    def _exec(self, code: str, filename: str, ns: Dict[str, Any], interactive: bool) -> Any:
        """Compile and run; the value of a last expression statement when
        ``interactive`` (a cell), None otherwise."""
        linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
        if interactive:
            code = _MAGIC_LINE_RE.sub(lambda m: "%s__magic__(%r, %r)" % (m.group(1), m.group(2), m.group(3)), code)
        tree = ast.parse(code, filename, "exec")
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
        """Names that complete the word before ``pos``: ``{"start", "matches"}``
        (``start`` is where the completed word begins in ``code``)."""
        import rlcompleter

        pos = len(code) if pos is None else max(0, min(int(pos), len(code)))
        head = code[:pos]
        m = re.search(r"[A-Za-z_][\w.]*$|[A-Za-z_]?$", head)
        word = m.group(0) if m else ""
        self._sync_names(self.ns)
        completer = rlcompleter.Completer(self.ns)
        matches: List[str] = []
        seen = set()
        if word:
            i = 0
            while len(matches) < MAX_COMPLETIONS:
                try:
                    item = completer.complete(word, i)
                except Exception:  # noqa: BLE001 - an attribute that raises when looked at
                    break
                if item is None:
                    break
                i += 1
                item = item.rstrip("(")
                if item not in seen and not item.split(".")[-1].startswith("__"):
                    seen.add(item)
                    matches.append(item)
        matches.sort(key=lambda s: (s.split(".")[-1].startswith("_"), s.lower()))
        return {"start": pos - len(word), "word": word, "matches": matches}


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


def console_of(doc, name: str = "console") -> Console:
    """The console of ``doc``, made on first use."""
    state = doc.addon_state.setdefault(name, {}) if isinstance(getattr(doc, "addon_state", None), dict) else None
    if state is None:
        raise RuntimeError("This document keeps no add-on state")
    console = state.get("console")
    if console is None or console.doc is not doc:
        console = state["console"] = Console(doc)
    return console

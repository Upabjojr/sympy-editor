"""sympy-editor add-on: a Python console beside the formula.

Two ways of running Python, in one panel under the editor:

* **Console** - input and output as in IPython: ``In [n]:``, the value of a
  last expression shown as ``Out[n]`` (typeset when it is mathematics),
  ``_``/``__``/``_n``/``Out``, ``obj?`` and ``obj??``, the line magics
  ``%who``, ``%whos``, ``%reset`` and ``%time``, completion with Tab;
* **Script** - a whole file, edited in the panel or opened from a file, run
  as ``python script.py`` runs it, what it defines then left in the
  console's namespace (as IPython's ``%run``).

Both run in the document's Python - the app's own interpreter on a phone,
the server's process under ``serve()``, Pyodide in a standalone page - with
SymPy imported and ``editor`` (:class:`~sympy_editor_console.console.Editor`)
to read and change the formula: ``editor.expr``, ``editor.selection`` (what
is selected when the code runs), ``editor["/1"]``, ``editor.apply(...)``.
A change made through it is a step of the formula's history like any edit.

Methods: ``run`` (``{"code", "path", "children", "interactive"}``: a cell;
``{"incomplete": true}`` without running when ``interactive`` and the code
is an unfinished block), ``script`` (``{"code", "name", "path",
"children"}``), ``complete`` (``{"code", "pos"}``), ``use`` (``{"n", "token",
"path", "children"}``: ``Out[n]`` of the namespace ``token`` put in the
formula), ``reset``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from sympy_editor.addons import Addon

from .console import Console, Editor, console_of, render

__all__ = ["ConsoleAddon", "ADDON", "Console", "Editor", "console_of", "render"]

STATIC = Path(__file__).parent / "static"


def _short(text: Any, n: int = 48) -> str:
    lines = [line.strip() for line in str(text).strip().splitlines() if line.strip()]
    text = lines[0] + (" …" if len(lines) > 1 else "") if lines else ""
    return text if len(text) <= n else text[: n - 1] + "…"


class ConsoleAddon(Addon):
    name = "console"
    label = "Python console"
    js = (STATIC / "console.js").read_text(encoding="utf-8")
    css = (STATIC / "console.css").read_text(encoding="utf-8")

    def contribute(self, doc, snap: Dict[str, Any], expr) -> None:
        # Small on purpose (it rides with every answer): which namespace the
        # panel is speaking to, and the number of the next input.
        state = doc.addon_state.get(self.name) or {}
        console = state.get("console")
        if console is not None and console.doc is doc:
            snap["console"] = {"token": console.token, "next": console.count}

    @staticmethod
    def _where(payload: Dict[str, Any]):
        path = payload.get("path") or None
        children = payload.get("children")
        return path, [int(i) for i in children] if isinstance(children, list) and children else None

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        console = console_of(doc, self.name)
        if method == "run":
            code = str(payload.get("code", ""))
            if payload.get("interactive") and Console.needs_more(code):
                return {"incomplete": True}
            if not code.strip():
                return {"empty": True, "next": console.count}
            path, children = self._where(payload)
            result = console.run_cell(code, path, children)
            result.update(token=console.token, next=console.count)
            return result
        if method == "script":
            path, children = self._where(payload)
            result = console.run_script(str(payload.get("code", "")), str(payload.get("name") or "script.py"), path, children)
            result.update(token=console.token, next=console.count)
            return result
        if method == "complete":
            return console.complete(str(payload.get("code", "")), payload.get("pos"))
        if method == "reset":
            console.reset()
            return {"token": console.token, "next": console.count}
        if method == "hello":
            return {"token": console.token, "next": console.count}
        if method == "use":
            n = int(payload.get("n"))
            # The numbers start again with every namespace: the Out[1] on the
            # screen from before a reset is not the Out[1] of this one, and
            # the button beside it put this one's value in the formula.
            token = payload.get("token")
            if token is not None and str(token) != console.token:
                raise ValueError(f"Out[{n}] belongs to a namespace that is gone (a reset, another session, "
                                 "or Python started again): run its input again to have the value")
            if n not in console.outputs:
                raise ValueError(f"There is no Out[{n}] (the namespace was reset, or it had no value)")
            value = console.outputs[n]
            path, children = self._where(payload)
            if children:
                doc.replace(path or "/", value, children=children)
            elif not path or path == "/":
                doc.set(value)
            else:
                doc.replace(path, value)
            return None
        raise ValueError(f"The console has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]):
        if method == "run":
            return "Console: " + _short(payload.get("code", ""))
        if method == "script":
            return "Script: " + _short(payload.get("name") or "script.py")
        if method == "use":
            return "Console: Out[%s]" % payload.get("n")
        return None


ADDON = ConsoleAddon()

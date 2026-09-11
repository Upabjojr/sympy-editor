"""sympy-editor add-on: LaTeX in, with its ambiguities laid open.

A panel under the formula takes LaTeX (typed or pasted), reads it with the
add-on's grammar (SymPy's Lark grammar, extended - see ``parser.py``), and
shows a first reading rendered, with

* every **ambiguity** of the text as a choice - ``f(x)`` applied or
  multiplied, how far ``\\sin x \\cos y`` reaches - preset by convention and
  changed with a menu, the reading following;
* every **constant name** the text uses (``\\pi``, ``e``, ``i``...) as a
  switch: the constant, or a plain symbol of that name.

What is read goes into the document over the selection or as the whole
expression.  Methods: ``read`` (a query: ``{"latex", "choices", "constants"}``
→ the reading, its ambiguities and constants), ``insert`` (the same, plus
``"path"``: committed) and ``warm`` (builds the parsers, which the add-on
otherwise starts building in the background as it is switched on: the first
reading should not wait for them).  Needs the ``lark`` package (pure Python).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from sympy import Basic

from sympy_editor.addons import Addon

from .parser import CONSTANTS, LatexReader, read_latex

__all__ = ["LatexAddon", "ADDON", "LatexReader", "read_latex", "CONSTANTS"]

STATIC = Path(__file__).parent / "static"


class LatexAddon(Addon):
    name = "latex"
    label = "LaTeX"
    requires = ("lark>=1.1",)
    js = (STATIC / "latex.js").read_text(encoding="utf-8")
    css = (STATIC / "latex.css").read_text(encoding="utf-8")

    def __init__(self) -> None:
        self.reader = LatexReader()

    def activate(self) -> None:
        try:
            import lark  # noqa: F401
        except ImportError:
            raise ImportError("The LaTeX add-on needs the lark package (pure Python): pip install lark") from None
        super().activate()
        # Half a second on a laptop, seconds on a phone: the grammar is built
        # now, off to one side, not when the user has begun to type.
        self.reader.warm(background=True)

    def client_options(self) -> Dict[str, Any]:
        return {"constants": [{"name": name, "value": str(value), "default": default, "label": label}
                              for name, (value, default, label) in CONSTANTS.items()]}

    def python_sources(self) -> Dict[str, str]:
        # the grammar files travel with the package into a Pyodide page
        out = super().python_sources()
        for path in sorted((STATIC / "grammar").glob("*.lark")):
            out["static/grammar/" + path.name] = path.read_text(encoding="utf-8")
        return out

    # -- reading -----------------------------------------------------------------

    @staticmethod
    def _known(doc) -> Dict[str, Any]:
        """The document's own names: its symbols (assumptions and shapes come
        along when a name is reused) and the functions it applies."""
        known: Dict[str, Any] = {}
        try:
            for sym in doc.expr.free_symbols:
                known[str(sym)] = sym
            for name, obj in doc.declared.items():
                known[str(name)] = obj
            for fn in doc.expr.atoms(__import__("sympy").core.function.AppliedUndef):
                known[fn.func.__name__] = fn.func
        except Exception:  # noqa: BLE001 - a document without an expression to speak of
            pass
        return known

    def read(self, doc, payload: Dict[str, Any]) -> Dict[str, Any]:
        choices = payload.get("choices") or {}
        constants = payload.get("constants") or {}
        return self.reader.read(str(payload.get("latex", "")), choices=dict(choices) if isinstance(choices, dict) else {},
                                constants=dict(constants) if isinstance(constants, dict) else {}, known=self._known(doc))

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "warm":
            # The panel asks as soon as it is shown ("background"): where
            # there are threads the parsers are being built in one (activate()
            # started it) and this answers at once; where there are none
            # (Pyodide) they are built now, before anything is typed.
            if not (payload.get("background") and self.reader.warm(background=True)):
                self.reader.warm()
            return {"ready": self.reader.ready}
        if method == "read":
            result = self.read(doc, payload)
            result.pop("expr", None)
            return result
        if method == "insert":
            result = self.read(doc, payload)
            if not result.get("ok"):
                raise ValueError(result.get("error") or "This LaTeX could not be read")
            expr: Basic = result["expr"]
            path = str(payload.get("path") or "/")
            if path == "/":
                doc.set(expr)
            else:
                doc.replace(path, expr)
            return None
        raise ValueError(f"The LaTeX add-on has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]):
        if method == "insert":
            text = str(payload.get("latex", "")).replace("\n", " ").strip()
            return "LaTeX: " + (text if len(text) <= 48 else text[:47] + "…")
        return None


ADDON = LatexAddon()

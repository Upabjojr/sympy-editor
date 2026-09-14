"""A sympy-editor add-on: write a formula by hand.

A panel with a writing area under the formula.  What is written there - with a
pen, a finger or the mouse, as strokes of ``(x, y, t)`` points - goes to the
stroke model of math-ocr (a checkout beside sympy-editor's: see
:mod:`sympy_editor_ink.recognizer`), which answers with LaTeX and a few other
readings.  The LaTeX add-on's reader turns the chosen one into SymPy, in the
document's own names, and it goes in over the selection or as the whole
expression.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from sympy import Basic

from sympy_editor.addons import Addon

from .recognizer import StrokeRecognizer, functions_as_commands, with_braces

__all__ = ["InkAddon", "ADDON", "StrokeRecognizer", "functions_as_commands", "with_braces"]

STATIC = Path(__file__).parent / "static"


class InkAddon(Addon):
    name = "ink"
    label = "Handwriting"
    #: pip names needed at run time - and a math-ocr checkout, which pip cannot give
    requires = ("numpy", "onnxruntime")
    js = (STATIC / "ink.js").read_text(encoding="utf-8")
    css = (STATIC / "ink.css").read_text(encoding="utf-8")

    def __init__(self, recognizer: Optional[StrokeRecognizer] = None) -> None:
        self.recognizer = recognizer or StrokeRecognizer()

    def activate(self) -> None:
        super().activate()
        # The model loaded as the add-on is switched on, off to one side - not
        # at the first formula, which would wait for it.
        self.recognizer.warm(background=True)

    def client_options(self) -> Dict[str, Any]:
        return {"status": self.recognizer.status()}

    @staticmethod
    def _latex():
        try:
            from sympy_editor_latex import ADDON as latex
        except ImportError:
            raise ImportError("The handwriting add-on reads what it recognizes with the LaTeX add-on: "
                              "pip install -e addons/sympy_editor_latex") from None
        return latex

    def _reading(self, doc, latex: str) -> Dict[str, Any]:
        """The LaTeX as SymPy would get it - read as the LaTeX panel reads,
        in the document's names - without the expression itself."""
        res = self._latex().read(doc, {"latex": latex})
        out = {k: res.get(k) for k in ("ok", "src", "latex", "error", "incomplete")}
        out["readings"] = 1 + sum(len(a.get("options") or []) - 1 for a in res.get("ambiguities") or [])
        return out

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "status":
            return self.recognizer.status()
        if method == "recognize":
            result = self.recognizer.recognize(payload.get("strokes"), beam=payload.get("beam", 4))
            for cand in result["candidates"]:
                cand["reading"] = self._reading(doc, cand["latex"])
            return result
        if method == "read":
            return {"reading": self._reading(doc, str(payload.get("latex", "")))}
        if method == "insert":
            res = self._latex().read(doc, {"latex": str(payload.get("latex", ""))})
            if not res.get("ok"):
                raise ValueError(res.get("error") or "This could not be read")
            expr: Basic = res["expr"]
            path = str(payload.get("path") or "/")
            children = payload.get("children")
            if children:
                doc.replace(path, expr, children=[int(i) for i in children])
            elif path == "/":
                doc.set(expr)
            else:
                doc.replace(path, expr)
            return None
        raise ValueError(f"The handwriting add-on has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]):
        if method == "insert":
            text = str(payload.get("latex", "")).replace("\n", " ").strip()
            return "Handwriting: " + (text if len(text) <= 48 else text[:47] + "…")
        return None


ADDON = InkAddon()

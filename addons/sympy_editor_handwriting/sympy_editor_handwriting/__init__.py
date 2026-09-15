# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Francesco Bonazzi
"""A sympy-editor add-on: write a formula by hand.

A panel with a writing area under the formula.  What is written there - with a
pen, a finger or the mouse, as strokes of ``(x, y, t)`` points - goes to the
stroke model of math-ocr (a checkout beside sympy-editor's: see
:mod:`sympy_editor_handwriting.recognizer`), which answers with LaTeX and a few other
readings.  The LaTeX add-on's reader turns the chosen one into SymPy, in the
document's own names, and it goes in over the selection or as the whole
expression.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from sympy import Basic

from sympy_editor.addons import Addon

from .recognizer import StrokeRecognizer, functions_as_commands, sized_delimiters, with_braces

__all__ = ["HandwritingAddon", "ADDON", "StrokeRecognizer", "functions_as_commands", "sized_delimiters", "with_braces"]

STATIC = Path(__file__).parent / "static"


class HandwritingAddon(Addon):
    name = "handwriting"
    label = "Handwriting"
    #: pip names needed at run time - and a math-ocr checkout, which pip cannot give
    requires = ("numpy", "onnxruntime")
    js = (STATIC / "handwriting.js").read_text(encoding="utf-8")
    css = (STATIC / "handwriting.css").read_text(encoding="utf-8")

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

    @staticmethod
    def _picks(payload: Dict[str, Any]) -> Dict[str, Any]:
        """The reader's options a payload carries: the alternative picked at
        each ambiguity, and the constants switched on or off."""
        choices, constants = payload.get("choices"), payload.get("constants")
        return {"choices": dict(choices) if isinstance(choices, dict) else {},
                "constants": dict(constants) if isinstance(constants, dict) else {}}

    def _read(self, doc, latex: str, picks: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._latex().read(doc, dict(picks or {}, latex=latex))

    def _reading(self, doc, latex: str, picks: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """The LaTeX as SymPy would get it - read as the LaTeX panel reads, in
        the document's names, with the options picked - and the options
        themselves: each ambiguity with the whole expression under each of its
        alternatives, each constant name with its switch, and every decision
        taken (``choices``, so that the next pick changes only itself)."""
        res = self._read(doc, latex, picks)
        out = {k: res.get(k) for k in ("ok", "src", "latex", "error", "incomplete")}
        out["ambiguities"] = res.get("ambiguities") or []
        out["constants"] = res.get("constants") or []
        out["choices"] = res.get("choices") or {}
        out["readings"] = 1 + sum(len(a.get("options") or []) - 1 for a in out["ambiguities"])
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
            return {"reading": self._reading(doc, str(payload.get("latex", "")), self._picks(payload))}
        if method == "insert":
            res = self._read(doc, str(payload.get("latex", "")), self._picks(payload))
            if not res.get("ok"):
                raise ValueError(res.get("error") or "This could not be read")
            # where the LaTeX panel would put it: the selection, the caret, the end
            self._latex().put(doc, res["expr"], payload, str(payload.get("latex", "")))
            return None
        raise ValueError(f"The handwriting add-on has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]):
        if method == "insert":
            text = str(payload.get("latex", "")).replace("\n", " ").strip()
            return "Handwriting: " + (text if len(text) <= 48 else text[:47] + "…")
        return None


ADDON = HandwritingAddon()

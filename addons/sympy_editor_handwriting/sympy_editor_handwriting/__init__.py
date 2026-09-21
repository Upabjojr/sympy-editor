# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Francesco Bonazzi
"""A sympy-editor add-on: write a formula by hand, on the formula itself.

The editor's own formula is the writing area: the Pen (in the editor's tools)
gives it room and takes the pointer, and what is written there - with a pen, a
finger or the mouse, as strokes of ``(x, y, t)`` points - goes to the stroke
model of math-ocr (a checkout beside sympy-editor's: see
:mod:`sympy_editor_handwriting.recognizer`), which answers with LaTeX and a few
other readings.  The LaTeX add-on's reader turns the best one into SymPy, in
the document's own names, and it goes into the formula at once - over the
selection, at the cursor, or together with the piece of the formula the ink
was written by, which the strokes carry as a stand-in (``\\Delta``: see
``write`` below).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional

import sympy
from sympy import Basic

from sympy_editor.addons import Addon

from .recognizer import StrokeRecognizer, functions_as_commands, sized_delimiters, stand_in, with_braces

__all__ = ["HandwritingAddon", "Engine", "ADDON", "StrokeRecognizer", "functions_as_commands",
           "sized_delimiters", "with_braces"]

STATIC = Path(__file__).parent / "static"

#: What a reading carries where a node of the formula stands: ``\Delta`` for the
#: triangle drawn in its place (see recognizer.stand_in), or the token a model
#: trained with context boxes writes (``\ctx``).
STAND_IN = re.compile(r"\\Delta(?![A-Za-z])")


#: What a nested reading carries where the piece goes: a symbol whose name no
#: formula uses, read by the LaTeX add-on and then replaced by the piece itself
#: (``xreplace``).  The piece's own LaTeX, read back, would not be the piece:
#: ``f(x)`` would be ``f*x``, ``x_1`` a symbol ``x_{1}``, ``lamda`` ``lambda``.
PIECE_NAME = "nestedpiece"
PIECE_TEX = r"\mathit{%s}" % PIECE_NAME


def _stand_in(token: str):
    return re.compile(re.escape(token) + r"(?![A-Za-z])")


def _takes_context(recognizer) -> bool:
    """Whether a recognizer's ``recognize`` takes the piece's box itself."""
    try:
        return "context" in inspect.signature(recognizer.recognize).parameters
    except (TypeError, ValueError):
        return False


def _nest(tex: str, piece: str, stand=STAND_IN) -> str:
    """``tex`` with the node's LaTeX in the stand-in's place: grouped where it is
    more than one symbol, in delimiters where a script follows it."""
    single = bool(re.fullmatch(r"[A-Za-z0-9]|\\[A-Za-z]+|\{[^{}]*\}", piece.strip()))

    def put(m: "re.Match[str]") -> str:
        after = tex[m.end():m.end() + 2].lstrip()[:1]
        if after in ("^", "_"):
            return piece if single else r"\left(" + piece + r"\right)"
        return piece if single else "{" + piece + "}"

    return stand.sub(put, tex, count=1)


class Engine:
    """One way of reading pen strokes, and where the reading happens.

    A *Python* engine has a recognizer of its own - anything with
    ``status()``, ``warm(background)`` and ``recognize(strokes, beam, limit)``
    as :class:`~sympy_editor_handwriting.recognizer.StrokeRecognizer` has
    them - and the strokes go to it.  A *host* engine reads in the page's
    host instead: the app's own recognizer (Apple's Vision, say) or the
    browser's Handwriting Recognition API, which the panel asks directly; its
    readings then come back here to be read as SymPy, like any others.
    """

    def __init__(self, name: str, label: str, recognizer=None, *, where: str = "python",
                 note: str = "") -> None:
        if where not in ("python", "host"):
            raise ValueError("where must be 'python' or 'host'")
        if where == "python" and recognizer is None:
            raise ValueError(f"the {name!r} engine reads here, so it needs a recognizer")
        self.name = name
        self.label = label
        self.recognizer = recognizer
        self.where = where
        #: What the panel says about this engine under its name - what it is
        #: good for, and what it is not.
        self.note = note

    def status(self) -> Dict[str, Any]:
        """Whether this engine can read here, and if not, why.  A host engine
        can only be asked in the page: the panel decides, and says so."""
        if self.where == "host":
            return {"available": None, "reason": "the page says whether its host can read strokes"}
        return self.recognizer.status()

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "label": self.label, "where": self.where,
                "note": self.note, "status": self.status()}


class HandwritingAddon(Addon):
    name = "handwriting"
    label = "Handwriting"
    #: pip names needed at run time - and a math-ocr checkout, which pip cannot give
    requires = ("numpy", "onnxruntime")
    js = (STATIC / "handwriting.js").read_text(encoding="utf-8")
    css = (STATIC / "handwriting.css").read_text(encoding="utf-8")

    def __init__(self, recognizer: Optional[StrokeRecognizer] = None,
                 engines: Optional[Iterable[Engine]] = None, engine: Optional[str] = None) -> None:
        #: What can read strokes here, by name.  math-ocr's stroke model is
        #: the one that reads mathematics; the host's own reader is offered
        #: beside it (see :class:`Engine`), and a page in an app or in a
        #: browser that has one says so.
        self.engines: "OrderedDict[str, Engine]" = OrderedDict()
        for eng in engines or self._default_engines(recognizer):
            self.engines[eng.name] = eng
        #: Which of them is asked.  A host engine the page cannot offer falls
        #: back to the first that reads here (see :meth:`_engine`).
        self.engine = engine or next(iter(self.engines))
        first = self.engines[self.engine]
        #: What ``recognizer`` used to be: the model, for whoever asks.
        self.recognizer = first.recognizer or next(
            (e.recognizer for e in self.engines.values() if e.recognizer is not None), None)

    @staticmethod
    def _default_engines(recognizer: Optional[StrokeRecognizer]) -> List[Engine]:
        return [
            Engine("math-ocr", "math-ocr's stroke model", recognizer or StrokeRecognizer(),
                   note="reads mathematics: fractions, exponents, roots, the layout as written"),
            Engine("host", "what this device reads handwriting with", where="host",
                   note="the app's or the browser's own reader: text, a line at a time - "
                        "it knows nothing of fractions or exponents, and needs no model here"),
        ]

    def activate(self) -> None:
        super().activate()
        # The model loaded as the add-on is switched on, off to one side - not
        # at the first formula, which would wait for it.
        for eng in self.engines.values():
            if eng.recognizer is not None:
                eng.recognizer.warm(background=True)

    def _engine(self, name: Optional[str] = None) -> Engine:
        """The engine to ask: the one named, or the one chosen."""
        eng = self.engines.get(str(name or self.engine))
        if eng is None:
            raise ValueError(f"No handwriting engine called {name or self.engine!r}")
        return eng

    def _reader(self, name: Optional[str] = None) -> Engine:
        """The engine to hand strokes to here: the one named or chosen when it
        reads here, else the first that does - a host engine reads in the
        page, and strokes that reach Python are for one of these."""
        eng = self._engine(name)
        if eng.recognizer is not None:
            return eng
        for other in self.engines.values():
            if other.recognizer is not None:
                return other
        raise ValueError("No handwriting engine reads strokes here")

    def client_options(self) -> Dict[str, Any]:
        chosen = self._engine()
        return {"status": chosen.status() if chosen.where == "python" else self.engines["math-ocr"].status()
                if "math-ocr" in self.engines else chosen.status(),
                "engine": self.engine,
                "engines": [eng.describe() for eng in self.engines.values()]}

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

    @staticmethod
    def _piece(doc, nest) -> Optional[Basic]:
        """The node at ``nest`` ("" and "/" are the whole formula), or None."""
        if nest is None:
            return None
        try:
            return doc.get(str(nest))
        except Exception:  # noqa: BLE001 - a path the document no longer has
            return None

    def _read(self, doc, latex: str, picks: Optional[Dict[str, Any]] = None, nest=None) -> Dict[str, Any]:
        payload = dict(picks or {}, latex=latex)
        piece = self._piece(doc, nest) if PIECE_TEX in latex else None
        if piece is not None:
            payload["pieces"] = {PIECE_NAME: piece}
        return self._latex().read(doc, payload)

    def _reading(self, doc, latex: str, picks: Optional[Dict[str, Any]] = None, nest=None) -> Dict[str, Any]:
        """The LaTeX as SymPy would get it - read as the LaTeX panel reads, in
        the document's names, with the options picked - and the options
        themselves: each ambiguity with the whole expression under each of its
        alternatives, each constant name with its switch, and every decision
        taken (``choices``, so that the next pick changes only itself)."""
        res = self._read(doc, latex, picks, nest)
        out = {k: res.get(k) for k in ("ok", "src", "latex", "error", "incomplete")}
        out["ambiguities"] = res.get("ambiguities") or []
        out["constants"] = res.get("constants") or []
        out["choices"] = res.get("choices") or {}
        out["readings"] = 1 + sum(len(a.get("options") or []) - 1 for a in out["ambiguities"])
        return out

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "status":
            return self._engine().status()
        if method == "engines":
            return {"engine": self.engine, "engines": [eng.describe() for eng in self.engines.values()]}
        if method == "engine":
            # Which engine reads from now on.  A host engine is the page's to
            # run: nothing is loaded here, and "write" takes its readings in.
            eng = self._engine(payload.get("name"))
            self.engine = eng.name
            if eng.recognizer is not None:
                eng.recognizer.warm(background=True)
            return {"engine": self.engine, "where": eng.where, "status": eng.status()}
        if method == "recognize":
            result = self._reader(payload.get("engine")).recognizer.recognize(payload.get("strokes"),
                                                                              beam=payload.get("beam", 4))
            for cand in result["candidates"]:
                cand["reading"] = self._reading(doc, cand["latex"])
            return result
        if method == "read":
            return {"reading": self._reading(doc, str(payload.get("latex", "")), self._picks(payload),
                                             payload.get("nest"))}
        if method == "write":
            # What is written, read: strokes in, readings out - and, with ``nest``
            # (a node's path), read together with that node, whose LaTeX takes the
            # place of the stand-in (``\Delta`` for the triangle, or the
            # recognizer's own ``stand_in`` token for the box).  Readings that
            # do not carry it exactly once are of the ink alone, and say so.
            #
            # The strokes may have been read already, by the host's own reader
            # (a host engine, see Engine): then the readings come in as
            # ``candidates`` and only the nesting and the SymPy are done here.
            given = payload.get("candidates")
            if given is not None:
                result = {"candidates": [dict(c) for c in given], "ms": payload.get("ms", 0),
                          "strokes": len(payload.get("strokes") or []), "engine": payload.get("engine") or self.engine}
            else:
                # A piece to read with (``context``, its box): given to a
                # recognizer that takes it as such, drawn into the strokes as
                # the triangle for one that does not.
                reader = self._reader(payload.get("engine"))
                rec = reader.recognizer
                strokes, box = payload.get("strokes"), payload.get("context")
                nesting = box is not None and payload.get("nest") is not None
                if nesting and _takes_context(rec):
                    result = rec.recognize(strokes, beam=payload.get("beam", 4), context=box)
                else:
                    if nesting:
                        strokes = stand_in(box, strokes)
                    result = rec.recognize(strokes, beam=payload.get("beam", 4))
                result["engine"] = reader.name
            stand = _stand_in(result.get("stand_in") or r"\Delta")
            nest = payload.get("nest")        # "" is the whole formula, a piece like any other
            node = self._piece(doc, nest)
            piece = sympy.latex(node) if node is not None else None
            out = []
            for cand in result["candidates"]:
                latex, display, nested = cand["latex"], cand.get("display") or cand["latex"], False
                if piece is not None and len(stand.findall(latex)) == 1:
                    # the piece goes in as itself: a placeholder in the text,
                    # the piece in the SymPy (see PIECE_TEX); its LaTeX is shown
                    latex = stand.sub(lambda m: PIECE_TEX, latex, count=1)
                    display, nested = _nest(display, piece, stand), True
                c = {"latex": latex, "display": display, "raw": cand.get("raw"), "score": cand.get("score"),
                     "nested": nested, "reading": self._reading(doc, latex, nest=nest if nested else None)}
                if nested:
                    c["nest"] = str(nest)
                out.append(c)
            result["candidates"] = out
            result["nested"] = any(c["nested"] for c in out)
            return result
        if method == "insert":
            res = self._read(doc, str(payload.get("latex", "")), self._picks(payload), payload.get("nest"))
            if not res.get("ok"):
                raise ValueError(res.get("error") or "This could not be read")
            # where the LaTeX panel would put it: the selection, the caret, the end
            self._latex().put(doc, res["expr"], payload, str(payload.get("latex", "")))
            return None
        raise ValueError(f"The handwriting add-on has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]):
        if method == "insert":
            text = str(payload.get("display") or payload.get("latex", "")).replace("\n", " ").strip()
            return "Handwriting: " + (text if len(text) <= 48 else text[:47] + "…")
        return None


ADDON = HandwritingAddon()

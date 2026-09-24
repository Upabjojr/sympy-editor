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


#: What a reading of ink over an operator may say, as LaTeX, and the
#: operator it is (``Document.operator``'s).
OPERATOR_LATEX = {
    "=": "=", "<": "<", ">": ">",
    "\\le": "<=", "\\leq": "<=", "\\leqslant": "<=", "\u2264": "<=", "<=": "<=",
    "\\ge": ">=", "\\geq": ">=", "\\geqslant": ">=", "\u2265": ">=", ">=": ">=",
    "\\ne": "!=", "\\neq": "!=", "\u2260": "!=", "!=": "!=",
    "+": "+", "-": "-", "\u2212": "-",
    "*": "*", "\\cdot": "*", "\\times": "*", "\\ast": "*",
    "/": "/", "\\div": "/", "^": "^",
    "&": "&", "\\land": "&", "\\wedge": "&", "|": "|", "\\lor": "|", "\\vee": "|",
}
#: How each operator is shown on its reading's button.
OPERATOR_TEX = {"=": "=", "<": "<", ">": ">", "<=": "\\le", ">=": "\\ge", "!=": "\\ne", "+": "+", "-": "-",
                "*": "\\cdot", "/": "/", "^": "\\hat{\\,}", "&": "\\land", "|": "\\lor"}


def operator_of(latex: str) -> Optional[str]:
    """The operator a reading is, or None: ``\\leq`` -> ``<=``, ``=`` ->
    ``=``; braces and spaces around it do not count."""
    text = re.sub(r"\s+", "", str(latex or "")).strip("{}")
    return OPERATOR_LATEX.get(text)


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
    def _piece(doc, nest, children=None) -> Optional[Basic]:
        """The node at ``nest`` ("" and "/" are the whole formula) - or, with
        ``children``, those of its arguments, as the product or sum they
        form - or None."""
        if nest is None:
            return None
        try:
            node = doc.get(str(nest))
            if not children:
                return node
            args = [node.args[int(i)] for i in children]
            return args[0] if len(args) == 1 else node.func(*args)
        except Exception:  # noqa: BLE001 - a path the document no longer has
            return None

    def _read(self, doc, latex: str, picks: Optional[Dict[str, Any]] = None, nest=None,
              children=None) -> Dict[str, Any]:
        payload = dict(picks or {}, latex=latex)
        piece = self._piece(doc, nest, children) if PIECE_TEX in latex else None
        if piece is not None:
            payload["pieces"] = {PIECE_NAME: piece}
        return self._latex().read(doc, payload)

    @staticmethod
    def _operator_reading(latex: str) -> Dict[str, Any]:
        """A reading of ink written over a selected operator: the operator it
        is (what ``Document.operator`` takes), never an expression."""
        op = operator_of(latex)
        if op is None:
            return {"ok": False, "src": "", "latex": latex, "error": f"Not an operator: {latex}",
                    "ambiguities": [], "constants": [], "choices": {}, "readings": 0}
        return {"ok": True, "src": op, "latex": latex, "operator": op, "error": None,
                "ambiguities": [], "constants": [], "choices": {}, "readings": 1}

    def _reading(self, doc, latex: str, picks: Optional[Dict[str, Any]] = None, nest=None,
                 children=None) -> Dict[str, Any]:
        """The LaTeX as SymPy would get it - read as the LaTeX panel reads, in
        the document's names, with the options picked - and the options
        themselves: each ambiguity with the whole expression under each of its
        alternatives, each constant name with its switch, and every decision
        taken (``choices``, so that the next pick changes only itself)."""
        res = self._read(doc, latex, picks, nest, children)
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
        if method == "latex_of":
            # The pieces offered to read the ink with, typeset on their
            # buttons: the LaTeX of each, as the editor draws it.
            out = {}
            for path in payload.get("paths") or []:
                try:
                    out[str(path)] = sympy.latex(doc.get(str(path)), **dict(doc.printer_settings))
                except Exception:  # noqa: BLE001 - a path gone with an edit: its button keeps its text
                    continue
            return {"latex": out}
        if method == "read":
            return {"reading": self._reading(doc, str(payload.get("latex", "")), self._picks(payload),
                                             payload.get("nest"), payload.get("children"))}
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
            if payload.get("operator"):
                # Written over a selected operator: it takes that operator's
                # place, so it is read as an operator - never as a formula,
                # nor together with a piece.  The readings that are no
                # operator go; the rest once each, best first.
                if given is not None:
                    result = {"candidates": [dict(c) for c in given], "ms": payload.get("ms", 0),
                              "engine": payload.get("engine") or self.engine}
                else:
                    reader = self._reader(payload.get("engine"))
                    result = reader.recognizer.recognize(payload.get("strokes"), beam=payload.get("beam", 4))
                    result["engine"] = reader.name
                out, seen = [], set()
                for cand in result["candidates"]:
                    reading = self._operator_reading(cand["latex"])
                    if not reading["ok"] or reading["operator"] in seen:
                        continue
                    seen.add(reading["operator"])
                    out.append({"latex": cand["latex"], "display": OPERATOR_TEX.get(reading["operator"], cand["latex"]),
                                "raw": cand.get("raw"), "score": cand.get("score"), "nested": False,
                                "reading": reading})
                if not out and result["candidates"]:
                    first = result["candidates"][0]["latex"]
                    out.append({"latex": first, "display": first, "raw": result["candidates"][0].get("raw"),
                                "score": result["candidates"][0].get("score"), "nested": False,
                                "reading": self._operator_reading(first)})
                result["candidates"] = out
                result["nested"] = False
                return result
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
                family = self._siblings(doc, rec, payload) if nesting else None
                if family is not None:
                    result = rec.recognize(strokes, beam=payload.get("beam", 4), context=family["boxes"])
                    result["engine"] = reader.name
                    return self._with_siblings(doc, result, family)
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
            # a reading nested among siblings replaces the run it names: its
            # ``children`` are the piece's, and where it goes
            res = self._read(doc, str(payload.get("latex", "")), self._picks(payload), payload.get("nest"),
                             payload.get("children") if payload.get("nest") is not None else None)
            if not res.get("ok"):
                raise ValueError(res.get("error") or "This could not be read")
            # where the LaTeX panel would put it: the selection, the caret, the end
            self._latex().put(doc, res["expr"], payload, str(payload.get("latex", "")))
            return None
        raise ValueError(f"The handwriting add-on has no method {method!r}")

    @staticmethod
    def _siblings(doc, rec, payload) -> Optional[Dict[str, Any]]:
        """The printed siblings to read the ink with, when the piece is one of
        the factors of a product or the terms of a sum and the model reads
        several boxes: ``{"parent", "index" (argument per box), "boxes",
        "tokens"}``, the boxes left to right and at most as many as the model
        has tokens for, around the piece.  None otherwise."""
        sibs = payload.get("siblings") or []
        tokens = rec.box_tokens() if hasattr(rec, "box_tokens") else []
        if len(sibs) < 2 or len(tokens) < 2:
            return None
        parent = None
        index = []
        for s_ in sibs:
            path = str(s_.get("path", ""))
            head, _, last = path.rpartition("/")
            if not last.isdigit() or (parent is not None and head != parent):
                return None
            parent = head
            index.append(int(last))
        try:
            node = doc.get(parent)
        except Exception:  # noqa: BLE001 - a path the document no longer has
            return None
        if not isinstance(node, (sympy.Add, sympy.Mul)) or not node.is_commutative:
            return None
        if sorted(index) != list(range(len(node.args))):
            return None                       # not every argument is printed as a box of its own
        nest = str(payload.get("nest"))
        at = next((k for k, s_ in enumerate(sibs) if str(s_.get("path")) == nest), None)
        if at is None:
            return None
        lo = min(max(0, at - len(tokens) // 2), max(0, len(sibs) - len(tokens)))
        window = list(range(lo, min(len(sibs), lo + len(tokens))))
        return {"parent": parent, "index": [index[k] for k in window],
                "boxes": [list(sibs[k]["box"]) for k in window], "tokens": tokens[:len(window)]}

    def _with_siblings(self, doc, result, family) -> Dict[str, Any]:
        """Readings of ink written among siblings: each names a run of the
        boxes, side by side, once each; that run of the parent's arguments is
        what it replaces (``nest`` the parent, ``children`` the run), the run
        itself in its place.  Readings that name no such run are of the ink
        alone."""
        node = doc.get(family["parent"])
        find = re.compile("|".join(re.escape(t) + r"(?![A-Za-z])" for t in
                                   sorted(family["tokens"], key=len, reverse=True)))
        out = []
        for cand in result["candidates"]:
            latex, display = cand["latex"], cand.get("display") or cand["latex"]
            c = {"latex": latex, "display": display, "raw": cand.get("raw"), "score": cand.get("score"),
                 "nested": False}
            hits = list(find.finditer(latex))
            named = [family["tokens"].index(m.group(0)) for m in hits]
            run = named == list(range(named[0], named[0] + len(named))) if named else False
            joined = run and all(not latex[a.end():b.start()].strip() for a, b in zip(hits, hits[1:]))
            if joined:
                children = [family["index"][k] for k in named]
                piece = self._piece(doc, family["parent"], children)
                a, b = hits[0].start(), hits[-1].end()
                latex = latex[:a] + PIECE_TEX + latex[b:]
                shown = list(find.finditer(display))
                if len(shown) == len(hits):
                    one = _stand_in(shown[0].group(0))
                    display = _nest(display[:shown[0].start()] + shown[0].group(0) + display[shown[-1].end():],
                                    sympy.latex(piece), one)
                c.update(latex=latex, display=display, nested=True, nest=family["parent"], children=children)
            c["reading"] = self._reading(doc, c["latex"], nest=c.get("nest"), children=c.get("children"))
            out.append(c)
        result["candidates"] = out
        result["nested"] = any(c["nested"] for c in out)
        return result

    def describe(self, method: str, payload: Dict[str, Any]):
        if method == "insert":
            text = str(payload.get("display") or payload.get("latex", "")).replace("\n", " ").strip()
            return "Handwriting: " + (text if len(text) <= 48 else text[:47] + "…")
        return None


ADDON = HandwritingAddon()

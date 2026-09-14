"""math-ocr's stroke model, run beside the editor's Python.

The model, and the code that turns pen strokes into its input, live in the
math-ocr project rather than here: its weights are not this package's to
carry, and its features have to
be exactly the ones it was trained on.  So the recognizer finds a math-ocr
checkout, imports its ``mathocr.data.inkml`` and ``mathocr.tokenizer`` (numpy
and the standard library only - no PyTorch), and runs the exported ONNX graphs
with onnxruntime.

Where: ``SYMPY_EDITOR_MATHOCR`` names the checkout; without it, a folder
``math-ocr`` beside any folder above this file is used (a checkout next to
sympy-editor's).  Which model: ``SYMPY_EDITOR_MATHOCR_MODEL``, a folder of the
checkout or any path, by default ``export/stroke_b_int8`` - the larger stroke
model, quantised (38.9 % exact match on math-ocr's held-out test split).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_MODEL = "export/stroke_b_int8"
MAX_POINTS = 20000        # more ink than a formula needs: refused rather than slowed down on
MAX_TOKENS = 150          # the decoder's position table holds 168: never run past it

#: Function names that the training labels spell as letters (``sin``, ``log``,
#: ``lim``...) - and so the model does, with no command for any of them in its
#: vocabulary.  The LaTeX reader would read the letters as a product.  Longest
#: first, so that ``sinh`` is not ``sin h``.
FUNCTION_NAMES = sorted(
    "arcsin arccos arctan sinh cosh tanh coth sin cos tan sec csc cot log ln exp lim max min det".split(),
    key=len, reverse=True)

#: The tokens that take arguments, and how many.  Normalised for training, the
#: labels - and so the model - write a one-token argument without its braces
#: (``\frac 12``, ``x^2``).
ARITY = {"\\frac": 2, "\\binom": 2, "\\sqrt": 1, "^": 1, "_": 1, "\\hat": 1, "\\bar": 1, "\\vec": 1,
         "\\dot": 1, "\\ddot": 1, "\\tilde": 1, "\\overline": 1, "\\underline": 1, "\\widehat": 1,
         "\\widetilde": 1, "\\mathbb": 1, "\\mathcal": 1, "\\mathrm": 1, "\\mathbf": 1, "\\mathfrak": 1,
         "\\overrightarrow": 1, "\\text": 1}


def functions_as_commands(tokens: Sequence[str]) -> List[str]:
    """``s i n x`` -> ``\\sin x``: a run of one-letter tokens spelling a
    function name becomes its command."""
    out: List[str] = []
    i = 0
    while i < len(tokens):
        for name in FUNCTION_NAMES:
            run = tokens[i:i + len(name)]
            if len(run) == len(name) and all(len(t) == 1 and t.isalpha() for t in run) and "".join(run) == name:
                out.append("\\" + name)
                i += len(name)
                break
        else:
            out.append(tokens[i])
            i += 1
    return out


def _group(tokens: Sequence[str], i: int) -> Tuple[List[str], int]:
    """``tokens[i]`` is ``{``: the tokens inside it, and the index after its ``}``."""
    depth = 0
    for j in range(i, len(tokens)):
        if tokens[j] == "{":
            depth += 1
        elif tokens[j] == "}":
            depth -= 1
            if depth == 0:
                return list(tokens[i + 1:j]), j + 1
    return list(tokens[i + 1:]), len(tokens)              # unbalanced: the rest


def _unit(tokens: Sequence[str], i: int) -> Tuple[List[str], int]:
    t = tokens[i]
    if t == "{":
        inner, j = _group(tokens, i)
        return ["{"] + with_braces(inner) + ["}"], j
    out, j = [t], i + 1
    if t == "\\sqrt" and j < len(tokens) and tokens[j] == "[":          # \sqrt[n]{x}
        close = next((k for k in range(j, len(tokens)) if tokens[k] == "]"), None)
        if close is not None:
            out += ["["] + with_braces(tokens[j + 1:close]) + ["]"]
            j = close + 1
    for _ in range(ARITY.get(t, 0)):
        if j >= len(tokens) or tokens[j] in ("}", "^", "_"):
            break
        if tokens[j] == "{":
            inner, j = _group(tokens, j)
            out += ["{"] + with_braces(inner) + ["}"]
        else:
            piece, j = _unit(tokens, j)
            out += ["{"] + piece + ["}"]
    return out, j


def with_braces(tokens: Sequence[str]) -> List[str]:
    """Every argument braced: ``\\frac 12`` -> ``\\frac{1}{2}``, ``x^2`` -> ``x^{2}``."""
    out: List[str] = []
    i = 0
    while i < len(tokens):
        piece, i = _unit(tokens, i)
        out += piece
    return out


def find_mathocr() -> Optional[Path]:
    """The math-ocr checkout: ``SYMPY_EDITOR_MATHOCR``, or a ``math-ocr``
    folder beside one of the folders above this file."""
    env = os.environ.get("SYMPY_EDITOR_MATHOCR")
    places = [Path(env).expanduser()] if env else [p / "math-ocr" for p in Path(__file__).resolve().parents]
    for place in places:
        if _is_mathocr(place):
            return place
    return None


def _is_mathocr(place: Path) -> bool:
    return (place / "mathocr" / "tokenizer.py").is_file() and (place / "mathocr" / "data" / "inkml.py").is_file()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _strokes(raw) -> list:
    """The page's strokes - lists of ``[x, y, t]`` - as math-ocr's arrays,
    whatever does not read as finite numbers dropped."""
    import numpy as np
    out, total = [], 0
    for stroke in raw or []:
        pts = []
        for p in stroke or []:
            try:
                x, y = float(p[0]), float(p[1])
                t = float(p[2]) if len(p) > 2 else float(len(pts))
            except (TypeError, ValueError, IndexError):
                continue
            if math.isfinite(x) and math.isfinite(y) and math.isfinite(t):
                pts.append((x, y, t))
        if pts:
            out.append(np.asarray(pts, dtype=np.float32))
            total += len(pts)
    if total > MAX_POINTS:
        raise ValueError(f"Too much ink to read ({total} points): clear some of it")
    return out


def _beam_search(enc, dec, feats, bos: int, eos: int, beam: int = 4, max_tokens: int = MAX_TOKENS,
                 alpha: float = 0.7) -> List[Tuple[float, List[int]]]:
    """math-ocr's beam search (``mathocr/infer.py``) over the exported graphs.
    The decoder step has no cache, so each step feeds the whole prefix - cheap
    for formulas a dozen tokens long.  Finished readings are length-normalised
    as GNMT does, ``((5 + n) / 6) ** alpha``; best first."""
    import numpy as np
    mem, mask = enc.run(None, {"src": feats[None].astype(np.float32),
                               "src_len": np.array([len(feats)], dtype=np.int64)})
    mem, mask = np.repeat(mem, beam, axis=0), np.repeat(mask, beam, axis=0)
    tokens = np.full((beam, 1), bos, dtype=np.int64)
    scores = np.full(beam, -1e9)
    scores[0] = 0.0
    finished: List[Tuple[float, List[int]]] = []
    for _ in range(max_tokens):
        logits = dec.run(None, {"tokens": tokens, "memory": mem, "mem_mask": mask})[0].astype(np.float64)
        logits -= logits.max(axis=1, keepdims=True)
        logp = logits - np.log(np.exp(logits).sum(axis=1, keepdims=True))
        cand = (scores[:, None] + logp).ravel()
        top = np.argpartition(-cand, beam - 1)[:beam]
        top = top[np.argsort(-cand[top])]
        rows, picked = top // logp.shape[1], top % logp.shape[1]
        tokens = np.concatenate([tokens[rows], picked[:, None]], axis=1)
        scores = cand[top].copy()
        alive = 0
        for i in range(beam):
            if picked[i] == eos:
                n = tokens.shape[1] - 1
                finished.append((float(scores[i]) / (((5 + n) / 6.0) ** alpha), tokens[i, 1:-1].tolist()))
                scores[i] = -1e9
            else:
                alive += 1
        if not alive or len(finished) >= beam:
            break
    if not finished:
        best = int(scores.argmax())
        finished.append((float(scores[best]), tokens[best, 1:].tolist()))
    return sorted(finished, key=lambda f: -f[0])


class StrokeRecognizer:
    """Pen strokes in, LaTeX readings out.  Loads nothing until asked; one
    model per recognizer, shared by every document that uses it."""

    def __init__(self, mathocr=None, model=None, threads: Optional[int] = None) -> None:
        self._root = Path(mathocr).expanduser() if mathocr else None
        self._model = model
        self._threads = threads
        self._lock = threading.Lock()
        self._loaded = None

    @property
    def root(self) -> Optional[Path]:
        return self._root if self._root is not None else find_mathocr()

    def model_dir(self) -> Optional[Path]:
        wanted = Path(str(self._model or os.environ.get("SYMPY_EDITOR_MATHOCR_MODEL") or DEFAULT_MODEL)).expanduser()
        if wanted.is_absolute():
            return wanted
        root = self.root
        return None if root is None else root / wanted

    def status(self) -> Dict[str, Any]:
        """Whether recognition can run here - and if not, why - without
        loading anything."""
        if importlib.util.find_spec("onnxruntime") is None:
            return {"available": False, "reason": "onnxruntime is not installed in this Python (pip install onnxruntime)"}
        root = self.root
        if root is None or not _is_mathocr(root):
            where = f" at {root}" if root is not None else ""
            return {"available": False,
                    "reason": f"math-ocr was not found{where}: set SYMPY_EDITOR_MATHOCR to its folder"}
        model = self.model_dir()
        if model is None or not (model / "encoder.onnx").is_file() or not (model / "decoder_step.onnx").is_file():
            return {"available": False, "reason": f"No exported model in {model} (encoder.onnx, decoder_step.onnx)"}
        mode = (_read_json(model / "meta.json") or {}).get("mode", "stroke")
        if mode != "stroke":
            return {"available": False, "reason": f"{model.name} is a {mode} model: pen strokes need a stroke model"}
        return {"available": True, "model": str(model), "mathocr": str(root)}

    def load(self):
        """(encoder, decoder, tokenizer, math-ocr's inkml module, its
        tokenizer module, meta), loaded once."""
        with self._lock:
            if self._loaded is not None:
                return self._loaded
            st = self.status()
            if not st["available"]:
                raise RuntimeError(st["reason"])
            root, model = Path(st["mathocr"]), Path(st["model"])
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            inkml = importlib.import_module("mathocr.data.inkml")
            tokenizer = importlib.import_module("mathocr.tokenizer")
            import onnxruntime as ort
            opts = ort.SessionOptions()
            # One thread: math-ocr measured more of them to be many times
            # slower on a busy CPU (MATHOCR_THREADS overrides, as there).
            opts.intra_op_num_threads = max(1, int(self._threads or os.environ.get("MATHOCR_THREADS", 1)))
            opts.inter_op_num_threads = 1
            providers = ["CPUExecutionProvider"]
            enc = ort.InferenceSession(str(model / "encoder.onnx"), opts, providers=providers)
            dec = ort.InferenceSession(str(model / "decoder_step.onnx"), opts, providers=providers)
            tok = tokenizer.Tokenizer.load(model / "vocab.json")
            self._loaded = (enc, dec, tok, inkml, tokenizer, _read_json(model / "meta.json") or {})
            return self._loaded

    def warm(self, background: bool = True) -> bool:
        """Load the model now rather than at the first formula - in a thread
        of its own where there are threads.  False when it cannot run here."""
        if self._loaded is not None:
            return True
        if not self.status()["available"]:
            return False

        def go():
            try:
                self.load()
            except Exception:  # noqa: BLE001 - the first reading loads it again, and says what is wrong
                pass

        if background:
            try:
                threading.Thread(target=go, name="ink-model", daemon=True).start()
                return True
            except RuntimeError:
                pass
        go()
        return self._loaded is not None

    def recognize(self, strokes, beam: int = 4, limit: int = 5) -> Dict[str, Any]:
        """The readings of ``strokes`` (lists of ``[x, y, t]``), best first:
        ``{"candidates": [{"latex", "raw", "score"}], "ms", "strokes", "points"}``.
        ``latex`` is what the editor reads - functions as commands, every
        argument braced; ``raw`` is the model's own text."""
        enc, dec, tok, inkml, tokenizer, meta = self.load()
        ink = inkml.Ink(strokes=_strokes(strokes), label="")
        if not ink.strokes:
            raise ValueError("Nothing is written yet")
        feats = inkml.ink_to_features(ink)
        t0 = time.perf_counter()
        found = _beam_search(enc, dec, feats, int(meta.get("bos_id", tokenizer.BOS_ID)),
                             int(meta.get("eos_id", tokenizer.EOS_ID)), beam=max(1, min(int(beam), 8)))
        ms = (time.perf_counter() - t0) * 1000
        specials = {tokenizer.PAD_ID, tokenizer.BOS_ID, tokenizer.EOS_ID}
        out, seen = [], set()
        for score, ids in found:
            toks = [tok.itos[i] for i in ids if 0 <= i < len(tok.itos) and i not in specials]
            latex = tokenizer.detokenize(with_braces(functions_as_commands(toks)))
            if not latex or latex in seen:
                continue
            seen.add(latex)
            out.append({"latex": latex, "raw": tokenizer.detokenize(toks), "score": round(float(score), 3)})
            if len(out) >= limit:
                break
        return {"candidates": out, "ms": round(ms, 1), "strokes": len(ink.strokes),
                "points": int(sum(len(s) for s in ink.strokes))}

# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Francesco Bonazzi
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
checkout or any path, by default ``export/stroke_b_sib2_int8`` - the larger stroke
model, quantised, trained to write around printed pieces, one box per sibling
(50.2 % exact match on math-ocr's held-out test split; 73.1 % on short ink
written around a piece; 79.0 % on ink written among siblings, the right ones
named 93.7 % of the time - before quantisation, which costs about 2 points).

In the Android app (Chaquopy) there is no onnxruntime for Python: the model
runs in onnxruntime-android, the Maven library, through Chaquopy's Java
bridge (:class:`_JavaSession`), and math-ocr's two modules and the model come
with the app - its build stages them beside its Python (mobile/build.py).

The iOS app has no onnxruntime for Python either: ONNX Runtime's own iOS
library is linked into the app, which gives its Python a small built-in
module, ``_sympy_ort`` (mobile/ios/SymPyEditor/OrtModule.m), and the model
runs through that (:class:`_NativeSession`).  NumPy is BeeWare's build for
iOS; the rest is staged as on Android.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: ONNX Runtime's official builds send telemetry to Microsoft - over HTTPS
#: from Linux, macOS, Android and iOS (the 1DS client), through ETW on Windows -
#: unless told not to.  Read when it starts, so it is set here, before any
#: import of it: no uploader, no events, no device identifier.  One set to
#: something else by whoever runs this Python is left as it is.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")


def quiet_onnxruntime(ort) -> None:
    """Switch off the telemetry events of an onnxruntime already imported
    (by the user's own code, say, before the variable above could count)."""
    try:
        ort.disable_telemetry_events()
    except Exception:  # noqa: BLE001 - an older build without the call
        pass


DEFAULT_MODEL = "export/stroke_b_sib2_int8"
MAX_POINTS = 20000        # more ink than a formula needs: refused rather than slowed down on
MAX_TOKENS = 150          # the decoder's position table holds 168: never run past it
MAX_COORDINATE = 1e9      # farther than anything is drawn, and well short of where float32 squares overflow

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


#: The model as the apps carry it: a package of its own.
APP_MODEL_PACKAGE = "mathocr_model"
#: The model's attribution and licence terms, a file beside its weights.
MODEL_NOTICE = "NOTICE"


def read_notice(data: Optional[bytes]) -> Optional[str]:
    """A model's NOTICE as text for the add-on's guide, or None without one."""
    text = data.decode("utf-8", errors="replace").strip() if data else ""
    return text or None


def _on_android() -> bool:
    """The Android app's CPython (Chaquopy), with Java a module away."""
    return "ANDROID_ROOT" in os.environ and importlib.util.find_spec("java") is not None


def _in_a_page() -> bool:
    """Pyodide: this Python runs in a browser's page."""
    return sys.platform == "emscripten"


#: The module the iOS app builds into its interpreter: ONNX Runtime's C API,
#: as much of it as :class:`_NativeSession` needs.
IOS_MODULE = "_sympy_ort"


def _on_ios() -> bool:
    """The iOS app's CPython, with ONNX Runtime built into it."""
    return sys.platform == "ios" and IOS_MODULE in sys.builtin_module_names


def _in_app() -> bool:
    """In one of the apps: the model and math-ocr's modules come with it."""
    return _on_android() or _on_ios()


def _app_status() -> Dict[str, Any]:
    if importlib.util.find_spec(APP_MODEL_PACKAGE) is None or importlib.util.find_spec("mathocr") is None:
        return {"available": False, "reason": "This build of the app carries no handwriting model "
                                              "(one built beside a math-ocr checkout does)"}
    if importlib.util.find_spec("numpy") is None:
        return {"available": False, "reason": "numpy is not in this build of the app"}
    if _on_android():
        try:
            from java import jclass
            jclass("ai.onnxruntime.OrtEnvironment")
        except Exception:  # noqa: BLE001 - a missing class is a Java exception
            return {"available": False, "reason": "onnxruntime-android is not in this build of the app"}
    import pkgutil
    try:
        notice = read_notice(pkgutil.get_data(APP_MODEL_PACKAGE, MODEL_NOTICE))
    except OSError:
        notice = None
    return {"available": True, "model": "the app's own copy", "mathocr": "the app's own copy", "notice": notice}


def _primitive(jarr, dtype):
    """A Java primitive array as a numpy array - through the buffer protocol
    where Chaquopy offers it, element by element otherwise."""
    import numpy as np
    try:
        return np.frombuffer(memoryview(jarr), dtype=dtype).copy()
    except (TypeError, ValueError):
        return np.array(list(jarr), dtype=dtype)


class _JavaSession:
    """onnxruntime-android behind the one call of onnxruntime's Python API
    that the beam search makes - ``run(None, feeds)``, numpy arrays in and
    out - through Chaquopy's Java bridge.  An input passed again as the very
    same array (the encoder's memory, at every decoding step) is not copied
    into Java again."""

    def __init__(self, model: bytes, threads: int = 1) -> None:
        from java import jarray, jbyte, jclass
        self._env = jclass("ai.onnxruntime.OrtEnvironment").getEnvironment()
        try:
            self._env.setTelemetry(False)    # the app has no network either (its manifest)
        except Exception:  # noqa: BLE001
            pass
        opts = jclass("ai.onnxruntime.OrtSession$SessionOptions")()
        opts.setIntraOpNumThreads(max(1, int(threads)))
        self._session = self._env.createSession(jarray(jbyte)(model), opts)
        self._outputs = [str(name) for name in self._session.getOutputNames().toArray()]
        self._cache: Dict[str, tuple] = {}

    def _tensor(self, value):
        import numpy as np
        from java import jarray, jbyte, jclass, jfloat, jlong
        tensor = jclass("ai.onnxruntime.OnnxTensor")
        arr = np.asarray(value)
        shape = jarray(jlong)([int(n) for n in arr.shape])
        if arr.dtype == np.bool_:
            data = jclass("java.nio.ByteBuffer").wrap(jarray(jbyte)(arr.astype(np.uint8).tobytes()))
            return tensor.createTensor(self._env, data, shape, jclass("ai.onnxruntime.OnnxJavaType").BOOL)
        if arr.dtype == np.int64:
            data = jclass("java.nio.LongBuffer").wrap(jarray(jlong)(arr.ravel().tolist()))
            return tensor.createTensor(self._env, data, shape)
        data = jclass("java.nio.FloatBuffer").wrap(jarray(jfloat)(arr.astype(np.float32).ravel().tolist()))
        return tensor.createTensor(self._env, data, shape)

    @staticmethod
    def _array(value):
        import numpy as np
        from java import jarray, jbyte, jfloat, jlong
        info = value.getInfo()
        shape = [int(n) for n in info.getShape()]
        kind = str(info.type)
        if kind == "FLOAT":
            buf = value.getFloatBuffer()
            out = jarray(jfloat)(buf.remaining())
            buf.get(out)
            flat = _primitive(out, np.float32)
        elif kind == "BOOL":
            buf = value.getByteBuffer()
            out = jarray(jbyte)(buf.remaining())
            buf.get(out)
            flat = _primitive(out, np.int8) != 0
        elif kind == "INT64":
            buf = value.getLongBuffer()
            out = jarray(jlong)(buf.remaining())
            buf.get(out)
            flat = _primitive(out, np.int64)
        else:
            raise TypeError(f"The model answered with a {kind} tensor")
        return flat.reshape(shape)

    def run(self, names, feeds):
        from java import jclass
        inputs = jclass("java.util.HashMap")()
        for name, value in feeds.items():
            cached = self._cache.get(name)
            if cached is None or cached[0] is not value:
                if cached is not None:
                    cached[1].close()
                cached = self._cache[name] = (value, self._tensor(value))   # the array kept: its id cannot be reused
            inputs.put(name, cached[1])
        result = self._session.run(inputs)
        try:
            return [self._array(result.get(self._outputs.index(n))) for n in (names or self._outputs)]
        finally:
            result.close()


class _NativeSession:
    """The iOS app's ONNX Runtime behind the same one call, ``run(None,
    feeds)``: the app's built-in module takes and gives tensors as (type code,
    shape, bytes) - it knows nothing of numpy - and this turns them into
    arrays.  An input is read where it lies, not copied."""

    CODES = {"f": "float32", "q": "int64", "?": "bool"}

    def __init__(self, model: bytes, threads: int = 1) -> None:
        ort = importlib.import_module(IOS_MODULE)
        self._session = ort.Session(model, max(1, int(threads)))
        self._outputs = list(self._session.output_names)

    def run(self, names, feeds):
        import numpy as np
        given = {}
        for name, value in feeds.items():
            arr = np.asarray(value)
            code = "?" if arr.dtype == np.bool_ else "q" if arr.dtype == np.int64 else "f"
            arr = np.ascontiguousarray(arr, dtype=self.CODES[code])
            given[name] = (code, tuple(int(n) for n in arr.shape), arr)
        got = [np.frombuffer(data, dtype=self.CODES[code]).reshape(shape)
               for code, shape, data in self._session.run(given)]
        return [got[self._outputs.index(n)] for n in (names or self._outputs)]


#: Delimiters that open and close a pair, and those that do both.
OPENERS = ("(", "[", "\\{", "\\langle", "\\lfloor", "\\lceil")
CLOSERS = (")", "]", "\\}", "\\rangle", "\\rfloor", "\\rceil")
BARS = ("|", "\\|")


def sized_delimiters(tokens: Sequence[str]) -> List[str]:
    """``\\left`` and ``\\right`` on every pair of delimiters, for display: a
    parenthesis round a fraction as tall as the fraction.  The model never
    writes them - math-ocr's tokenizer drops them as purely visual.

    A closer takes the nearest opener at its level, of any kind (``[0, 1)``
    is an interval); bars pair with the bar before them, and one left over
    (``P(A|B)``) stays as it is, as does anything unmatched.  A pair never
    spans a brace, a cell of a matrix or an environment - LaTeX would refuse
    it - and the bracket of ``\\sqrt[3]`` is syntax, not a delimiter."""
    left, right = set(), set()
    levels: List[List[Tuple[int, str]]] = [[]]
    for i, t in enumerate(tokens):
        if t == "{" or t.startswith("\\begin{"):
            levels.append([])
            continue
        if t == "}" or t.startswith("\\end{"):
            if len(levels) > 1:
                levels.pop()
            continue
        stack = levels[-1]
        if t in ("&", "\\\\"):
            stack.clear()                                   # a new cell: nothing open crosses into it
        elif t == "[" and i and tokens[i - 1] == "\\sqrt":
            stack.append((i, "sqrt["))
        elif t in OPENERS:
            stack.append((i, t))
        elif t in CLOSERS:
            while stack and stack[-1][1] in BARS:
                stack.pop()                                 # a bar left open inside: not a pair
            if stack:
                j, opener = stack.pop()
                if opener != "sqrt[":
                    left.add(j)
                    right.add(i)
        elif t in BARS:
            if stack and stack[-1][1] == t:
                j, _ = stack.pop()
                left.add(j)
                right.add(i)
            else:
                stack.append((i, t))
    out: List[str] = []
    for i, t in enumerate(tokens):
        if i in left:
            out.append("\\left")
        elif i in right:
            out.append("\\right")
        out.append(t)
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


def _meta(data) -> Dict[str, Any]:
    """A model's ``meta.json`` as read: the object it holds, and nothing for
    a file that holds something else (a list, a number - valid JSON all the
    same), which used to raise from wherever its first key was asked for."""
    return data if isinstance(data, dict) else {}


def _leaf(path) -> str:
    """The last name of a path, whichever way its slashes lean."""
    return re.split(r"[\\/]", str(path).rstrip("\\/"))[-1]


def page_status(status) -> Dict[str, Any]:
    """What a page is told of a recognizer's status: whether it reads, why
    not, the model's name and its notice.  Never where anything is on this
    machine: a page is saved and passed on, and the folders of whoever made
    it went with every one of them."""
    status = status if isinstance(status, dict) else {}
    out: Dict[str, Any] = {"available": status.get("available")}
    reason = status.get("reason")
    if reason:
        reason = str(reason)
        for key in ("model", "mathocr"):          # a recognizer of someone's own may name them in its reason
            where = str(status.get(key) or "")
            if len(where) > 1 and where in reason:
                reason = reason.replace(where, _leaf(where))
        out["reason"] = reason
    if status.get("model"):
        out["model"] = _leaf(status["model"])
    if status.get("notice"):
        out["notice"] = str(status["notice"])
    return out


def _sequence(value) -> bool:
    return isinstance(value, (list, tuple)) or (hasattr(value, "tolist") and hasattr(value, "__len__"))


INK_SHAPE = "The ink comes as a list of strokes, each a list of points [x, y, t]"


def check_strokes(raw) -> list:
    """The strokes as they came, their shape looked at before anything is
    made of them: a list of strokes, each a list of points, each point two
    numbers or three.  Counted first - a million points are refused for
    being too many before one of them is read - and anything of another
    shape is refused in words, where it used to raise whatever the first
    line to trip on it raised (``KeyError: 0`` for a point given as an
    object)."""
    if raw is None:
        return []
    if not _sequence(raw):
        raise ValueError(INK_SHAPE)
    total = 0
    for stroke in raw:
        if stroke is None:
            continue
        if not _sequence(stroke):
            raise ValueError(INK_SHAPE)
        total += len(stroke)
        if total > MAX_POINTS:
            raise ValueError(f"Too much ink to read (more than {MAX_POINTS} points): clear some of it")
    out = []
    for stroke in raw:
        if stroke is None:
            continue
        for p in stroke:
            if not _sequence(p) or len(p) < 2:
                raise ValueError(INK_SHAPE)
        out.append(stroke)
    return out


def check_box(box) -> List[float]:
    """The box of a printed piece, ``[x0, y0, x1, y1]``, as four numbers."""
    try:
        if _sequence(box) and len(box) == 4:
            out = [float(v) for v in box]
            if all(math.isfinite(v) and abs(v) <= MAX_COORDINATE for v in out):
                return out
    except (TypeError, ValueError):
        pass
    raise ValueError("The box of the piece the ink is written by is four numbers: left, top, right, bottom")


def check_beam(beam, default: int = 4) -> int:
    """How many readings are followed at once: a whole number, 1 to 8."""
    if beam is None:
        return default
    try:
        if isinstance(beam, bool) or (isinstance(beam, float) and not math.isfinite(beam)):
            raise ValueError
        return max(1, min(int(beam), 8))
    except (TypeError, ValueError):
        raise ValueError("The beam is a whole number, 1 to 8") from None


def _strokes(raw) -> list:
    """The page's strokes - lists of ``[x, y, t]`` - as math-ocr's arrays,
    whatever does not read as finite numbers dropped.  A point farther than
    anything is drawn is refused: the features square the distances, in
    float32, and ``1e30`` squared is infinite (it used to come back as
    "OverflowError: cannot convert float infinity to integer")."""
    import numpy as np
    out = []
    for stroke in check_strokes(raw):
        pts = []
        for p in stroke:
            try:
                x, y = float(p[0]), float(p[1])
                t = float(p[2]) if len(p) > 2 else float(len(pts))
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(t)):
                continue
            if max(abs(x), abs(y)) > MAX_COORDINATE:
                raise ValueError("The ink lies farther than anything can be drawn: write it again")
            pts.append((x, y, t if abs(t) <= MAX_COORDINATE else float(len(pts))))
        if pts:
            out.append(np.asarray(pts, dtype=np.float32))
    return out


#: What a model without a context input reads where the piece stands: the
#: triangle :func:`stand_in` draws.
TRIANGLE_TOKEN = "\\Delta"


def stand_in(box, strokes) -> list:
    """The piece at ``box`` (``[x0, y0, x1, y1]``) drawn into the strokes for a
    model that has no context input: a triangle 0.8 of its height and no wider
    than 1.2 times that, centred in it and drawn before the ink.  The model
    reads it as ``\\Delta``."""
    x0, y0, x1, y1 = check_box(box)
    strokes = check_strokes(strokes)
    w, h = abs(x1 - x0), abs(y1 - y0)
    ht = h * 0.8
    tw = min(w, ht * 1.2)
    a, b = min(x0, x1) + (w - tw) / 2, min(y0, y1) + (h - ht) / 2
    pts: list = []
    t = 0.0
    for (ax, ay), (bx, by) in (((a, b + ht), (a + tw / 2, b)), ((a + tw / 2, b), (a + tw, b + ht)),
                               ((a + tw, b + ht), (a, b + ht))):
        n = max(2, round(math.hypot(bx - ax, by - ay) / 2))
        for i in range(1 if pts else 0, n + 1):
            pts.append([ax + (bx - ax) * i / n, ay + (by - ay) * i / n, t])
            t += 8
    shift = t + 250
    rest = [[[p[0], p[1], (p[2] if len(p) > 2 else 0) + shift] for p in s] for s in strokes or []]
    return [pts] + rest


def _beam_search(enc, dec, feats, bos: int, eos: int, beam: int = 4, max_tokens: int = MAX_TOKENS,
                 alpha: float = 0.7, once: Sequence[int] = (),
                 never: Sequence[int] = ()) -> List[Tuple[float, List[int]]]:
    """math-ocr's beam search (``mathocr/infer.py``) over the exported graphs.
    The decoder step has no cache, so each step feeds the whole prefix - cheap
    for formulas a dozen tokens long.  Finished readings are length-normalised
    as GNMT does, ``((5 + n) / 6) ** alpha``; best first.

    ``once``: tokens no reading carries twice, and every reading carries one
    of - the printed pieces the ink is written with (one box, or one per
    sibling), which the model's best reading nearly always names once but
    its alternatives, left to themselves, now and then twice or not at all.
    ``never``: tokens no reading carries."""
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
        if len(never):
            logp[:, list(never)] = -np.inf
        if len(once):
            some = np.zeros(len(tokens), dtype=bool)
            for t in once:
                has = (tokens == t).any(axis=1)
                logp[has, t] = -np.inf          # not a second time
                some |= has
            logp[~some, eos] = -np.inf          # nor the end before one
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
        if _in_app():
            return _app_status()
        if _in_a_page():
            # Pyodide: there is no onnxruntime for it and no model in the
            # page - "pip install onnxruntime" is no advice to give there.
            return {"available": False,
                    "reason": "A page that runs its own Python carries no handwriting model: the model reads "
                              "beside a Python of this machine - the local server, Jupyter - and in the Android and iOS apps"}
        if importlib.util.find_spec("onnxruntime") is None:
            return {"available": False, "reason": "onnxruntime is not installed in this Python (pip install onnxruntime)"}
        # The reasons name folders, never where they are: they are shown in
        # the page.  The paths looked at are beside them, for this Python.
        root = self.root
        if root is None or not _is_mathocr(root):
            where = f" in the folder {root.name}" if root is not None else ""
            return {"available": False, "mathocr": str(root) if root is not None else None,
                    "reason": f"math-ocr was not found{where}: set SYMPY_EDITOR_MATHOCR to its folder"}
        model = self.model_dir()
        if model is None or not (model / "encoder.onnx").is_file() or not (model / "decoder_step.onnx").is_file():
            name = model.name if model is not None else DEFAULT_MODEL
            return {"available": False, "mathocr": str(root), "model": str(model) if model is not None else None,
                    "reason": f"No exported model in {name} (encoder.onnx, decoder_step.onnx)"}
        mode = _meta(_read_json(model / "meta.json")).get("mode", "stroke")
        if mode != "stroke":
            return {"available": False, "mathocr": str(root), "model": str(model),
                    "reason": f"{model.name} is a {mode} model: pen strokes need a stroke model"}
        notice = read_notice((model / MODEL_NOTICE).read_bytes()) if (model / MODEL_NOTICE).is_file() else None
        return {"available": True, "model": str(model), "mathocr": str(root), "notice": notice}

    def load(self):
        """(encoder, decoder, tokenizer, math-ocr's inkml module, its
        tokenizer module, meta), loaded once."""
        with self._lock:
            if self._loaded is not None:
                return self._loaded
            st = self.status()
            if not st["available"]:
                raise RuntimeError(st["reason"])
            if _in_app():
                import pkgutil
                inkml = importlib.import_module("mathocr.data.inkml")
                tokenizer = importlib.import_module("mathocr.tokenizer")

                def data(name):
                    try:
                        got = pkgutil.get_data(APP_MODEL_PACKAGE, name)
                        if got is not None:
                            return got
                    except Exception:  # noqa: BLE001 - an importer without get_data
                        pass
                    import importlib.resources
                    return importlib.resources.files(APP_MODEL_PACKAGE).joinpath(name).read_bytes()

                threads = int(self._threads or os.environ.get("MATHOCR_THREADS", 1))
                session = _JavaSession if _on_android() else _NativeSession
                enc, dec = session(data("encoder.onnx"), threads), session(data("decoder_step.onnx"), threads)
                tok = tokenizer.Tokenizer(json.loads(data("vocab.json").decode("utf-8")))
                self._loaded = (enc, dec, tok, inkml, tokenizer, _meta(json.loads(data("meta.json").decode("utf-8"))))
                return self._loaded
            root, model = Path(st["mathocr"]), Path(st["model"])
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            inkml = importlib.import_module("mathocr.data.inkml")
            tokenizer = importlib.import_module("mathocr.tokenizer")
            import onnxruntime as ort
            quiet_onnxruntime(ort)
            opts = ort.SessionOptions()
            # One thread: math-ocr measured more of them to be many times
            # slower on a busy CPU (MATHOCR_THREADS overrides, as there).
            opts.intra_op_num_threads = max(1, int(self._threads or os.environ.get("MATHOCR_THREADS", 1)))
            opts.inter_op_num_threads = 1
            providers = ["CPUExecutionProvider"]
            try:
                enc = ort.InferenceSession(str(model / "encoder.onnx"), opts, providers=providers)
                dec = ort.InferenceSession(str(model / "decoder_step.onnx"), opts, providers=providers)
                tok = tokenizer.Tokenizer.load(model / "vocab.json")
            except Exception as exc:  # noqa: BLE001 - a file that is no model, a vocabulary that is not there
                # Said in the page, as every refusal is: onnxruntime's own
                # words name the file by its whole path, which stays here.
                said = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
                for place in (model, root):
                    said = said.replace(str(place), place.name)
                raise RuntimeError(f"The model in {model.name} could not be loaded: {said}") from None
            self._loaded = (enc, dec, tok, inkml, tokenizer, _meta(_read_json(model / "meta.json")))
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

    def recognize(self, strokes, beam: int = 4, limit: int = 5, context=None) -> Dict[str, Any]:
        """The readings of ``strokes`` (lists of ``[x, y, t]``), best first:
        ``{"candidates": [{"latex", "display", "raw", "score"}], "ms", "strokes", "points", "stand_in"}``.
        ``latex`` is what the editor reads - functions as commands, every
        argument braced; ``display`` the same with its delimiters sized, to
        be shown; ``raw`` the model's own text.

        ``context``: the box ``[x0, y0, x1, y1]`` of a printed piece the ink
        is written around.  A model trained with context boxes (its meta names
        a ``context_token``) is given the box itself and writes that token
        where the piece stands; any other is given :func:`stand_in`'s
        triangle and writes ``\\Delta``.  ``stand_in`` in the answer says which.

        ``context`` may also be a list of boxes, left to right: printed
        siblings (the factors of a product, the terms of a sum), for a model
        with a token for each (:meth:`box_tokens`).  Every reading names the
        boxes the ink goes with, each once: ``boxes`` in the answer lists the
        tokens, box by box."""
        # What came is looked at before anything is loaded or drawn: the ink
        # counted, the boxes four numbers each.
        strokes, beam = check_strokes(strokes), check_beam(beam)
        if context is not None:
            many = _sequence(context) and len(context) and _sequence(context[0])
            context = [check_box(b) for b in context] if many else check_box(context)
        enc, dec, tok, inkml, tokenizer, meta = self.load()
        token = meta.get("context_token")
        boxed = int(meta.get("in_dim", 6)) == 7
        boxes = None
        if context is not None:
            boxes = [tuple(b) for b in (context if many else [context])]
            if len(boxes) > len(self.box_tokens()):
                raise ValueError(f"This model reads ink with at most {len(self.box_tokens())} printed pieces")
        if boxes is not None and not token:
            strokes = stand_in(boxes[0], strokes)
        ink = inkml.Ink(strokes=_strokes(strokes), label="")
        if boxes is not None and token:
            ink.context = boxes
        if not ink.strokes:
            raise ValueError("Nothing is written yet")
        # a context-aware model takes a seventh channel, with or without a box
        feats = inkml.ink_to_features(ink, with_context=True) if boxed else inkml.ink_to_features(ink)
        t0 = time.perf_counter()
        # the boxes' tokens: each at most once and one at least with boxes, none for boxes not there
        ids = [tok.stoi[t] for t in self.box_tokens() if t in tok.stoi] if token else []
        given = len(boxes) if boxes is not None else 0
        found = _beam_search(enc, dec, feats, int(meta.get("bos_id", tokenizer.BOS_ID)),
                             int(meta.get("eos_id", tokenizer.EOS_ID)), beam=beam,
                             once=ids[:given], never=ids[given:])
        ms = (time.perf_counter() - t0) * 1000
        specials = {tokenizer.PAD_ID, tokenizer.BOS_ID, tokenizer.EOS_ID}
        out, seen = [], set()
        for score, ids in found:
            toks = [tok.itos[i] for i in ids if 0 <= i < len(tok.itos) and i not in specials]
            fixed = with_braces(functions_as_commands(toks))
            latex = tokenizer.detokenize(fixed)
            if not latex or latex in seen:
                continue
            seen.add(latex)
            out.append({"latex": latex, "display": tokenizer.detokenize(sized_delimiters(fixed)),
                        "raw": tokenizer.detokenize(toks), "score": round(float(score), 3)})
            if len(out) >= limit:
                break
        return {"candidates": out, "ms": round(ms, 1), "strokes": len(ink.strokes),
                "points": int(sum(len(s) for s in ink.strokes)),
                "stand_in": token if (context is not None and token) else TRIANGLE_TOKEN,
                "boxes": self.box_tokens()[:len(boxes)] if (boxes is not None and token) else []}

    def box_tokens(self) -> List[str]:
        """The tokens the model writes for printed boxes, box by box: none for
        a model without boxes, ``\\ctx`` alone for one box, more for siblings."""
        meta = self.load()[5]
        if not meta.get("context_token"):
            return [TRIANGLE_TOKEN]
        return list(meta.get("context_tokens") or [meta["context_token"]])

# sympy-editor-handwriting — write a formula by hand

Writing by hand on the editor's own formula: no pad of its own, no second
formula.  **Write**, among the editor's tools, takes the pointer and gives the
formula area room; with it off the editor is the editor it was.  A moment
after the pen lifts, the strokes go to the stroke model of
[math-ocr](../../../math-ocr) — a 5 M-parameter recognizer of handwritten
mathematics that reads the pen *trajectory*, not a picture of it — which
answers with LaTeX and a few other readings, best first.  The LaTeX add-on's
reader turns them into SymPy, in the document's own names, and the one picked
goes into the formula when **Apply to the formula** is pressed - nothing
changes before that.

Where it goes is what the editor says: over the selected sub-expression (or
the selected range) - hidden, its place kept, while the pen is on or ink
waits, back only when the writing is discarded -, at the cursor, or - with neither - against the piece of
the formula it is written by.  That piece is drawn into the strokes as a
stand-in (a triangle, which the model reads as `\Delta`), so the ink is read
*together with* it: a bar under it with ink under the bar is a fraction over
it, a small letter at its top-right corner its exponent, a letter beside it a
product.  Python puts the piece itself back in the stand-in's place: the
reading carries a placeholder symbol there (`\mathit{nestedpiece}`, with the
piece's path as `nest`), and the SymPy read from it has the placeholder
replaced by the node - never the piece's LaTeX read back, which would make
`f(x)` a product, `x_1` a symbol `x_{1}` and `e` Euler's number.  The
readings show the piece's LaTeX (`display`).

While the Pen is on, the formula opens a space where what is written will go,
and it widens as you write (a stroke that ends by the edge of the screen
opens more space ahead and scrolls the box back into sight); a tap still selects a piece or puts the cursor
between two, so where to write is chosen as it always was.

The strip under the editor holds only what came of it: the readings, to pick
from and to correct by hand (**✎ LaTeX**); the pieces the ink can be read
with, and `alone`; the ways the LaTeX itself can be read; **Apply to the
formula**; and, once applied, the formula before and after, marked as the
history marks a step, to **Keep** or to undo.

## What reads the strokes

The add-on has more than one engine, and the strip's menu picks between the
ones this page has:

| Engine | Where it reads | What it reads |
| --- | --- | --- |
| `math-ocr` | here, in Python | **mathematics**: fractions, exponents, roots, the layout as written. This is what the add-on is for |
| `host` | in the page, by the device | **text**, a line at a time: Apple's Vision in the iOS and macOS apps, or a browser that has the Handwriting Recognition API. It knows nothing of two-dimensional layout, so `x²` comes back as `x2` and a fraction as two lines |

The host engine is there for a device that carries no model — an App Store
build without one, a browser on a phone — and for a line of ordinary algebra,
which it reads well enough for the LaTeX reader to turn into SymPy. It is not
a replacement for the stroke model.

An engine is anything with `status()`, `warm(background)` and
`recognize(strokes, beam, limit)`; pass your own:

```python
from sympy_editor_handwriting import Engine, HandwritingAddon

addon = HandwritingAddon(engines=[Engine("mine", "My recognizer", MyRecognizer()),
                                  Engine("host", "This device", where="host")])
```

A `where="host"` engine reads in the page: the panel asks
`window.SympyEditorApp.recognizeInk(token, strokes)` (the apps) or the
browser's `navigator.createHandwritingRecognizer()`, and the host answers
`SympyEditor.inkRead(token, '{"candidates": [{"latex": "…"}]}')`. What comes
back is read as SymPy here, like any other reading.

## What it needs

* **A math-ocr checkout**, found as `SYMPY_EDITOR_MATHOCR`, or else as a
  folder `math-ocr` beside sympy-editor's.  The add-on imports two of its
  modules — `mathocr.data.inkml` (the strokes' features, exactly as in
  training) and `mathocr.tokenizer` — neither of which needs PyTorch, and runs
  an exported model with onnxruntime.
* **The model**: `SYMPY_EDITOR_MATHOCR_MODEL`, a folder with `encoder.onnx`,
  `decoder_step.onnx`, `vocab.json` and `meta.json`; by default the checkout's
  `export/stroke_b_sib2_int8` (the larger stroke model, int8: 5.9 MB, 50.2 % exact
  match on math-ocr's held-out test split, some 40 ms a formula on one CPU thread).
  It is trained to write around printed pieces: given a piece's box, it
  writes `\ctx` where the piece stands (73.1 % exact on short ink written
  around a piece); given the boxes of a product's factors or a sum's terms,
  one token each (`\ctx`, `\ctxb`, ...), it says which the ink goes with -
  `\frac{\ctxb}{\theta}` for a bar and a theta under the second (79.0 % exact,
  the right pieces 93.7 % of the time; int8 costs about 2 points of each).  An older model still works: one with
  a single token is given the one piece's box, and one without any, such as
  `export/stroke_b_int8`, a triangle in it, for which it writes `\Delta`.
* **Python packages**: `onnxruntime`, `numpy`, and the LaTeX add-on
  (`sympy-editor-latex`).

The weights are not in this package, and must not be: the model is
math-ocr's, not this package's to redistribute.

When any of this is missing the panel says what, and the rest of the editor is
unaffected.

## Trying it

```sh
pip install onnxruntime
pip install -e addons/sympy_editor_latex -e addons/sympy_editor_handwriting
python addons/sympy_editor_handwriting/serve.py                    # the local server, with the panel on
```

or, from Python, `serve(expr, addons=["handwriting", "latex"])`.

## Where it runs

* **With a Python beside the editor** — the local server, the Jupyter widget —
  on onnxruntime and a math-ocr checkout, as above.
* **In the Android app**, debug and release builds alike (`python mobile/build.py
  android`, `--release`): the model runs in [onnxruntime-android](https://onnxruntime.ai/docs/install/#install-on-android),
  the Maven library, which the add-on's Python calls through Chaquopy's Java
  bridge; the build stages the add-on, math-ocr's two modules and the model
  beside the app's Python (`stage_ink` in `mobile/build.py`), all of it
  git-ignored.  The model's `NOTICE` (the terms it is distributed under), when
  its export has one, goes into the app with it, and the add-on's guide shows
  it; a build without one warns.
* **Not** in a self-contained Pyodide page, the web site or the iOS app, so
  `addon.json` keeps it out of their bundles.

## Two things the model's output needs

Both done on its tokens, before the LaTeX reader sees them:

* The training labels spell functions as letters (`sin x`, `log n`, `lim_{x\to0}`),
  and the model has no command for any of them: a run of letters spelling a
  function becomes its command (`\sin x`), the longest name first.
* Training normalised `x^{2}` to `x^2` and `\frac{1}{2}` to `\frac 12`: every
  argument gets its braces back.

## Licence

This add-on - everything under `addons/sympy_editor_handwriting/` - is free software
under the **GNU Affero General Public License, version 3 or (at your option)
any later version** (`AGPL-3.0-or-later`, the text in [`LICENSE`](LICENSE)).
Copyright (c) 2026 Francesco Bonazzi.

The same licence as sympy-editor and its other add-ons.  Whoever distributes
the add-on, or a build that carries it (the Android app does), or lets people
use it over a network, must offer them its source under the same licence.

What it runs on keeps its own terms: SymPy (BSD-3-Clause), NumPy
(BSD-3-Clause), onnxruntime and onnxruntime-android (MIT).  math-ocr and its
model are not part of the add-on; the Android app carries the model with the
NOTICE that states its terms.

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

Written over a selected operator (the `=` of an equation, a `+`), the ink is
read as the operator that takes its place - `=`, `<`, `>`, `≤`, `≥`, `≠`,
`+`, `−`, `·`, `/`... - and nothing is read together with it.

Where it goes is what the editor says: over the selected sub-expression (or
the selected range) - hidden, its place kept, while the pen is on or ink
waits, back only when the writing is discarded (putting the pen away clears
the ink that was not applied) -, at the cursor, or - with neither - against the piece of
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

While the Pen is on, the formula opens a space where what is written will go -
at the cursor, on the side of an operator the cursor is drawn on (after the
`+` of `a + b` when the cursor stands after it) - and it widens as you write (a box that opens past the edge of the screen is
scrolled towards the middle, and a stroke that ends by the edge opens more
space ahead and scrolls the box back into sight - written freely, with nothing
selected and no cursor, the area itself is given that space: it scrolls on to
the right and grows downwards, the formula staying where it is); a tap still selects a piece or puts the cursor
between two, so where to write is chosen as it always was.

The strip under the editor holds only what came of it: the readings, to pick
from and to correct by hand (**✎ LaTeX**); the pieces the ink can be read
with, and `alone`; the ways the LaTeX itself can be read; **Apply to the
formula**; and, once applied, the formula before and after, marked as the
history marks a step, to **Keep** or to undo.

## What reads the strokes

The add-on has more than one engine, and the strip's menu picks between the
ones this page has; picking another reads the ink on the page again, at once:

| Engine | Where it reads | What it reads |
| --- | --- | --- |
| `math-ocr` | here, in Python | **mathematics**: fractions, exponents, roots, the layout as written. This is what the add-on is for |
| `host` | in the page, by the device | **text**, a line at a time: Apple's Vision in the iOS and macOS apps, or a browser that has the Handwriting Recognition API. It knows nothing of two-dimensional layout, so `x²` comes back as `x2` and a fraction as two lines |

The host engine is there for a device that carries no model — an App Store
build without one, a browser on a phone — and for a line of ordinary algebra,
which it reads well enough for the LaTeX reader to turn into SymPy. It is not
a replacement for the stroke model.

Which engine reads is the document's choice, not the add-on's - one add-on
object serves every document of a Python, and a choice kept on it was every
page's: it is kept in `doc.addon_state["handwriting"]`, travels with the
session (`export_state` / `restore_state`) and with every snapshot
(`snap["handwriting"]["engine"]`, which the panel follows).
`HandwritingAddon(engine=...)` is what a document that has not chosen asks.
Where the engine chosen cannot read - the device's own reader, on a device
that has none - the page asks the first that can; where none can, the Pen is
off and the strip under the formula says why (the Pen's own title too).

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

When any of this is missing the strip under the formula says what - it stays
in sight for as long as nothing can read - and the rest of the editor is
unaffected.

A page is told whether the model reads, why not, the model's name and its
notice: never where the model or the checkout are on the machine that built
the page, which a saved page would carry to whoever it is passed on to.
`StrokeRecognizer.status()` has the paths, for the Python that asks.

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
* **In the iOS app** (`python mobile/build.py ios`): there is no onnxruntime
  wheel for iOS, so ONNX Runtime's own iOS library (`onnxruntime.xcframework`,
  pinned and downloaded by the build) is linked into the app, and a module
  built into the app's interpreter - `_sympy_ort`,
  `mobile/ios/SymPyEditor/OrtModule.m` - gives the add-on's Python a session
  to run the model in.  NumPy is BeeWare's build for iOS.  The build stages
  all of it, with the add-on, math-ocr's two modules, the model and its
  `NOTICE`, in a folder of its own (`mobile/ios/ink`, `ios_ink` in
  `mobile/build.py`), git-ignored: NumPy is built per platform, and the Mac
  app shares the rest of the iOS app's Python.
* **Not** in a self-contained Pyodide page, the web site or the Mac app, so
  `addon.json` keeps it out of their bundles.  A page that runs its own Python
  and has the add-on all the same (`save_html(expr, ..., addons=["handwriting"])`)
  installs nothing for it - onnxruntime has no wheel for Pyodide, and
  `pyodide_packages()` asks for none - and works as any other page: the panel
  asks the page's own Python what reads there, which is nothing, says so, and
  asks the device's own reader where there is one.

## What the pointer does

* A stroke is one pointer's, from where it comes down to where it lifts:
  another pointer coming down meanwhile waits - it does not take the stroke's
  place.
* With a pen, a touch is the hand that holds it: ignored while the pen is
  down and for half a second after it lifts, and a pen coming down takes over
  from a touch that came down before it.  Two fingers, with no pen about,
  zoom and drag the formula.
* A contact the system cancels (`pointercancel`) leaves nothing: no stroke,
  no tap.
* Zoomed, the ink is magnified about the corner of the formula as it is
  drawn, so it stays by what it was written by.
* With the last stroke gone - erased, taken back or cleared - so are the
  readings, and Apply.

## What Python refuses

`handle` looks at a payload's shape before anything is loaded or read, and
refuses in words: strokes that are no list of lists of points, more than
20 000 points (counted before any is read), a box that is not four numbers,
readings that are no list or carry no text, `children` that are no whole
numbers.  A reading that holds the placeholder (`\mathit{nestedpiece}`) with
no piece to put there - a path that is gone, arguments the node has not - is
refused rather than read with the placeholder's name in it.

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
the add-on, or a build that carries it (the Android and iOS apps do), or lets people
use it over a network, must offer them its source under the same licence.

What it runs on keeps its own terms: SymPy (BSD-3-Clause), NumPy
(BSD-3-Clause), onnxruntime and onnxruntime-android (MIT).  math-ocr and its
model are not part of the add-on; the Android app carries the model with the
NOTICE that states its terms.

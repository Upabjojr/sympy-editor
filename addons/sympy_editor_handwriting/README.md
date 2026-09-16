# sympy-editor-handwriting — write a formula by hand

A panel under the formula with an area to write in, with a pen, a finger or
the mouse.  A moment after the pen lifts, the strokes go to the stroke model
of [math-ocr](../../../math-ocr) — a 5 M-parameter recognizer of handwritten
mathematics that reads the pen *trajectory*, not a picture of it — which
answers with LaTeX and a few other readings, best first.  The LaTeX add-on's
reader turns the chosen one into SymPy, in the document's own names; it can be
corrected in its box, and goes in over the selection (a node or a range) or as
the whole expression - and the page goes back up to the formula.

## What it needs

* **A math-ocr checkout**, found as `SYMPY_EDITOR_MATHOCR`, or else as a
  folder `math-ocr` beside sympy-editor's.  The add-on imports two of its
  modules — `mathocr.data.inkml` (the strokes' features, exactly as in
  training) and `mathocr.tokenizer` — neither of which needs PyTorch, and runs
  an exported model with onnxruntime.
* **The model**: `SYMPY_EDITOR_MATHOCR_MODEL`, a folder with `encoder.onnx`,
  `decoder_step.onnx`, `vocab.json` and `meta.json`; by default the checkout's
  `export/stroke_b_int8` (the larger stroke model, int8: 5.9 MB, 38.9 % exact
  match on math-ocr's held-out test split, some 40 ms a formula on one CPU thread).
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

# Third-party components

SymPy Editor itself — everything under `src/`, `addons/`, `mobile/`, `webapp/`
and `desktop/` — is AGPL-3.0-or-later (see [`LICENSE`](LICENSE)).  This file
lists what it uses, what its builds *carry*, and the terms of each: a copy of
the editor that carries a component must carry that component's terms with it.

Versions are the ones pinned in the build files named beside them; the licences
are those the projects state for those versions.

## Needed to run at all

| Component | Licence | Where |
| --- | --- | --- |
| [SymPy](https://www.sympy.org) (1.14) | BSD-3-Clause | the expressions themselves; `pyproject.toml`. Its licence is in [`LICENSE`](LICENSE) in full, as it asks, together with the MIT terms of the latex2sympy files SymPy carries |
| [mpmath](https://mpmath.org) | BSD-3-Clause | SymPy's own dependency, carried with it |

## In the page

| Component | Licence | Where |
| --- | --- | --- |
| [KaTeX](https://katex.org) (0.16.22, with its fonts) | MIT | the formula is rendered with it; `src/sympy_editor/html.py`. From jsDelivr on a page that has the network, vendored into the app and the web bundle (`mobile/build_www.py`) |
| [Plotly.js](https://plotly.com/javascript/) | MIT | the plot add-on's graphs; fetched from jsDelivr when that add-on is on (`addons/sympy_editor_plot`). No build vendors it |

## The add-ons' own dependencies

| Component | Licence | Add-on |
| --- | --- | --- |
| [lark](https://github.com/lark-parser/lark) (1.3) | MIT | LaTeX (`sympy-editor-latex`) |
| [sympy-matching](https://pypi.org/project/sympy-matching/) (0.0.4), with omnimatch and multiset | MIT | Rewrite rules (`sympy-editor-matching`) |
| [NumPy](https://numpy.org) (1.26) | BSD-3-Clause | Handwriting; the plot add-on's `fast` extra |
| [onnxruntime](https://onnxruntime.ai) | MIT | Handwriting, where Python runs the model itself |

An add-on may have a licence of its own; each says so in its own `README.md`
and `LICENSE`.  All of the add-ons shipped here are AGPL-3.0-or-later.

## The web bundle (`webapp/dist`, `python webapp/build.py`)

Everything above that the page uses, vendored beside it, and:

| Component | Licence | Note |
| --- | --- | --- |
| [Pyodide](https://pyodide.org) (0.28.3) | MPL-2.0 | the runtime: `pyodide.js`, `pyodide.asm.*`, `python_stdlib.zip` |
| CPython (inside Pyodide) | PSF-2.0 | |
| micropip | MPL-2.0 | Pyodide's own; a page with add-ons installs their requirements with it |
| SymPy and mpmath wheels | BSD-3-Clause | taken from PyPI and Pyodide's index |

The bundle's `vendor/NOTICE.txt` lists these and carries the editor's `LICENSE`
(and so SymPy's) in full.  It is written by `mobile/build_www.py`.

## The Android app (`python mobile/build.py android`)

KaTeX as above, vendored in the assets, and:

| Component | Licence | Note |
| --- | --- | --- |
| [Chaquopy](https://chaquo.com/chaquopy/) 16.1 | MIT | the Python runtime and its Gradle plugin; `mobile/android/build.gradle.kts` |
| LLVM libc++ (`chaquopy-libcxx`) | Apache-2.0 with LLVM Exception | carried by Chaquopy's packages |
| OpenBLAS (`chaquopy-openblas`) | BSD-3-Clause | under NumPy |
| GCC's libgfortran (`chaquopy-libgfortran`) | GPL-3.0 with GCC Runtime Library Exception | under OpenBLAS |
| SymPy, mpmath, lark, NumPy, sympy-matching, omnimatch, multiset | as above | the app's Python, installed by Chaquopy |
| [ONNX Runtime for Android](https://onnxruntime.ai) 1.29 | MIT | the handwriting model runs on it; `mobile/android/app/build.gradle.kts` |
| androidx.appcompat 1.7, androidx.webkit 1.11 | Apache-2.0 | the shell around the WebView |

**The handwriting model is not part of this project.**  It is math-ocr's, kept
out of this repository, and staged into the app at build time together with its
own `NOTICE` — the terms it is distributed under, which are not the editor's
and are not a free-software licence: it may not be used commercially.  The
build warns when an export carries no `NOTICE`, and the add-on's guide shows
the one it carried (`mobile/build.py`, `stage_ink`).  A build made without the
model needs none of this.

## Development only

pytest, Playwright, Pillow and the Android SDK/Gradle toolchain are used to
build and to test; no build carries them.

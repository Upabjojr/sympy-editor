#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 Francesco Bonazzi
"""The local server with the handwriting panel on, to try it in a browser.

    python addons/sympy_editor_ink/serve.py                          # opens the page
    python addons/sympy_editor_ink/serve.py --no-browser --port 8766
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ADDONS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ADDONS.parent / "src"))
for pkg in ("sympy_editor_ink", "sympy_editor_latex", "sympy_editor_plot", "sympy_editor_tree", "sympy_editor_matching"):
    sys.path.insert(0, str(ADDONS / pkg))              # run from a checkout without installing

from sympy import symbols  # noqa: E402

from sympy_editor import Document, serve  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = ap.parse_args(argv)
    from sympy_editor_ink import ADDON as ink
    from sympy_editor_latex import ADDON as latex
    status = ink.recognizer.status()
    print("handwriting model:", status["model"] if status["available"] else "NOT AVAILABLE - " + status["reason"], flush=True)
    x = symbols("x")
    # The panel and the LaTeX one on; the other drafts a switch away in the ≡ drawer.
    doc = Document(x**2 + 1, addons=[ink, latex], available=[ink, latex, "sympy_editor_plot", "sympy_editor_tree", "sympy_editor_matching"])
    serve(doc, host=args.host, port=args.port, open_browser=not args.no_browser, title="SymPy Editor - handwriting")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""A page with the three add-ons, to try them in a browser.

    python addons/demo.py            # -> addons/demo.html (Pyodide: self-contained, opens anywhere)
    python addons/demo.py --serve    # the local HTTP server: edits run in this Python

The Pyodide page carries the add-ons' packages with it; the matching add-on
needs sympy-matching, which the page installs with micropip on load.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
for pkg in ("sympy_editor_tree", "sympy_editor_plot", "sympy_editor_matching"):
    sys.path.insert(0, str(HERE / pkg))         # run from a checkout without installing

from sympy import cos, sin, symbols  # noqa: E402

from sympy_editor import Document, save_html, serve  # noqa: E402


def addons():
    from sympy_editor_matching import ADDON as matching
    from sympy_editor_plot import ADDON as plot
    from sympy_editor_tree import ADDON as tree
    return [tree, plot, matching]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--serve", action="store_true", help="serve with the local HTTP backend instead of writing a page")
    ap.add_argument("--out", type=Path, default=HERE / "demo.html")
    args = ap.parse_args(argv)
    x = symbols("x")
    tree, plot, matching = addons()
    # Two on to start with, the third a click away in the Add-ons menu.
    doc = Document(sin(x) ** 2 / x + cos(x) ** 2, addons=[tree, plot], available=[matching])
    if args.serve:
        serve(doc, title="SymPy editor - add-ons")
        return 0
    save_html(doc, args.out, title="SymPy editor - add-ons")
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

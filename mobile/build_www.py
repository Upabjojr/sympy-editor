#!/usr/bin/env python3
"""Build the web bundle shared by the mobile apps (and usable on the desktop).

    python mobile/build_www.py                 # -> mobile/www/ (self-contained, offline)
    python mobile/build_www.py --cdn           # index.html only, assets from the CDNs
    python mobile/build_www.py --android       # also copy the bundle into the Android assets

The bundle is the same editor page ``sympy_editor.to_html`` produces for the
desktop, with KaTeX and the part of Pyodide that SymPy needs vendored under
``www/vendor/`` so that the app works without a network.  Downloads are cached
in ``~/.cache/sympy-editor/`` (override with ``--cache``).

Nothing here is specific to a platform: Android and iOS each wrap ``www/`` in
a WebView (see ``mobile/android`` and ``mobile/ios``).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))  # run from a checkout without installing

from sympy import Function, Integral, Sum, exp, oo, pi, sin, sqrt, symbols  # noqa: E402

from sympy_editor import to_html  # noqa: E402
from sympy_editor.html import KATEX_VERSION, PYODIDE_VERSION, default_urls  # noqa: E402

PYODIDE_CORE = ("pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")
PYODIDE_PACKAGES = ("sympy",)   # their dependency closure is read from pyodide-lock.json

NOTICE = """Third-party components vendored in this bundle
================================================
KaTeX {katex}      MIT           https://katex.org
Pyodide {pyodide}  MPL-2.0       https://pyodide.org  (core runtime, python_stdlib.zip)
CPython (in Pyodide)  PSF-2.0    https://www.python.org
SymPy, mpmath (wheels) BSD-3     https://www.sympy.org  https://mpmath.org
sympy-editor          BSD-3
"""


def demo_expression():
    x, y, n = symbols("x y n")
    f = Function("f")
    return Integral(exp(-(x**2) / 2) / sqrt(2 * pi), (x, -oo, y)) + Sum(f(n) / n**2, (n, 1, oo)) - sin(x) / (x + 1)


def fetch(url: str, dest: Path, cache: Path) -> Path:
    """Download ``url`` into the cache once, then copy it to ``dest``."""
    cached = cache / url.split("://", 1)[1]
    if not cached.exists():
        cached.parent.mkdir(parents=True, exist_ok=True)
        print("  downloading", url)
        with urllib.request.urlopen(url, timeout=120) as resp, open(cached, "wb") as out:
            shutil.copyfileobj(resp, out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cached, dest)
    return dest


def vendor(out: Path, cache: Path) -> dict:
    """Vendor KaTeX and the Pyodide subset; return the relative URLs to use."""
    katex_base = f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/"
    kdir = out / "vendor" / "katex"
    fetch(katex_base + "katex.min.js", kdir / "katex.min.js", cache)
    css = fetch(katex_base + "katex.min.css", kdir / "katex.min.css", cache).read_text(encoding="utf-8")
    for font in sorted(set(re.findall(r"url\(fonts/([^)]+?\.woff2)\)", css))):
        fetch(katex_base + "fonts/" + font, kdir / "fonts" / font, cache)

    pyodide_base = default_urls()["pyodideIndex"]
    pdir = out / "vendor" / "pyodide"
    for name in PYODIDE_CORE:
        fetch(pyodide_base + name, pdir / name, cache)
    lock = json.loads((pdir / "pyodide-lock.json").read_text(encoding="utf-8"))
    todo, files = list(PYODIDE_PACKAGES), {}
    while todo:
        name = todo.pop()
        if name in files:
            continue
        info = lock["packages"][name]
        files[name] = info["file_name"]
        todo.extend(info["depends"])
    for file_name in files.values():
        fetch(pyodide_base + file_name, pdir / file_name, cache)
    (out / "vendor" / "NOTICE.txt").write_text(NOTICE.format(katex=KATEX_VERSION, pyodide=PYODIDE_VERSION), encoding="utf-8")
    return {
        "katexJs": "vendor/katex/katex.min.js",
        "katexCss": "vendor/katex/katex.min.css",
        "pyodideJs": "vendor/pyodide/pyodide.js",
        "pyodideIndex": "vendor/pyodide/",
    }


def build(out: Path, *, cdn: bool = False, cache: Path | None = None, expr=None, title: str = "SymPy editor") -> Path:
    out.mkdir(parents=True, exist_ok=True)
    urls = None if cdn else vendor(out, cache or Path.home() / ".cache" / "sympy-editor")
    page = to_html(expr if expr is not None else demo_expression(), urls=urls, title=title,
                   options={"rememberZoom": True})   # an app keeps its zoom between launches
    (out / "index.html").write_text(page, encoding="utf-8")
    return out


def copy_android_assets(www: Path) -> Path:
    dest = HERE / "android" / "app" / "src" / "main" / "assets" / "www"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(www, dest)
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE / "www", help="output directory (default: mobile/www)")
    ap.add_argument("--cdn", action="store_true", help="do not vendor; load KaTeX and Pyodide from the CDNs")
    ap.add_argument("--cache", type=Path, default=None, help="download cache directory")
    ap.add_argument("--android", action="store_true", help="also copy the bundle to mobile/android/app/src/main/assets/www")
    args = ap.parse_args(argv)
    out = build(args.out, cdn=args.cdn, cache=args.cache)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"Wrote {out} ({size / 1e6:.1f} MB)")
    if args.android:
        print("Copied to", copy_android_assets(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build the web bundle shared by the mobile apps (and usable on the desktop).

    python mobile/build_www.py                 # -> mobile/www/ (self-contained, offline)
    python mobile/build_www.py --cdn           # index.html only, assets from the CDNs
    python mobile/build_www.py --android       # also copy the bundle into the Android assets

The bundle is the same editor page ``sympy_editor.to_html`` produces for the
desktop, with KaTeX vendored under ``www/vendor/`` so that it works without a
network.  Where the host application has no Python of its own (the web app)
the part of Pyodide that SymPy needs is vendored as well; ``--native`` (which
``--android`` implies, and the iOS build passes too) leaves it out, because
both apps ship CPython and SymPy themselves.  Downloads are cached in
``~/.cache/sympy-editor/`` (override with ``--cache``).

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

from sympy_editor import Document, to_html  # noqa: E402
from sympy_editor.addons import scan_addons  # noqa: E402
from sympy_editor.html import KATEX_VERSION, PYODIDE_VERSION, SYMPY_VERSION, SYMPY_WHEEL, default_urls  # noqa: E402

#: The add-on folders the bundle knows: what the apps stage beside their
#: Python (mobile/build.py), and what a Pyodide bundle carries in the page.
ADDONS_DIR = HERE.parent / "addons"

PYODIDE_CORE = ("pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")
PYODIDE_PACKAGES = ("mpmath",)  # from Pyodide's index (dependency closure read from pyodide-lock.json); SymPy itself is the PyPI wheel

NOTICE = """Third-party components vendored in this bundle
================================================
KaTeX {katex}      MIT           https://katex.org
Pyodide {pyodide}  MPL-2.0       https://pyodide.org  (core runtime, python_stdlib.zip)
CPython (in Pyodide)  PSF-2.0    https://www.python.org
SymPy {sympy} (wheel from PyPI)  BSD-3  https://www.sympy.org
mpmath (wheel)        BSD-3     https://mpmath.org
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


def vendor(out: Path, cache: Path, pyodide: bool = True) -> dict:
    """Vendor KaTeX and (unless the host runs Python itself) the Pyodide
    subset; return the relative URLs to use."""
    katex_base = f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/"
    kdir = out / "vendor" / "katex"
    fetch(katex_base + "katex.min.js", kdir / "katex.min.js", cache)
    css = fetch(katex_base + "katex.min.css", kdir / "katex.min.css", cache).read_text(encoding="utf-8")
    for font in sorted(set(re.findall(r"url\(fonts/([^)]+?\.woff2)\)", css))):
        fetch(katex_base + "fonts/" + font, kdir / "fonts" / font, cache)

    if not pyodide:
        shutil.rmtree(out / "vendor" / "pyodide", ignore_errors=True)     # a leftover from an earlier build
        (out / "vendor" / "NOTICE.txt").write_text(
            NOTICE.format(katex=KATEX_VERSION, pyodide=PYODIDE_VERSION, sympy=SYMPY_VERSION)
            .replace("Pyodide", "(not vendored here) Pyodide"), encoding="utf-8")
        return {"katexJs": "vendor/katex/katex.min.js", "katexCss": "vendor/katex/katex.min.css"}

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
    wheel = SYMPY_WHEEL.rsplit("/", 1)[1]
    fetch(SYMPY_WHEEL, pdir / wheel, cache)
    (out / "vendor" / "NOTICE.txt").write_text(NOTICE.format(katex=KATEX_VERSION, pyodide=PYODIDE_VERSION, sympy=SYMPY_VERSION), encoding="utf-8")
    return {
        "katexJs": "vendor/katex/katex.min.js",
        "katexCss": "vendor/katex/katex.min.css",
        "pyodideJs": "vendor/pyodide/pyodide.js",
        "pyodideIndex": "vendor/pyodide/",
        "sympyWheel": "vendor/pyodide/" + wheel,
    }


def app_logo() -> str:
    """The app's own icon as inline SVG, for the corner of the toolbar.

    Inline, because the bundle has to work with no network and the icon is a
    few kilobytes; the same drawing the launcher shows (``mobile/icon``,
    written by ``make_icons.py``).  Missing, it is simply left out.
    """
    svg = HERE / "icon" / "icon.svg"
    if not svg.is_file():
        return ""
    return svg.read_text(encoding="utf-8").split("?>", 1)[-1].strip()


def build(out: Path, *, cdn: bool = False, cache: Path | None = None, expr=None, title: str = "SymPy editor",
          head: str = "", native: bool = False, addons_dir: Path | None = None) -> Path:
    """Write the bundle to ``out``; ``head`` is extra ``<head>`` markup (the
    web app's manifest and service worker, see ``webapp/build.py``).

    With ``native``, the page edits through the host application's own Python
    (``window.SympyEditorPy``) instead of Pyodide: that is what both apps use,
    each shipping CPython and SymPy itself, so nothing of Pyodide is vendored
    into the bundle."""
    out.mkdir(parents=True, exist_ok=True)
    urls = None if cdn else vendor(out, cache or Path.home() / ".cache" / "sympy-editor", pyodide=not native)
    # The add-ons, off to start with and a click away in the Add-ons menu: the
    # document's catalogue names them by module; the app's Python imports them
    # from the folders it bundles, a Pyodide page from the packages it carries.
    available = [m["module"] for m in scan_addons(addons_dir if addons_dir is not None else ADDONS_DIR).values()]
    doc = Document(expr if expr is not None else demo_expression(), available=available)
    page = to_html(doc, urls=urls, title=title, head=head,
                   backend="native" if native else None,
                   element_id="sympy-editor-app",                         # reproducible: the web app's cache is keyed by content
                   # the app wears its own icon beside the title: in a WebView
                   # there is no title bar to say whose window this is
                   logo=app_logo(),
                   # an app keeps its zoom, its sessions and its add-on switches between launches
                   options={"rememberZoom": True, "sessions": True, "rememberAddons": True})
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
    ap.add_argument("--native", action="store_true",
                    help="the host application runs Python (the Android app): no Pyodide in the bundle")
    args = ap.parse_args(argv)
    out = build(args.out, cdn=args.cdn, cache=args.cache, native=args.native or args.android)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"Wrote {out} ({size / 1e6:.1f} MB)")
    if args.android:
        print("Copied to", copy_android_assets(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

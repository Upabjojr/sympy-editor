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
# From Pyodide's index (the dependency closure is read from pyodide-lock.json);
# SymPy itself is the PyPI wheel.  micropip is here because a page that
# carries add-ons installs their requirements with it, and loadPackage can
# only find it if it was vendored beside the rest.
PYODIDE_PACKAGES = ("mpmath", "micropip")

#: What every bundle carries in the page.  THIRD-PARTY.md, beside the LICENSE,
#: lists all of it - these are the lines a copy must carry with it.
NOTICE_PAGE = """Third-party components vendored in this bundle
================================================
KaTeX {katex} (with its fonts)   MIT   https://katex.org
Plotly.js (the plot add-on, fetched from jsDelivr, not vendored)  MIT  https://plotly.com/javascript/
sympy-editor and its add-ons     AGPL-3.0-or-later
"""

#: A page that carries its own Python: Pyodide and the wheels beside it.
NOTICE_PYODIDE = """Pyodide {pyodide}  MPL-2.0   https://pyodide.org  (core runtime, python_stdlib.zip)
CPython (in Pyodide)  PSF-2.0   https://www.python.org
micropip (Pyodide's, for an add-on's requirements)  MPL-2.0  https://pyodide.org
SymPy {sympy} (wheel from PyPI)  BSD-3   https://www.sympy.org
mpmath (wheel)        BSD-3     https://mpmath.org
"""

#: A page inside the app: the Python is Chaquopy's, and the rest is Java.
NOTICE_NATIVE = """The Python beside this bundle, and what the app is built on:
Chaquopy 16.1 (the Python runtime and its plugin)  MIT  https://chaquo.com/chaquopy/
  LLVM libc++ (chaquopy-libcxx)   Apache-2.0 with LLVM Exception
  OpenBLAS (chaquopy-openblas)    BSD-3     https://www.openblas.net
  GCC libgfortran (chaquopy-libgfortran)  GPL-3.0 with GCC Runtime Library Exception
SymPy {sympy}, mpmath, lark, NumPy, sympy-matching, omnimatch, multiset  BSD-3 / MIT
ONNX Runtime for Android 1.29 (the handwriting model runs on it)  MIT  https://onnxruntime.ai
androidx.appcompat 1.7, androidx.webkit 1.11   Apache-2.0
The handwriting model, when the build carries one, is not part of this project:
it comes with a NOTICE of its own, beside the app's Python, which states the
terms it is distributed under.
"""


def notice(pyodide: bool = True) -> str:
    """The bundle's NOTICE.txt: what it carries in the page, then what runs its
    Python - Pyodide's wheels, or the app's own - and then sympy-editor's
    LICENSE, which carries SymPy's licence in full (a binary copy must carry
    it).  THIRD-PARTY.md has the whole list, with what each is used for."""
    listed = NOTICE_PAGE.format(katex=KATEX_VERSION)
    listed += (NOTICE_PYODIDE if pyodide else NOTICE_NATIVE).format(pyodide=PYODIDE_VERSION, sympy=SYMPY_VERSION)
    return listed + "\n" + (HERE.parent / "LICENSE").read_text(encoding="utf-8")


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
        (out / "vendor" / "NOTICE.txt").write_text(notice(pyodide=False), encoding="utf-8")
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
    (out / "vendor" / "NOTICE.txt").write_text(notice(), encoding="utf-8")
    return {
        "katexJs": "vendor/katex/katex.min.js",
        "katexCss": "vendor/katex/katex.min.css",
        "pyodideJs": "vendor/pyodide/pyodide.js",
        "pyodideIndex": "vendor/pyodide/",
        "sympyWheel": "vendor/pyodide/" + wheel,
    }


def app_logo(debug: bool = False) -> str:
    """The app's own icon as inline SVG, for the corner of the toolbar.

    Inline, because the bundle has to work with no network and the icon is a
    few kilobytes; the same drawing the launcher shows (``mobile/icon``,
    written by ``make_icons.py``) - the badged one for a debug build, so the
    page wears the mark its launcher icon does.  Missing, it is left out.
    """
    svg = HERE / "icon" / ("icon-debug.svg" if debug else "icon.svg")
    if not svg.is_file():
        svg = HERE / "icon" / "icon.svg"
    if not svg.is_file():
        return ""
    return svg.read_text(encoding="utf-8").split("?>", 1)[-1].strip()


#: What a debug build calls itself, wherever it is named (see mobile/build.py).
DEBUG_SUFFIX = " (debug)"


def bundled_addons(addons_dir: Path | None = None) -> list:
    """The add-ons a bundle carries, by module name (never the template: that
    one is an example to copy, not something to ship)."""
    scanned = scan_addons(addons_dir if addons_dir is not None else ADDONS_DIR)
    return [m["module"] for m in scanned.values() if m.get("bundle") is not False]


def document_with_addons(expr, *, enable: bool = False, addons_dir: Path | None = None) -> Document:
    """A document that knows the bundled add-ons, and (with ``enable``) opens
    with them switched on rather than a click away.

    One at a time, and a failure is that add-on's alone: switching one on
    imports it here, and an add-on whose requirement this machine has not got
    (the LaTeX reader wants lark) would otherwise take the whole build down
    with it.  The page installs those requirements when it boots, so the
    add-on is still listed and can be switched on there.
    """
    available = bundled_addons(addons_dir)
    doc = Document(expr, available=available)
    if enable:
        for name in available:
            try:
                doc.enable(name)
            except Exception as exc:
                print(f"  {name} stays off: {type(exc).__name__}: {exc}")
    return doc


def build(out: Path, *, cdn: bool = False, cache: Path | None = None, expr=None, title: str = "SymPy Editor",
          head: str = "", native: bool = False, addons_dir: Path | None = None, debug: bool = False,
          enable_addons: bool = False) -> Path:
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
    # ``enable_addons`` starts them switched on rather than a click away: the
    # web site shows the editor with everything it has, where an app would
    # rather open quickly and let its owner choose (and remembers the choice).
    #
    # One at a time, and a failure is that add-on's alone: switching one on
    # imports it here, and an add-on with a requirement this machine has not
    # got (the LaTeX reader wants lark) would otherwise take the whole site
    # build down with it.  The page itself installs those requirements when it
    # boots, so the add-on is still listed and can be switched on there.
    doc = document_with_addons(expr if expr is not None else demo_expression(),
                               enable=enable_addons, addons_dir=addons_dir)
    return _write(out, doc, urls, title, head, native, debug)


def _write(out, doc, urls, title, head, native, debug):
    # A debug build is a second application on the phone: it says so over the
    # formula and wears the badged icon, as its launcher entry does.
    if debug and not title.endswith(DEBUG_SUFFIX):
        title += DEBUG_SUFFIX
    page = to_html(doc, urls=urls, title=title, head=head,
                   backend="native" if native else None,
                   element_id="sympy-editor-app",                         # reproducible: the web app's cache is keyed by content
                   # the app wears its own icon beside the title: in a WebView
                   # there is no title bar to say whose window this is
                   logo=app_logo(debug),
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
    ap.add_argument("--enable-addons", action="store_true",
                    help="start with every bundled add-on switched on (the web site does)")
    ap.add_argument("--native", action="store_true",
                    help="the host application runs Python (the Android app): no Pyodide in the bundle")
    ap.add_argument("--title", default="SymPy Editor", help="the page's title, over the formula")
    ap.add_argument("--debug", action="store_true",
                    help="a debug build: the title says so and the icon beside it wears the bug badge")
    args = ap.parse_args(argv)
    out = build(args.out, cdn=args.cdn, cache=args.cache, native=args.native or args.android,
                title=args.title, debug=args.debug, enable_addons=args.enable_addons)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"Wrote {out} ({size / 1e6:.1f} MB)")
    if args.android:
        print("Copied to", copy_android_assets(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

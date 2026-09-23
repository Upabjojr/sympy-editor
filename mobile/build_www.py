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
import email
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))  # run from a checkout without installing

from sympy import Function, Integral, Sum, exp, oo, pi, sin, sqrt, symbols  # noqa: E402

from sympy_editor import Document, to_html  # noqa: E402
from sympy_editor.addons import scan_addons  # noqa: E402
from sympy_editor.html import (  # noqa: E402
    KATEX_VERSION, PYODIDE_VERSION, SYMPY_VERSION, SYMPY_WHEEL, addon_catalog, default_urls, pyodide_requirements)

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
sympy-editor and its add-ons     AGPL-3.0-or-later
"""

#: What an add-on loads from a CDN and the bundle carries instead, by the
#: start of its URL: the licence line its NOTICE entry gets.
ASSET_LICENCES = {
    "https://cdn.jsdelivr.net/npm/plotly.js": "Plotly.js (the plot add-on)  MIT  https://plotly.com/javascript/",
}

#: A page that carries its own Python: Pyodide and the wheels beside it.
NOTICE_PYODIDE = """Pyodide {pyodide}  MPL-2.0   https://pyodide.org  (core runtime, python_stdlib.zip)
CPython (in Pyodide)  PSF-2.0   https://www.python.org
micropip (Pyodide's, for an add-on's requirements)  MPL-2.0  https://pyodide.org
SymPy {sympy} (wheel from PyPI)  BSD-3   https://www.sympy.org
mpmath (wheel)        BSD-3     https://mpmath.org
"""

#: Packages a Pyodide page has without micropip: never vendored as wheels of
#: the add-ons' requirements (SymPy is SYMPY_WHEEL, the rest Pyodide's own).
PROVIDED = {"sympy", "mpmath", "micropip", "packaging"}

#: A page inside an app: the bundle is the same for Android, iOS and the
#: Mac, so it lists what each of them runs its Python on.
NOTICE_NATIVE = """The Python beside this bundle, and what each app is built on.
In every app:
SymPy {sympy}, mpmath, lark, sympy-matching, omnimatch, multiset  BSD-3 / MIT
CPython  PSF-2.0  https://www.python.org, built with OpenSSL (Apache-2.0),
  libffi (MIT), XZ/liblzma (0BSD), bzip2 (bzip2 licence), and SQLite (public
  domain) on Android, mpdecimal (BSD-2-Clause) on iOS and the Mac
Android:
Chaquopy 16.1 (the Python runtime and its plugin)  MIT  https://chaquo.com/chaquopy/
  LLVM libc++ (chaquopy-libcxx)   Apache-2.0 with LLVM Exception
  OpenBLAS (chaquopy-openblas)    BSD-3     https://www.openblas.net
  GCC libgfortran (chaquopy-libgfortran)  GPL-3.0 with GCC Runtime Library Exception
NumPy  BSD-3  https://numpy.org
ONNX Runtime for Android 1.29 (the handwriting model runs on it)  MIT  https://onnxruntime.ai
androidx.appcompat 1.7, androidx.webkit 1.11 and what they depend on   Apache-2.0
Kotlin standard library  Apache-2.0
The handwriting model, when the build carries one, is not part of this project:
it comes with a NOTICE of its own, beside the app's Python, which states the
terms it is distributed under.
iOS and the Mac:
Python-Apple-support 3.13 (BeeWare's build of CPython)  MIT  https://github.com/beeware/Python-Apple-support
"""


def notice(pyodide: bool = True, extra: str = "") -> str:
    """The bundle's NOTICE.txt: what it carries in the page, then what runs its
    Python - Pyodide's wheels, or the app's own - and then sympy-editor's
    LICENSE, which carries SymPy's licence in full (a binary copy must carry
    it).  THIRD-PARTY.md has the whole list, with what each is used for."""
    listed = NOTICE_PAGE.format(katex=KATEX_VERSION)
    listed += (NOTICE_PYODIDE if pyodide else NOTICE_NATIVE).format(pyodide=PYODIDE_VERSION, sympy=SYMPY_VERSION)
    listed += extra
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


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _wheel_meta(path: Path) -> dict:
    """Name, version and licence of a wheel, from its METADATA."""
    with zipfile.ZipFile(path) as z:
        meta = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        md = email.message_from_bytes(z.read(meta))
    classifiers = [c.rsplit("::", 1)[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    first_line = ((md.get("License") or "").strip().splitlines() or [""])[0]
    licence = md.get("License-Expression") or (classifiers[0] if classifiers else first_line)
    return {"name": md["Name"], "version": md["Version"], "license": licence or "see the wheel's METADATA"}


def vendor_wheels(requirements, python: str, pdir: Path, cache: Path) -> list:
    """The add-ons' requirements and everything they depend on, as wheels in
    ``pdir``: a Pyodide page then installs them from the bundle and asks
    PyPI for nothing - the apps' web views and the web app work offline.

    pip resolves the closure for Pyodide's Python (pure-Python wheels only:
    ``--platform any --abi none``; a requirement with compiled code has no
    such wheel and stops the build, which is better than a bundle that goes
    online).  The resolution is kept in the cache under a key of the
    requirements and the Python version, so a second build needs no network.
    Returns the wheels' metadata, their file names under ``file``."""
    requirements = sorted(requirements)
    if not requirements:
        return []
    key = hashlib.sha256(json.dumps([requirements, python]).encode()).hexdigest()[:16]
    where = cache / "wheels" / key
    listing = where / "wheels.json"
    if not listing.is_file():
        shutil.rmtree(where, ignore_errors=True)
        where.mkdir(parents=True)
        print("  resolving", ", ".join(requirements), "for Python", python)
        subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--only-binary=:all:",
                        "--platform", "any", "--abi", "none", "--implementation", "py",
                        "--python-version", python, "--dest", str(where), *requirements], check=True)
        listing.write_text(json.dumps(sorted(p.name for p in where.glob("*.whl"))), encoding="utf-8")
    out = []
    for name in json.loads(listing.read_text(encoding="utf-8")):
        meta = _wheel_meta(where / name)
        if _norm(meta["name"]) in PROVIDED:
            continue
        shutil.copyfile(where / name, pdir / name)
        out.append(dict(meta, file=name))
    return out


def vendor_assets(doc: Document, out: Path, cache: Path) -> tuple:
    """What the add-ons load from a CDN - a script or a stylesheet named in
    their options (Plotly, for the plot) - copied into ``vendor/addons/``.

    Returns ``({url: path in the bundle}, NOTICE lines)``; the page's option
    ``localAssets`` sends every load of such a URL to the copy, so an add-on
    works with no network in the apps as it does in a browser online.  An
    asset with no licence line in :data:`ASSET_LICENCES` stops the build:
    what the bundle carries is listed in its NOTICE, always."""
    found = {}
    for addon in addon_catalog(doc):
        for value in addon.client_options().values():
            if isinstance(value, str) and re.match(r"https://[^\s]+\.(js|css)$", value):
                found[value] = addon.name
    assets, lines = {}, []
    for url in sorted(found):
        licence = next((line for start, line in ASSET_LICENCES.items() if url.startswith(start)), None)
        if licence is None:
            raise SystemExit(f"{url} (the {found[url]} add-on) has no licence line in ASSET_LICENCES: "
                             "check its licence and add one")
        rel = "vendor/addons/" + url.split("://", 1)[1]
        fetch(url, out / rel, cache)
        assets[url] = rel
        lines.append(licence + "\n")
    return assets, "".join(lines)


def vendor(out: Path, cache: Path, pyodide: bool = True, requirements=(), extra_notice: str = "") -> dict:
    """Vendor KaTeX and (unless the host runs Python itself) the Pyodide
    subset, with the wheels of the add-ons' ``requirements``; return the
    relative URLs to use."""
    katex_base = f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/"
    kdir = out / "vendor" / "katex"
    fetch(katex_base + "katex.min.js", kdir / "katex.min.js", cache)
    css = fetch(katex_base + "katex.min.css", kdir / "katex.min.css", cache).read_text(encoding="utf-8")
    for font in sorted(set(re.findall(r"url\(fonts/([^)]+?\.woff2)\)", css))):
        fetch(katex_base + "fonts/" + font, kdir / "fonts" / font, cache)

    if not pyodide:
        shutil.rmtree(out / "vendor" / "pyodide", ignore_errors=True)     # a leftover from an earlier build
        (out / "vendor" / "NOTICE.txt").write_text(notice(pyodide=False, extra=extra_notice), encoding="utf-8")
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
    python = ".".join(str(lock["info"]["python"]).split(".")[:2])
    extra = vendor_wheels(requirements, python, pdir, cache)
    listed = "".join(f"{w['name']} {w['version']} (wheel, for an add-on)  {w['license']}\n" for w in extra)
    (out / "vendor" / "NOTICE.txt").write_text(notice(extra=extra_notice + listed), encoding="utf-8")
    urls = {
        "katexJs": "vendor/katex/katex.min.js",
        "katexCss": "vendor/katex/katex.min.css",
        "pyodideJs": "vendor/pyodide/pyodide.js",
        "pyodideIndex": "vendor/pyodide/",
        "sympyWheel": "vendor/pyodide/" + wheel,
    }
    if extra:
        urls["wheels"] = ["vendor/pyodide/" + w["file"] for w in extra]
    return urls


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
    # Everything the page needs is in the bundle, the add-ons' requirements
    # included: an app, and the web app once installed, work with no network.
    urls, assets = None, {}
    if not cdn:
        cache = cache or Path.home() / ".cache" / "sympy-editor"
        shutil.rmtree(out / "vendor" / "addons", ignore_errors=True)     # a leftover from an earlier build
        assets, assets_notice = vendor_assets(doc, out, cache)
        urls = vendor(out, cache, pyodide=not native, requirements=pyodide_requirements(doc),
                      extra_notice=assets_notice)
    return _write(out, doc, urls, title, head, native, debug, assets)


def _write(out, doc, urls, title, head, native, debug, assets=None):
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
                   options=dict({"rememberZoom": True, "sessions": True, "rememberAddons": True},
                                # the add-ons' CDN files, loaded from the bundle's copies
                                **({"localAssets": assets} if assets else {})))
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

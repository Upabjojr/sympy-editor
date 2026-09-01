#!/usr/bin/env python3
"""Build the web app: the same page as the Android app, as an installable,
offline-capable site (a PWA).

    python webapp/build.py                  # -> webapp/dist/ (self-contained, ~30 MB)
    python webapp/build.py --cdn            # small: KaTeX and Pyodide from the CDNs (needs a network at run time)
    python webapp/build.py --serve          # build, then serve it at http://127.0.0.1:8000/
    python webapp/build.py --shelf DIR      # only the showcase: the derivations page and an editor (~1.5 MB)

``dist/`` is a static site: copy it to any web server (GitHub Pages, S3, a
folder behind nginx...).  It must be served over https (or from localhost) for
the service worker and the install prompt; opening index.html as a file does
not work (WebAssembly and fetch need an origin).

What it adds to the bundle of ``mobile/build_www.py``:

- ``manifest.webmanifest`` and icons (``icon.svg``, ``icon-192.png``,
  ``icon-512.png``): installable on phones and desktops, standalone window.
- ``sw.js``: a service worker that precaches every file of the bundle on the
  first visit and serves it from the cache afterwards (offline); a new build
  gets a new cache name (a hash of the bundle) and replaces the old one.
- The page's ``<head>``: the manifest link, theme colours, the mobile web app
  meta tags and the service-worker registration.

Sessions, history and zoom are kept in the browser's storage, as in the app.

`dist/derivations/` comes with it: the page that introduces the project with
every derivation of `examples/derivations/` embedded, each with its own
player.  `--shelf DIR` builds that page on its own, with an editor beside it
and KaTeX with them - a folder to drop into any site.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import http.server
import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "mobile"))
sys.path.insert(0, str(ROOT / "src"))

import build_www  # noqa: E402

from sympy_editor.html import default_urls  # noqa: E402

NAME = "SymPy editor"
SHORT_NAME = "SymPy"
THEME = "#3b82f6"
BACKGROUND = "#ffffff"

HEAD = """<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icon-192.png">
<meta name="theme-color" content="{theme}">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="{short}">
<meta name="description" content="Click-to-edit editor for SymPy expressions; works offline once installed.">
<script>
if ("serviceWorker" in navigator) {{
  window.addEventListener("load", function () {{
    navigator.serviceWorker.register("sw.js").catch(function (e) {{ console.warn("service worker:", e); }});
  }});
}}
</script>
"""

SW = """// sympy-editor web app: precache the bundle, serve it from the cache (offline).
var CACHE = "sympy-editor-%(hash)s";
var FILES = %(files)s;
self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(CACHE).then(function (cache) { return cache.addAll(FILES); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener("fetch", function (event) {
  if (event.request.method !== "GET") return;
  var url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;    // CDN files (a --cdn build) go to the network
  event.respondWith(caches.match(event.request, { ignoreSearch: true }).then(function (hit) {
    return hit || fetch(event.request).then(function (response) {
      if (response.ok) { var copy = response.clone(); caches.open(CACHE).then(function (cache) { cache.put(event.request, copy); }); }
      return response;
    });
  }));
});
"""


#: The app's own icon: SymPy's mark with a pencil over it, drawn by
#: mobile/make_icons.py.  The web app wears the same face as the phone app.
LOGO = ROOT / "mobile/icon/icon.svg"


def write_icons(out: Path) -> None:
    """`icon.svg` and the two PNG sizes the manifest asks for, from the app's
    own logo.  The PNGs need rsvg-convert; without it they are drawn by
    `icon_png` below, which needs nothing at all - a build never fails for
    want of an icon."""
    if LOGO.is_file():
        (out / "icon.svg").write_text(LOGO.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        (out / "icon.svg").write_text(icon_svg(), encoding="utf-8")
    for size in (192, 512):
        target = out / f"icon-{size}.png"
        if LOGO.is_file() and shutil.which("rsvg-convert"):
            subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(LOGO), "-o", str(target)],
                           check=True)
        else:
            target.write_bytes(icon_png(size))


def icon_svg() -> str:
    """The fallback mark, drawn here: used only when the logo is missing."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        f'<rect width="512" height="512" rx="96" fill="{THEME}"/>'
        '<text x="256" y="352" font-family="Georgia, Times New Roman, serif" font-size="300" font-style="italic" '
        'text-anchor="middle" fill="#ffffff">&#x222B;<tspan font-size="170" font-style="normal">x</tspan></text>'
        "</svg>\n"
    )


def icon_png(size: int) -> bytes:
    """A PNG icon without any imaging library: a rounded blue square with a
    white sigma drawn from four thick strokes."""
    r, g, b = int(THEME[1:3], 16), int(THEME[3:5], 16), int(THEME[5:7], 16)
    radius = size * 0.19
    t = size * 0.075                                   # stroke half-width
    s = size
    # the sigma: top bar, diagonal to the middle, diagonal back down, bottom bar
    strokes = [((0.30, 0.25), (0.72, 0.25)), ((0.30, 0.25), (0.55, 0.50)), ((0.55, 0.50), (0.30, 0.75)), ((0.30, 0.75), (0.72, 0.75))]

    def near_stroke(x, y):
        for (x1, y1), (x2, y2) in strokes:
            x1, y1, x2, y2 = x1 * s, y1 * s, x2 * s, y2 * s
            dx, dy = x2 - x1, y2 - y1
            u = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
            if (x - x1 - u * dx) ** 2 + (y - y1 - u * dy) ** 2 <= t * t:
                return True
        return False

    def inside_round(x, y):
        cx = min(max(x, radius), s - radius)
        cy = min(max(y, radius), s - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius

    raw = bytearray()
    for y in range(size):
        raw.append(0)                                  # filter: none
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            if not inside_round(px, py):
                raw += b"\x00\x00\x00\x00"
            elif near_stroke(px, py):
                raw += b"\xff\xff\xff\xff"
            else:
                raw += bytes((r, g, b, 255))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


#: The shelf: one page introducing the project, then every derivation with a
#: player of its own.  Written by `derivations_page`.
SHELF = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Derivations \u2014 SymPy editor</title>
<meta name="description" content="Worked mathematical derivations, step by step: what SymPy editor's history viewer is for.">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icon-192.png">
<link rel="stylesheet" href="{katex_css}">
<style>
{editor_css}
:root {{ color-scheme: light dark; }}
body {{ margin: 0; padding: 0 1.2rem 5rem; color: #1f2328;
       background: #f6f8fa linear-gradient(180deg, #eef2f7 0%, #f9fafb 22rem, #f6f8fa 100%);
       font: 16px/1.65 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 58rem; margin: 0 auto; }}
header {{ padding: 3.5rem 0 2.5rem; }}
p.eyebrow {{ margin: 0 0 0.6rem; font-size: 0.8rem; font-weight: 600; letter-spacing: 0.09em;
            text-transform: uppercase; color: #3b82f6; }}
h1 {{ font-size: 2.2rem; margin: 0 0 0.5rem; letter-spacing: -0.015em;
      display: flex; align-items: center; gap: 0.8rem; }}
h1 img {{ border-radius: 0.9rem; box-shadow: 0 1px 2px rgba(27, 31, 36, 0.12), 0 10px 24px -12px rgba(27, 31, 36, 0.35); }}
header p {{ margin: 0 0 1rem; color: #57606a; max-width: 42rem; }}
header .actions {{ display: flex; flex-wrap: wrap; gap: 0.6rem; margin-top: 1.5rem; }}
header a.button {{ display: inline-block; padding: 0.55rem 1.15rem; border-radius: 0.55rem;
                  border: 1px solid #d0d7de; text-decoration: none; color: inherit; font-size: 0.95rem;
                  background: #ffffff; box-shadow: 0 1px 2px rgba(27, 31, 36, 0.06);
                  transition: transform 120ms ease, box-shadow 120ms ease, border-color 120ms ease; }}
header a.primary {{ background: linear-gradient(180deg, #4f8ef7, #2e6fe3); border-color: #2e6fe3; color: #ffffff;
                   box-shadow: 0 1px 2px rgba(46, 111, 227, 0.25), 0 6px 16px -8px rgba(46, 111, 227, 0.6); }}
header a.button:hover {{ border-color: #3b82f6; transform: translateY(-1px);
                        box-shadow: 0 2px 4px rgba(27, 31, 36, 0.08), 0 10px 20px -10px rgba(27, 31, 36, 0.25); }}
h2.shelf {{ font-size: 0.95rem; text-transform: uppercase; letter-spacing: 0.08em; color: #57606a;
           display: flex; align-items: center; gap: 0.8rem; margin: 2.6rem 0 0; }}
h2.shelf::after {{ content: ""; flex: 1; height: 1px;
                  background: linear-gradient(90deg, #d0d7de, rgba(208, 215, 222, 0)); }}
.card {{ margin: 1.8rem 0 0; padding: 1.3rem 1.4rem 1.1rem; background: #ffffff;
        border: 1px solid #d8dee4; border-radius: 1rem;
        box-shadow: 0 1px 2px rgba(27, 31, 36, 0.04), 0 12px 28px -22px rgba(27, 31, 36, 0.4); }}
.card h3 {{ font-size: 1.15rem; margin: 0 0 0.15rem; }}
.card p {{ margin: 0 0 0.9rem; color: #57606a; font-size: 0.95rem; }}
.card .steps {{ float: right; font-size: 0.8rem; font-variant-numeric: tabular-nums;
               color: #2e6fe3; background: rgba(59, 130, 246, 0.1);
               border-radius: 1rem; padding: 0.15rem 0.65rem; margin: 0.1rem 0 0 0.8rem; }}
.card .se-history-page {{ height: 30rem; min-height: 18rem; border: 1px solid #e4e8ec; border-radius: 0.6rem; }}
.snippets {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(21rem, 1fr));
             gap: 1.2rem; margin-top: 1.8rem; }}
.snippet {{ background: #ffffff; border: 1px solid #d8dee4; border-radius: 1rem; padding: 1.2rem 1.3rem;
           box-shadow: 0 1px 2px rgba(27, 31, 36, 0.04), 0 12px 28px -22px rgba(27, 31, 36, 0.4); }}
.snippet h3 {{ font-size: 1.02rem; margin: 0 0 0.2rem; }}
.snippet p {{ margin: 0 0 0.8rem; color: #57606a; font-size: 0.92rem; }}
.snippet pre {{ margin: 0; padding: 0.9rem 1rem; border-radius: 0.6rem; overflow-x: auto;
               background: #0e1116; color: #d6dde6; font-size: 0.83rem; line-height: 1.6; }}
.snippet pre + p {{ margin-top: 0.8rem; }}
.snippet .k {{ color: #7cadf8; }} .snippet .s {{ color: #8ddb8c; }}
.snippet .c {{ color: #768390; font-style: italic; }} .snippet .f {{ color: #e3b341; }}
figure.shot {{ margin: 1.8rem 0 0; padding: 0.8rem 0.8rem 0.6rem; background: #ffffff;
              border: 1px solid #d8dee4; border-radius: 1rem;
              box-shadow: 0 1px 2px rgba(27, 31, 36, 0.04), 0 12px 28px -22px rgba(27, 31, 36, 0.4); }}
figure.shot img {{ display: block; width: 100%; height: auto; border-radius: 0.6rem;
                  border: 1px solid #e4e8ec; }}
figure.shot figcaption {{ padding: 0.7rem 0.4rem 0.2rem; color: #57606a; font-size: 0.9rem; }}
figure.shot.phones {{ display: flex; flex-wrap: wrap; gap: 1.1rem; justify-content: center; }}
figure.shot.phones img {{ width: min(16rem, 46%); border-radius: 1.1rem; }}
figure.shot.phones figcaption {{ flex-basis: 100%; text-align: center; }}
footer {{ margin-top: 4rem; padding-top: 1.4rem; border-top: 1px solid #d0d7de;
         color: #57606a; font-size: 0.9rem; }}
footer code {{ font-size: 0.85em; }}
footer nav.legal {{ margin-top: 0.7rem; }}
footer nav.legal a {{ color: inherit; }}
header a.button code {{ font-size: 0.85em; }}
@media (prefers-color-scheme: dark) {{
  body {{ color: #e6e6e6; background: #1b1d20 linear-gradient(180deg, #202329 0%, #1c1e22 22rem, #1b1d20 100%); }}
  header p, .card p, footer {{ color: #a0a0a0; }}
  p.eyebrow {{ color: #7cadf8; }}
  h2.shelf {{ color: #a0a0a0; }}
  h2.shelf::after {{ background: linear-gradient(90deg, #3a3f45, rgba(58, 63, 69, 0)); }}
  header a.button {{ background: #24272c; border-color: #3a3f45; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.4); }}
  header a.primary {{ background: linear-gradient(180deg, #3f7cec, #2a62c9); border-color: #2a62c9; color: #ffffff; }}
  .card {{ background: #212429; border-color: #363b41;
          box-shadow: 0 1px 2px rgba(0, 0, 0, 0.35), 0 14px 30px -22px rgba(0, 0, 0, 0.8); }}
  .card .steps {{ color: #7cadf8; background: rgba(59, 130, 246, 0.16); }}
  .card .se-history-page {{ border-color: #33383e; }}
  .snippet, figure.shot {{ background: #212429; border-color: #363b41;
                          box-shadow: 0 1px 2px rgba(0, 0, 0, 0.35), 0 14px 30px -22px rgba(0, 0, 0, 0.8); }}
  .snippet p, figure.shot figcaption {{ color: #a0a0a0; }}
  figure.shot img {{ border-color: #33383e; }}
  footer {{ border-color: #444; }}
}}
</style>
</head>
<body>
<main>
<header>
  <p class="eyebrow">Free &amp; open source \u00b7 BSD 3-Clause</p>
  <h1><img src="icon.svg" alt="" width="56" height="56"> SymPy editor</h1>
  <p>A click-to-edit editor for SymPy expressions: select a piece of a formula
  and change it in place - type over it, apply any SymPy function to it, pull
  it apart - with the formula drawn as mathematics the whole time, never as
  code. It runs in the browser, in a Jupyter notebook, or as an app.</p>
  <p>Every edit is a step, and the steps make a <b>history</b>: what changed,
  what produced it, and what it became. The viewer below is that history,
  and it does not need the editor at all - a derivation computed in Python
  is shown the same way. Here are {count} of them.</p>
  <div class="actions">
    <a class="button primary" href="{editor_href}">Open the editor</a>
    <a class="button" href="https://github.com/Upabjojr/sympy-editor">Source on GitHub</a>
    <a class="button" href="https://pypi.org/project/sympy-editor/"><code>pip install sympy-editor</code></a>
  </div>
</header>
<h2 class="shelf">Use it</h2>
<div class="snippets">
<section class="snippet">
  <h3>In a Jupyter notebook</h3>
  <p>The widget runs every edit in the kernel's own SymPy: <code>w.expr</code>
  is the live, edited expression.</p>
  <pre><code>pip install <span class="s">"sympy-editor[jupyter]"</span></code></pre>
  <p></p>
  <pre><code><span class="k">from</span> sympy <span class="k">import</span> symbols, sin
<span class="k">from</span> sympy_editor <span class="k">import</span> edit

x = <span class="f">symbols</span>(<span class="s">"x"</span>)
w = <span class="f">edit</span>(<span class="f">sin</span>(x) / x)  <span class="c"># click it, edit in place</span>
w.expr                <span class="c"># what it is now, live</span>
w.<span class="f">on_change</span>(<span class="k">lambda</span> e: <span class="f">print</span>(<span class="s">"now:"</span>, e))</code></pre>
</section>
<section class="snippet">
  <h3>A page of its own</h3>
  <p>One self-contained file whose edits run in the browser \u2014 or a local
  server that hands the result back to Python.</p>
  <pre><code><span class="k">from</span> sympy_editor <span class="k">import</span> save_html, serve

<span class="f">save_html</span>(expr, <span class="s">"expr.html"</span>)  <span class="c"># one file, no server</span>
new = <span class="f">serve</span>(expr)             <span class="c"># blocks until Done</span></code></pre>
</section>
<section class="snippet">
  <h3>A history from plain Python</h3>
  <p>The viewer on this page needs no editor: a list of expressions and a
  word about each step is enough.</p>
  <pre><code><span class="k">from</span> sympy_editor <span class="k">import</span> History
<span class="k">from</span> sympy_editor <span class="k">import</span> save_history_html

steps = <span class="f">History</span>([
    <span class="f">Integral</span>(x * <span class="f">sin</span>(x), x),
    (-x * <span class="f">cos</span>(x)
     + <span class="f">Integral</span>(<span class="f">cos</span>(x), x), <span class="s">"by parts"</span>),
    (-x * <span class="f">cos</span>(x) + <span class="f">sin</span>(x), <span class="s">"the last integral"</span>),
])
<span class="f">save_history_html</span>(steps, <span class="s">"steps.html"</span>)</code></pre>
</section>
</div>
{figures}
<h2 class="shelf">Worked derivations</h2>
{cards}
<footer>Each one is a <code>sympy_editor.History</code>: a list of expressions
and a word about what turned each into the next (<code>examples/derivations/</code>
in the repository). Press <b>Play</b> on any of them, or <b>Save</b> to keep it
as a single file that works offline.
<nav class="legal">\u00a9 2026 <a href="https://github.com/Upabjojr">Francesco Bonazzi</a> \u00b7
<a href="https://github.com/Upabjojr/sympy-editor">GitHub</a> \u00b7
<a href="https://pypi.org/project/sympy-editor/">PyPI</a> \u00b7
<a href="license.html">License (BSD 3-Clause)</a> \u00b7
<a href="privacy.html">Privacy</a></nav></footer>
</main>
<script>
{editor_js}
</script>
<script>
{mounts}
</script>
</body>
</html>
"""

#: The frame of a document page (the licence, the privacy statement): the
#: shelf's own dress - the mark beside the title, cards with a drawn icon
#: each, the same colours in the light and in the dark.
DOC_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} \u2014 SymPy editor</title>
<meta name="description" content="{description}">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<style>
:root {{ color-scheme: light dark; }}
body {{ margin: 0; padding: 0 1.2rem 5rem; background: #ffffff; color: #1f2328;
       font: 16px/1.65 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 46rem; margin: 0 auto; }}
header {{ padding: 3rem 0 1.6rem; }}
h1 {{ font-size: 1.7rem; margin: 0 0 0.3rem; letter-spacing: -0.01em;
      display: flex; align-items: center; gap: 0.6rem; }}
h1 img {{ border-radius: 0.7rem; }}
header p.lead {{ margin: 0.4rem 0 0; color: #656d76; }}
a {{ color: #3b82f6; }}
nav.crumbs {{ font-size: 0.9rem; margin: 0 0 0.8rem; }}
nav.crumbs a {{ text-decoration: none; }}
.card {{ border: 1px solid #d0d7de; border-radius: 0.8rem; padding: 1.1rem 1.3rem;
        margin: 1.1rem 0; display: flex; gap: 1rem; align-items: flex-start; }}
.card svg {{ flex: 0 0 auto; width: 2.1rem; height: 2.1rem; margin-top: 0.15rem;
            stroke: #3b82f6; }}
.card h2 {{ font-size: 1.05rem; margin: 0 0 0.3rem; }}
.card p {{ margin: 0 0 0.5rem; }} .card p:last-child {{ margin-bottom: 0; }}
.card ul {{ margin: 0.3rem 0 0.5rem; padding-left: 1.1rem; }}
pre.licence {{ border: 1px solid #d0d7de; border-radius: 0.8rem; padding: 1.2rem 1.4rem;
              overflow-x: auto; font-size: 0.85rem; line-height: 1.55; }}
footer {{ margin-top: 3rem; padding-top: 1.2rem; border-top: 1px solid #d0d7de;
         color: #656d76; font-size: 0.9rem; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #1e1e1e; color: #e6e6e6; }}
  header p.lead, footer {{ color: #a0a0a0; }}
  .card, pre.licence, footer {{ border-color: #444; }}
}}
</style>
</head>
<body>
<main>
<header>
  <nav class="crumbs"><a href="index.html">\u2190 SymPy editor</a></nav>
  <h1><img src="icon.svg" alt="" width="44" height="44"> {title}</h1>
  <p class="lead">{lead}</p>
</header>
{body}
<footer>{footer}</footer>
</main>
</body>
</html>
"""

#: Small drawn icons for the cards, in the flat stroke style of the editor's
#: own (fill none, round caps, currentColor-free: the accent is set in CSS).
DOC_ICONS = {
    "device": '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
              '<rect x="7" y="2.5" width="10" height="19" rx="2.2"/><path d="M10.5 18.5h3"/></svg>',
    "store": '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<path d="M4.5 9.5 5.6 4h12.8l1.1 5.5"/><path d="M4.5 9.5h15V19a1.8 1.8 0 0 1-1.8 1.8H6.3A1.8 1.8 0 0 1 4.5 19Z"/>'
             '<path d="M9.5 13.5h5"/></svg>',
    "globe": '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.3 3.9 5.1 3.9 8.5s-1.3 6.2-3.9 8.5c-2.6-2.3-3.9-5.1-3.9-8.5S9.4 5.8 12 3.5Z"/></svg>',
    "mail": '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            '<rect x="3" y="5.5" width="18" height="13" rx="2"/><path d="m4 7 8 6 8-6"/></svg>',
    "scale": '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<path d="M12 4v16M7 20h10M12 4 5.5 7M12 4l6.5 3"/><path d="M3 12.5 5.5 7 8 12.5a2.7 2.7 0 0 1-5 0ZM16 12.5 18.5 7 21 12.5a2.7 2.7 0 0 1-5 0Z"/></svg>',
}

PRIVACY_CARDS = [
    ("device", "Everything stays on your device", """
<p>The editor computes where it runs. Expressions, sessions, their histories
and the zoom are kept in the app's own storage on your device (the browser's
local storage); deleting a session, or the app or the site data, removes
them. There are no accounts, no cookies, no analytics and no telemetry
&mdash; the source is public, and none of that is in it.</p>"""),
    ("store", "The apps and the stores", """
<p>The Android and iOS apps carry everything they need and make no network
requests: nothing you type ever leaves the phone. Installing them through
Google Play or the App Store means Google or Apple collect their own data
&mdash; downloads, crashes, device statistics &mdash; under
<a href="https://policies.google.com/privacy">Google's</a> and
<a href="https://www.apple.com/legal/privacy/">Apple's</a> privacy policies;
of that, the developer only ever sees aggregate statistics.</p>"""),
    ("globe", "This website", """
<p>These pages are served by GitHub Pages, which logs visits (your IP
address) as any web host does &mdash; see the
<a href="https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement">GitHub
Privacy Statement</a>. The derivations on the front page carry their own
rendering and load nothing else. The <em>editor</em> page fetches its
Python runtime (Pyodide, SymPy) from public CDNs &mdash; jsDelivr and
PyPI's file host &mdash; the way any download does, so those services see
that request; what you then type in the editor still stays in your
browser.</p>"""),
    ("mail", "If you write to us", """
<p>The project has no chat and collects no messages. If you open an issue or
a discussion on the
<a href="https://github.com/Upabjojr/sympy-editor">GitHub repository</a>,
that is public and processed by GitHub under its own terms, and we see what
you chose to post &mdash; nothing more.</p>"""),
]


def doc_pages(folder: Path) -> None:
    """Write the licence and the privacy statement beside the shelf:
    ``license.html`` and ``privacy.html`` in its dress, and ``LICENSE.txt``
    verbatim.  The pages a store listing and a curious visitor both ask
    for, kept where the site is built so a rebuild never loses them."""
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    (folder / "LICENSE.txt").write_text(licence, encoding="utf-8")
    card = lambda icon, title, body: f'<section class="card">{DOC_ICONS[icon]}<div><h2>{title}</h2>{body}</div></section>'
    (folder / "license.html").write_text(DOC_PAGE.replace("\\u2014", "\u2014").replace("\\u2190", "\u2190").format(
        title="License", description="SymPy editor is free software under the BSD 3-Clause License.",
        lead="SymPy editor is free software, under the BSD 3-Clause License.",
        body=card("scale", "In short",
                  """<p>Use it, copy it, change it, redistribute it &mdash; commercially or
not &mdash; as long as the copyright notice travels with it, and without
using the author's name to promote what you make from it. It comes with no
warranty. The short version is not the licence; the licence is:</p>""")
             + f'<pre class="licence">{html.escape(licence)}</pre>',
        footer='The same text as a plain file: <a href="LICENSE.txt">LICENSE.txt</a>. '
               'The rendering (KaTeX) and the in-browser Python (Pyodide, SymPy) have free licences of their own, '
               'listed in <a href="https://github.com/Upabjojr/sympy-editor">the repository</a>.'),
        encoding="utf-8")
    cards = "".join(card(icon, title, body) for icon, title, body in PRIVACY_CARDS)
    (folder / "privacy.html").write_text(DOC_PAGE.replace("\\u2014", "\u2014").replace("\\u2190", "\u2190").format(
        title="Privacy", description="SymPy editor collects no data: the mathematics stays on your device.",
        lead="The short version: the editor computes on your device, and nothing you type is sent anywhere by us.",
        body=cards,
        footer="This page describes SymPy editor 0.1.0 (September 2026). "
               "If the facts change, this page changes with them."),
        encoding="utf-8")


CARD = """<section class="card" id="{slug}">
  <span class="steps">{steps} steps</span>
  <h3>{title}</h3>
  <p>{why}</p>
  <div id="{element}"></div>
</section>"""


def manifest() -> dict:
    return {
        "name": NAME, "short_name": SHORT_NAME, "description": "Click-to-edit editor for SymPy expressions",
        "start_url": "./index.html", "scope": "./", "display": "standalone", "orientation": "any",
        "theme_color": THEME, "background_color": BACKGROUND,
        "icons": [
            {"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }


def derivations_page(folder: Path, *, urls: dict | None = None,
                     editor_href: str = "../index.html") -> Path | None:
    """The project introduced, then the whole shelf of worked derivations,
    each with its own player, as `folder/index.html`.

    Every viewer on the page shares one copy of the editor's code - a page of
    ten separate exports would carry ten copies of it - so the whole thing is
    about the size of a single one.
    """
    import importlib.util

    source = ROOT / "examples/derivations/derivations.py"
    if not source.is_file():
        print("no examples/derivations: skipping the shelf")
        return None
    spec = importlib.util.spec_from_file_location("derivations", source)
    shelf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shelf)

    from sympy_editor.html import build_history_config, read_static
    cards, mounts = [], []
    for i, (slug, make) in enumerate(shelf.DERIVATIONS):
        history = make()
        why = (make.__doc__ or "").strip().splitlines()[0]
        element = f"derivation-{slug}"
        cards.append(CARD.format(element=element, slug=slug, title=html.escape(history.title),
                                 why=html.escape(why), steps=len(history)))
        config = build_history_config(history, title=history.title, urls=urls)
        config["hideTitle"] = True          # the card above the viewer already names it
        config["heading"] = "Steps"
        mounts.append(f'SympyEditor.mountHistory(document.getElementById("{element}"), '
                      f"{json.dumps(config, ensure_ascii=False).replace('<', chr(92) + 'u003c')});")
    # Screenshots are content, not build products: they are taken from a
    # live JupyterLab and live beside the page.  The section appears only
    # where they do, so a bare rebuild is never a page of broken images.
    shots = [("jupyter-widget.png",
              "The widget in JupyterLab: the formula is the interface \u2014 click a piece to select it, "
              "type over it, or apply any SymPy function; the kernel computes, and <code>w.expr</code> follows."),
             ("jupyter-plot.png",
              "Two widgets wired together in a notebook: every edit committed in the editor redraws the plot "
              "(<code>examples/plot_alongside.ipynb</code> in the repository).")]
    sections = []
    figs = "\n".join(f'<figure class="shot"><img src="{name}" alt="" loading="lazy">'
                     f"<figcaption>{caption}</figcaption></figure>"
                     for name, caption in shots if (folder / name).is_file())
    if figs:
        sections.append('<h2 class="shelf">In the notebook</h2>\n' + figs)
    # ...and on a phone: the Android app, photographed running.  The images
    # are content beside the page, like the notebook's.
    phones = [("android-editor.png", "The editor on Android: the Gaussian integral selected"),
              ("android-history.png", "A derivation's history on Android, each step's change in red and green")]
    have = [(n, alt) for n, alt in phones if (folder / n).is_file()]
    if have:
        imgs = "".join(f'<img src="{n}" alt="{alt}" loading="lazy">' for n, alt in have)
        sections.append('<h2 class="shelf">On a phone</h2>\n<figure class="shot phones">' + imgs
                        + "<figcaption>The Android app: the same editor with CPython and SymPy packaged"
                        " inside, so every edit \u2014 and every step of a history \u2014 is computed on"
                        " the phone, offline.</figcaption></figure>")
    figures = "\n".join(sections)
    page = SHELF.format(
        katex_css=html.escape(urls["katexCss"] if urls else default_urls()["katexCss"], quote=True),
        editor_css=read_static("editor.css"), cards="\n".join(cards), figures=figures,
        editor_js=read_static("editor.js"), mounts="\n".join(mounts), count=len(cards),
        editor_href=html.escape(editor_href, quote=True))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(page, encoding="utf-8")
    doc_pages(folder)
    icon = ROOT / "mobile" / "icon" / "icon.svg"     # the pages' mark, where write_icons did not run (dist/derivations)
    if icon.is_file() and not (folder / "icon.svg").exists():
        shutil.copyfile(icon, folder / "icon.svg")
    return folder / "index.html"


def shelf_site(out: Path, *, cache: Path | None = None, cdn: bool = False) -> Path:
    """The showcase on its own: the page that introduces the project with
    every derivation embedded, an editor to try beside it, and KaTeX with
    them.  Small enough to drop into any site as a subfolder - which is what
    it is for.
    """
    out.mkdir(parents=True, exist_ok=True)
    urls = None if cdn else build_www.vendor(out, cache or Path.home() / ".cache" / "sympy-editor", pyodide=False)
    write_icons(out)
    from sympy_editor import to_html

    icon = '<link rel="icon" href="icon.svg" type="image/svg+xml">\n<link rel="apple-touch-icon" href="icon-192.png">\n'
    editor = to_html(build_www.demo_expression(), title=NAME, head=icon)   # Pyodide from the CDN, ~0.5 MB
    (out / "editor.html").write_text(editor, encoding="utf-8")
    derivations_page(out, urls=urls, editor_href="editor.html")
    return out


def build(out: Path, *, cdn: bool = False, cache: Path | None = None) -> Path:
    head = HEAD.format(theme=THEME, short=SHORT_NAME)
    build_www.build(out, cdn=cdn, cache=cache, title=NAME, head=head)
    vendored = (out / "vendor/katex/katex.min.js").is_file()
    derivations_page(                           # before sw.js: the precache lists what is there
        out / "derivations",
        urls=({"katexJs": "../vendor/katex/katex.min.js", "katexCss": "../vendor/katex/katex.min.css"}
              if vendored else None))
    (out / "manifest.webmanifest").write_text(json.dumps(manifest(), indent=2), encoding="utf-8")
    write_icons(out)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p.name != "sw.js")
    digest = hashlib.sha256()
    for name in files:
        digest.update(name.encode()); digest.update((out / name).read_bytes())
    (out / "sw.js").write_text(SW % {"hash": digest.hexdigest()[:12], "files": json.dumps(["./" + f for f in files])}, encoding="utf-8")
    return out


def serve(directory: Path, port: int) -> None:
    handler = type("H", (http.server.SimpleHTTPRequestHandler,), {})
    handler.extensions_map.update({".wasm": "application/wasm", ".whl": "application/zip", ".mjs": "text/javascript",
                                   ".webmanifest": "application/manifest+json"})
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), lambda *a, **k: handler(*a, directory=str(directory), **k)) as httpd:
        print(f"Serving {directory} at http://127.0.0.1:{httpd.server_address[1]}/  (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE / "dist", help="output directory (default: webapp/dist)")
    ap.add_argument("--cdn", action="store_true", help="do not vendor; load KaTeX and Pyodide from the CDNs")
    ap.add_argument("--cache", type=Path, default=None, help="download cache directory")
    ap.add_argument("--shelf", type=Path, default=None, metavar="DIR",
                    help="build only the showcase (the derivations page and an editor) into DIR")
    ap.add_argument("--serve", action="store_true", help="serve the result locally after building")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    out = shelf_site(args.shelf, cache=args.cache, cdn=args.cdn) if args.shelf \
        else build(args.out, cdn=args.cdn, cache=args.cache)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"Wrote {out} ({size / 1e6:.1f} MB)")
    if args.serve:
        serve(out, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())

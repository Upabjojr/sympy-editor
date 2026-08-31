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
body {{ margin: 0; padding: 0 1.2rem 5rem; background: #ffffff; color: #1f2328;
       font: 16px/1.65 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 58rem; margin: 0 auto; }}
header {{ padding: 3.5rem 0 2.5rem; }}
h1 {{ font-size: 2rem; margin: 0 0 0.4rem; letter-spacing: -0.01em;
      display: flex; align-items: center; gap: 0.7rem; }}
h1 img {{ border-radius: 0.8rem; }}
header p {{ margin: 0 0 1rem; color: #656d76; max-width: 42rem; }}
header .actions {{ display: flex; flex-wrap: wrap; gap: 0.6rem; margin-top: 1.4rem; }}
header a.button {{ display: inline-block; padding: 0.5rem 1.1rem; border-radius: 0.4rem;
                  border: 1px solid #d0d7de; text-decoration: none; color: inherit; font-size: 0.95rem; }}
header a.primary {{ background: #3b82f6; border-color: #3b82f6; color: #ffffff; }}
header a.button:hover {{ border-color: #3b82f6; }}
h2.shelf {{ font-size: 1.1rem; text-transform: uppercase; letter-spacing: 0.06em; color: #656d76;
           border-top: 1px solid #d0d7de; padding-top: 1.4rem; margin: 2rem 0 0; }}
.card {{ margin: 2.4rem 0 0; }}
.card h3 {{ font-size: 1.15rem; margin: 0 0 0.15rem; }}
.card p {{ margin: 0 0 0.8rem; color: #656d76; font-size: 0.95rem; }}
.card .steps {{ float: right; font-size: 0.85rem; color: #656d76; }}
.card .se-history-page {{ height: 30rem; min-height: 18rem; }}
footer {{ margin-top: 4rem; padding-top: 1.4rem; border-top: 1px solid #d0d7de;
         color: #656d76; font-size: 0.9rem; }}
footer code {{ font-size: 0.85em; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #1e1e1e; color: #e6e6e6; }}
  header p, .card p, .card .steps, footer, h2.shelf {{ color: #a0a0a0; }}
  header a.button {{ border-color: #444; }} header a.primary {{ border-color: #3b82f6; }}
  h2.shelf, footer {{ border-color: #444; }}
}}
</style>
</head>
<body>
<main>
<header>
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
  </div>
</header>
<h2 class="shelf">Worked derivations</h2>
{cards}
<footer>Each one is a <code>sympy_editor.History</code>: a list of expressions
and a word about what turned each into the next (<code>examples/derivations/</code>
in the repository). Press <b>Play</b> on any of them, or <b>Save</b> to keep it
as a single file that works offline.</footer>
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
    page = SHELF.format(
        katex_css=html.escape(urls["katexCss"] if urls else default_urls()["katexCss"], quote=True),
        editor_css=read_static("editor.css"), cards="\n".join(cards),
        editor_js=read_static("editor.js"), mounts="\n".join(mounts), count=len(cards),
        editor_href=html.escape(editor_href, quote=True))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(page, encoding="utf-8")
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

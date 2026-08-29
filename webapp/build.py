#!/usr/bin/env python3
"""Build the web app: the same page as the Android app, as an installable,
offline-capable site (a PWA).

    python webapp/build.py                  # -> webapp/dist/ (self-contained, ~30 MB)
    python webapp/build.py --cdn            # small: KaTeX and Pyodide from the CDNs (needs a network at run time)
    python webapp/build.py --serve          # build, then serve it at http://127.0.0.1:8000/

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
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "mobile"))
sys.path.insert(0, str(ROOT / "src"))

import build_www  # noqa: E402

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


def icon_svg() -> str:
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


def build(out: Path, *, cdn: bool = False, cache: Path | None = None) -> Path:
    head = HEAD.format(theme=THEME, short=SHORT_NAME)
    build_www.build(out, cdn=cdn, cache=cache, title=NAME, head=head)
    (out / "manifest.webmanifest").write_text(json.dumps(manifest(), indent=2), encoding="utf-8")
    (out / "icon.svg").write_text(icon_svg(), encoding="utf-8")
    for size in (192, 512):
        (out / f"icon-{size}.png").write_bytes(icon_png(size))
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
    ap.add_argument("--serve", action="store_true", help="serve the result locally after building")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    out = build(args.out, cdn=args.cdn, cache=args.cache)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"Wrote {out} ({size / 1e6:.1f} MB)")
    if args.serve:
        serve(out, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())

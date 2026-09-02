#!/usr/bin/env python3
"""Store screenshots of the app, taken from the app's own page.

    python mobile/screenshots.py            # -> mobile/ios/build/screenshots/{raw,framed}/*.png

The page is the bundle the apps show (``build_www.build`` with the native
backend) opened in Playwright's WebKit - the engine of the iOS WebView -
with ``window.SympyEditorPy`` bridged to ``mobile/app/sympy_editor_app.py``
in this process, so the pictures are of the app editing, not of a mock-up.
1242 x 2688 is the iPhone 6.5" size the App Store asks for; ``framed/``
puts each screen under a caption on a coloured ground, ``raw/`` is the
screen alone.  Needs ``playwright`` (with ``playwright install webkit``)
and Pillow.
"""

from __future__ import annotations

import functools
import http.server
import json
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "src"), str(HERE / "app"), str(HERE)]

import build_www  # noqa: E402
import sympy_editor_app as app  # noqa: E402
from sympy import Integral, Matrix, Sum, factorial, oo, sin, symbols  # noqa: E402

W, H, SCALE = 414, 896, 3          # points and scale of the 6.5" iPhone: 1242 x 2688 pixels
ED = "document.querySelector('.sympy-editor').__sympyEditor"
BRIDGE = """
window.SympyEditorPy = {
  newDoc: (req, id, srepr, settings) => window.__py('new_doc', [id, srepr, settings])
      .then(r => window.__sympyEditorNative(req, true, r), e => window.__sympyEditorNative(req, false, String(e))),
  handle: (req, id, msg) => window.__py('handle', [id, msg])
      .then(r => window.__sympyEditorNative(req, true, r), e => window.__sympyEditorNative(req, false, String(e))),
  version: (req) => window.__py('version', []).then(r => window.__sympyEditorNative(req, true, r)),
};
"""
x, n = symbols("x n")


def serve(folder: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Screen:
    """The app's page over one starting expression."""

    def __init__(self, pw, expr, work: Path, out: Path):
        folder = work / "www"
        build_www.build(folder, native=True, expr=expr)
        # the shots must show the app, which computes in its own Python: a
        # page that loaded Pyodide instead would be a picture of a spinner
        assert not (folder / "vendor" / "pyodide").exists(), "the bundle carries Pyodide: not the apps' page"
        self.srv, self.out = serve(folder), out
        self.browser = pw.webkit.launch()
        ctx = self.browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=SCALE, is_mobile=True, has_touch=True)
        ctx.expose_function("__py", lambda fn, args: getattr(app, fn)(*args))
        ctx.add_init_script(BRIDGE)
        self.page = ctx.new_page()
        self.page.on("pageerror", lambda e: print("page error:", e))
        self.page.goto(f"http://127.0.0.1:{self.srv.server_address[1]}/index.html")
        self.page.wait_for_selector(".sympy-editor .katex", timeout=60000)
        assert self.ev("!!window.SympyEditorPy && !window.pyodide && !window.loadPyodide"), "the page is not on the native backend"
        self.page.wait_for_timeout(600)
        self.page.evaluate("document.activeElement && document.activeElement.blur()")

    def ev(self, js):
        return self.page.evaluate(js)

    def send(self, msg):
        self.ev(f"{ED}.send({json.dumps(msg)})")
        self.page.wait_for_function(f"!{ED}.busy")
        self.page.wait_for_timeout(900)          # the change animation

    def path(self, src):
        return next(k for k, v in self.ev(f"{ED}.state.nodes").items() if v["src"] == src)

    def select(self, src=None):
        self.ev(f"{ED}.select({json.dumps(self.path(src) if src else None)})")
        self.page.wait_for_timeout(300)

    def symbols(self):
        self.ev("document.querySelector('.se-symbols').open = true")

    def fit(self, zoom):
        """The largest zoom at or under ``zoom`` that keeps the whole formula on screen."""
        while zoom > 0.5:
            self.ev(f"{ED}.setZoom({zoom})")
            self.page.wait_for_timeout(200)
            if not self.ev("(() => { const v = document.querySelector('.se-view'); return v.scrollWidth > v.clientWidth + 1; })()"):
                break
            zoom = round(zoom - 0.05, 2)
        self.ev("document.querySelector('.se-view').scrollLeft = 0")

    def history(self):
        self.ev(f"{ED}.showHistory()")
        self.page.wait_for_selector(".se-history-frame", timeout=30000)
        self.page.frame_locator(".se-history-frame").locator(".step").first.wait_for(timeout=30000)
        self.page.wait_for_timeout(1500)

    def shot(self, name):
        self.page.wait_for_timeout(400)
        self.page.screenshot(path=str(self.out / f"{name}.png"))
        print("  ", name)

    def close(self):
        self.browser.close()
        self.srv.shutdown()


#: file name, caption
SHOTS = [
    ("01-select", "Tap any part of a formula"),
    ("02-apart", "Transform just that part"),
    ("03-doit", "Let SymPy do the calculus"),
    ("04-history", "Every step, and what it changed"),
    ("05-series", "Series, limits, sums…"),
    ("06-sum", "Typeset as in a textbook"),
    ("07-inv", "Matrices too"),
]


def take(work: Path, out: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        # an integral by partial fractions, and its history
        s = Screen(pw, Integral(1 / (x**2 - 1), x), work, out)
        s.fit(1.6)
        s.select("1/(x**2 - 1)")
        s.shot("01-select")
        s.send({"action": "apply", "path": s.path("1/(x**2 - 1)"), "op": "apart"})
        s.select()
        s.fit(1.3)
        s.shot("02-apart")
        s.send({"action": "call", "path": "/", "func": "doit"})
        s.select()
        s.fit(1.6)
        s.symbols()
        s.shot("03-doit")
        s.history()
        s.shot("04-history")
        s.close()
        s = Screen(pw, sin(x) / x, work, out)
        s.send({"action": "call", "path": "/", "func": "series(x, 0, 8)"})
        s.select()
        s.fit(1.6)
        s.shot("05-series")
        s.close()
        s = Screen(pw, Sum(x**n / factorial(n), (n, 0, oo)), work, out)
        s.fit(1.8)
        s.shot("06-sum")
        s.close()
        s = Screen(pw, Matrix([[2, 1], [1, 2]]), work, out)
        s.send({"action": "call", "path": "/", "func": "inv"})
        s.select()
        s.fit(1.8)
        s.symbols()
        s.shot("07-inv")
        s.close()


def frame(raw: Path, caption: str, out: Path) -> None:
    """The screen under its caption, on a green ground, in the store's size."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    PW, PH = W * SCALE, H * SCALE
    for candidate in ("/System/Library/Fonts/HelveticaNeue.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(candidate).exists():
            font = ImageFont.truetype(candidate, 92, index=1 if candidate.endswith(".ttc") else 0)
            break
    else:
        font = ImageFont.load_default(92)
    bg = Image.new("RGB", (PW, PH))
    d = ImageDraw.Draw(bg)
    for y in range(PH):                                   # a little lighter at the head
        k = 1 - y / PH
        d.line([(0, y), (PW, y)], fill=(int(30 + 20 * k), int(84 + 40 * k), int(60 + 30 * k)))
    lines, cur = [], ""
    for word in caption.split():
        trial = (cur + " " + word).strip()
        if cur and d.textlength(trial, font=font) > PW - 160:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    lines.append(cur)
    y = 190
    for line in lines:
        d.text(((PW - d.textlength(line, font=font)) / 2, y), line, font=font, fill="white")
        y += 112
    shot = Image.open(raw).convert("RGB")
    sw = int(PW * 0.86)
    sh = int(shot.height * sw / shot.width)
    shot = shot.resize((sw, sh), Image.LANCZOS)
    top, r, left = y + 120, 60, (PW - sw) // 2
    mask = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, sw - 1, sh - 1], r, fill=255)
    shadow = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([left + 6, top + 30, left + sw + 6, top + sh + 30], r, fill=(0, 0, 0, 140))
    bg = Image.alpha_composite(bg.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(40))).convert("RGB")
    bg.paste(shot, (left, top), mask)
    bg.save(out)


def main() -> int:
    out = HERE / "ios" / "build" / "screenshots"
    raw, framed, work = out / "raw", out / "framed", out / "work"
    for folder in (raw, framed, work):
        folder.mkdir(parents=True, exist_ok=True)
    print("Taking the screens")
    take(work, raw)
    for i, (name, caption) in enumerate(SHOTS, 1):
        frame(raw / f"{name}.png", caption, framed / f"{i}-{name.split('-', 1)[1]}.png")
    print(f"Wrote {out}/raw and {out}/framed ({len(SHOTS)} each, {W * SCALE} x {H * SCALE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

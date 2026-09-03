#!/usr/bin/env python3
"""Store screenshots of the app, taken from the app's own page.

    python mobile/screenshots.py            # -> mobile/ios/build/screenshots/{iphone,ipad}/{raw,framed}/*.png
    python mobile/screenshots.py --set examples   # ... /{iphone,ipad}/examples/{raw,framed}: the app's own examples

The page is the bundle the apps show (``build_www.build`` with the native
backend) opened in Playwright's WebKit - the engine of the iOS WebView -
with ``window.SympyEditorPy`` bridged to ``mobile/app/sympy_editor_app.py``
in this process, so the pictures are of the app editing, not of a mock-up.
1242 x 2688 (the 6.5" iPhone) and 2064 x 2752 (the 13" iPad) are the
sizes the App Store asks for; ``framed/`` puts each screen under a caption
on a coloured ground, ``raw/`` is the screen alone.  Needs ``playwright`` (with ``playwright install webkit``)
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
from sympy_editor.examples import EXAMPLES  # noqa: E402
from sympy import Derivative, Eq, Function, I, Integral, Limit, Matrix, Sum, cos, exp, oo, pi, sin, sqrt, symbols  # noqa: E402

#: points, scale, and how much larger than on the phone a formula may be
DEVICES = {
    "iphone": (414, 896, 3, 1.0),      # the 6.5" iPhone: 1242 x 2688 pixels
    "ipad": (1032, 1376, 2, 1.7),      # the 13" iPad:    2064 x 2752
}
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
x, n, t, m, hbar = symbols("x n t m hbar", positive=True)


def serve(folder: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Screen:
    """The app's page over one starting expression."""

    def __init__(self, pw, expr, work: Path, out: Path, device: str):
        self.w, self.h, self.scale, self.room = DEVICES[device]
        folder = work / "www"
        build_www.build(folder, native=True, expr=expr)
        # the shots must show the app, which computes in its own Python: a
        # page that loaded Pyodide instead would be a picture of a spinner
        assert not (folder / "vendor" / "pyodide").exists(), "the bundle carries Pyodide: not the apps' page"
        self.srv, self.out = serve(folder), out
        self.browser = pw.webkit.launch()
        ctx = self.browser.new_context(viewport={"width": self.w, "height": self.h}, device_scale_factor=self.scale,
                                       is_mobile=True, has_touch=True)
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
        """The path of the node whose source is ``src`` (or begins with it, when ``src`` ends in ``...``)."""
        nodes = self.ev(f"{ED}.state.nodes").items()
        if src.endswith("..."):
            return next(k for k, v in nodes if v["src"].startswith(src[:-3]))
        return next(k for k, v in nodes if v["src"] == src)

    def select(self, src=None):
        """Select the node whose source is ``src`` (a path, if it starts with ``/``), or nothing."""
        path = src if src is None or src.startswith("/") else self.path(src)
        self.ev(f"{ED}.select({json.dumps(path)})")
        self.page.wait_for_timeout(300)

    def symbols(self):
        self.ev("document.querySelector('.se-symbols').open = true")

    def fit(self, zoom):
        """The largest zoom at or under ``zoom`` (more on the iPad) that keeps the whole formula on screen."""
        zoom = round(zoom * self.room, 2)
        while zoom > 0.5:
            self.ev(f"{ED}.setZoom({zoom})")
            self.page.wait_for_timeout(200)
            if not self.ev("(() => { const v = document.querySelector('.se-view'); return v.scrollWidth > v.clientWidth + 1; })()"):
                break
            zoom = round(zoom - 0.05, 2)
        self.ev("document.querySelector('.se-view').scrollLeft = 0")

    def picker(self):
        """The sessions drawer, with the chooser of a new session open on the examples."""
        self.ev(f"{ED}.openDrawer()")
        self.page.wait_for_timeout(400)
        self.page.click(".se-session-new")
        self.page.wait_for_selector(".se-session-picker")
        self.page.wait_for_timeout(400)

    def full(self):
        """The full-screen view: the formula alone, as large as the screen."""
        self.ev(f"{ED}.setFullscreen(true)")
        self.page.wait_for_timeout(600)

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
    ("02-basel", "Let SymPy do the sum"),
    ("03-euler", "Euler's formula, one tap away"),
    ("04-history", "Every step, and what it changed"),
    ("05-limit", "Limits, series, integrals…"),
    ("06-full", "Full screen, as in a textbook"),
    ("07-taylor", "Expand into a series"),
    ("08-matrix", "Matrices too"),
]


def take(work: Path, out: Path, device: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        # the Basel problem
        s = Screen(pw, Sum(1 / n**2, (n, 1, oo)), work, out, device)
        s.fit(1.8)
        s.select("/")
        s.shot("01-select")
        s.send({"action": "call", "path": "/", "func": "doit"})
        s.select()
        s.fit(2.0)
        s.shot("02-basel")
        s.close()
        # Euler's formula, then his identity: e^{ix} -> cos x + i sin x -> -1 at x = pi
        s = Screen(pw, exp(I * x), work, out, device)
        s.send({"action": "call", "path": "/", "func": "rewrite(cos)"})
        s.select("I*sin(x)")
        s.fit(1.7)
        s.shot("03-euler")
        s.send({"action": "call", "path": "/", "func": "subs(x, pi)"})
        s.select()
        s.history()
        s.shot("04-history")
        s.close()
        # the limit that defines e
        s = Screen(pw, Limit((1 + 1 / n) ** n, n, oo), work, out, device)
        s.fit(1.8)
        s.shot("05-limit")
        s.close()
        # full screen: the Schroedinger equation, where the screen is wide enough
        # for it, the Gaussian integral where it is not
        psi, V = Function("psi")(x, t), Function("V")(x)
        famous = Eq(I * hbar * Derivative(psi, t), -hbar**2 / (2 * m) * Derivative(psi, (x, 2)) + V * psi) if device == "ipad" \
            else Eq(Integral(exp(-x**2), (x, -oo, oo)), sqrt(pi))
        s = Screen(pw, famous, work, out, device)
        s.full()
        s.fit(2.0)
        s.shot("06-full")
        s.close()
        # the Taylor series of the exponential
        s = Screen(pw, exp(x), work, out, device)
        s.send({"action": "call", "path": "/", "func": "series(x, 0, 7)"})
        s.select()
        s.fit(1.5)
        s.shot("07-taylor")
        s.close()
        # a rotation, inverted: the rotation back
        s = Screen(pw, Matrix([[cos(t), -sin(t)], [sin(t), cos(t)]]), work, out, device)
        s.send({"action": "call", "path": "/", "func": "inv"})
        s.send({"action": "apply", "path": "/", "op": "simplify"})
        s.select()
        s.fit(1.7)
        s.symbols()
        s.shot("08-matrix")
        s.close()


#: the second set: what "New session..." offers, each with what one does with it
EXAMPLE_SHOTS = [
    ("01-examples", "Start from an example"),
    ("02-gaussian", "Tap the integral"),
    ("03-erf", "SymPy evaluates it in place"),
    ("04-quadratic", "The quadratic formula"),
    ("05-limit", "A limit, and its value"),
    ("06-trig", "Either side of an equation"),
    ("07-rational", "Over a common denominator"),
    ("08-rotation", "A rotation, applied"),
    ("09-inverse", "A matrix inverted"),
    ("10-symbols", "Matrix symbols, transposed"),
    ("11-determinant", "Determinant and trace, evaluated"),
    ("12-array", "Arrays of any rank"),
]


def take_examples(work: Path, out: Path, device: str) -> None:
    from playwright.sync_api import sync_playwright

    ex = dict(EXAMPLES)
    with sync_playwright() as pw:
        s = Screen(pw, ex["Gaussian integral and a series"], work, out, device)
        s.fit(1.2)
        s.picker()
        s.shot("01-examples")
        s.close()
        s = Screen(pw, ex["Gaussian integral and a series"], work, out, device)
        s.fit(1.2)
        s.select("Integral(...")
        s.shot("02-gaussian")
        s.send({"action": "call", "path": s.path("Integral(..."), "func": "doit"})
        s.select()
        s.fit(1.2)
        s.shot("03-erf")
        s.close()
        s = Screen(pw, ex["Quadratic formula"], work, out, device)
        s.fit(1.6)
        s.select("-4*a*c + b**2")
        s.shot("04-quadratic")
        s.close()
        s = Screen(pw, ex["A limit"], work, out, device)
        s.send({"action": "call", "path": "/", "func": "doit"})
        s.history()
        s.shot("05-limit")
        s.close()
        s = Screen(pw, ex["Trigonometric identity"], work, out, device)
        s.fit(1.6)
        s.select("/0")
        s.shot("06-trig")
        s.close()
        s = Screen(pw, ex["Rational arithmetic"], work, out, device)
        s.send({"action": "apply", "path": "/", "op": "together"})
        s.select()
        s.fit(1.7)
        s.shot("07-rational")
        s.close()
        s = Screen(pw, ex["Rotation matrix times a vector"], work, out, device)
        s.send({"action": "call", "path": "/", "func": "doit"})
        s.select()
        s.fit(1.5)
        s.symbols()
        s.shot("08-rotation")
        s.close()
        s = Screen(pw, ex["Dense matrix"], work, out, device)
        s.send({"action": "call", "path": "/", "func": "inv"})
        s.select()
        s.fit(1.5)
        s.shot("09-inverse")
        s.close()
        s = Screen(pw, ex["Matrix symbols"], work, out, device)
        s.send({"action": "call", "path": "/", "func": "T"})
        s.select()
        s.fit(1.7)
        s.symbols()
        s.shot("10-symbols")
        s.close()
        s = Screen(pw, ex["Determinant and trace"], work, out, device)
        s.send({"action": "call", "path": "/", "func": "doit"})
        s.select()
        s.fit(1.6)
        s.shot("11-determinant")
        s.close()
        s = Screen(pw, ex["3-D array"], work, out, device)
        s.fit(2.0)
        s.shot("12-array")
        s.close()


def frame(raw: Path, caption: str, out: Path, device: str) -> None:
    """The screen under its caption, on a green ground, in the store's size."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    w, h, scale, _ = DEVICES[device]
    PW, PH = w * scale, h * scale
    u = PW / 1242                                          # everything is laid out for the phone, then scaled
    size = round(92 * u)
    for candidate in ("/System/Library/Fonts/HelveticaNeue.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(candidate).exists():
            font = ImageFont.truetype(candidate, size, index=1 if candidate.endswith(".ttc") else 0)
            break
    else:
        font = ImageFont.load_default(size)
    bg = Image.new("RGB", (PW, PH))
    d = ImageDraw.Draw(bg)
    for y in range(PH):                                   # a little lighter at the head
        k = 1 - y / PH
        d.line([(0, y), (PW, y)], fill=(int(30 + 20 * k), int(84 + 40 * k), int(60 + 30 * k)))
    lines, cur = [], ""
    for word in caption.split():
        trial = (cur + " " + word).strip()
        if cur and d.textlength(trial, font=font) > PW - 160 * u:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    lines.append(cur)
    y = 190 * u
    for line in lines:
        d.text(((PW - d.textlength(line, font=font)) / 2, y), line, font=font, fill="white")
        y += 112 * u
    shot = Image.open(raw).convert("RGB")
    # the screen fills the width the phone's does, or what is left under the caption
    sw = min(int(PW * 0.86), int((PH - y - 120 * u) * shot.width / shot.height))
    sh = int(shot.height * sw / shot.width)
    shot = shot.resize((sw, sh), Image.LANCZOS)
    top, r, left = int(y + 120 * u), int(60 * u), (PW - sw) // 2
    mask = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, sw - 1, sh - 1], r, fill=255)
    shadow = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([left + 6 * u, top + 30 * u, left + sw + 6 * u, top + sh + 30 * u], r, fill=(0, 0, 0, 140))
    bg = Image.alpha_composite(bg.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(40 * u))).convert("RGB")
    bg.paste(shot, (left, top), mask)
    bg.save(out)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", choices=sorted(DEVICES), action="append", help="one of them only (default: all)")
    ap.add_argument("--set", choices=("story", "examples"), default="story",
                    help="story: famous equations worked (default); examples: the app's own examples, under examples/")
    args = ap.parse_args(argv)
    shots, taker = (EXAMPLE_SHOTS, take_examples) if args.set == "examples" else (SHOTS, take)
    out = HERE / "ios" / "build" / "screenshots"
    for device in args.device or sorted(DEVICES):
        base = out / device / ("examples" if args.set == "examples" else "")
        raw, framed, work = base / "raw", base / "framed", out / "work"
        for folder in (raw, framed, work):
            folder.mkdir(parents=True, exist_ok=True)
        w, h, scale, _ = DEVICES[device]
        print(f"Taking the {device} screens ({w * scale} x {h * scale}), the {args.set} set")
        taker(work, raw, device)
        for i, (name, caption) in enumerate(shots, 1):
            frame(raw / f"{name}.png", caption, framed / f"{i}-{name.split('-', 1)[1]}.png", device)
        print(f"Wrote {raw} and {framed} ({len(shots)} each)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

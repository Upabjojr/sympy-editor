"""The handwriting panel in a real browser: the area that grows and scrolls,
undo / redo / clear, full screen with its sheet of readings, the reading's
options, and inserting with them.  The model is faked - what is tested is the
panel, not math-ocr - so it needs only Playwright with Chromium and the KaTeX
CDN (skipped otherwise)."""
import math
import sys
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import Symbol, cos, sin, symbols

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "sympy_editor_latex"))

playwright = pytest.importorskip("playwright.sync_api")
pytest.importorskip("lark")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_ink import InkAddon  # noqa: E402
from sympy_editor_latex import ADDON as LATEX  # noqa: E402

x, y = symbols("x y")


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


class FakeRecognizer:
    """Reads anything as the same formula: one with an ambiguity and a constant."""
    latex = r"\sin x \cos y + \pi"

    def status(self):
        return {"available": True, "model": "fake", "mathocr": "fake"}

    def warm(self, background=True):
        return True

    def recognize(self, strokes, beam=4, limit=5):
        return {"candidates": [{"latex": self.latex, "raw": self.latex, "score": 0.0}], "ms": 1.0,
                "strokes": len(strokes or []), "points": sum(len(s) for s in strokes or [])}


def _wait(predicate, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_the_area_grows_scrolls_undoes_and_goes_full_screen():
    doc = Document(x, addons=[InkAddon(FakeRecognizer()), LATEX])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            page = browser.new_page(viewport={"width": 760, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-ink .ink-canvas", timeout=30000)
            panel = page.locator(".se-addon-ink .ink-panel")
            geometry = ("() => { const p = document.querySelector('.se-addon-ink .ink-pad'), r = p.getBoundingClientRect(),"
                        " c = document.querySelector('.se-addon-ink .ink-canvas');"
                        " return {left: r.left, top: r.top, w: p.clientWidth, h: p.clientHeight, cw: c.clientWidth, ch: c.clientHeight,"
                        " sl: p.scrollLeft, st: p.scrollTop}; }")
            g = page.evaluate(geometry)
            page.wait_for_function("document.querySelector('.se-addon-ink .ink-canvas').clientWidth > 0")

            def stroke(x0, y0, x1, y1, steps=6):
                page.mouse.move(x0, y0)
                page.mouse.down()
                for i in range(1, steps + 1):
                    page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
                page.mouse.up()

            strokes = lambda: int(panel.get_attribute("data-strokes"))
            # a stroke well inside: read (by the fake), with the reading's options
            stroke(g["left"] + 30, g["top"] + 40, g["left"] + 120, g["top"] + 70)
            page.wait_for_selector(".se-addon-ink .ink-cand", timeout=15000)
            assert strokes() == 1
            src = page.locator(".se-addon-ink .ink-src")
            assert _wait(lambda: src.inner_text() == "sin(x)*cos(y) + pi")
            assert page.locator(".se-addon-ink .ink-point select").count() >= 1
            assert page.locator(".se-addon-ink .ink-const input").count() == 1
            assert page.locator(".se-addon-ink .ink-insert").inner_text() == "Add to end"    # neither a selection nor a cursor
            g = page.evaluate(geometry)
            assert g["cw"] == g["w"] and page.locator(".se-addon-ink .ink-scroll-right").is_hidden()
            # ink near the right edge: room beyond it, and the strip to scroll there
            stroke(g["left"] + g["w"] - 70, g["top"] + 40, g["left"] + g["w"] - 8, g["top"] + 50)
            g = page.evaluate(geometry)
            assert g["cw"] > g["w"] and page.locator(".se-addon-ink .ink-scroll-right").is_visible()
            page.locator(".se-addon-ink .ink-scroll-right").click()
            assert _wait(lambda: page.evaluate(geometry)["sl"] > 0)
            assert _wait(lambda: page.locator(".se-addon-ink .ink-scroll-left").is_visible())
            # ... and near the bottom
            g = page.evaluate(geometry)
            stroke(g["left"] + 40, g["top"] + g["h"] - 60, g["left"] + 60, g["top"] + g["h"] - 6)
            g = page.evaluate(geometry)
            assert g["ch"] > g["h"] and page.locator(".se-addon-ink .ink-scroll-down").is_visible()
            assert strokes() == 3
            # undo, redo, and a clear that undo brings back
            page.locator(".se-addon-ink .ink-undo").click()
            assert strokes() == 2
            page.locator(".se-addon-ink .ink-redo").click()
            assert strokes() == 3 and page.locator(".se-addon-ink .ink-redo").is_disabled()
            page.locator(".se-addon-ink .ink-clear").click()
            assert strokes() == 0 and page.evaluate(geometry)["cw"] == page.evaluate(geometry)["w"]   # back to the box
            page.locator(".se-addon-ink .ink-undo").click()
            assert strokes() == 3 and page.evaluate(geometry)["cw"] > page.evaluate(geometry)["w"]
            page.wait_for_selector(".se-addon-ink .ink-cand", timeout=15000)

            # full screen: the panel covers the window, the tools stay, the readings fold away
            page.locator(".se-addon-ink .ink-fullbtn").click()
            assert "ink-full" in panel.get_attribute("class")
            box = panel.bounding_box()
            assert box["x"] == 0 and box["y"] == 0 and box["width"] == 760 and box["height"] == 900
            for name in ("undo", "redo", "clear"):
                assert page.locator(f".se-addon-ink .ink-{name}").is_visible()
            head, body = page.locator(".se-addon-ink .ink-sheet-head"), page.locator(".se-addon-ink .ink-sheet-body")
            assert head.is_visible() and body.is_visible()
            assert _wait(lambda: head.inner_text().strip() == "sin(x)*cos(y) + pi")
            pad_before = page.evaluate(geometry)["h"]
            head.click()
            assert body.is_hidden() and _wait(lambda: page.evaluate(geometry)["h"] > pad_before)   # the area takes the room
            head.click()
            assert body.is_visible()
            # the reading's options: another reading of the ambiguity, pi a symbol
            select = page.locator(".se-addon-ink .ink-point select").filter(has=page.locator("option", has_text="sin(x*cos(y)) + pi")).first
            select.select_option(label="sin(x*cos(y)) + pi")
            assert _wait(lambda: src.inner_text() == "sin(x*cos(y)) + pi")
            page.locator(".se-addon-ink .ink-const input").uncheck()
            assert _wait(lambda: src.inner_text() == "pi + sin(x*cos(y))")
            page.keyboard.press("Escape")
            assert "ink-full" not in panel.get_attribute("class")
            # inserting goes back to the formula, with the options picked, and takes the ink
            page.locator(".se-addon-ink .ink-fullbtn").click()
            page.locator(".se-addon-ink .ink-insert-all").click()
            assert _wait(lambda: doc.expr == Symbol("pi") + sin(x * cos(y)))
            assert _wait(lambda: "ink-full" not in panel.get_attribute("class"))
            assert strokes() == 0 and page.locator(".se-addon-ink .ink-note").inner_text() == "Inserted."
            page.locator(".se-addon-ink .ink-undo").click()                    # the ink comes back
            assert strokes() == 3
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


TOUCH = """(t) => {
    const c = document.querySelector('.se-addon-ink .ink-canvas');
    c.dispatchEvent(new PointerEvent(t.type, {pointerId: t.id, pointerType: 'touch', isPrimary: t.id === 1,
        clientX: t.x, clientY: t.y, button: 0, buttons: t.type === 'pointerup' ? 0 : 1, bubbles: true, cancelable: true}));
}"""


def test_two_fingers_zoom_and_scroll_the_area_and_never_write():
    """A second finger drops the stroke the first one began and pinches
    instead: apart zooms in, both moving scroll - the way back after the area
    has been scrolled on - and the finger left when the other lifts writes
    nothing.  One finger writes again afterwards; a pinch on a trackpad
    (Ctrl and the wheel) zooms too."""
    doc = Document(x, addons=[InkAddon(FakeRecognizer()), LATEX])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            page = browser.new_page(viewport={"width": 760, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-ink .ink-canvas", timeout=30000)
            page.wait_for_function("document.querySelector('.se-addon-ink .ink-canvas').clientWidth > 0")
            panel = page.locator(".se-addon-ink .ink-panel")
            touch = lambda kind, pid, px, py: page.evaluate(TOUCH, {"type": kind, "id": pid, "x": px, "y": py})
            strokes = lambda: int(panel.get_attribute("data-strokes"))
            zoom = lambda: float(panel.get_attribute("data-zoom"))
            scroll = lambda: page.evaluate("(() => { const p = document.querySelector('.se-addon-ink .ink-pad'); return [p.scrollLeft, p.scrollTop]; })()")
            r = page.locator(".se-addon-ink .ink-pad").bounding_box()
            cx, cy = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2

            # the first finger begins a stroke; the second one takes it back and pinches
            touch("pointerdown", 1, cx - 40, cy)
            touch("pointermove", 1, cx - 30, cy + 10)
            touch("pointerdown", 2, cx + 40, cy)
            for k in range(1, 6):                                          # apart: twice the distance
                touch("pointermove", 1, cx - 40 - 8 * k, cy)
                touch("pointermove", 2, cx + 40 + 8 * k, cy)
            assert abs(zoom() - 160 / math.hypot(70, 10)) < 0.05, zoom()   # the fingers began 70 x 10 apart, end 160 apart
            before = scroll()
            for k in range(1, 6):                                          # both to the left: the area follows them
                touch("pointermove", 1, cx - 80 - 12 * k, cy)
                touch("pointermove", 2, cx + 80 - 12 * k, cy)
            assert scroll()[0] > before[0] + 40, (before, scroll())
            touch("pointerup", 2, cx + 20, cy)
            touch("pointermove", 1, cx - 100, cy + 30)                      # the finger left behind
            touch("pointermove", 1, cx - 60, cy + 50)
            touch("pointerup", 1, cx - 60, cy + 50)
            assert strokes() == 0
            # back the other way, with two fingers again
            touch("pointerdown", 1, cx - 60, cy)
            touch("pointerdown", 2, cx + 60, cy)
            far = scroll()[0]
            for k in range(1, 8):
                touch("pointermove", 1, cx - 60 + 15 * k, cy)
                touch("pointermove", 2, cx + 60 + 15 * k, cy)
            touch("pointerup", 1, cx, cy)
            touch("pointerup", 2, cx, cy)
            assert scroll()[0] < far - 60 and strokes() == 0
            # one finger writes again
            touch("pointerdown", 3, cx - 20, cy - 20)
            touch("pointermove", 3, cx + 10, cy)
            touch("pointerup", 3, cx + 20, cy + 10)
            assert strokes() == 1
            # a pinch on a trackpad: Ctrl and the wheel
            page.mouse.move(cx, cy)
            page.keyboard.down("Control")
            page.mouse.wheel(0, 120)
            page.keyboard.up("Control")
            assert _wait(lambda: zoom() < 1.8)
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()

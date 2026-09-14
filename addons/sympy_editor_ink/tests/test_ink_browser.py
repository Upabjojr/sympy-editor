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
        self.last = strokes                          # what the page sent: what a test can look at
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
            # the tools are icons, each named for a tooltip and a screen reader, and explained in the guide
            tools = page.locator(".se-addon-ink .ink-bar button")
            assert tools.count() == 5
            for i in range(5):
                assert tools.nth(i).inner_text().strip() == "" and tools.nth(i).locator("svg").count() == 1
                assert tools.nth(i).get_attribute("aria-label")
            page.locator(".se-addon-ink .se-addon-help").click()
            guide = page.locator(".se-help-view")
            for name in ("Undo", "Redo", "Erase", "Clear", "Read", "Full screen"):
                assert name in guide.inner_text(), name
            assert guide.locator("svg.ink-icon").count() == 6
            page.keyboard.press("Escape")
            assert _wait(lambda: page.locator(".se-help-view").count() == 0)
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
            # only the button scrolls: along its edge, above and below it, the pen writes
            chip = page.locator(".se-addon-ink .ink-scroll-left").bounding_box()
            g = page.evaluate(geometry)
            assert chip["height"] < g["h"] / 2 and chip["width"] < 60
            before = strokes()
            stroke(g["left"] + 6, g["top"] + 8, g["left"] + 14, chip["y"] - 6)
            assert strokes() == before + 1
            page.locator(".se-addon-ink .ink-undo").click()
            assert strokes() == before
            # ... and near the bottom
            g = page.evaluate(geometry)
            stroke(g["left"] + 40, g["top"] + g["h"] - 60, g["left"] + 60, g["top"] + g["h"] - 6)
            g = page.evaluate(geometry)
            assert g["ch"] > g["h"] and page.locator(".se-addon-ink .ink-scroll-down").is_visible()
            assert strokes() == 3
            # undo, redo, and a clear that undo brings back
            page.locator(".se-addon-ink .ink-undo").click()
            assert strokes() == 2
            g = page.evaluate(geometry)
            assert g["ch"] == g["h"] and g["cw"] > g["w"]      # the room below went with the stroke; the ink on the right keeps its own
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



def test_inserting_brings_the_formula_back_into_sight():
    """The panel sits below the editor: after a reading goes in - at the end,
    over the selection, as the whole expression - the page is back at the top
    of the editor, wherever it had been scrolled to."""
    doc = Document(x + y, addons=[InkAddon(FakeRecognizer()), LATEX])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            page = browser.new_page(viewport={"width": 760, "height": 520}, reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-ink .ink-canvas", timeout=30000)
            page.wait_for_function("document.querySelector('.se-addon-ink .ink-canvas').clientWidth > 0")
            # room below the panel, so the page can be scrolled away from the formula
            page.evaluate("document.body.appendChild(Object.assign(document.createElement('div'), {style: 'height: 3000px'}))")
            ed = "document.querySelector('.sympy-editor').__sympyEditor"
            top = "Math.round(document.querySelector('.sympy-editor').getBoundingClientRect().top)"
            panel = page.locator(".se-addon-ink .ink-panel")
            strokes = lambda: int(panel.get_attribute("data-strokes"))

            def write():
                page.locator(".se-addon-ink .ink-pad").scroll_into_view_if_needed()
                r = page.evaluate("() => { const b = document.querySelector('.se-addon-ink .ink-pad').getBoundingClientRect();"
                                  " return {left: b.left, top: b.top}; }")
                page.mouse.move(r["left"] + 30, r["top"] + 40)
                page.mouse.down()
                for i in range(1, 7):
                    page.mouse.move(r["left"] + 30 + 15 * i, r["top"] + 40 + 5 * i)
                page.mouse.up()
                assert _wait(lambda: not page.locator(".se-addon-ink .ink-insert").is_disabled(), timeout=15)

            def scrolled_away():
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_function(top + " < -100")

            def back_at_the_formula():
                page.wait_for_function(top + " >= -1 && " + top + " <= 1", timeout=5000)

            write()                                                      # Add to end
            assert page.locator(".se-addon-ink .ink-insert").inner_text() == "Add to end"
            scrolled_away()
            page.locator(".se-addon-ink .ink-insert").click()
            assert _wait(lambda: strokes() == 0 and doc.expr != x + y)
            back_at_the_formula()

            write()                                                      # over the selection
            xp = next(path for path, n in doc.snapshot()["nodes"].items() if n["src"] == "x")
            page.evaluate("p => %s.select(p)" % ed, xp)
            assert _wait(lambda: page.locator(".se-addon-ink .ink-insert").inner_text() == "Replace the selection")
            before = doc.expr
            scrolled_away()
            page.locator(".se-addon-ink .ink-insert").click()
            assert _wait(lambda: doc.expr != before)
            back_at_the_formula()

            page.evaluate(ed + ".select(null)")
            write()                                                      # the whole expression
            scrolled_away()
            page.locator(".se-addon-ink .ink-insert-all").click()
            assert _wait(lambda: doc.expr == sin(x) * cos(y) + Symbol("pi") or str(doc.expr) == "sin(x)*cos(y) + pi")
            back_at_the_formula()
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

            # a finger writing to the right edge makes room; a second finger takes the stroke back, and the room
            size = "(() => { const p = document.querySelector('.se-addon-ink .ink-pad'), c = document.querySelector('.se-addon-ink .ink-canvas'); return [c.clientWidth, p.clientWidth]; })()"
            touch("pointerdown", 1, r["x"] + r["width"] - 90, cy)
            for k in range(1, 5):
                touch("pointermove", 1, r["x"] + r["width"] - 90 + 20 * k, cy)
            canvas_w, pad_w = page.evaluate(size)
            assert canvas_w > pad_w
            touch("pointerdown", 2, cx, cy)
            canvas_w, pad_w = page.evaluate(size)
            assert canvas_w == pad_w
            touch("pointerup", 2, cx, cy)
            touch("pointerup", 1, r["x"] + r["width"] - 10, cy)
            assert strokes() == 0

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



def test_erase_takes_away_the_strokes_it_passes_over():
    """Erase turns the pointer into an eraser: a stroke it passes over goes,
    the others stay, in their order; Undo puts it back in its place, Redo
    takes it again, and switched off the pointer writes again."""
    fake = FakeRecognizer()
    doc = Document(x, addons=[InkAddon(fake), LATEX])
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
            strokes = lambda: int(panel.get_attribute("data-strokes"))
            c = page.evaluate("(() => { const r = document.querySelector('.se-addon-ink .ink-canvas').getBoundingClientRect(); return {x: r.left, y: r.top}; })()")

            def drag(x0, y0, x1, y1, steps=6):
                page.mouse.move(c["x"] + x0, c["y"] + y0)
                page.mouse.down()
                for i in range(1, steps + 1):
                    page.mouse.move(c["x"] + x0 + (x1 - x0) * i / steps, c["y"] + y0 + (y1 - y0) * i / steps)
                page.mouse.up()

            def read_strokes():                      # the strokes the page last sent to be read, by where they begin
                fake.last = None
                assert _wait(lambda: fake.last is not None)
                return [round(s[0][0] / 10) * 10 for s in fake.last]

            for left in (30, 150, 270):              # three strokes apart from each other
                drag(left, 50, left + 50, 60)
            assert strokes() == 3
            erase = page.locator(".se-addon-ink .ink-erase")
            erase.click()
            assert erase.get_attribute("aria-pressed") == "true"
            drag(175, 20, 175, 90)                   # across the middle one only
            assert strokes() == 2
            assert read_strokes() == [30, 270]
            page.locator(".se-addon-ink .ink-undo").click()      # back, in its place
            assert strokes() == 3 and read_strokes() == [30, 150, 270]
            page.locator(".se-addon-ink .ink-redo").click()
            assert strokes() == 2 and read_strokes() == [30, 270]
            drag(400, 20, 400, 90)                   # over nothing: nothing goes, nothing to undo
            assert strokes() == 2
            erase.click()                            # off: the pointer writes again
            assert erase.get_attribute("aria-pressed") == "false"
            drag(400, 50, 450, 60)
            assert strokes() == 3 and read_strokes() == [30, 270, 400]
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()

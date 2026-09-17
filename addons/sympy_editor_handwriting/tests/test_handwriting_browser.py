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
from sympy_editor_handwriting import HandwritingAddon  # noqa: E402
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
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
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
            page.wait_for_selector(".se-addon-handwriting .ink-canvas", timeout=30000)
            panel = page.locator(".se-addon-handwriting .ink-panel")
            geometry = ("() => { const p = document.querySelector('.se-addon-handwriting .ink-pad'), r = p.getBoundingClientRect(),"
                        " c = document.querySelector('.se-addon-handwriting .ink-canvas');"
                        " return {left: r.left, top: r.top, w: p.clientWidth, h: p.clientHeight, cw: c.clientWidth, ch: c.clientHeight,"
                        " sl: p.scrollLeft, st: p.scrollTop}; }")
            g = page.evaluate(geometry)
            page.wait_for_function("document.querySelector('.se-addon-handwriting .ink-canvas').clientWidth > 0")
            page.locator(".se-addon-handwriting .ink-latex").fill("")    # the pad opens with the formula: an empty one

            def stroke(x0, y0, x1, y1, steps=6):
                page.mouse.move(x0, y0)
                page.mouse.down()
                for i in range(1, steps + 1):
                    page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
                page.mouse.up()

            strokes = lambda: int(panel.get_attribute("data-strokes"))
            # the tools are icons, each named for a tooltip and a screen reader, and explained in the guide
            tools = page.locator(".se-addon-handwriting .ink-bar .ink-tool")
            assert tools.count() == 8
            for i in range(8):
                assert tools.nth(i).inner_text().strip() == "" and tools.nth(i).locator("svg").count() == 1
                assert tools.nth(i).get_attribute("aria-label")
            page.locator(".se-addon-handwriting .se-addon-help").click()
            guide = page.locator(".se-help-view")
            for name in ("Select", "Pen", "Eraser", "Done", "Undo", "Redo", "Clear", "Read", "Full screen"):
                assert name in guide.inner_text(), name
            assert guide.locator("svg.ink-icon").count() == 11
            page.keyboard.press("Escape")
            assert _wait(lambda: page.locator(".se-help-view").count() == 0)
            # a stroke well inside: read (by the fake), with the reading's options
            stroke(g["left"] + 30, g["top"] + 40, g["left"] + 120, g["top"] + 70)
            page.wait_for_selector(".se-addon-handwriting .ink-cand", timeout=15000)
            assert strokes() == 1
            src = page.locator(".se-addon-handwriting .ink-src")
            assert _wait(lambda: src.inner_text() == "sin(x)*cos(y) + pi")
            assert page.locator(".se-addon-handwriting .ink-point select").count() >= 1
            assert page.locator(".se-addon-handwriting .ink-const input").count() == 1
            assert page.locator(".se-addon-handwriting .ink-apply").inner_text() == "Apply to the formula"
            assert not page.locator(".se-addon-handwriting .ink-apply").is_disabled()
            g = page.evaluate(geometry)
            assert g["cw"] == g["w"] and page.locator(".se-addon-handwriting .ink-scroll-right").is_hidden()
            # ink near the right edge: room beyond it, and the strip to scroll there
            stroke(g["left"] + g["w"] - 70, g["top"] + 40, g["left"] + g["w"] - 8, g["top"] + 50)
            g = page.evaluate(geometry)
            assert g["cw"] > g["w"] and page.locator(".se-addon-handwriting .ink-scroll-right").is_visible()
            page.locator(".se-addon-handwriting .ink-scroll-right").click()
            assert _wait(lambda: page.evaluate(geometry)["sl"] > 0)
            assert _wait(lambda: page.locator(".se-addon-handwriting .ink-scroll-left").is_visible())
            # only the button scrolls: along its edge, above and below it, the pen writes
            chip = page.locator(".se-addon-handwriting .ink-scroll-left").bounding_box()
            g = page.evaluate(geometry)
            assert chip["height"] < g["h"] / 2 and chip["width"] < 60
            before = strokes()
            stroke(g["left"] + 6, g["top"] + 8, g["left"] + 14, chip["y"] - 6)
            assert strokes() == before + 1
            page.locator(".se-addon-handwriting .ink-undo").click()
            assert strokes() == before
            # ... and near the bottom
            g = page.evaluate(geometry)
            stroke(g["left"] + 40, g["top"] + g["h"] - 60, g["left"] + 60, g["top"] + g["h"] - 6)
            g = page.evaluate(geometry)
            assert g["ch"] > g["h"] and page.locator(".se-addon-handwriting .ink-scroll-down").is_visible()
            assert strokes() == 3
            # undo, redo, and a clear that undo brings back
            page.locator(".se-addon-handwriting .ink-undo").click()
            assert strokes() == 2
            g = page.evaluate(geometry)
            assert g["ch"] == g["h"] and g["cw"] > g["w"]      # the room below went with the stroke; the ink on the right keeps its own
            page.locator(".se-addon-handwriting .ink-redo").click()
            assert strokes() == 3 and page.locator(".se-addon-handwriting .ink-redo").is_disabled()
            page.locator(".se-addon-handwriting .ink-clear").click()
            assert strokes() == 0 and page.evaluate(geometry)["cw"] == page.evaluate(geometry)["w"]   # back to the box
            page.locator(".se-addon-handwriting .ink-undo").click()
            assert strokes() == 3 and page.evaluate(geometry)["cw"] > page.evaluate(geometry)["w"]
            page.wait_for_selector(".se-addon-handwriting .ink-cand", timeout=15000)

            # full screen: the panel covers the window, the tools stay, the readings fold away
            page.locator(".se-addon-handwriting .ink-fullbtn").click()
            assert "ink-full" in panel.get_attribute("class")
            box = panel.bounding_box()
            assert box["x"] == 0 and box["y"] == 0 and box["width"] == 760 and box["height"] == 900
            for name in ("undo", "redo", "clear"):
                assert page.locator(f".se-addon-handwriting .ink-{name}").is_visible()
            head, body = page.locator(".se-addon-handwriting .ink-sheet-head"), page.locator(".se-addon-handwriting .ink-sheet-body")
            assert head.is_visible() and body.is_visible()
            assert _wait(lambda: head.inner_text().strip() == "sin(x)*cos(y) + pi")
            pad_before = page.evaluate(geometry)["h"]
            head.click()
            assert body.is_hidden() and _wait(lambda: page.evaluate(geometry)["h"] > pad_before)   # the area takes the room
            head.click()
            assert body.is_visible()
            # the reading's options: another reading of the ambiguity, pi a symbol
            select = page.locator(".se-addon-handwriting .ink-point select").filter(has=page.locator("option", has_text="sin(x*cos(y)) + pi")).first
            select.select_option(label="sin(x*cos(y)) + pi")
            assert _wait(lambda: src.inner_text() == "sin(x*cos(y)) + pi")
            page.locator(".se-addon-handwriting .ink-const input").uncheck()
            assert _wait(lambda: src.inner_text() == "pi + sin(x*cos(y))")
            page.keyboard.press("Escape")
            assert "ink-full" not in panel.get_attribute("class")
            # applying puts the reading in with the options picked, takes the ink, and stays in full screen
            page.locator(".se-addon-handwriting .ink-fullbtn").click()
            page.locator(".se-addon-handwriting .ink-apply").click()
            assert _wait(lambda: doc.expr == Symbol("pi") + sin(x * cos(y)))
            assert _wait(lambda: page.locator(".se-addon-handwriting .ink-note").inner_text() == "Applied.")
            assert "ink-full" in panel.get_attribute("class")
            assert strokes() == 0 and page.locator(".se-addon-handwriting .ink-note").inner_text() == "Applied."
            page.locator(".se-addon-handwriting .ink-undo").click()                    # the ink comes back
            assert strokes() == 3
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()



def test_applying_keeps_the_view_as_it_is():
    """Apply makes the pad's formula the editor's and moves nothing: the pad
    stays where it was on the page, and in full screen it stays in full screen."""
    doc = Document(x + y, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _pad_page(p, doc)
        try:
            panel = page.locator(".se-addon-handwriting .ink-panel")
            field = page.locator(".se-addon-handwriting .ink-latex")
            apply = page.locator(".se-addon-handwriting .ink-apply")
            note = page.locator(".se-addon-handwriting .ink-note")
            page.evaluate("document.body.appendChild(Object.assign(document.createElement('div'), {style: 'height: 3000px'}))")
            top = lambda: round(page.evaluate("document.querySelector('.se-addon-handwriting .ink-pad').getBoundingClientRect().top"))

            def write():
                field.fill("")
                box = page.locator(".se-addon-handwriting .ink-pad").bounding_box()
                _drag(page, box["x"] + 40, box["y"] + 40, box["x"] + 160, box["y"] + 70)
                assert _wait(lambda: not apply.is_disabled(), 15)

            write()
            page.evaluate("window.scrollBy(0, 120)")                      # the page scrolled a little past the editor
            start = top()
            apply.click()
            assert _wait(lambda: str(doc.expr) == "sin(x)*cos(y) + pi")
            assert _wait(lambda: note.inner_text() == "Applied.")
            seen = []
            for _ in range(15):                                         # while the pad reloads the formula and reads it
                seen.append(top())
                page.wait_for_timeout(100)
            assert all(abs(t - start) <= 1 for t in seen), (start, seen)
            assert _wait(lambda: field.input_value() != "")               # the pad shows the editor's formula now
            # in full screen: still in full screen after applying
            page.locator(".se-addon-handwriting .ink-fullbtn").click()
            assert "ink-full" in panel.get_attribute("class")
            write()
            apply.click()
            assert _wait(lambda: note.inner_text() == "Applied.")
            assert "ink-full" in panel.get_attribute("class")
            assert page.errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()


TOUCH = """(t) => {
    const c = document.querySelector('.se-addon-handwriting .ink-canvas');
    c.dispatchEvent(new PointerEvent(t.type, {pointerId: t.id, pointerType: 'touch', isPrimary: t.id === 1,
        clientX: t.x, clientY: t.y, button: 0, buttons: t.type === 'pointerup' ? 0 : 1, bubbles: true, cancelable: true}));
}"""


def test_two_fingers_zoom_and_scroll_the_area_and_never_write():
    """A second finger drops the stroke the first one began and pinches
    instead: apart zooms in, both moving scroll - the way back after the area
    has been scrolled on - and the finger left when the other lifts writes
    nothing.  One finger writes again afterwards; a pinch on a trackpad
    (Ctrl and the wheel) zooms too."""
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
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
            page.wait_for_selector(".se-addon-handwriting .ink-canvas", timeout=30000)
            page.wait_for_function("document.querySelector('.se-addon-handwriting .ink-canvas').clientWidth > 0")
            page.locator(".se-addon-handwriting .ink-latex").fill("")    # the pad opens with the formula: an empty one
            panel = page.locator(".se-addon-handwriting .ink-panel")
            touch = lambda kind, pid, px, py: page.evaluate(TOUCH, {"type": kind, "id": pid, "x": px, "y": py})
            strokes = lambda: int(panel.get_attribute("data-strokes"))
            zoom = lambda: float(panel.get_attribute("data-zoom"))
            scroll = lambda: page.evaluate("(() => { const p = document.querySelector('.se-addon-handwriting .ink-pad'); return [p.scrollLeft, p.scrollTop]; })()")
            r = page.locator(".se-addon-handwriting .ink-pad").bounding_box()
            cx, cy = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2

            # a finger writing to the right edge makes room; a second finger takes the stroke back, and the room
            size = "(() => { const p = document.querySelector('.se-addon-handwriting .ink-pad'), c = document.querySelector('.se-addon-handwriting .ink-canvas'); return [c.clientWidth, p.clientWidth]; })()"
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
    doc = Document(x, addons=[HandwritingAddon(fake), LATEX])
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
            page.wait_for_selector(".se-addon-handwriting .ink-canvas", timeout=30000)
            page.wait_for_function("document.querySelector('.se-addon-handwriting .ink-canvas').clientWidth > 0")
            page.locator(".se-addon-handwriting .ink-latex").fill("")    # the pad opens with the formula: an empty one
            panel = page.locator(".se-addon-handwriting .ink-panel")
            strokes = lambda: int(panel.get_attribute("data-strokes"))
            c = page.evaluate("(() => { const r = document.querySelector('.se-addon-handwriting .ink-canvas').getBoundingClientRect(); return {x: r.left, y: r.top}; })()")

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
            erase = page.locator(".se-addon-handwriting .ink-erase")
            erase.click()
            assert erase.get_attribute("aria-pressed") == "true"
            drag(175, 20, 175, 90)                   # across the middle one only
            assert strokes() == 2
            assert read_strokes() == [30, 270]
            page.locator(".se-addon-handwriting .ink-undo").click()      # back, in its place
            assert strokes() == 3 and read_strokes() == [30, 150, 270]
            page.locator(".se-addon-handwriting .ink-redo").click()
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


def test_picking_a_reading_brings_its_buttons_into_sight():
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            page = browser.new_page(viewport={"width": 760, "height": 360})       # short: the buttons start below the fold
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-handwriting .ink-canvas", timeout=30000)
            page.wait_for_function("document.querySelector('.se-addon-handwriting .ink-canvas').clientWidth > 0")
            page.locator(".se-addon-handwriting .ink-latex").fill("")    # the pad opens with the formula: an empty one
            page.locator(".se-addon-handwriting .ink-pad").scroll_into_view_if_needed()
            r = page.locator(".se-addon-handwriting .ink-pad").bounding_box()
            page.mouse.move(r["x"] + 30, r["y"] + 40)
            page.mouse.down()
            for i in range(1, 7):
                page.mouse.move(r["x"] + 30 + 15 * i, r["y"] + 40 + 5 * i)
            page.mouse.up()
            page.wait_for_selector(".se-addon-handwriting .ink-cand", timeout=15000)
            # the first reading is chosen for the writer: the page stays with the pad
            before = page.evaluate("window.scrollY")
            page.wait_for_timeout(600)
            assert page.evaluate("window.scrollY") == before
            in_sight = ("() => { const b = document.querySelector('.se-addon-handwriting .ink-actions').getBoundingClientRect();"
                        " return b.top >= 0 && b.bottom <= window.innerHeight + 1; }")
            # as on a phone at the pad: the readings at the bottom of the screen, the buttons past it
            page.evaluate("document.querySelector('.se-addon-handwriting .ink-cand').scrollIntoView({block: 'end'})")
            assert not page.evaluate(in_sight)
            # picked by hand: its LaTeX and the buttons that put it in come into sight
            page.locator(".se-addon-handwriting .ink-cand").first.click()
            assert _wait(lambda: page.evaluate(in_sight), 5)
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


# ---- the formula in the pad ---------------------------------------------------------
READING = FakeRecognizer.latex
# where a piece of the drawn formula is: the glyphs in it, as the panel finds them
PIECE_BOX = """([s, e]) => {
  const el = document.querySelector(`.se-addon-handwriting .ink-formula [data-ls="${s}"][data-le="${e}"]`);
  if (!el) return null;
  let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
  for (const k of el.querySelectorAll('*')) {
    if (k.firstElementChild || !k.textContent.replace(/[\\s\\u200b]/g, '')) continue;
    const q = k.getBoundingClientRect();
    l = Math.min(l, q.left); t = Math.min(t, q.top); r = Math.max(r, q.right); b = Math.max(b, q.bottom);
  }
  return {x: (l + r) / 2, y: (t + b) / 2};
}"""


def _pad_page(p, doc):
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        browser = p.chromium.launch()
    except Exception as exc:
        srv.shutdown()
        pytest.skip(f"chromium not available: {exc}")
    page = browser.new_page(viewport={"width": 900, "height": 1000})
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(srv.url)
    page.wait_for_selector(".se-addon-handwriting .ink-canvas", timeout=30000)
    page.wait_for_function("document.querySelector('.se-addon-handwriting .ink-canvas').clientWidth > 0")
    page.locator(".se-addon-handwriting .ink-latex").fill("")    # the pad opens with the formula: an empty one
    page.locator(".se-addon-handwriting .ink-pad").scroll_into_view_if_needed()
    return srv, browser, page


def _drag(page, x0, y0, x1, y1, steps=8):
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
    page.mouse.up()


def test_a_piece_of_the_formula_is_selected_and_written_over():
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _pad_page(p, doc)
        try:
            panel = page.locator(".se-addon-handwriting .ink-panel")
            field = page.locator(".se-addon-handwriting .ink-latex")
            text = r"x^{2} + \frac{a}{b}"
            field.fill(text)
            page.wait_for_selector(".se-addon-handwriting .ink-formula [data-ls]")
            a = (text.index("{a}") + 1, text.index("{a}") + 2)
            frac = (text.index(r"\frac"), len(text))
            box = lambda r: page.evaluate(PIECE_BOX, list(r))
            # with Select, a tap selects the piece; again, what holds it; a tap on the piece, the piece again
            page.locator(".se-addon-handwriting .ink-select").click()
            assert panel.get_attribute("data-mode") == "select"
            page.mouse.click(box(a)["x"], box(a)["y"])
            assert panel.get_attribute("data-sel") == "%d,%d" % a
            page.mouse.click(box(a)["x"], box(a)["y"])
            assert panel.get_attribute("data-sel") == "%d,%d" % frac
            page.mouse.click(box(a)["x"], box(a)["y"])
            assert panel.get_attribute("data-sel") == "%d,%d" % a
            page.locator(".se-addon-handwriting .ink-pen").click()
            assert panel.get_attribute("data-mode") == "pen"
            # the Pen with the piece selected: it gives way to a hole at once, and what is written goes in it
            assert panel.get_attribute("data-hole") == "%d,%d" % a and panel.get_attribute("data-strokes") == "0"
            room = page.locator(".se-addon-handwriting .ink-formula [data-inkhole] .rule").bounding_box()
            _drag(page, room["x"] + 10, room["y"] + room["height"] / 2, room["x"] + room["width"] - 10, room["y"] + room["height"] / 2)
            assert _wait(lambda: panel.get_attribute("data-hole") == "%d,%d" % a)
            assert panel.get_attribute("data-strokes") == "1"
            assert page.locator(".se-addon-handwriting .ink-formula [data-inkhole]").count() == 1
            # the reading takes the piece's place in the text
            want = text[:a[0]] + READING + text[a[1]:]
            assert _wait(lambda: field.input_value() == want, 15)
            # what SymPy gets is shown of the piece written alone
            src = page.locator(".se-addon-handwriting .ink-src")
            assert _wait(lambda: src.inner_text() == "sin(x)*cos(y) + pi")
            assert "selected piece" in page.locator(".se-addon-handwriting .ink-reading-of").inner_text()
            # with Select, a tap outside the hole: the reading stays, the ink goes, and the new piece is selected
            page.locator(".se-addon-handwriting .ink-select").click()
            x_at = box((0, 1))
            page.mouse.click(x_at["x"], x_at["y"])
            assert _wait(lambda: panel.get_attribute("data-hole") == "")
            assert panel.get_attribute("data-strokes") == "0" and field.input_value() == want
            assert panel.get_attribute("data-sel") == "%d,%d" % (a[0], a[0] + len(READING))
            assert page.locator(".se-addon-handwriting .ink-formula [data-inkhole]").count() == 0
            # the hole closed: the reading is of the whole formula again
            assert _wait(lambda: src.inner_text() == "x**2 + (sin(x)*cos(y) + pi)/b")
            assert page.locator(".se-addon-handwriting .ink-reading-of").inner_text() == "The formula:"
            assert page.errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()


def test_write_and_done_a_bare_script_and_a_space_after_the_formula():
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _pad_page(p, doc)
        try:
            panel = page.locator(".se-addon-handwriting .ink-panel")
            field = page.locator(".se-addon-handwriting .ink-latex")
            done = page.locator(".se-addon-handwriting .ink-done")
            select, pen = page.locator(".se-addon-handwriting .ink-select"), page.locator(".se-addon-handwriting .ink-pen")
            field.fill("x^2")
            page.wait_for_selector(".se-addon-handwriting .ink-formula [data-ls]")
            two = page.evaluate(PIECE_BOX, [2, 3])
            select.click()
            page.mouse.click(two["x"], two["y"])
            assert panel.get_attribute("data-sel") == "2,3"
            pen.click()
            _drag(page, two["x"], two["y"], two["x"] + 40, two["y"] + 6)                  # the pen over the selection: room to write in
            assert panel.get_attribute("data-hole") == "2,3" and not done.is_disabled()
            assert _wait(lambda: field.input_value() == "x^{" + READING + "}", 15)     # a bare script: braced
            done.click()
            assert panel.get_attribute("data-hole") == "" and field.input_value() == "x^{" + READING + "}"
            # ink anywhere else is free: its reading goes after the formula; clearing the ink gives the text back
            text = field.input_value()
            pad = page.locator(".se-addon-handwriting .ink-pad").bounding_box()
            select.click()
            page.mouse.click(pad["x"] + pad["width"] - 30, pad["y"] + pad["height"] - 20)    # a tap on nothing: nothing selected
            assert panel.get_attribute("data-sel") == "" and panel.get_attribute("data-hole") == ""
            pen.click()
            _drag(page, pad["x"] + pad["width"] - 160, pad["y"] + 40, pad["x"] + pad["width"] - 60, pad["y"] + 70)
            assert _wait(lambda: panel.get_attribute("data-hole") == "free")
            assert _wait(lambda: field.input_value() == text + " " + READING, 15)
            page.locator(".se-addon-handwriting .ink-clear").click()
            assert _wait(lambda: field.input_value() == text)
            _drag(page, pad["x"] + pad["width"] - 160, pad["y"] + 40, pad["x"] + pad["width"] - 60, pad["y"] + 70)
            assert _wait(lambda: field.input_value() == text + " " + READING, 15)
            done.click()
            assert panel.get_attribute("data-hole") == "" and field.input_value() == text + " " + READING
            assert panel.get_attribute("data-sel") == "%d,%d" % (len(text) + 1, len(text) + 1 + len(READING))
            assert page.errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()


def test_an_empty_pad_is_written_on_whole_and_done_draws_the_reading():
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _pad_page(p, doc)
        try:
            panel = page.locator(".se-addon-handwriting .ink-panel")
            field = page.locator(".se-addon-handwriting .ink-latex")
            pad = page.locator(".se-addon-handwriting .ink-pad").bounding_box()
            _drag(page, pad["x"] + 40, pad["y"] + 50, pad["x"] + 160, pad["y"] + 80)
            assert panel.get_attribute("data-hole") == "free"
            assert _wait(lambda: field.input_value() == READING, 15)
            page.locator(".se-addon-handwriting .ink-done").click()
            assert panel.get_attribute("data-hole") == "" and panel.get_attribute("data-strokes") == "0"
            assert field.input_value() == READING
            assert page.locator(".se-addon-handwriting .ink-formula [data-ls]").count() > 0
            assert page.errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()


def test_undo_and_redo_go_through_every_edit_in_the_pad():
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _pad_page(p, doc)
        try:
            panel = page.locator(".se-addon-handwriting .ink-panel")
            field = page.locator(".se-addon-handwriting .ink-latex")
            undo, redo = page.locator(".se-addon-handwriting .ink-undo"), page.locator(".se-addon-handwriting .ink-redo")
            field.fill("x + y")
            page.wait_for_selector(".se-addon-handwriting .ink-formula [data-ls]")
            page.wait_for_timeout(1100)                                  # the typing so far is one edit
            # a selection is an edit
            page.locator(".se-addon-handwriting .ink-select").click()
            y_at = page.evaluate(PIECE_BOX, [4, 5])
            page.mouse.click(y_at["x"], y_at["y"])
            assert panel.get_attribute("data-sel") == "4,5"
            # typing in the LaTeX line in one go is one edit
            field.press("End")
            field.type(" + 1")
            assert field.input_value() == "x + y + 1"
            page.wait_for_timeout(1100)
            undo.click()
            assert field.input_value() == "x + y" and panel.get_attribute("data-sel") == "4,5"
            undo.click()
            assert field.input_value() == "x + y" and panel.get_attribute("data-sel") == ""
            redo.click()
            assert panel.get_attribute("data-sel") == "4,5"
            redo.click()
            assert field.input_value() == "x + y + 1" and panel.get_attribute("data-sel") == ""
            assert redo.is_disabled()
            # ink too: a stroke, taken back and put back, its reading with it
            page.locator(".se-addon-handwriting .ink-pen").click()
            pad = page.locator(".se-addon-handwriting .ink-pad").bounding_box()
            _drag(page, pad["x"] + pad["width"] - 200, pad["y"] + 40, pad["x"] + pad["width"] - 80, pad["y"] + 70)
            assert _wait(lambda: field.input_value() == "x + y + 1 " + READING, 15)
            undo.click()
            assert panel.get_attribute("data-strokes") == "0" and field.input_value() == "x + y + 1"
            redo.click()
            assert panel.get_attribute("data-strokes") == "1" and field.input_value() == "x + y + 1 " + READING
            # Ctrl+Z in the LaTeX line is the pad's Undo
            field.focus()
            page.keyboard.press("Control+z")
            assert panel.get_attribute("data-strokes") == "0" and field.input_value() == "x + y + 1"
            assert page.errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()


def test_undo_and_redo_keep_the_pad_where_it_is():
    """Pressed again and again with the page scrolled down to the panel, Undo
    and Redo never move the pad: the readings under it never take less room."""
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _pad_page(p, doc)
        try:
            panel = page.locator(".se-addon-handwriting .ink-panel")
            field = page.locator(".se-addon-handwriting .ink-latex")
            field.fill("x + y")
            page.wait_for_selector(".se-addon-handwriting .ink-formula [data-ls]")
            pad = page.locator(".se-addon-handwriting .ink-pad").bounding_box()
            for dy in (0, 60):                                                 # two strokes: readings with their options
                _drag(page, pad["x"] + pad["width"] - 220, pad["y"] + 30 + dy, pad["x"] + pad["width"] - 90, pad["y"] + 50 + dy)
                page.wait_for_timeout(900)
            assert _wait(lambda: page.locator(".se-addon-handwriting .ink-cand").count() > 0, 15)
            assert _wait(lambda: page.locator(".se-addon-handwriting .ink-point select").count() > 0, 15)
            page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            top = lambda: round(page.evaluate("document.querySelector('.se-addon-handwriting .ink-pad').getBoundingClientRect().top"))
            start = top()
            undo, redo = page.locator(".se-addon-handwriting .ink-undo"), page.locator(".se-addon-handwriting .ink-redo")
            seen = []
            for button in (undo, undo, undo, redo, redo, undo, redo, redo):
                if button.is_disabled():
                    continue
                button.click()
                for _ in range(8):                                              # while it is read again, too
                    seen.append(top())
                    page.wait_for_timeout(100)
            assert all(abs(t - start) <= 1 for t in seen), (start, seen)
            assert page.errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()


def test_a_pieces_options_and_its_edited_latex_go_into_the_formula():
    """The options of a piece written by hand are that piece's, and the one
    picked stays with it in the formula; the pen beside the chosen reading
    edits its LaTeX; Apply with the piece still open puts it all in."""
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _pad_page(p, doc)
        try:
            panel = page.locator(".se-addon-handwriting .ink-panel")
            field = page.locator(".se-addon-handwriting .ink-latex")
            src = page.locator(".se-addon-handwriting .ink-src")
            text = r"x^{2} + \frac{a}{b}"
            field.fill(text)
            page.wait_for_selector(".se-addon-handwriting .ink-formula [data-ls]")
            a = (text.index("{a}") + 1, text.index("{a}") + 2)

            def write_over_a():
                page.locator(".se-addon-handwriting .ink-select").click()
                c = page.evaluate(PIECE_BOX, list(a))
                page.mouse.click(c["x"], c["y"])
                assert panel.get_attribute("data-sel") == "%d,%d" % a
                page.locator(".se-addon-handwriting .ink-pen").click()
                _drag(page, c["x"], c["y"], c["x"] + 40, c["y"] + 8)
                assert _wait(lambda: src.inner_text() == "sin(x)*cos(y) + pi", 15)

            # an option picked for the piece: its reading, then the whole formula's, keeps it
            write_over_a()
            menu = page.locator(".se-addon-handwriting .ink-point select").filter(has=page.locator("option", has_text="sin(x*cos(y)) + pi")).first
            menu.select_option(label="sin(x*cos(y)) + pi")
            assert _wait(lambda: src.inner_text() == "sin(x*cos(y)) + pi")
            page.locator(".se-addon-handwriting .ink-done").click()
            assert _wait(lambda: src.inner_text() == "x**2 + (sin(x*cos(y)) + pi)/b")
            assert field.input_value() == text[:a[0]] + READING + text[a[1]:]           # the LaTeX as written
            # another piece written over: the option picked for the first one stays
            written = field.input_value()
            bb = (written.rindex("{b}") + 1, written.rindex("{b}") + 2)
            page.locator(".se-addon-handwriting .ink-select").click()
            c = page.evaluate(PIECE_BOX, list(bb))
            page.mouse.click(c["x"], c["y"])
            page.locator(".se-addon-handwriting .ink-pen").click()
            _drag(page, c["x"], c["y"], c["x"] + 40, c["y"] + 8)
            assert _wait(lambda: field.input_value() == written[:bb[0]] + READING + written[bb[1]:], 15)
            page.locator(".se-addon-handwriting .ink-done").click()
            assert _wait(lambda: src.inner_text().startswith("x**2 + (sin(x*cos(y)) + pi)/"))
            # the pen beside the chosen reading: its LaTeX, edited, in the piece's place
            field.fill(text)
            page.wait_for_selector(".se-addon-handwriting .ink-formula [data-ls]")
            write_over_a()
            page.locator(".se-addon-handwriting .ink-cand-edit").click()
            box = page.locator(".se-addon-handwriting .ink-cand-input")
            assert box.input_value() == READING
            box.fill("n + 1")
            box.press("Enter")
            assert _wait(lambda: field.input_value() == text.replace("{a}", "{n + 1}"))
            assert _wait(lambda: src.inner_text() == "n + 1")
            assert page.locator(".se-addon-handwriting .ink-cand.ink-edited.ink-chosen").count() == 1
            # Apply with the piece still open: the formula, as it reads with the piece in
            page.locator(".se-addon-handwriting .ink-apply").click()
            assert _wait(lambda: str(doc.expr) == "x**2 + (n + 1)/b")
            assert page.errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()


def test_with_a_piece_selected_the_pen_writes_in_its_place_wherever_it_writes():
    """A stroke begun away from the selected piece - easily, with a finger on a
    small one - still writes in its place, and so does the next one."""
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _pad_page(p, doc)
        try:
            panel = page.locator(".se-addon-handwriting .ink-panel")
            field = page.locator(".se-addon-handwriting .ink-latex")
            field.fill("x + y")
            page.wait_for_selector(".se-addon-handwriting .ink-formula [data-ls]")
            page.locator(".se-addon-handwriting .ink-select").click()
            y_at = page.evaluate(PIECE_BOX, [4, 5])
            page.mouse.click(y_at["x"], y_at["y"])
            assert panel.get_attribute("data-sel") == "4,5"
            page.locator(".se-addon-handwriting .ink-pen").click()
            pad = page.locator(".se-addon-handwriting .ink-pad").bounding_box()
            _drag(page, pad["x"] + pad["width"] - 220, pad["y"] + pad["height"] - 60, pad["x"] + pad["width"] - 120, pad["y"] + pad["height"] - 30)
            assert panel.get_attribute("data-hole") == "4,5"
            assert _wait(lambda: field.input_value() == "x + " + READING, 15)
            # the ink went into the room made for the piece
            inside = page.evaluate("""() => {
              const r = document.querySelector('.se-addon-handwriting .ink-formula [data-inkhole] .rule').getBoundingClientRect();
              return {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
            }""")
            assert inside["right"] > inside["left"]
            # a second stroke beside where the first was written: still that piece's, the room still open,
            # and brought along with the first - the room does not stretch to the far side of the pad
            _drag(page, pad["x"] + pad["width"] - 100, pad["y"] + pad["height"] - 60, pad["x"] + pad["width"] - 50, pad["y"] + pad["height"] - 30)
            assert _wait(lambda: panel.get_attribute("data-strokes") == "2")
            assert panel.get_attribute("data-hole") == "4,5"
            room = page.locator(".se-addon-handwriting .ink-formula [data-inkhole] .rule").bounding_box()
            assert room["width"] < 350, room
            assert _wait(lambda: field.input_value() == "x + " + READING, 15)
            assert page.errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()


PIECE_RECT = """([s, e]) => {
  const el = document.querySelector(`.se-addon-handwriting .ink-formula [data-ls="${s}"][data-le="${e}"]`);
  let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
  for (const k of el.querySelectorAll('*')) {
    if (k.firstElementChild || !k.textContent.replace(/[\\s\\u200b]/g, '')) continue;
    const q = k.getBoundingClientRect();
    l = Math.min(l, q.left); t = Math.min(t, q.top); r = Math.max(r, q.right); b = Math.max(b, q.bottom);
  }
  return {left: l, top: t, right: r, bottom: b};
}"""


def test_the_cursor_takes_what_is_written_or_typed_as_a_new_piece():
    """A tap near a piece's edge, or beside the formula, puts the cursor
    there: what the Pen writes goes in at it as a new piece, and the keyboard
    button types there."""
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _pad_page(p, doc)
        try:
            panel = page.locator(".se-addon-handwriting .ink-panel")
            field = page.locator(".se-addon-handwriting .ink-latex")
            field.fill("x + y")
            page.wait_for_selector(".se-addon-handwriting .ink-formula [data-ls]")
            page.locator(".se-addon-handwriting .ink-select").click()
            # beside the formula, to its right: the cursor at the end; the keyboard button types there
            yr = page.evaluate(PIECE_RECT, [4, 5])
            page.mouse.click(yr["right"] + 60, (yr["top"] + yr["bottom"]) / 2)
            assert panel.get_attribute("data-sel") == "5,5"
            page.locator(".se-addon-handwriting .ink-type").click()
            assert page.evaluate("[document.activeElement.className, document.activeElement.selectionStart, document.activeElement.selectionEnd]") == ["ink-latex", 5, 5]
            # at the right edge of x: the cursor after it
            xr = page.evaluate(PIECE_RECT, [0, 1])
            page.mouse.click(xr["right"] - 1, (xr["top"] + xr["bottom"]) / 2)
            assert panel.get_attribute("data-sel") == "1,1"
            # the Pen, pressed: the room to write in at the cursor, before any stroke; Select, pressed: gone again
            page.locator(".se-addon-handwriting .ink-pen").click()
            assert panel.get_attribute("data-hole") == "1,1" and panel.get_attribute("data-strokes") == "0"
            assert page.locator(".se-addon-handwriting .ink-formula [data-inkhole]").count() == 1
            page.locator(".se-addon-handwriting .ink-select").click()
            assert panel.get_attribute("data-hole") == "" and panel.get_attribute("data-sel") == "1,1"
            # the Pen writes at the cursor: a new piece there
            page.locator(".se-addon-handwriting .ink-pen").click()
            assert panel.get_attribute("data-hole") == "1,1"
            pad = page.locator(".se-addon-handwriting .ink-pad").bounding_box()
            _drag(page, pad["x"] + pad["width"] - 220, pad["y"] + 60, pad["x"] + pad["width"] - 120, pad["y"] + 90)
            assert panel.get_attribute("data-hole") == "1,1"
            # room to write in at the cursor: not small, and apart from the x before it
            room = page.locator(".se-addon-handwriting .ink-formula [data-inkhole] .rule").bounding_box()
            xr = page.evaluate(PIECE_RECT, [0, 1])
            assert room["width"] >= 100 and room["x"] - xr["right"] >= 5, (room, xr)
            assert _wait(lambda: field.input_value() == "x " + READING + " + y", 15)
            assert "at the cursor" in page.locator(".se-addon-handwriting .ink-reading-of").inner_text()
            page.locator(".se-addon-handwriting .ink-done").click()
            assert panel.get_attribute("data-hole") == ""
            assert panel.get_attribute("data-sel") == "2,%d" % (2 + len(READING))       # the new piece, selected
            # right of a power's exponent: the cursor after the power, not in its exponent
            field.fill("x^{2} + y")
            page.wait_for_selector(".se-addon-handwriting .ink-formula [data-ls]")
            page.locator(".se-addon-handwriting .ink-select").click()
            er = page.evaluate(PIECE_RECT, [3, 4])
            page.mouse.click(er["right"] - 1, (er["top"] + er["bottom"]) / 2)
            assert panel.get_attribute("data-sel") == "5,5"
            assert page.errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()


def test_zoom_buttons_and_the_room_to_write_in_brought_into_sight():
    """The pad's zoom buttons go as the editor's; a room opened to write in
    comes into the middle of the pad; a stroke ending near an edge of what is
    in sight scrolls the pad on a little."""
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _pad_page(p, doc)
        try:
            panel = page.locator(".se-addon-handwriting .ink-panel")
            field = page.locator(".se-addon-handwriting .ink-latex")
            plus, minus = page.locator(".se-addon-handwriting .ink-zoom-in"), page.locator(".se-addon-handwriting .ink-zoom-out")
            level = page.locator(".se-addon-handwriting .ink-zoom-level")
            assert level.inner_text() == "100%"
            plus.click()
            assert panel.get_attribute("data-zoom") == "1.20" and level.inner_text() == "120%"
            minus.click()
            minus.click()
            assert panel.get_attribute("data-zoom") == "0.83" and level.inner_text() == "83%"
            level.click()
            assert panel.get_attribute("data-zoom") == "1.00" and level.inner_text() == "100%"
            # a formula wider than the pad; the cursor at its far end, then the pad back at its start
            text = " + ".join("x_{%d}" % i for i in range(40))
            field.fill(text)
            page.wait_for_selector(".se-addon-handwriting .ink-formula [data-ls]")
            padbox = "() => { const p = document.querySelector('.se-addon-handwriting .ink-pad'); const r = p.getBoundingClientRect(); return {left: r.left, top: r.top, w: p.clientWidth, h: p.clientHeight, sl: p.scrollLeft}; }"
            page.evaluate("document.querySelector('.se-addon-handwriting .ink-pad').scrollLeft = 1e6")
            page.wait_for_timeout(200)
            last = text.rindex("x_{39}")
            page.locator(".se-addon-handwriting .ink-select").click()
            lr = page.evaluate(PIECE_RECT, [last, len(text)])
            page.mouse.click(lr["right"] - 1, (lr["top"] + lr["bottom"]) / 2)
            assert panel.get_attribute("data-sel") == "%d,%d" % (len(text), len(text))
            page.evaluate("document.querySelector('.se-addon-handwriting .ink-pad').scrollLeft = 0")
            page.wait_for_timeout(200)
            # the Pen: the room at the cursor, in the middle of the pad
            page.locator(".se-addon-handwriting .ink-pen").click()
            assert panel.get_attribute("data-hole") == "%d,%d" % (len(text), len(text))
            def centred():
                box = page.evaluate(padbox)
                room = page.locator(".se-addon-handwriting .ink-formula [data-inkhole] .rule").bounding_box()
                return abs(room["x"] + room["width"] / 2 - (box["left"] + box["w"] / 2)) <= 30
            assert _wait(centred, 5)
            # free ink (Select: the empty room goes; a tap on nothing: no cursor), a stroke ending near
            # the right edge of what is in sight: the pad scrolls on a little
            page.locator(".se-addon-handwriting .ink-select").click()
            assert panel.get_attribute("data-hole") == ""
            box = page.evaluate(padbox)
            page.mouse.click(box["left"] + box["w"] * 0.25, box["top"] + box["h"] - 12)       # clear of the scroll buttons
            assert panel.get_attribute("data-sel") == ""
            page.locator(".se-addon-handwriting .ink-pen").click()
            box = page.evaluate(padbox)
            before = box["sl"]
            _drag(page, box["left"] + box["w"] - 140, box["top"] + 40, box["left"] + box["w"] - 12, box["top"] + 60)
            assert _wait(lambda: page.evaluate(padbox)["sl"] > before + 20, 5)
            assert page.errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()

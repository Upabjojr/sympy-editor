"""The handwriting add-on on the editor's own formula, in a real browser: the
editor untouched until the Pen is on, the ink over the formula, the reading in
the formula at once, the piece it is read together with, the strip that keeps
or takes the change back, and the eraser.  The model is faked - what is tested
is the add-on, not math-ocr - so it needs only Playwright with Chromium and the
KaTeX CDN (skipped otherwise)."""
import os
import sys
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import cos, pi, sin, symbols

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

    def readings(self):
        return [self.latex]

    def recognize(self, strokes, beam=4, limit=5):
        self.last = strokes                          # what the page sent: what a test can look at
        return {"candidates": [{"latex": t, "raw": t, "score": -i} for i, t in enumerate(self.readings())],
                "ms": 1.0, "strokes": len(strokes or []), "points": sum(len(s) for s in strokes or [])}


class LetterRecognizer(FakeRecognizer):
    """Reads anything as whatever ``latex`` says - one letter, by default."""
    latex = "z"


class TwoRecognizer(FakeRecognizer):
    """Two readings of the same ink: y first, z second."""

    def readings(self):
        return ["y", "z"]


class MuteRecognizer(FakeRecognizer):
    """Reads nothing: the ink stays on the formula, to erase or to clear."""

    def readings(self):
        return []


def _wait(predicate, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


# The box of the smallest piece of the formula whose text is `want`.
TEXT_RECT = """(want) => {
  const els = [...document.querySelectorAll('.se-view [data-path]')]
    .filter(e => e.textContent.replace(/[\\s\\u200b]/g, '') === want);
  if (!els.length) return null;
  const el = els.sort((a, b) => b.getAttribute('data-path').length - a.getAttribute('data-path').length)[0];
  const q = el.getBoundingClientRect();
  return {left: q.left, top: q.top, right: q.right, bottom: q.bottom, path: el.getAttribute('data-path')};
}"""


def _page(p, doc, pen=True):
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
    page.wait_for_selector(".se-stage .hw-ink", timeout=30000)
    page.wait_for_selector(".se-view [data-path]")
    page.wait_for_function("document.querySelector('.se-stage .hw-ink').clientWidth > 0")
    if pen:
        page.locator('[data-cmd="addon:handwriting:pen"]').click()
    return srv, browser, page


def _close(srv, browser):
    browser.close()
    srv.shutdown()
    srv.server_close()


def _drag(page, x0, y0, x1, y1, steps=8):
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
    page.mouse.up()


def test_without_the_pen_the_editor_is_the_editor_it_was():
    """The add-on puts no pad and no second formula on the page: the ink layer
    takes no pointer until the Pen is on, and the formula is selected as it is
    without the add-on.  The Pen gives the formula room to write in."""
    doc = Document(x + y, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            # one formula, one canvas, and the canvas is on the editor's own stage
            assert page.locator(".se-view").count() == 1
            assert page.locator("canvas").count() == 1
            assert page.locator(".se-stage > canvas.hw-ink").count() == 1
            # no box, no name, nothing under the editor: the add-on shows nowhere
            assert page.locator(".se-addons [data-addon=handwriting]").count() == 0
            assert page.locator(".hw-panel").is_hidden()
            inert = "getComputedStyle(document.querySelector('.hw-ink')).pointerEvents"
            assert page.evaluate(inert) == "none"
            # the formula still selects on a tap
            r = page.evaluate(TEXT_RECT, "x")
            page.mouse.click((r["left"] + r["right"]) / 2, (r["top"] + r["bottom"]) / 2)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            was = page.evaluate("document.querySelector('.se-view').clientHeight")
            # the Pen: the layer takes the pointer, the formula grows
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            assert _wait(lambda: page.evaluate(inert) == "auto")
            assert _wait(lambda: page.locator(".hw-panel").is_visible())   # and the strip, with the Pen
            assert page.locator(".sympy-editor.se-inking").count() == 1
            assert _wait(lambda: page.evaluate("document.querySelector('.se-view').clientHeight") > was)
            assert page.locator('[data-cmd="addon:handwriting:pen"]').get_attribute("aria-pressed") == "true"
            # and off again: as it was
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            assert _wait(lambda: page.evaluate(inert) == "none")
            assert page.locator(".sympy-editor.se-inking").count() == 0
            assert _wait(lambda: page.evaluate("document.querySelector('.se-view').clientHeight") == was)
            assert page.locator(".hw-panel").is_hidden()
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_ways_to_read_an_ambiguous_part_are_typeset_buttons():
    """Where the reading's LaTeX can be read several ways, each way is a button
    showing the whole expression typeset; a press reads it again that way."""
    doc = Document(x + y, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 40, view["y"] + 90, view["x"] + 110, view["y"] + 120)
            page.locator(".hw-cand").first.wait_for(timeout=15000)
            page.locator(".hw-cand").first.click()
            usual, other = str(sin(x) * cos(y) + pi), str(sin(x * cos(y)) + pi)
            assert _wait(lambda: page.locator(".hw-src").inner_text() == usual, 15)
            row = page.locator(".hw-point").filter(
                has=page.locator(".hw-fragment", has_text=r"\sin x \cos y").filter(has_not_text=r"\pi"))
            buttons = row.locator(".hw-choice .hw-option")
            titles = [buttons.nth(i).get_attribute("title") for i in range(buttons.count())]
            assert usual in titles and other in titles
            assert all(buttons.nth(i).locator(".katex").count() == 1 for i in range(buttons.count()))
            assert row.locator(".hw-chosen").get_attribute("title") == usual
            buttons.nth(titles.index(other)).click()
            assert _wait(lambda: page.locator(".hw-src").inner_text() == other, 15)
            assert row.locator(".hw-chosen").get_attribute("title") == other
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_selection_written_over_is_hidden_until_the_writing_is_discarded():
    """Bug: with a piece selected, taking the pen left the piece on the screen
    under the ink.  It is what the writing replaces: hidden while the pen is
    on and while ink waits (its place kept, its outline gone too), back when
    the writing is discarded, and replaced - not shown again - once applied."""
    doc = Document(x + y, addons=[HandwritingAddon(LetterRecognizer()), LATEX])
    hidden = """() => {
        const el = [...document.querySelectorAll('.se-view [data-path]')]
            .find(e => e.textContent.replace(/[\\s\\u200b]/g, '') === 'y');
        const box = document.querySelector('.se-view .se-box-select');
        return {y: el ? getComputedStyle(el).visibility : null,
                box: box ? getComputedStyle(box).display !== 'none' : false};
    }"""
    pen = '[data-cmd="addon:handwriting:pen"]'
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            r = page.evaluate(TEXT_RECT, "y")
            page.mouse.click((r["left"] + r["right"]) / 2, (r["top"] + r["bottom"]) / 2)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            assert page.evaluate(hidden) == {"y": "visible", "box": True}
            width = page.evaluate("document.querySelector('.se-view .katex').getBoundingClientRect().width")
            page.locator(pen).click()                             # the pen: y goes, its outline too
            assert _wait(lambda: page.evaluate(hidden) == {"y": "hidden", "box": False})
            page.locator(pen).click()                             # nothing written: discarded, y is back
            assert _wait(lambda: page.evaluate(hidden) == {"y": "visible", "box": True})
            page.locator(pen).click()
            view = page.locator(".se-view").bounding_box()
            # every frame from the stroke to its reading: the pause between
            # them (0.7 s) showed y again for a moment
            page.evaluate("""() => { window.__shown = 0; const look = () => {
                const el = [...document.querySelectorAll('.se-view [data-path]')]
                    .find(e => e.textContent.replace(/[\\s\\u200b]/g, '') === 'y');
                if (el && getComputedStyle(el).visibility === 'visible') window.__shown++;
                if (!window.__stop) requestAnimationFrame(look); }; requestAnimationFrame(look); }""")
            _drag(page, view["x"] + 40, view["y"] + 90, view["x"] + 110, view["y"] + 120)
            assert _wait(lambda: page.locator(".hw-apply").is_visible(), 15)
            page.evaluate("window.__stop = true")
            assert page.evaluate("window.__shown") == 0
            assert page.evaluate(hidden)["y"] == "hidden"
            # the place is kept: the formula did not close up around the hole
            assert page.evaluate("document.querySelector('.se-view .katex').getBoundingClientRect().width") >= width - 1
            page.locator(pen).click()                             # the pen down takes the ink: back
            assert _wait(lambda: page.evaluate(hidden) == {"y": "visible", "box": True})
            page.locator(pen).click()                                      # the pen on again: hidden again
            _drag(page, view["x"] + 40, view["y"] + 90, view["x"] + 110, view["y"] + 120)
            page.locator('[data-cmd="addon:handwriting:clear"]').click()   # the ink discarded...
            assert page.evaluate(hidden)["y"] == "hidden"                  # ...the pen still on
            page.locator(pen).click()                                      # and put away: back
            assert _wait(lambda: page.evaluate(hidden) == {"y": "visible", "box": True})
            # written again and applied: replaced, never shown again
            page.locator(pen).click()
            _drag(page, view["x"] + 40, view["y"] + 90, view["x"] + 110, view["y"] + 120)
            apply = page.locator(".hw-apply")
            assert _wait(lambda: apply.is_visible() and not apply.is_disabled(), 15)
            apply.click()
            assert _wait(lambda: str(doc.expr) == "x + z", 15), str(doc.expr)
            assert _wait(lambda: page.evaluate("document.querySelectorAll('.se-view .hw-covered').length") == 0), \
                page.evaluate("[...document.querySelectorAll('.se-view .hw-covered')].map(e => e.textContent)")
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_writing_at_the_edge_of_the_screen_scrolls_the_room_into_sight():
    """A stroke that ends by the right edge of the view opens space ahead of
    it and scrolls: the blue box to write in is in sight again, the stroke
    too, and there is room to go on writing."""
    doc = Document(x + y, addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            page.set_viewport_size({"width": 420, "height": 800})
            r = page.evaluate(TEXT_RECT, "y")
            page.mouse.click((r["left"] + r["right"]) / 2, (r["top"] + r["bottom"]) / 2)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            view = page.locator(".se-view").bounding_box()
            assert page.evaluate("document.querySelector('.se-view').scrollLeft") == 0
            r = page.evaluate(TEXT_RECT, "y")
            y0 = (r["top"] + r["bottom"]) / 2
            # from beside y to a few pixels short of the view's right edge
            _drag(page, r["right"] + 10, y0, view["x"] + view["width"] - 6, y0 + 10)
            page.wait_for_function("document.querySelector('.se-view').scrollLeft > 20", timeout=5000)
            page.wait_for_timeout(600)                                   # the smooth scroll settles
            shown = page.evaluate("""() => {
                const v = document.querySelector('.se-view').getBoundingClientRect();
                return {left: v.left, right: v.right};
            }""")
            # what is left of the view on the right is space to write in
            ink_right = view["x"] + view["width"] - 6 - page.evaluate("document.querySelector('.se-view').scrollLeft")
            assert shown["right"] - ink_right > 0.2 * view["width"], (shown, ink_right)
            assert ink_right > shown["left"]                              # the stroke itself still in sight
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_free_writing_at_the_edge_of_the_view_is_given_space_to_go_on():
    """With nothing selected and no cursor there is no room to grow, and a
    stroke that reached the edge of the view had nowhere to continue.  The
    view is given space past the ink - to the right it scrolls there, down
    it grows - without the formula moving; and the space goes with the ink."""
    doc = Document(x + y, addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            page.set_viewport_size({"width": 420, "height": 800})
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            assert page.locator(".se-view .se-selected").count() == 0 and page.locator(".se-caret").count() == 0
            page.wait_for_timeout(300)                                   # the view settles at its writing height
            geo = lambda: page.evaluate("""() => {
                const v = document.querySelector('.se-view'), r = v.getBoundingClientRect();
                const g = [...v.querySelectorAll('[data-path]')].find(e => e.textContent.replace(/[\\s\\u200b]/g, '') === 'x').getBoundingClientRect();
                return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, height: r.height,
                        scrollLeft: v.scrollLeft, scrollWidth: v.scrollWidth, clientWidth: v.clientWidth,
                        glyph: [g.left + v.scrollLeft, g.top - r.top]};
            }""")
            before = geo()
            assert before["scrollLeft"] == 0 and before["scrollWidth"] <= before["clientWidth"] + 1
            # a stroke under the formula, ending a few pixels short of the right edge
            y0 = before["bottom"] - 50
            _drag(page, before["right"] - 80, y0, before["right"] - 6, y0 + 8)
            page.wait_for_function("document.querySelector('.se-view').scrollLeft > 20", timeout=5000)
            page.wait_for_timeout(600)                                   # the smooth scroll settles
            after = geo()
            ink_right = before["right"] - 6 - after["scrollLeft"]
            assert after["right"] - ink_right > 0.2 * (after["right"] - after["left"]), (after, ink_right)   # space ahead
            assert ink_right > after["left"]                             # the stroke still in sight
            assert abs(after["glyph"][0] - before["glyph"][0]) < 1, (before, after)    # the formula did not move
            assert abs(after["glyph"][1] - before["glyph"][1]) < 1, (before, after)
            assert page.locator("[data-strokes]").first.get_attribute("data-strokes") == "1"
            # a stroke by the bottom edge: the view grows under it
            _drag(page, after["left"] + 60, after["bottom"] - 30, after["left"] + 90, after["bottom"] - 5)
            assert _wait(lambda: geo()["height"] > after["height"] + 30), (after, geo())
            page.wait_for_timeout(300)
            taller = geo()
            assert abs(taller["glyph"][1] - before["glyph"][1]) < 1, (before, taller)
            # the ink gone, the space goes with it
            page.locator('[data-cmd="addon:handwriting:clear"]').click()
            assert _wait(lambda: geo()["scrollWidth"] <= geo()["clientWidth"] + 1), geo()
            assert _wait(lambda: abs(geo()["height"] - before["height"]) < 2), (before, geo())
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_a_room_opened_past_the_edge_is_scrolled_towards_the_middle():
    """The space to write in opens beside the selection or at the cursor; when
    that is past the edge of the view, the view scrolls to bring the blue box
    towards the middle - for a selection, and for a cursor."""
    from sympy import Add, Symbol
    doc = Document(Add(*[Symbol(f"a{i}") for i in range(30)]), addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    box = """() => {
        const v = document.querySelector('.se-view').getBoundingClientRect();
        return {left: v.left, right: v.right, scroll: document.querySelector('.se-view').scrollLeft,
                wide: document.querySelector('.se-view').scrollWidth};
    }"""
    pen = '[data-cmd="addon:handwriting:pen"]'
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            page.set_viewport_size({"width": 420, "height": 800})
            v = page.evaluate(box)
            assert v["wide"] > 2 * (v["right"] - v["left"]) and v["scroll"] == 0      # a formula past the edge
            # a selection past the right edge: taking the pen brings its room in
            last = sorted(doc.expr.args, key=str)[-1]
            ed = "document.querySelector('.sympy-editor').__sympyEditor"
            path = page.evaluate(f"Object.keys({ed}.state.nodes).find(k => {ed}.state.nodes[k].src === '{last}')")
            page.evaluate(f"{ed}.select('{path}')")
            page.locator(pen).click()
            page.wait_for_function("document.querySelector('.se-view').scrollLeft > 50", timeout=5000)
            page.wait_for_timeout(1000)
            # and it settles: the editor reports the selection after every
            # scroll, and reopening the room at each report kept it scrolling
            page.evaluate("""() => { window.__scrolls = 0; document.querySelector('.se-view')
                .addEventListener('scroll', () => window.__scrolls++); }""")
            page.wait_for_timeout(500)
            assert page.evaluate("window.__scrolls") == 0
            room = page.evaluate("""() => { const el = document.querySelector('.se-view .se-selected')
                || [...document.querySelectorAll('.se-view [data-path]')].find(e => e.style.marginRight);
                const q = el.getBoundingClientRect(); return {right: q.right, margin: parseFloat(el.style.marginRight) || 0}; }""")
            v = page.evaluate(box)
            centre = room["right"] + room["margin"] / 2                  # the middle of the blue box
            assert v["left"] < centre < v["right"], (room, v)
            assert abs(centre - (v["left"] + v["right"]) / 2) < 0.3 * (v["right"] - v["left"]), (room, v)
            # a cursor at the start, the view scrolled to the end: brought back
            page.locator(pen).click()
            page.evaluate(f"{ed}.select(null)")
            page.evaluate("document.querySelector('.se-view').scrollLeft = 1e6")
            page.locator(".se-view").focus()
            page.keyboard.press("ArrowLeft")                                # a caret at the first position
            assert _wait(lambda: page.evaluate(f"!!{ed}.caret"))
            page.locator(pen).click()
            page.wait_for_function("document.querySelector('.se-view').scrollLeft < 100", timeout=5000)
            # a cursor at the end, the view at the start: scrolled there, and
            # the cursor and the room are still there once it has (the scroll
            # used to take the cursor away, and the room with it)
            page.locator(pen).click()
            page.evaluate("document.querySelector('.se-view').scrollLeft = 0")
            page.evaluate(f"{ed}._hideCaret(); {ed}.select(null)")
            page.locator(".se-view").focus()
            page.keyboard.press("ArrowRight")                               # a caret at the last position
            assert _wait(lambda: page.evaluate(f"!!{ed}.caret"))
            page.locator(pen).click()
            page.wait_for_function("document.querySelector('.se-view').scrollLeft > 50", timeout=5000)
            page.wait_for_timeout(1000)
            assert page.evaluate(f"!!{ed}.caret")
            room = page.evaluate("""() => { const el = [...document.querySelectorAll('.se-view [data-path]')]
                .find(e => e.style.marginRight); if (!el) return null; const q = el.getBoundingClientRect();
                return {left: q.right, right: q.right + parseFloat(el.style.marginRight)}; }""")
            v = page.evaluate(box)
            assert room and v["left"] <= room["left"] and room["right"] <= v["right"] + 1, (room, v)
            assert page.errors == []
        finally:
            _close(srv, browser)


@pytest.mark.parametrize("src", ["a + b", "a - 2*b", "Mul(2, 3, evaluate=False)", "a & b", "a | b",
                                 "Eq(x, y)"])
def test_the_room_opens_where_the_caret_is_drawn(src):
    """Bug: with the cursor after the + of a + b, the box to write in opened
    before the + (a margin on a), though the reading went after it.  The
    room opens on the side of the operator the caret is drawn on - for every
    caret beside an operator glyph."""
    from sympy import sympify
    doc = Document(sympify(src), addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    ed = "document.querySelector('.sympy-editor').__sympyEditor"
    pen = '[data-cmd="addon:handwriting:pen"]'
    # every caret position that touches an operator glyph, and on which side
    beside = """() => { const ed = %s, out = [];
        const ops = [...document.querySelectorAll('.se-view .mbin, .se-view .mrel')]
            .map(o => o.getBoundingClientRect()).filter(r => r.width);
        ed._caretPositions().forEach((p, k) => ops.forEach(o => {
            if (Math.abs(p.x - o.right) < 6) out.push({k, after: true});
            else if (Math.abs(p.x - o.left) < 6) out.push({k, after: false});
        }));
        return out; }""" % ed
    measure = """() => {
        const el = [...document.querySelectorAll('.se-view [data-path]')].find(e => e.style.marginLeft || e.style.marginRight);
        if (!el) return null;
        const q = el.getBoundingClientRect(), ml = parseFloat(el.style.marginLeft) || 0;
        return ml ? {left: q.left - ml, right: q.left} : {left: q.right, right: q.right + parseFloat(el.style.marginRight)};
    }"""
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            spots = page.evaluate(beside)
            assert any(s["after"] for s in spots), (src, spots)
            for spot in spots:
                page.evaluate(f"""() => {{ const ed = {ed}; ed.select(null);
                    const p = ed._caretPositions()[{spot["k"]}]; ed._showCaret(p.gap, p.x); }}""")
                assert _wait(lambda: page.evaluate(f"!!{ed}.caret"))
                page.locator(pen).click()
                assert _wait(lambda: page.evaluate(measure)), (src, spot)
                room = page.evaluate(measure)
                # the room's margin moves the glyphs after it: the operators
                # where they are now, one of them beside the room
                glyph = page.evaluate("""() => [...document.querySelectorAll('.se-view .mbin, .se-view .mrel')]
                    .map(o => o.getBoundingClientRect()).filter(r => r.width)
                    .map(o => ({left: o.left, right: o.right}))""")
                if spot["after"]:
                    assert any(abs(g["right"] - room["left"]) < 8 for g in glyph), (src, spot, room, glyph)
                else:
                    assert any(abs(g["left"] - room["right"]) < 8 for g in glyph), (src, spot, room, glyph)
                page.locator(pen).click()                       # the pen off: the room closes
                assert _wait(lambda: not page.evaluate(measure))
            assert page.errors == []
        finally:
            _close(srv, browser)


class LeqRecognizer(FakeRecognizer):
    """Reads anything as a less-or-equal sign (and a letter after it)."""

    def readings(self):
        return [r"\leq", "x"]


def test_writing_over_a_selected_operator_replaces_it():
    """Bug: with the = of an equation selected, the pen asked which piece to
    read the ink with.  A selected operator is where the ink goes: no piece
    to read it with, the ink read as an operator, the = hidden meanwhile, and
    Apply puts the new relation in its place."""
    from sympy import Eq, Le
    doc = Document(Eq(x, y), addons=[HandwritingAddon(LeqRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            eq = page.evaluate("""() => { for (const el of document.querySelectorAll('.se-view *')) {
                if (el.querySelector('[data-path]')) continue;
                if ((el.textContent || '').trim() === '=') { const r = el.getBoundingClientRect();
                    return [r.left + r.width / 2, r.top + r.height / 2]; } } return null; }""")
            page.mouse.click(eq[0], eq[1])
            ed = "document.querySelector('.sympy-editor').__sympyEditor"
            assert _wait(lambda: page.evaluate(f"!!{ed}.junction"))
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            assert _wait(lambda: page.evaluate(f"getComputedStyle({ed}.junction.el).visibility") == "hidden")
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 40, view["y"] + 90, view["x"] + 70, view["y"] + 110)
            apply = page.locator(".hw-apply")
            ok = _wait(lambda: apply.is_visible() and not apply.is_disabled(), 15)
            assert ok, (page.locator(".hw-panel").inner_text(), page.evaluate(f"!!{ed}.junction"),
                        page.locator(".hw-panel").get_attribute("data-aim"))
            assert page.locator(".hw-with-option").count() == 0            # no piece to read it with
            assert page.locator(".hw-panel").get_attribute("data-aim") == "operator"
            assert "operator's place" in page.locator(".hw-reading-of").inner_text()
            assert page.locator(".hw-src").inner_text() == "<="
            assert page.locator(".hw-cand").count() == 1                   # "x" is no operator
            assert str(doc.expr) == "Eq(x, y)"                               # nothing touched yet
            apply.click()
            assert _wait(lambda: doc.expr == Le(x, y), 15), str(doc.expr)
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_keep_scrolls_back_to_the_formula():
    """After Keep, the page goes back up from the strip to the formula pad."""
    doc = Document(x + y, addons=[HandwritingAddon(LetterRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            page.set_viewport_size({"width": 420, "height": 420})
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 40, view["y"] + 60, view["x"] + 100, view["y"] + 90)
            apply = page.locator(".hw-apply")
            apply.scroll_into_view_if_needed(timeout=15000)
            assert _wait(lambda: apply.is_visible() and not apply.is_disabled(), 15)
            apply.click()
            keep = page.locator(".hw-keep")
            assert _wait(lambda: keep.is_visible(), 15)
            # a page that goes on below the editor (the phone's does): the strip
            # going away must not be what brings the formula back
            page.evaluate("document.body.appendChild(Object.assign(document.createElement('div'), {style: 'height: 2000px'}))")
            keep.scroll_into_view_if_needed()
            in_view = """() => { const r = document.querySelector('.se-view').getBoundingClientRect();
                return r.top >= -1 && r.top < window.innerHeight - 40; }"""
            assert not page.evaluate(in_view)
            keep.click()
            page.wait_for_function(in_view, timeout=5000)
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_pen_button_pulses_in_one_colour_while_writing():
    """Writing mode is plain at a glance: the Pen's button pulses in one colour
    - no gradient - while it is on, and only the Pen's, only then; "the
    readings" pulses the same way while it waits to be pressed."""
    doc = Document(x + y, addons=[HandwritingAddon(LetterRecognizer()), LATEX])
    pen = '[data-cmd="addon:handwriting:pen"]'
    look = """(sel) => { const cs = getComputedStyle(document.querySelector(sel));
        return {name: cs.animationName, image: cs.backgroundImage, colour: cs.backgroundColor}; }"""
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            assert page.evaluate(look, pen)["name"] == "none"
            page.locator(pen).click()
            on = page.evaluate(look, pen)
            assert on["name"] == "hw-signal" and on["image"] == "none"         # one colour, no gradient
            colours = set()
            for _ in range(8):
                colours.add(page.evaluate(look, pen)["colour"])
                page.wait_for_timeout(120)
            assert len(colours) > 2                                          # it fades on and off
            assert all(c.replace(" ", "").startswith("rgba(15,118,110") for c in colours), colours
            erase = '[data-cmd="addon:handwriting:erase"]'
            page.locator(erase).click()
            assert page.evaluate(look, erase)["name"] == "none"             # the eraser: plainly pressed
            page.locator(erase).click()
            # "the readings": the same pulse while it waits (the strip out of sight)
            page.set_viewport_size({"width": 900, "height": 430})
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 40, view["y"] + 60, view["x"] + 100, view["y"] + 90)
            down = page.locator(".hw-down")
            page.wait_for_function("document.querySelector('.hw-down').classList.contains('hw-down-on')", timeout=15000)
            assert "hw-signal" in page.evaluate(look, ".hw-down")["name"]
            # opaque and white: the page never shows through it, pulse or not
            import re as _re
            for _ in range(8):
                colour = page.evaluate(look, ".hw-down")["colour"]
                parts = [float(v) for v in _re.findall(r"-?[\d.]+(?:e-?\d+)?", colour)]
                if colour.startswith("oklab"):                            # the mix, as Chromium reports it
                    assert "/" not in colour and parts[0] > 0.85, colour  # opaque, and light
                else:
                    assert len(parts) == 3 or parts[3] == 1, colour       # rgb(...), or rgba(..., 1)
                    assert min(parts[:3]) > 170, colour                   # white, a teal tint at most
                page.wait_for_timeout(120)
            page.locator(pen).click()
            assert page.evaluate(look, pen)["name"] == "none"               # off again: plain
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_pieces_to_read_with_are_typeset():
    """The pieces offered after "What is written goes with:" are drawn as the
    formula draws them - typeset - not as their SymPy source."""
    from sympy import sin
    rec = LetterRecognizer()
    rec.latex = r"\Delta^{2}"
    doc = Document(sin(x), addons=[HandwritingAddon(rec), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            r = page.evaluate(TEXT_RECT, "sin(x)")
            ht = r["bottom"] - r["top"]
            _drag(page, r["right"] + 3, r["top"] - 0.3 * ht, r["right"] + 13, r["top"] + 0.15 * ht)
            pieces = page.locator(".hw-with-option[data-path]")
            assert _wait(lambda: pieces.count() >= 1, 15)
            assert _wait(lambda: all(pieces.nth(i).locator(".katex").count() == 1 for i in range(pieces.count())), 10)
            texs = [pieces.nth(i).get_attribute("data-tex") for i in range(pieces.count())]
            assert any("\\sin" in t for t in texs), texs
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_what_is_written_over_a_selection_takes_its_place_when_applied():
    """A piece selected in the editor, then written on: the reading waits to be
    applied - the formula is not touched until then - and takes that piece's
    place when it is, with the strip to keep the change or take it back."""
    doc = Document(x + y, addons=[HandwritingAddon(LetterRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            r = page.evaluate(TEXT_RECT, "y")
            page.mouse.click((r["left"] + r["right"]) / 2, (r["top"] + r["bottom"]) / 2)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 40, view["y"] + 90, view["x"] + 110, view["y"] + 120)
            apply = page.locator(".hw-apply")
            assert _wait(lambda: apply.is_visible() and not apply.is_disabled(), 15)
            assert str(doc.expr) == "x + y"                  # read, and nothing touched yet
            assert page.locator(".hw-applied").is_hidden()
            apply.click()
            assert _wait(lambda: str(doc.expr) == "x + z", 15), str(doc.expr)
            assert _wait(lambda: apply.is_hidden())          # in: there is nothing left to apply
            said = page.locator(".hw-reading-of")
            assert _wait(lambda: "selection's place" in said.inner_text())
            # the line under the readings is the reading itself, as SymPy gets it
            assert page.locator(".hw-src").inner_text() == "z"
            # the ink has gone off the formula: the formula itself says it now
            assert page.locator(".hw-panel").get_attribute("data-strokes") == "0"
            # and the strip: from x + y (what went, red) to x + z (what came, green)
            strip = page.locator(".hw-applied")
            assert not strip.is_hidden()
            assert strip.locator(".hw-was").get_attribute("data-latex") == "x + y"
            assert strip.locator(".hw-now").get_attribute("data-latex") == "x + z"
            assert strip.locator(".hw-was .rep-removed").count() >= 1
            assert strip.locator(".hw-now .rep-added").count() >= 1
            # Undo the change: the formula as it was, and the strip goes
            page.locator(".hw-back").click()
            assert _wait(lambda: str(doc.expr) == "x + y", 15), str(doc.expr)
            assert _wait(lambda: strip.is_hidden())
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_free_ink_is_read_together_with_the_piece_it_is_written_by():
    """With nothing selected, ink written at a piece's top-right corner is read
    together with that piece - the stand-in carries it into the strokes - and
    comes back as its exponent.  The pieces it can be read with are offered,
    and `alone`: the ink by itself, the piece kept."""
    rec = LetterRecognizer()
    rec.latex = r"\Delta^{2}"
    doc = Document(x, addons=[HandwritingAddon(rec), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            r = page.evaluate(TEXT_RECT, "x")
            ht = r["bottom"] - r["top"]
            _drag(page, r["right"] + 3, r["top"] - 0.3 * ht, r["right"] + 13, r["top"] + 0.15 * ht)
            assert _wait(lambda: page.locator(".hw-apply").is_visible(), 15)
            page.locator(".hw-apply").click()
            assert _wait(lambda: str(doc.expr) == "x**2", 15), str(doc.expr)
            # the model was sent the piece as a stand-in: a stroke more than was written
            assert len(rec.last) == 2
            said = page.locator(".hw-reading-of")
            assert "together with" in said.inner_text()
            options = page.locator(".hw-with-option")
            assert _wait(lambda: options.count() >= 2)
            assert page.locator(".hw-alone").count() == 1
            # alone: the ink by itself, after the formula - and the piece is not lost
            rec.latex = "y"
            page.locator(".hw-alone").click()
            assert _wait(lambda: str(doc.expr) == "x*y", 15), str(doc.expr)
            assert page.locator(".hw-alone").get_attribute("aria-pressed") == "true"
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_another_reading_changes_the_formula_instead_of_piling_up():
    """The best reading goes in at once; picking another takes the first back
    and puts that one in - never both."""
    doc = Document(x, addons=[HandwritingAddon(TwoRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 200, view["y"] + 60, view["x"] + 260, view["y"] + 100)
            assert _wait(lambda: page.locator(".hw-apply").is_visible(), 15)
            page.locator(".hw-apply").click()
            assert _wait(lambda: str(doc.expr) == "x*y", 15), str(doc.expr)
            cands = page.locator(".hw-cand")
            assert _wait(lambda: cands.count() == 2)
            assert cands.nth(0).get_attribute("aria-selected") == "true"
            cands.nth(1).click()
            assert _wait(lambda: str(doc.expr) == "x*z", 15), str(doc.expr)
            assert cands.nth(1).get_attribute("aria-selected") == "true"
            # Keep: the strip goes, the formula stays as it is
            page.locator(".hw-keep").click()
            assert _wait(lambda: page.locator(".hw-applied").is_hidden())
            assert str(doc.expr) == "x*z"
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_eraser_takes_strokes_away_and_clear_takes_them_all():
    doc = Document(x, addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            panel = page.locator(".hw-panel")
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 200, view["y"] + 40, view["x"] + 240, view["y"] + 70)
            _drag(page, view["x"] + 300, view["y"] + 40, view["x"] + 340, view["y"] + 70)
            assert _wait(lambda: panel.get_attribute("data-strokes") == "2")
            assert _wait(lambda: "Nothing could be read" in page.locator(".hw-note").inner_text(), 15)
            page.locator('[data-cmd="addon:handwriting:erase"]').click()
            _drag(page, view["x"] + 195, view["y"] + 55, view["x"] + 245, view["y"] + 55)
            assert _wait(lambda: panel.get_attribute("data-strokes") == "1")
            assert str(doc.expr) == "x"
            page.locator('[data-cmd="addon:handwriting:clear"]').click()
            assert _wait(lambda: panel.get_attribute("data-strokes") == "0")
            assert page.locator('[data-cmd="addon:handwriting:clear"]').is_disabled()
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_tools_are_icons_and_the_guide_explains_them():
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            tools = page.locator(".se-tools .hw-tool")
            assert tools.count() == 5
            for i in range(5):
                assert tools.nth(i).inner_text().strip() == ""
                icon = tools.nth(i).locator("svg")
                assert icon.count() == 1
                assert icon.bounding_box()["width"] >= 12, i        # drawn, not a button of nothing
                assert tools.nth(i).get_attribute("aria-label")
            page.locator('[data-cmd="addon:handwriting:pen"]').click()      # the strip, with its "?"
            page.locator(".hw-help").click()
            guide = page.locator(".se-help-view")
            for word in ("Write", "Erase", "Clear ink", "Read with", "Keep", "Undo the change",
                         "LaTeX", "zoom"):
                assert word in guide.inner_text(), word
            page.keyboard.press("Escape")
            assert _wait(lambda: page.locator(".se-help-view").count() == 0)
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_a_stroke_is_taken_back_and_written_again():
    """Undo and Redo, among the tools, are of the ink: they take back the last
    stroke written and put it again, and reading follows the ink."""
    doc = Document(x, addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            panel = page.locator(".hw-panel")
            undo = page.locator('[data-cmd="addon:handwriting:undo"]')
            redo = page.locator('[data-cmd="addon:handwriting:redo"]')
            assert undo.is_disabled() and redo.is_disabled()
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 200, view["y"] + 40, view["x"] + 240, view["y"] + 70)
            _drag(page, view["x"] + 300, view["y"] + 40, view["x"] + 340, view["y"] + 70)
            assert _wait(lambda: panel.get_attribute("data-strokes") == "2")
            assert not undo.is_disabled() and redo.is_disabled()
            undo.click()
            assert _wait(lambda: panel.get_attribute("data-strokes") == "1")
            assert not redo.is_disabled()
            redo.click()
            assert _wait(lambda: panel.get_attribute("data-strokes") == "2")
            assert redo.is_disabled()
            # back to nothing: the ink is gone and so is what was said of it
            undo.click()
            undo.click()
            assert _wait(lambda: panel.get_attribute("data-strokes") == "0")
            assert undo.is_disabled()
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_erasing_and_clearing_are_steps_undo_takes_back():
    """Undo and Redo walk the ink's own history: a stroke written, one sweep
    of the eraser (however many strokes it takes) and Clear ink are each a
    step.  They used to follow the strokes written alone, so an erasure or
    a Clear could not be taken back."""
    doc = Document(x, addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            panel = page.locator(".hw-panel")
            undo = page.locator('[data-cmd="addon:handwriting:undo"]')
            redo = page.locator('[data-cmd="addon:handwriting:redo"]')
            erase = page.locator('[data-cmd="addon:handwriting:erase"]')
            clear = page.locator('[data-cmd="addon:handwriting:clear"]')
            strokes = lambda: panel.get_attribute("data-strokes")
            view = page.locator(".se-view").bounding_box()
            X, Y = view["x"], view["y"]
            for k in range(3):
                _drag(page, X + 150 + 70 * k, Y + 40, X + 190 + 70 * k, Y + 70)
            assert _wait(lambda: strokes() == "3")
            # one sweep over two strokes: one step
            erase.click()
            _drag(page, X + 150, Y + 55, X + 260, Y + 55, steps=24)
            assert _wait(lambda: strokes() == "1")
            erase.click()                                     # back to writing
            undo.click()
            assert _wait(lambda: strokes() == "3")            # both come back at once
            redo.click()
            assert _wait(lambda: strokes() == "1")            # and go again
            # Clear ink is a step too
            clear.click()
            assert _wait(lambda: strokes() == "0")
            assert not undo.is_disabled() and redo.is_disabled()
            undo.click()
            assert _wait(lambda: strokes() == "1")
            undo.click()
            assert _wait(lambda: strokes() == "3")            # before the erasure
            undo.click()
            assert _wait(lambda: strokes() == "2")            # and the strokes, one by one
            redo.click(); redo.click(); redo.click()
            assert _wait(lambda: strokes() == "0") and redo.is_disabled()
            # something new done: what was taken back cannot come again
            undo.click()
            assert _wait(lambda: strokes() == "1")
            _drag(page, X + 400, Y + 40, X + 430, Y + 70)
            assert _wait(lambda: strokes() == "2") and redo.is_disabled()
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_a_reading_s_latex_can_be_corrected_by_hand():
    """The model read a glyph wrong: the reading's own LaTeX is opened,
    corrected, and what is typed goes into the formula like any reading -
    and stays among them."""
    doc = Document(x, addons=[HandwritingAddon(LetterRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 200, view["y"] + 60, view["x"] + 260, view["y"] + 100)
            assert _wait(lambda: page.locator(".hw-apply").is_visible(), 15)
            assert page.locator(".hw-latexrow").is_hidden()
            page.locator(".hw-edit").click()
            field = page.locator(".hw-latex")
            assert _wait(lambda: field.is_visible())
            assert field.input_value() == "z"
            field.fill(r"\frac{w}{2}")
            field.press("Enter")
            assert _wait(lambda: page.locator(".hw-src").inner_text() == "w/2", 15)
            page.locator(".hw-apply").click()
            assert _wait(lambda: str(doc.expr) == "w*x/2", 15), str(doc.expr)
            # it is one of the readings now, and the one picked
            edited = page.locator(".hw-cand-edited")
            assert _wait(lambda: edited.count() == 1)
            assert edited.get_attribute("aria-selected") == "true"
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_formula_zooms_while_writing_and_the_ink_zooms_with_it():
    """The zoom buttons work with the Pen on, and the strokes are magnified
    with the formula they were written on."""
    doc = Document(x, addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 200, view["y"] + 40, view["x"] + 260, view["y"] + 90)
            assert _wait(lambda: page.locator(".hw-panel").get_attribute("data-strokes") == "1")
            ink = "() => { const c = document.querySelector('.hw-ink'); const x = c.getContext('2d');" \
                  " const d = x.getImageData(0, 0, c.width, c.height).data;" \
                  " let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 40) n++; return n; }"
            was = page.evaluate(ink)
            assert was > 0
            page.locator('[data-cmd="zoomin"]').click()
            page.locator('[data-cmd="zoomin"]').click()
            assert _wait(lambda: page.evaluate(ink) > was * 1.2, 5)     # the ink grew with the formula
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_formula_opens_a_space_to_write_in_and_widens_it():
    """With the Pen on, the formula opens a space where what is written will
    go - after the selection - and it widens as the ink does; it closes with
    the Pen."""
    doc = Document(x + y, addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            r = page.evaluate(TEXT_RECT, "x")
            page.mouse.click((r["left"] + r["right"]) / 2, (r["top"] + r["bottom"]) / 2)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            gap = ("(path) => parseFloat(getComputedStyle("
                   "document.querySelector(`.se-view [data-path=\"${path}\"]`)).marginRight) || 0")
            assert page.evaluate(gap, r["path"]) == 0
            drawn = ("() => { const c = document.querySelector('.hw-ink');"
                     " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;"
                     " let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 20) n++; return n; }")
            assert page.evaluate(drawn) == 0
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            assert _wait(lambda: page.evaluate(gap, r["path"]) > 20)      # room, at once
            # and the room is drawn: a box in dashes, to write inside
            assert _wait(lambda: page.evaluate(drawn) > 0)
            was = page.evaluate(gap, r["path"])
            # written across it: the space is as wide as the ink needs
            view = page.locator(".se-view").bounding_box()
            _drag(page, r["right"] + 20, view["y"] + 40, r["right"] + 20 + 3 * was, view["y"] + 90)
            assert _wait(lambda: page.evaluate(gap, r["path"]) > was * 2)
            # the Pen off: the formula as it was
            page.locator('[data-cmd="addon:handwriting:clear"]').click()
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            assert _wait(lambda: page.evaluate(gap, r["path"]) == 0)
            assert _wait(lambda: page.evaluate(drawn) == 0)               # the box goes with the Pen
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_a_tap_still_selects_while_the_pen_is_on():
    """Choosing where to write is what it always was: with nothing written
    yet, a tap on a piece selects it (and the space opens there) - it is a
    stroke only once there is ink."""
    doc = Document(x + y, addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            panel = page.locator(".hw-panel")
            r = page.evaluate(TEXT_RECT, "y")
            page.mouse.click((r["left"] + r["right"]) / 2, (r["top"] + r["bottom"]) / 2)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            assert panel.get_attribute("data-strokes") == "0"          # a tap, not a dot
            sel = page.evaluate("document.querySelector('.se-view .se-selected').dataset.path")
            assert sel == r["path"], sel
            # and with ink on the formula a tap is a dot of its own
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 300, view["y"] + 40, view["x"] + 340, view["y"] + 70)
            assert _wait(lambda: panel.get_attribute("data-strokes") == "1")
            page.mouse.click(view["x"] + 320, view["y"] + 100)
            assert _wait(lambda: panel.get_attribute("data-strokes") == "2")
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_piece_to_go_with_is_asked_before_the_readings_and_the_parser():
    """The choices come in the order one makes them: which piece of the formula
    what is written goes with, then which reading, then how the LaTeX is read."""
    rec = LetterRecognizer()
    rec.latex = r"\Delta^{2}"
    doc = Document(x, addons=[HandwritingAddon(rec), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            r = page.evaluate(TEXT_RECT, "x")
            ht = r["bottom"] - r["top"]
            _drag(page, r["right"] + 3, r["top"] - 0.3 * ht, r["right"] + 13, r["top"] + 0.15 * ht)
            assert _wait(lambda: page.locator(".hw-with-option").count() >= 2, 15)
            order = page.evaluate("""() => [...document.querySelector('.hw-panel').children]
                .map(e => e.className.split(' ')[0])""")
            assert order.index("hw-with") < order.index("hw-cands"), order
            assert order.index("hw-cands") < order.index("hw-parse"), order
            assert order.index("hw-parse") < order.index("hw-actions"), order
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_a_button_comes_up_to_go_down_to_what_was_read():
    """Writing on a formula that fills the screen, the readings are out of
    sight: a moment after the pen rests a button rises at the foot of the
    screen, and a press goes down to them.  Writing again sends it away."""
    doc = Document(x, addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            page.set_viewport_size({"width": 900, "height": 430})
            down = page.locator(".hw-down")
            seen = """() => { const r = document.querySelector('.hw-panel').getBoundingClientRect();
                      return r.top < innerHeight - 40 && r.bottom > 0; }"""
            assert _wait(lambda: not page.evaluate(seen))       # the strip is below the screen
            assert down.is_hidden()
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 200, view["y"] + 40, view["x"] + 260, view["y"] + 90)
            assert _wait(lambda: down.is_visible(), 6)
            page.wait_for_timeout(250)                          # it rises, then bobs
            down.click()
            assert _wait(lambda: page.evaluate(seen), 5)        # down at the readings
            assert _wait(lambda: down.is_hidden(), 3)
            # writing again: away it goes, and it comes back after the pen rests
            page.evaluate("window.scrollTo(0, 0)")
            _drag(page, view["x"] + 320, view["y"] + 40, view["x"] + 360, view["y"] + 90)
            assert _wait(lambda: down.is_visible(), 6)
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_switching_the_add_on_off_takes_its_strip_and_its_ink_away():
    """The strip under the editor is the add-on's, not a panel the editor puts
    away for it: switching the add-on off has to take it, and the ink layer
    over the formula, off the page."""
    doc = Document(x, addons=[HandwritingAddon(FakeRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            assert page.locator(".hw-panel").count() == 1 and page.locator(".hw-ink").count() == 1
            assert page.locator('[data-cmd="addon:handwriting:pen"]').count() == 1
            page.locator('[data-cmd="drawer"]').click()
            page.locator('.se-drawer-entry[data-sheet="addons"]').click()     # the Add-ons window
            switch = page.locator('.se-sheet-view .se-addon-row input[id*="handwriting"]')
            assert _wait(lambda: switch.count() == 1)
            switch.uncheck()
            assert _wait(lambda: page.locator(".hw-panel").count() == 0, 15)
            assert page.locator(".hw-ink").count() == 0
            assert page.locator('[data-cmd="addon:handwriting:pen"]').count() == 0
            assert page.locator(".sympy-editor.se-inking").count() == 0
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_applying_over_a_selection_lets_the_selection_go():
    """What was written is in the formula: the piece it replaced is not the
    selection any more, and the space it was written in has closed - the pen
    is not left standing on a piece that has gone."""
    doc = Document(x + y, addons=[HandwritingAddon(LetterRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            r = page.evaluate(TEXT_RECT, "y")
            page.mouse.click((r["left"] + r["right"]) / 2, (r["top"] + r["bottom"]) / 2)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 40, view["y"] + 90, view["x"] + 110, view["y"] + 120)
            assert _wait(lambda: page.locator(".hw-apply").is_visible(), 15)
            page.locator(".hw-apply").click()
            assert _wait(lambda: str(doc.expr) == "x + z", 15), str(doc.expr)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 0)
            gap = ("() => { const el = document.querySelector('.se-view [data-path]');"
                   " return parseFloat(getComputedStyle(el).marginRight) || 0; }")
            assert _wait(lambda: page.evaluate(gap) == 0)
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_space_to_write_in_follows_the_formula():
    """The box is drawn where the piece is, not where it was: scrolling the
    formula sideways carries it along, and so does zooming."""
    doc = Document(x + y, addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            page.set_viewport_size({"width": 420, "height": 700})
            r = page.evaluate(TEXT_RECT, "x")
            page.mouse.click((r["left"] + r["right"]) / 2, (r["top"] + r["bottom"]) / 2)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            # the leftmost column of the canvas that has anything drawn on it
            left = """() => { const c = document.querySelector('.hw-ink');
                const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                for (let x = 0; x < c.width; x++)
                  for (let y = 0; y < c.height; y++)
                    if (d[(y * c.width + x) * 4 + 3] > 20) return x;
                return -1; }"""
            assert _wait(lambda: page.evaluate(left) >= 0, 5)
            was = page.evaluate(left)
            # make the formula overflow, then scroll it: the box goes with it
            page.evaluate("""() => { const v = document.querySelector('.se-view');
                v.style.maxWidth = '120px'; v.scrollLeft = 40; v.dispatchEvent(new Event('scroll')); }""")
            page.wait_for_timeout(300)
            moved = page.evaluate(left)
            assert 0 <= moved < was, (was, moved)        # it went left with the formula (-1: nothing drawn at all)
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_device_s_own_reader_can_be_asked_instead_of_the_model():
    """The add-on offers more than one engine: math-ocr's stroke model, which
    reads mathematics, and whatever the device reads handwriting with - the
    app's own reader (Apple's Vision) or the browser's.  A host engine reads
    in the page and only the reading comes back here, to be read as SymPy."""
    doc = Document(x, addons=[HandwritingAddon(LetterRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            # a host that reads ink, as an app offers one: in place before the
            # page loads, which is when the add-on looks for it
            page.add_init_script("""
                window.SympyEditorApp = Object.assign(window.SympyEditorApp || {}, {
                    recognizeInk: function (token, json) {
                        window.__askedWith = JSON.parse(json);
                        setTimeout(function () {
                            window.SympyEditor.inkRead(token, JSON.stringify(
                                {candidates: [{latex: "w + 2"}, {latex: "w+2"}]}));
                        }, 10);
                    }
                });
            """)
            page.reload()
            page.wait_for_selector(".se-stage .hw-ink", timeout=30000)
            page.wait_for_selector(".se-view [data-path]")
            menu = page.locator(".hw-engine")
            page.locator('[data-cmd="addon:handwriting:pen"]').click()    # the strip, with the chooser in it
            assert _wait(lambda: menu.count() == 1 and menu.is_visible())
            assert [o.strip() for o in menu.locator("option").all_inner_texts()][0].startswith("math-ocr")
            menu.select_option("host")
            assert _wait(lambda: "own reader" in page.locator(".hw-note").inner_text(), 5)

            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 200, view["y"] + 60, view["x"] + 260, view["y"] + 100)
            assert _wait(lambda: page.locator(".hw-cand").count() == 2, 15)
            # the strokes went to the host, and no stand-in was drawn into them
            assert page.evaluate("window.__askedWith.length") == 1
            assert page.locator(".hw-src").inner_text() == "w + 2"
            page.locator(".hw-apply").click()
            assert _wait(lambda: str(doc.expr) == "x*(w + 2)", 15), str(doc.expr)
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_picking_another_reader_reads_the_ink_again_at_once():
    """The ink on the page was read by the reader chosen then: picking another
    reads the same ink again straight away - nothing has to be written anew -
    and picking the first one back does too."""
    doc = Document(x, addons=[HandwritingAddon(LetterRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            page.add_init_script("""
                window.__hostAsked = 0;
                window.SympyEditorApp = Object.assign(window.SympyEditorApp || {}, {
                    recognizeInk: function (token, json) {
                        window.__hostAsked++;
                        setTimeout(function () {
                            window.SympyEditor.inkRead(token, JSON.stringify({candidates: [{latex: "w + 2"}]}));
                        }, 10);
                    }
                });
            """)
            page.reload()
            page.wait_for_selector(".se-stage .hw-ink", timeout=30000)
            page.wait_for_selector(".se-view [data-path]")
            menu = page.locator(".hw-engine")
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            assert _wait(lambda: menu.count() == 1 and menu.is_visible())

            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 200, view["y"] + 60, view["x"] + 260, view["y"] + 100)
            assert _wait(lambda: page.locator(".hw-cand").count() >= 1, 15)
            by_model = page.locator(".hw-src").inner_text()
            assert by_model != "w + 2" and page.evaluate("window.__hostAsked") == 0

            menu.select_option("host")                       # no new stroke: the same ink, the other reader
            assert _wait(lambda: page.locator(".hw-src").inner_text() == "w + 2", 15)
            assert page.evaluate("window.__hostAsked") == 1
            menu.select_option("math-ocr")                   # and back
            assert _wait(lambda: page.locator(".hw-src").inner_text() == by_model, 15)
            assert page.evaluate("window.__hostAsked") == 1
            assert str(doc.expr) == "x"                      # read, not applied
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_writing_tools_stay_off_while_the_pen_is():
    """Everything but the Pen is for writing, so with the Pen off they are all
    off - and they stay off through what the editor does to its toolbar: a tap
    that selects a piece, and a tap on nothing that lets it go, both update
    every button on the strip."""
    doc = Document(x + y, addons=[HandwritingAddon(MuteRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            tools = {name: page.locator('[data-cmd="addon:handwriting:%s"]' % name)
                     for name in ("pen", "erase", "undo", "redo", "clear")}
            off = lambda: all(tools[n].is_disabled() for n in ("erase", "undo", "redo", "clear"))
            assert off() and not tools["pen"].is_disabled()

            r = page.evaluate(TEXT_RECT, "x")
            page.mouse.click((r["left"] + r["right"]) / 2, (r["top"] + r["bottom"]) / 2)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            assert off(), "a tap that selects must not wake the writing tools"

            view = page.locator(".se-view").bounding_box()
            page.mouse.click(view["x"] + 10, view["y"] + view["height"] / 2)   # empty space: the selection goes
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 0)
            assert off(), "nor a tap that lets the selection go"

            # with the Pen on they wake as they should: the eraser at once,
            # the rest once there is ink
            tools["pen"].click()
            assert _wait(lambda: not tools["erase"].is_disabled())
            assert tools["undo"].is_disabled() and tools["clear"].is_disabled()
            _drag(page, view["x"] + 200, view["y"] + 40, view["x"] + 250, view["y"] + 80)
            assert _wait(lambda: not tools["clear"].is_disabled() and not tools["undo"].is_disabled())

            # and the Pen off again puts them all away, and the ink with them
            tools["pen"].click()
            assert _wait(off)
            assert page.locator(".hw-panel").get_attribute("data-strokes") == "0"
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_putting_the_pen_down_clears_the_ink():
    """Android's Back (SympyEditor.back) puts the pen down, as pressing the
    Pen again does, and says it closed something; the pen put away takes
    the ink that was not applied with it, whichever way it went."""
    doc = Document(x + y, addons=[HandwritingAddon(MuteRecognizer())])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 200, view["y"] + 40, view["x"] + 250, view["y"] + 80)
            pen = page.locator('[data-cmd="addon:handwriting:pen"]')
            assert page.locator("[data-pen]").first.get_attribute("data-pen") == "on"
            assert _wait(lambda: page.locator("[data-strokes]").first.get_attribute("data-strokes") == "1")
            assert page.evaluate("SympyEditor.back()") is True
            assert _wait(lambda: page.locator("[data-pen]").first.get_attribute("data-pen") == "off")
            assert page.locator("[data-strokes]").first.get_attribute("data-strokes") == "0"
            assert pen.get_attribute("aria-pressed") in ("false", None)
            assert page.evaluate("SympyEditor.back()") is False
            # the Pen tool itself: on, a stroke, off - the stroke is gone, and
            # it is not there when the pen comes back
            pen.click()
            _drag(page, view["x"] + 200, view["y"] + 40, view["x"] + 250, view["y"] + 80)
            assert _wait(lambda: page.locator("[data-strokes]").first.get_attribute("data-strokes") == "1")
            pen.click()
            assert _wait(lambda: page.locator("[data-pen]").first.get_attribute("data-pen") == "off")
            assert page.locator("[data-strokes]").first.get_attribute("data-strokes") == "0"
            pen.click()
            assert page.locator("[data-strokes]").first.get_attribute("data-strokes") == "0"
            assert page.errors == []
        finally:
            _close(srv, browser)


# ---- pointers: a hand on the screen, a contact taken back ---------------------

class KeptRecognizer(LetterRecognizer):
    """Keeps every ink it was asked to read, in order."""

    def __init__(self):
        self.calls = []

    def recognize(self, strokes, beam=4, limit=5):
        self.calls.append(strokes)
        return super().recognize(strokes, beam=beam, limit=limit)


# A pointer event on the ink layer, as a pen, a finger or the mouse sends it.
FIRE = """(a) => {
  const [type, kind, id, x, y, buttons] = a;
  document.querySelector('.hw-ink').dispatchEvent(new PointerEvent(type, {
    bubbles: true, cancelable: true, pointerId: id, pointerType: kind, clientX: x, clientY: y,
    button: 0, buttons: buttons, isPrimary: true}));
}"""

# A piece of the formula (the smallest whose text is `want`), where the ink's
# own coordinates have it: from the ink layer's corner, the view's scroll added.
IN_INK = """(want) => {
  const els = [...document.querySelectorAll('.se-view [data-path]')]
    .filter(e => e.textContent.replace(/[\\s\\u200b]/g, '') === want);
  const el = els.sort((a, b) => b.getAttribute('data-path').length - a.getAttribute('data-path').length)[0];
  const q = el.getBoundingClientRect(), c = document.querySelector('.hw-ink').getBoundingClientRect();
  const v = document.querySelector('.se-view');
  return {l: q.left - c.left + v.scrollLeft, r: q.right - c.left + v.scrollLeft,
          t: q.top - c.top + v.scrollTop, b: q.bottom - c.top + v.scrollTop, cx: c.left, cy: c.top};
}"""

ED = "document.querySelector('.sympy-editor').__sympyEditor"


def _fire(page, *event):
    page.evaluate(FIRE, list(event))


def _stroke(page, kind, pid, x0, y0, moves=14, dx=3):
    _fire(page, "pointerdown", kind, pid, x0, y0, 1)
    for i in range(1, moves + 1):
        _fire(page, "pointermove", kind, pid, x0 + dx * i, y0, 1)
    _fire(page, "pointerup", kind, pid, x0 + dx * moves, y0, 0)


def test_a_hand_resting_on_the_screen_does_not_break_the_stroke():
    """Bug: a second pointer coming down while a stroke was being written
    took the stroke's place.  The edge of the hand touching the screen as the
    pen wrote cut the stroke where it touched - the rest of it was never
    recorded - and left a stroke of its own where the hand was.  A touch
    while the pen is down, or a moment after it lifts, is the hand; two
    fingers, with no pen about, still zoom."""
    rec = KeptRecognizer()
    doc = Document(x + y, addons=[HandwritingAddon(rec), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            panel = page.locator(".hw-panel")
            box = page.locator(".hw-ink").bounding_box()
            X, Y = box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5
            _stroke(page, "pen", 11, X, Y)                       # ink on the formula: a tap is a dot from now on
            assert _wait(lambda: panel.get_attribute("data-strokes") == "1")
            # the second stroke, and the hand comes down half way through it
            _fire(page, "pointerdown", "pen", 11, X, Y + 20, 1)
            for i in range(1, 8):
                _fire(page, "pointermove", "pen", 11, X + 3 * i, Y + 20, 1)
            _fire(page, "pointerdown", "touch", 22, X + 150, Y + 40, 1)
            for i in range(8, 15):
                _fire(page, "pointermove", "pen", 11, X + 3 * i, Y + 20, 1)
                _fire(page, "pointermove", "touch", 22, X + 150 + i, Y + 40, 1)
            _fire(page, "pointerup", "pen", 11, X + 42, Y + 20, 0)
            # the hand lifts after the pen, and touches once more as it goes
            _fire(page, "pointerup", "touch", 22, X + 165, Y + 40, 0)
            _fire(page, "pointerdown", "touch", 23, X + 140, Y + 60, 1)
            _fire(page, "pointerup", "touch", 23, X + 140, Y + 60, 0)
            assert _wait(lambda: len(rec.calls) > 0 and len(rec.calls[-1]) >= 2, 15)
            page.wait_for_timeout(900)                           # anything more would have been read by now
            ink = [s for s in rec.calls[-1] if len(s) != 0][-2:]
            assert panel.get_attribute("data-strokes") == "2"
            assert [len(s) for s in ink] == [15, 15], [len(s) for s in rec.calls[-1]]
            left = X - box["x"]
            assert abs(ink[1][0][0] - left) < 1 and abs(ink[1][-1][0] - (left + 42)) < 1     # the whole of it
            assert all(p[0] < left + 60 for s in ink for p in s)                              # and none of the hand
            # two fingers, the pen long gone: the formula is zoomed, and nothing is written
            zoom = page.evaluate(ED + ".zoom")
            _fire(page, "pointerdown", "touch", 31, X - 100, Y + 40, 1)
            _fire(page, "pointerdown", "touch", 32, X, Y + 40, 1)
            for i in range(1, 9):
                _fire(page, "pointermove", "touch", 32, X + 12 * i, Y + 40, 1)
            _fire(page, "pointerup", "touch", 32, X + 96, Y + 40, 0)
            _fire(page, "pointerup", "touch", 31, X - 100, Y + 40, 0)
            assert page.evaluate(ED + ".zoom") > zoom * 1.3
            assert panel.get_attribute("data-strokes") == "2"
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_a_pen_takes_over_from_the_hand_that_came_down_first():
    """The hand is often on the screen before the pen: its touch begins a
    stroke, and the pen coming down takes over - the stroke is the pen's,
    whole, and nothing of the hand's is kept.  Any other pointer coming down
    while a stroke is written waits for it to end."""
    rec = KeptRecognizer()
    doc = Document(x + y, addons=[HandwritingAddon(rec), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            panel = page.locator(".hw-panel")
            box = page.locator(".hw-ink").bounding_box()
            X, Y = box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5
            _stroke(page, "pen", 11, X, Y)
            page.wait_for_timeout(700)                           # the hand's moment after that stroke is over
            _fire(page, "pointerdown", "touch", 22, X + 150, Y + 40, 1)
            _fire(page, "pointermove", "touch", 22, X + 152, Y + 41, 1)
            _fire(page, "pointerdown", "pen", 11, X, Y + 20, 1)
            for i in range(1, 15):
                _fire(page, "pointermove", "pen", 11, X + 3 * i, Y + 20, 1)
                _fire(page, "pointermove", "touch", 22, X + 152 + i, Y + 41, 1)
                if i == 7:                                       # and a mouse button pressed in the middle of it
                    _fire(page, "pointerdown", "mouse", 1, X + 200, Y, 1)
                    _fire(page, "pointerup", "mouse", 1, X + 200, Y, 0)
            _fire(page, "pointerup", "pen", 11, X + 42, Y + 20, 0)
            _fire(page, "pointerup", "touch", 22, X + 166, Y + 41, 0)
            assert _wait(lambda: len(rec.calls) > 0 and len(rec.calls[-1]) >= 2, 15)
            page.wait_for_timeout(900)
            assert panel.get_attribute("data-strokes") == "2"
            ink = rec.calls[-1][-2:]
            left = X - box["x"]
            assert [len(s) for s in ink] == [15, 15]
            assert all(p[0] < left + 60 for s in ink for p in s)
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_a_contact_the_system_takes_back_leaves_nothing():
    """Bug: ``pointercancel`` ended a contact as ``pointerup`` does: what the
    system had taken back stayed on the formula as a stroke, and a short one
    went through as a tap, selecting what was under it.  A cancelled contact
    leaves nothing, and the next stroke is written as any other."""
    rec = KeptRecognizer()
    doc = Document(x + y, addons=[HandwritingAddon(rec), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            panel = page.locator(".hw-panel")
            box = page.locator(".hw-ink").bounding_box()
            X, Y = box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5
            _fire(page, "pointerdown", "touch", 7, X, Y, 1)
            for i in range(1, 12):
                _fire(page, "pointermove", "touch", 7, X + 4 * i, Y + 2 * i, 1)
            _fire(page, "pointercancel", "touch", 7, X + 44, Y + 22, 0)
            assert panel.get_attribute("data-strokes") == "0"
            # a short one, on a glyph: no tap either
            g = page.evaluate(TEXT_RECT, "x")
            _fire(page, "pointerdown", "touch", 8, (g["left"] + g["right"]) / 2, (g["top"] + g["bottom"]) / 2, 1)
            _fire(page, "pointercancel", "touch", 8, (g["left"] + g["right"]) / 2, (g["top"] + g["bottom"]) / 2, 0)
            page.wait_for_timeout(1000)
            assert page.locator(".se-view .se-selected").count() == 0
            assert panel.get_attribute("data-strokes") == "0" and rec.calls == []
            assert page.locator(".hw-cand").count() == 0
            # ink written before is still read, the cancelled stroke after it left out
            _drag(page, X, Y, X + 40, Y + 20)
            assert _wait(lambda: panel.get_attribute("data-strokes") == "1")
            _fire(page, "pointerdown", "touch", 9, X, Y + 40, 1)
            for i in range(1, 12):
                _fire(page, "pointermove", "touch", 9, X + 4 * i, Y + 40, 1)
            _fire(page, "pointercancel", "touch", 9, X + 44, Y + 40, 0)
            assert _wait(lambda: len(rec.calls) == 1, 15)
            assert panel.get_attribute("data-strokes") == "1"
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_erasing_the_last_stroke_takes_the_readings_with_it():
    """Bug: with every stroke rubbed out the readings stayed, Apply with them
    - and Apply put into the formula the reading of ink that was not there.
    It is as when the last stroke is taken back: no readings, no Apply."""
    doc = Document(x + y, addons=[HandwritingAddon(LetterRecognizer()), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            panel = page.locator(".hw-panel")
            box = page.locator(".hw-ink").bounding_box()
            X, Y = box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5
            _drag(page, X, Y, X + 44, Y + 22, steps=11)
            apply = page.locator(".hw-apply")
            assert _wait(lambda: apply.is_visible() and not apply.is_disabled(), 15)
            assert page.locator(".hw-cand").count() == 1
            page.locator('[data-cmd="addon:handwriting:erase"]').click()
            _drag(page, X - 10, Y, X + 44, Y + 22, steps=11)
            assert _wait(lambda: panel.get_attribute("data-strokes") == "0")
            page.wait_for_timeout(1000)                          # nothing is read of nothing
            assert page.locator(".hw-cand").count() == 0
            assert not apply.is_visible() and apply.is_disabled()
            assert "Read in" not in page.locator(".hw-note").inner_text()
            assert str(doc.expr) == "x + y"
            assert page.errors == []
        finally:
            _close(srv, browser)


@pytest.mark.parametrize("selected", [None, "x"])
def test_ink_stays_by_what_it_was_written_by_when_the_formula_is_zoomed(selected):
    """Bug: zoomed, the ink was magnified about the corner of the ink layer,
    while the formula - centred - grows where it stands: a line drawn under
    the y of x + y was 500 px to the right of the y at twice the size, and
    the piece to read the ink with was guessed from there.  The ink keeps its
    place by the formula: under the y still - and, written in the space
    opened by a selection, as far from the selection as it was, in the
    formula's own measure."""
    rec = KeptRecognizer()
    doc = Document(x + y, addons=[HandwritingAddon(rec), LATEX])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            if selected:
                r = page.evaluate(TEXT_RECT, selected)
                page.mouse.click((r["left"] + r["right"]) / 2, (r["top"] + r["bottom"]) / 2)
                assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            page.locator('[data-cmd="addon:handwriting:pen"]').click()
            page.wait_for_timeout(400)                           # the view has grown, the space opened
            by = selected or "y"
            g = page.evaluate(IN_INK, by)
            # under the y, from its left to its right; or, in the space the
            # selection opened, a line beginning 10 px after the x
            x0 = g["r"] + 10 if selected else g["l"]
            x1 = x0 + 30 if selected else g["r"]
            y0 = (g["t"] + g["b"]) / 2 if selected else g["b"] + 3
            _drag(page, g["cx"] + x0, g["cy"] + y0, g["cx"] + x1, g["cy"] + y0, steps=10)
            assert _wait(lambda: len(rec.calls) == 1, 15)
            g = page.evaluate(IN_INK, by)                        # the space widened as the line was drawn
            s = rec.calls[-1][-1]
            edge = g["r"] if selected else g["l"]
            level = (g["t"] + g["b"]) / 2 if selected else g["b"]
            was = (s[0][0] - edge, s[-1][0] - edge, s[0][1] - level)
            for n, zoom in enumerate((2, 0.6), start=2):
                page.evaluate(ED + ".setZoom(%s)" % zoom)
                page.wait_for_timeout(300)
                # read again, to see where the ink is: what is sent is the ink as it is kept
                page.locator('[data-cmd="addon:handwriting:undo"]').click()
                page.locator('[data-cmd="addon:handwriting:redo"]').click()
                assert _wait(lambda: len(rec.calls) == n, 15)
                g = page.evaluate(IN_INK, by)
                s = rec.calls[-1][-1]
                edge = g["r"] if selected else g["l"]
                level = (g["t"] + g["b"]) / 2 if selected else g["b"]
                now = (s[0][0] - edge, s[-1][0] - edge, s[0][1] - level)
                assert all(abs(a - b * zoom) < 2.5 for a, b in zip(now, was)), (zoom, was, now)
            assert page.errors == []
        finally:
            _close(srv, browser)


# ---- which engine reads, and a page where none does ---------------------------

NO_READER_OF_ITS_OWN = """
    try { delete Navigator.prototype.createHandwritingRecognizer; } catch (e) {}
    try { delete navigator.createHandwritingRecognizer; } catch (e) {}
"""
A_READER_OF_ITS_OWN = """
    window.SympyEditorApp = Object.assign(window.SympyEditorApp || {}, {
        recognizeInk: function (token, json) {
            window.__askedWith = JSON.parse(json);
            setTimeout(function () {
                window.SympyEditor.inkRead(token, JSON.stringify({candidates: [{latex: "w + 2"}]}));
            }, 10);
        }
    });
"""


def _again(page, script):
    """The page loaded again with ``script`` run before it: what the device
    offers is looked for when the add-on starts."""
    page.add_init_script(script)
    page.reload()
    page.wait_for_selector(".se-stage .hw-ink", timeout=30000)
    page.wait_for_selector(".se-view [data-path]")
    page.wait_for_function("document.querySelector('.se-stage .hw-ink').clientWidth > 0")


def test_one_document_s_choice_of_engine_is_not_another_s():
    """Bug: a page that chose the device's own reader chose it for every
    page built after it, the add-on being one object: on a device with no
    such reader those had the Pen off, the strip hidden, and the menu to
    choose the model again inside the hidden strip.  The choice is the
    document's; and a document that did choose the device's reader, opened
    where there is none, is read by the first engine that can."""
    rec = KeptRecognizer()
    addon = HandwritingAddon(rec)
    chose = Document(y, addons=[addon, LATEX])
    chose.handle({"action": "addon", "addon": "handwriting", "method": "engine", "name": "host"})
    fresh = Document(x, addons=[addon, LATEX])
    pen = '[data-cmd="addon:handwriting:pen"]'
    with playwright.sync_playwright() as p:
        for doc, after in ((fresh, "x*z"), (chose, "y*z")):
            srv, browser, page = _page(p, doc, pen=False)
            try:
                _again(page, NO_READER_OF_ITS_OWN)
                assert _wait(lambda: not page.locator(pen).is_disabled()), page.locator(".hw-note").inner_text()
                page.locator(pen).click()
                assert _wait(lambda: page.locator(".hw-panel").is_visible())
                assert page.locator(".hw-engine").is_hidden()          # one engine that can read: nothing to choose
                asked = len(rec.calls)
                view = page.locator(".se-view").bounding_box()
                _drag(page, view["x"] + 200, view["y"] + 60, view["x"] + 260, view["y"] + 100)
                apply = page.locator(".hw-apply")
                assert _wait(lambda: apply.is_visible() and not apply.is_disabled(), 15)
                assert len(rec.calls) == asked + 1                      # read by the model
                apply.click()
                assert _wait(lambda: str(doc.expr) == after, 15), str(doc.expr)
                assert page.errors == []
            finally:
                _close(srv, browser)
        # the choice is kept all the same: where the device has a reader, it is the one asked
        srv, browser, page = _page(p, chose, pen=False)
        try:
            _again(page, A_READER_OF_ITS_OWN)
            page.locator(pen).click()
            menu = page.locator(".hw-engine")
            assert _wait(lambda: menu.is_visible())
            assert menu.input_value() == "host"
            assert page.errors == []
        finally:
            _close(srv, browser)


class NoModel(FakeRecognizer):
    """A recognizer with no model to run, as in a page that runs its own Python."""

    def status(self):
        return {"available": False, "reason": "A page that runs its own Python carries no handwriting model"}


def test_where_nothing_reads_the_strip_says_so_and_the_device_s_reader_is_asked_when_there_is_one():
    """Bug: where the model cannot run - a page that runs its own Python, a
    Python without the model - the Pen was off and nothing said why: the
    strip that holds the reason was hidden with the Pen.  It shows, with the
    reason, and the Pen's own title says it too; and on a device with a
    reader of its own that reader is asked, with nothing to switch."""
    doc = Document(x, addons=[HandwritingAddon(NoModel()), LATEX])
    pen = '[data-cmd="addon:handwriting:pen"]'
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, pen=False)
        try:
            _again(page, NO_READER_OF_ITS_OWN)
            assert _wait(lambda: page.locator(".hw-panel").is_visible())
            said = page.locator(".hw-note").inner_text()
            assert "carries no handwriting model" in said and "no reader of its own" in said
            assert "error" in page.locator(".hw-note").get_attribute("class")
            assert _wait(lambda: page.locator(pen).is_disabled())
            assert "carries no handwriting model" in page.locator(pen).get_attribute("title")
            assert page.errors == []
        finally:
            _close(srv, browser)
        srv, browser, page = _page(p, doc, pen=False)
        try:
            _again(page, A_READER_OF_ITS_OWN)
            assert _wait(lambda: not page.locator(pen).is_disabled())
            assert page.locator(".hw-panel").is_hidden()               # it can be written on: the editor as it was
            page.locator(pen).click()
            view = page.locator(".se-view").bounding_box()
            _drag(page, view["x"] + 200, view["y"] + 60, view["x"] + 260, view["y"] + 100)
            assert _wait(lambda: page.locator(".hw-cand").count() == 1, 15)
            assert page.evaluate("window.__askedWith.length") == 1
            assert page.locator(".hw-src").inner_text() == "w + 2"
            assert page.errors == []
        finally:
            _close(srv, browser)


@pytest.mark.skipif(not os.environ.get("SYMPY_EDITOR_SLOW_TESTS"), reason="set SYMPY_EDITOR_SLOW_TESTS=1")
def test_a_page_that_runs_its_own_python_edits_and_says_the_model_is_not_in_it(tmp_path):
    """Bug: with the add-on in its catalogue a standalone page could not
    edit: it was asked to install onnxruntime, which Pyodide has no wheel
    of, and the one install failed for every add-on - the LaTeX add-on had no
    lark.  Nothing is installed for the model now; the page's own Python says
    that it has none (the options, written by the Python that built the page,
    may say that one had), the strip shows it, and the rest of the page
    works."""
    from sympy_editor import save_html
    from sympy_editor_handwriting import ADDON
    path = tmp_path / "page.html"
    save_html(Document(x + 1, addons=[ADDON, LATEX]), path)
    pen = '[data-cmd="addon:handwriting:pen"]'
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        try:
            page = browser.new_page(viewport={"width": 900, "height": 1000})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script("delete window.Worker;" + NO_READER_OF_ITS_OWN)   # Python in the page itself
            page.goto(path.as_uri())
            page.wait_for_selector(".se-stage .hw-ink", timeout=60000)
            page.wait_for_function("document.querySelector('.se-loading').hidden", timeout=240000)
            assert _wait(lambda: "runs its own Python" in page.locator(".hw-note").inner_text(), 120), \
                page.locator(".hw-note").inner_text()
            assert page.locator(".hw-panel").is_visible() and page.locator(pen).is_disabled()
            # the LaTeX add-on has its lark, and the formula is edited
            read = page.evaluate("""async () => {
                const ed = document.querySelector('.sympy-editor').__sympyEditor;
                const snap = await ed.backend.send({action: 'addon', addon: 'latex', method: 'read',
                                                    latex: '\\\\frac{1}{2}'}, () => {});
                return snap.query;
            }""")
            assert read["result"]["ok"] and read["result"]["src"] == "1/2", read
            page.evaluate(ED + ".send({action: 'set', src: 'x + 2'})")
            assert _wait(lambda: page.locator(".se-source").inner_text().strip() == "x + 2", 30)
            assert errors == []
        finally:
            browser.close()

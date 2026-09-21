"""The handwriting add-on on the editor's own formula, in a real browser: the
editor untouched until the Pen is on, the ink over the formula, the reading in
the formula at once, the piece it is read together with, the strip that keeps
or takes the change back, and the eraser.  The model is faked - what is tested
is the add-on, not math-ocr - so it needs only Playwright with Chromium and the
KaTeX CDN (skipped otherwise)."""
import sys
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import symbols

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
            zoom = lambda: page.evaluate("document.querySelector('.sympy-editor').__se ? 0 : 0")
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
            page.locator('.se-toolbar [data-cmd="drawer"]').click()
            switch = page.locator('.se-drawer .se-addon-row input[id*="handwriting"]')
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
            assert moved < was, (was, moved)             # it went left with the formula
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

            # and the Pen off again puts them all away, ink or no ink
            tools["pen"].click()
            assert _wait(off)
            assert page.locator(".hw-panel").get_attribute("data-strokes") == "1"
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_back_puts_the_pen_down_and_keeps_the_ink():
    """Android's Back (SympyEditor.back) puts the pen down, as pressing the
    Pen again does - the ink stays - and says it closed something."""
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
            assert page.locator("[data-strokes]").first.get_attribute("data-strokes") == "1"
            assert pen.get_attribute("aria-pressed") in ("false", None)
            assert page.evaluate("SympyEditor.back()") is False
            assert page.errors == []
        finally:
            _close(srv, browser)

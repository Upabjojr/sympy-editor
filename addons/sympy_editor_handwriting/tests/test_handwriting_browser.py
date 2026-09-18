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


def test_what_is_written_over_a_selection_takes_its_place_at_once():
    """A piece selected in the editor, then written on: the reading takes that
    piece's place as soon as it is read, and the strip shows what changed."""
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
            assert _wait(lambda: str(doc.expr) == "x + z", 15), str(doc.expr)
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
            assert _wait(lambda: str(doc.expr) == "x*z", 15), str(doc.expr)
            assert page.locator(".hw-latexrow").is_hidden()
            page.locator(".hw-edit").click()
            field = page.locator(".hw-latex")
            assert _wait(lambda: field.is_visible())
            assert field.input_value() == "z"
            field.fill(r"\frac{w}{2}")
            field.press("Enter")
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

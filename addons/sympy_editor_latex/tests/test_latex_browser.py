"""The LaTeX add-on in a real browser, typing into the formula itself: the
field where the reading will land, what it reads as you type, the ambiguities
and the constants, and applying - with the formula before and after to keep or
to take back.  Needs Playwright with Chromium and the KaTeX CDN (skipped
otherwise)."""
import sys
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import Symbol, cos, pi, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")
pytest.importorskip("lark")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_latex import ADDON, LatexAddon  # noqa: E402

x, y = symbols("x y")

TOOL = '[data-cmd="addon:latex:type"]'

#: A point in the space between the two terms of the formula, beside the
#: operator glyph: the glyph itself selects the operator, the space by it
#: gives a cursor (the same rule as tests/test_browser.py's _gap_between).
GAP = """() => {
    const a = document.querySelector('[data-path="/0"]').getBoundingClientRect();
    const b = document.querySelector('[data-path="/1"]').getBoundingClientRect();
    const [lo, hi] = a.right <= b.left ? [a.right, b.left] : [b.right, a.left];
    const y = (a.top + a.bottom) / 2;
    const onOp = (x) => (document.elementsFromPoint(x, y) || []).some(el => {
        const t = (el.textContent || '').trim();
        return t.length <= 1 && '+-\u2212\u22c5'.includes(t) && t && !el.querySelector('[data-path]');
    });
    let x = Math.round((lo + hi) / 2);
    if (onOp(x)) {
        let lx = x, rx = x;
        while (onOp(lx) && lx - 1 > lo) lx -= 1;
        while (onOp(rx) && rx + 1 < hi) rx += 1;
        x = !onOp(rx) ? rx : lx;
    }
    return {x: x, y: y};
}"""


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def _wait(check, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if check():
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return check()


def _page(p, doc, tool=True, **kwargs):
    """The editor served with the add-on, and a page showing it.  `tool`: wait
    for the add-on's own tool, which a page that has it off has not got."""
    srv = EditorServer(doc, port=0, **kwargs)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        browser = p.chromium.launch()
    except Exception as exc:
        srv.shutdown()
        pytest.skip(f"chromium not available: {exc}")
    page = browser.new_page(viewport={"width": 900, "height": 900})
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    if tool:
        page.wait_for_selector(TOOL, timeout=15000)
    return srv, browser, page


def _close(srv, browser):
    browser.close()
    srv.shutdown()
    srv.server_close()


def _type(page, text):
    """Open the field if it is closed, and type `text` into it."""
    if page.locator(".se-view .ltx-field").count() == 0:
        page.locator(TOOL).click()
        page.wait_for_selector(".se-view .ltx-field", timeout=10000)
    page.locator(".se-view .ltx-field").fill(text)


def test_the_field_opens_in_the_formula_and_reads_as_it_is_typed():
    """The tool opens a field inside the formula - there is no box under the
    editor to type in - and what is typed is read as it is typed: the reading
    beside the field, what SymPy gets of it under the editor.  A text that
    stops mid-expression is unfinished, not wrong, and Escape leaves."""
    doc = Document(x + y, addons=[ADDON])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            assert page.locator(".ltx-panel").is_hidden()          # nothing said before anything is typed
            page.locator(TOOL).click()
            page.wait_for_selector(".se-view .ltx-field", timeout=10000)
            assert page.evaluate("document.activeElement.className") == "ltx-field"
            assert page.locator(TOOL).get_attribute("aria-pressed") == "true"

            page.locator(".se-view .ltx-field").fill(r"\frac{x^2}{2}")
            assert _wait(lambda: page.locator(".ltx-src").inner_text() == "x**2/2", 15)
            # what it reads as is shown once, under the editor - not in the
            # formula beside the LaTeX it is the reading of
            assert page.locator(".ltx-preview .katex").count() == 1
            assert page.locator(".se-view .katex").count() == 1                # the formula's own, and no copy
            assert page.locator(".se-view .ltx-slot").count() == 1             # the field alone stands in it
            assert doc.expr == x + y                                          # and nothing has changed yet

            # a text that stops in the middle of an expression: not finished
            page.locator(".se-view .ltx-field").fill(r"\frac{x")
            assert _wait(lambda: "ltx-note pending" in (page.locator(".ltx-note").get_attribute("class") or ""), 15)
            assert page.locator(".ltx-apply").is_disabled()

            page.locator(".se-view .ltx-field").press("Escape")
            assert _wait(lambda: page.locator(".se-view .ltx-field").count() == 0)
            assert page.locator(TOOL).get_attribute("aria-pressed") == "false"
            assert doc.expr == x + y
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_reading_goes_where_the_editor_says():
    """Over the selection, at the cursor, or after the whole formula when
    there is neither: the editor decides, as it does for handwriting."""
    doc = Document(x + y, addons=[ADDON])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            # nothing selected: after the formula, as if typed there
            _type(page, "z")
            assert _wait(lambda: page.locator(".ltx-src").inner_text() == "z", 15)
            assert "after the formula" in page.locator(".ltx-reading-of").inner_text()
            page.locator(".ltx-apply").click()
            assert _wait(lambda: str(doc.expr) == "z*(x + y)", 15), str(doc.expr)

            # a selection: the reading takes its place
            page.locator(".ltx-keep").click()
            path = next(p for p, n in doc.snapshot()["nodes"].items() if n["src"] == "z")
            page.evaluate("p => document.querySelector('.sympy-editor').__sympyEditor.select(p)", path)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            _type(page, r"\sqrt{5}")
            assert _wait(lambda: "selection's place" in page.locator(".ltx-reading-of").inner_text(), 15)
            assert _wait(lambda: not page.locator(".ltx-apply").is_disabled(), 15)
            page.locator(".ltx-apply").click()
            assert _wait(lambda: str(doc.expr) == "sqrt(5)*(x + y)", 15), str(doc.expr)
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_ambiguities_and_the_constants_are_offered_under_the_editor():
    """Where the text can be read several ways, a menu per point with the whole
    expression under each reading; every name that usually means a constant is
    a switch.  Both live in the strip, and the reading follows a pick."""
    doc = Document(x + y, addons=[ADDON])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            _type(page, r"\sin x \cos y + \pi")
            usual, other = str(sin(x) * cos(y) + pi), str(sin(x * cos(y)) + pi)
            assert _wait(lambda: page.locator(".ltx-src").inner_text() == usual, 15)
            rows = page.locator(".ltx-point")
            assert rows.count() == 2
            row = rows.filter(has=page.locator(".ltx-fragment", has_text=r"\sin x \cos y").filter(has_not_text=r"\pi"))
            choice = row.locator(".ltx-choice")
            options = choice.locator("option").all_inner_texts()
            assert usual in options and other in options and choice.input_value() == str(options.index(usual))
            choice.select_option(str(options.index(other)))
            assert _wait(lambda: page.locator(".ltx-src").inner_text() == other, 15)

            const = page.locator(".ltx-const input")
            assert const.count() == 1 and const.is_checked() and "pi" in page.locator(".ltx-const").inner_text()
            const.uncheck()
            as_symbol = str(Symbol("pi") + sin(x * cos(y)))
            assert _wait(lambda: page.locator(".ltx-src").inner_text() == as_symbol, 15)

            page.locator(".ltx-apply").click()
            assert _wait(lambda: Symbol("pi") in doc.expr.free_symbols, 15), str(doc.expr)
            assert pi not in doc.expr.atoms()
            assert doc.history_labels()["actions"][-1] == r"LaTeX: \sin x \cos y + \pi"
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_what_it_did_is_shown_and_can_be_taken_back():
    """Applying shows the formula before and after, marked as the history marks
    a step, to keep or to undo; the field closes, the tool comes up."""
    doc = Document(x + y, addons=[ADDON])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            _type(page, "7")
            assert _wait(lambda: not page.locator(".ltx-apply").is_disabled(), 15)
            page.locator(".ltx-apply").click()
            assert _wait(lambda: str(doc.expr) == "7*x + 7*y", 15), str(doc.expr)
            strip = page.locator(".ltx-applied")
            # the document changed before the page did: the strip is drawn
            # when the new state arrives in it
            assert _wait(lambda: not strip.is_hidden(), 10)
            assert strip.locator(".ltx-was").get_attribute("data-latex") == "x + y"
            assert strip.locator(".ltx-now").get_attribute("data-latex") == "7 x + 7 y"
            assert strip.locator(".ltx-was .rep-removed").count() >= 1
            assert strip.locator(".ltx-now .rep-added").count() >= 1
            assert page.locator(".se-view .ltx-field").count() == 0      # the field has done its work
            assert page.locator(TOOL).get_attribute("aria-pressed") == "false"

            page.locator(".ltx-back").click()
            assert _wait(lambda: str(doc.expr) == "x + y", 15), str(doc.expr)
            assert _wait(lambda: strip.is_hidden())
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_add_on_switched_off_takes_its_field_and_its_strip_away():
    doc = Document(x + y, available=[LatexAddon()])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc, tool=False)      # off to start with
        try:
            page.locator('.se-toolbar [data-cmd="drawer"]').click()
            switch = page.locator('.se-drawer .se-addon-row input[id*="latex"]')
            assert _wait(lambda: switch.count() == 1)
            switch.check()                                   # on: the tool appears
            assert _wait(lambda: page.locator(TOOL).count() == 1, 15)
            page.locator(".se-drawer-close").click()          # the drawer is over the formula
            assert _wait(lambda: page.locator(".se-backdrop").is_hidden())
            page.locator(TOOL).click()
            assert _wait(lambda: page.locator(".se-view .ltx-field").count() == 1)
            page.locator('.se-toolbar [data-cmd="drawer"]').click()
            assert _wait(lambda: switch.is_visible())
            switch.uncheck()
            assert _wait(lambda: page.locator(TOOL).count() == 0, 15)
            assert page.locator(".se-view .ltx-field").count() == 0 and page.locator(".ltx-panel").count() == 0
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_what_is_typed_at_a_cursor_goes_in_at_the_cursor():
    """A cursor between two terms, then the tool: the field opens there and
    what is read goes in there - not after the whole formula.  (Putting the
    field in the formula is itself a change the editor answers to, and its
    answer used to take the cursor away with it.)"""
    doc = Document(x + y, addons=[ADDON])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            gap = page.evaluate(GAP)
            page.mouse.click(gap["x"], gap["y"])
            assert _wait(lambda: page.locator(".se-caret").count() == 1), "no cursor to type at"
            page.locator(TOOL).click()
            page.wait_for_selector(".se-view .ltx-field", timeout=10000)
            assert _wait(lambda: "at the cursor" in page.locator(".ltx-reading-of").inner_text(), 10)
            assert page.locator(".se-caret").count() == 1, "the cursor must stay while one types at it"
            page.locator(".se-view .ltx-field").fill("7")
            assert _wait(lambda: not page.locator(".ltx-apply").is_disabled(), 15)
            page.locator(".se-view .ltx-field").press("Enter")      # as one finishes typing
            assert _wait(lambda: str(doc.expr) == "x + y + 7", 15), str(doc.expr)
            # the field has done its work: it goes, and the focus goes back to
            # the formula (on a phone, that is what puts the keyboard away)
            assert _wait(lambda: page.locator(".se-view .ltx-field").count() == 0)
            assert "se-view" in page.evaluate("document.activeElement.className")
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_piece_being_replaced_makes_way_for_the_field():
    """Opening the field on a selection puts it where that piece is, and takes
    the piece off the screen while it is there: what will happen is plain -
    this is a replacement, not something added beside it.  Escape puts the
    piece back, and what is applied takes its place."""
    from sympy import symbols as _symbols

    b, i, p_ = _symbols("b i p")
    doc = Document(b ** i + 1, addons=[ADDON])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            path = next(k for k, n in doc.snapshot()["nodes"].items() if n["src"] == "i")
            page.evaluate("p => document.querySelector('.sympy-editor').__sympyEditor.select(p)", path)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            shown = "p => { const el = document.querySelector(`.se-view [data-path=\"${p}\"]`);" \
                    " return el ? getComputedStyle(el).display !== 'none' : null; }"
            assert page.evaluate(shown, path) is True

            page.locator(TOOL).click()
            page.wait_for_selector(".se-view .ltx-field", timeout=10000)
            assert _wait(lambda: page.evaluate(shown, path) is False), "the piece must make way for the field"
            assert "selection's place" in page.locator(".ltx-reading-of").inner_text()

            # it stays away for as long as the field is there - even when the
            # selection itself goes (the field has the focus, and the editor
            # lets a selection go for all sorts of reasons)
            page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.select(null)")
            page.wait_for_timeout(400)
            assert page.evaluate(shown, path) is False, "the piece came back while the field was still open"
            assert "selection's place" in page.locator(".ltx-reading-of").inner_text()

            # Escape: the piece comes back and the formula is as it was
            page.locator(".se-view .ltx-field").press("Escape")
            assert _wait(lambda: page.evaluate(shown, path) is True)
            assert doc.expr == b ** i + 1

            # and again, this time applied: the exponent is what was typed
            page.evaluate("p => document.querySelector('.sympy-editor').__sympyEditor.select(p)", path)
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            page.locator(TOOL).click()
            page.wait_for_selector(".se-view .ltx-field", timeout=10000)
            page.locator(".se-view .ltx-field").fill(r"\cos p")
            assert _wait(lambda: not page.locator(".ltx-apply").is_disabled(), 15)
            page.locator(".se-view .ltx-field").press("Enter")
            # while the change goes in, the piece it replaces stays off the
            # screen: showing it again for that moment is a flicker of
            # something already spent
            while doc.expr == b ** i + 1:
                assert page.evaluate(shown, path) is not True, "the old exponent came back mid-change"
            assert _wait(lambda: doc.expr == b ** cos(p_) + 1, 15), str(doc.expr)
            assert page.locator(".se-view .ltx-field").count() == 0
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_the_field_keeps_its_taps_and_keys_to_itself():
    """The field sits inside the formula, where the editor watches for taps,
    drags and keys of its own.  While it is open it is what the user is
    working in: a tap in it must not select the piece behind it, a double tap
    must not open the editor's own box over it, and the editor's keys are not
    for it."""
    doc = Document(x + y, addons=[ADDON])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.select('/1')")
            assert _wait(lambda: page.locator(".se-view .se-selected").count() == 1)
            page.locator(TOOL).click()
            page.wait_for_selector(".se-view .ltx-field", timeout=10000)
            page.locator(".se-view .ltx-field").fill(r"\sqrt{2}")
            assert _wait(lambda: page.locator(".ltx-src").inner_text() == "sqrt(2)", 15)

            box = page.locator(".se-view .ltx-field").bounding_box()
            middle = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.click(*middle)
            page.wait_for_timeout(300)
            assert page.evaluate("document.activeElement.className") == "ltx-field"
            assert page.locator(".se-view .se-inline").count() == 0
            assert page.locator(".se-caret").count() == 0          # no cursor put in the formula behind it

            page.mouse.dblclick(*middle)                            # would open the editor's own box
            page.wait_for_timeout(400)
            assert page.locator(".se-view .se-inline").count() == 0
            assert page.locator(".se-view .ltx-field").count() == 1
            assert page.evaluate("document.activeElement.className") == "ltx-field"

            # the editor's keys are the field's while it is open: Delete types
            # a character, it does not delete the selection
            page.locator(".se-view .ltx-field").press("End")
            page.locator(".se-view .ltx-field").type("+1")
            assert _wait(lambda: page.locator(".ltx-src").inner_text() == "1 + sqrt(2)", 15)
            assert str(doc.expr) == "x + y"                         # and nothing has happened to the formula
            assert page.errors == []
        finally:
            _close(srv, browser)


def test_back_closes_the_field_and_brings_back_what_it_covered():
    """Android's Back (SympyEditor.back) closes the field as Esc does - what
    it would have replaced shows again, nothing is changed - and says it
    closed something, so the app stays."""
    doc = Document(x + y, addons=[ADDON])
    with playwright.sync_playwright() as p:
        srv, browser, page = _page(p, doc)
        try:
            _type(page, r"\cos p")
            assert page.locator(".se-view .ltx-field").count() == 1
            assert page.evaluate("SympyEditor.back()") is True
            assert _wait(lambda: page.locator(".se-view .ltx-field").count() == 0)
            assert page.locator(TOOL).get_attribute("aria-pressed") == "false"
            assert doc.expr == x + y
            assert page.evaluate("SympyEditor.back()") is False           # nothing more to close
            assert page.errors == []
        finally:
            _close(srv, browser)

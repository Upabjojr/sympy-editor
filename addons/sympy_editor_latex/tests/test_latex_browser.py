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
            assert page.locator(".se-view .ltx-ghost .katex").count() == 1     # as it will look, beside the field
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

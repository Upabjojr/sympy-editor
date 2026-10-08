"""The steps panel in a real browser: it follows the selection, draws the
steps with KaTeX, applies one as a step of the history, offers what to do
with a plain expression, and has its guide.  Needs Playwright with Chromium
and the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from sympy import Eq, Integral, Or, cos, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_steps import ADDON  # noqa: E402

x = symbols("x")


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


@contextmanager
def _page(doc):
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(srv.url)
                page.wait_for_selector(".se-addon-steps .st-panel", timeout=30000)
                yield page
                assert errors == []
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _source(page):
    return page.evaluate("document.querySelector('.se-source').textContent")


def _select(page, doc, typename):
    path = next(p for p, n in doc.snapshot()["nodes"].items() if n["type"] == typename)
    page.locator(f'.se-view [data-path="{path}"]').first.click(force=True)
    return path


def test_the_steps_of_the_selected_integral_and_applying_one():
    doc = Document(1 + Integral(x * sin(x), x), addons=[ADDON])
    with _page(doc) as page:
        # nothing selected: the whole formula, a sum - what can be done with it
        page.wait_for_selector(".se-addon-steps .st-offer[data-task='differentiate']")
        assert "choose what to do" in page.locator(".se-addon-steps .st-note").inner_text()
        _select(page, doc, "Integral")
        page.wait_for_selector(".se-addon-steps .st-step")
        rows = page.locator(".se-addon-steps .st-step")
        assert rows.first.locator(".st-text").inner_text().startswith("Integration by parts")
        page.wait_for_selector(".se-addon-steps .st-step .st-math .katex")             # drawn by KaTeX
        assert page.locator(".se-addon-steps .st-start .katex").count() == 1           # the integral it starts from
        assert page.locator(".se-addon-steps .st-offer").count() == 0
        # the + C is a remark: nothing to apply
        assert rows.last.locator(".st-apply").count() == 0
        assert "st-remark" in rows.last.get_attribute("class")
        assert not doc.can_undo                                                       # asking changed nothing
        applicable = page.locator(".se-addon-steps .st-step .st-apply")
        applicable.last.click()
        page.wait_for_function("document.querySelector('.se-source').textContent === '-x*cos(x) + sin(x) + 1'")
        assert doc.expr == 1 - x * cos(x) + sin(x)
        assert doc.history_labels()["actions"][-1].startswith("Steps: Known integral")
        # the panel follows the new state: the whole formula has no integral left
        page.wait_for_selector(".se-addon-steps .st-offer[data-task='integrate']")


def test_a_plain_expression_is_differentiated_on_request():
    doc = Document(x**2 * sin(x), addons=[ADDON])
    with _page(doc) as page:
        page.wait_for_selector(".se-addon-steps .st-offer[data-task='differentiate']")
        page.locator(".se-addon-steps .st-offer[data-task='differentiate']").click()
        page.wait_for_selector(".se-addon-steps .st-step")
        assert page.locator(".se-addon-steps .st-task").input_value() == "differentiate"
        texts = page.locator(".se-addon-steps .st-step .st-text").all_inner_texts()
        assert texts[0].startswith("Product rule")
        assert page.locator(".se-addon-steps .st-varlabel").is_hidden()               # one symbol: no choice to make


def test_an_equation_is_solved_and_what_cannot_be_is_said():
    doc = Document(Eq(x**2 - 5 * x + 6, 0), addons=[ADDON])
    with _page(doc) as page:
        page.wait_for_selector(".se-addon-steps .st-step")
        rows = page.locator(".se-addon-steps .st-step")
        assert rows.count() == 4
        assert rows.nth(1).locator(".st-text").inner_text().startswith("The discriminant")
        assert rows.nth(1).locator(".st-apply").count() == 0
        rows.last.locator(".st-apply").click()
        page.wait_for_function("document.querySelector('.se-source').textContent.includes('Eq(x, 3)')")
        assert doc.expr == Or(Eq(x, 2), Eq(x, 3))
        # the solutions are no integral, derivative or equation: said in words
        page.wait_for_function("document.querySelector('.se-addon-steps .st-note').textContent.length > 0")
        assert page.locator(".se-addon-steps .st-step").count() == 0


def test_the_guide():
    doc = Document(Integral(x, x), addons=[ADDON])
    with _page(doc) as page:
        page.locator(".se-addon-steps .se-addon-help").click()
        text = page.locator(".se-help-view").inner_text()
        assert "Apply" in text and "Undo" in text and "manualintegrate" in text
        page.keyboard.press("Escape")

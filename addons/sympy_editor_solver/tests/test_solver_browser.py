"""The solver's panel in a real browser: reading the selection, solving,
substituting back, inserting, a range as a system, the guide.  Needs
Playwright with Chromium and the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import And, Eq, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_solver import ADDON  # noqa: E402

x, y, z = symbols("x y z")

ED = "document.querySelector('.sympy-editor').__sympyEditor"


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def _page(p, doc):
    try:
        browser = p.chromium.launch()
    except Exception as exc:
        pytest.skip(f"chromium not available: {exc}")
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    page.wait_for_selector(".se-addon-solver .sv-problem .sv-formula", timeout=15000)
    return browser, srv, page, errors


def test_solve_check_insert_and_guide():
    doc = Document(Eq(x**2, 4), addons=[ADDON])
    with playwright.sync_playwright() as p:
        browser, srv, page, errors = _page(p, doc)
        try:
            panel = page.locator(".se-addon-solver")
            assert "an equation" in panel.locator(".sv-problem").inner_text()
            page.wait_for_selector(".se-addon-solver .sv-problem .katex")                     # typeset
            assert panel.locator(".sv-unknowns input[data-name='x']").is_checked()
            panel.locator(".sv-solve").click()
            page.wait_for_selector(".se-addon-solver .sv-list li >> nth=1")
            assert "2 solutions" in panel.locator(".sv-summary").inner_text()
            assert "solveset" in panel.locator(".sv-method").inner_text()
            assert panel.locator(".sv-list li .katex").count() == 2
            # substitute back: the equation as substituted, and True
            panel.locator(".sv-list li").first.locator(".sv-check-btn").click()
            page.wait_for_selector(".se-addon-solver .sv-verdict.sv-holds")
            assert panel.locator(".sv-row.sv-holds").count() == 1
            assert not doc.can_undo                                                           # a query: nothing changed
            # the domain: positive reals leave one
            panel.locator(".sv-domain").select_option("positive")
            assert panel.locator(".sv-list li").count() == 0                                  # the old list is stale
            panel.locator(".sv-solve").click()
            page.wait_for_function("document.querySelectorAll('.se-addon-solver .sv-list li').length === 1")
            # insert: a step of the history, in place of the equation
            panel.locator(".sv-list li .sv-insert").click()
            page.wait_for_function("document.querySelector('.se-source').textContent === 'Eq(x, 2)'")
            assert doc.expr == Eq(x, 2) and doc.history_labels()["actions"][-1] == "Solve: a solution Eq(x, 2)"
            assert "Undo" in panel.locator(".sv-status").inner_text()
            page.evaluate(f"{ED}.send({{action: 'undo'}})")
            page.wait_for_function("document.querySelector('.se-source').textContent === 'Eq(x**2, 4)'")
            # the guide
            panel.locator(".se-addon-help").click()
            assert "Substitute back" in page.locator(".se-help-view").inner_text()
            page.keyboard.press("Escape")
            assert errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()


def test_a_range_of_equations_is_a_system_and_words_for_infinite_sets():
    doc = Document(And(Eq(x + y, 3), Eq(x - y, 1), Eq(z, 5)), addons=[ADDON])
    with playwright.sync_playwright() as p:
        browser, srv, page, errors = _page(p, doc)
        try:
            panel = page.locator(".se-addon-solver")
            assert "a system of 3 equations" in panel.locator(".sv-problem").inner_text()
            order = [str(a) for a in doc.expr.args]
            i, j = sorted([order.index("Eq(x + y, 3)"), order.index("Eq(x - y, 1)")])
            assert j == i + 1                                                                 # adjacent: one range
            page.evaluate(f"{ED}._setRange('/', {i}, {j})")
            page.wait_for_function("document.querySelector('.se-addon-solver .sv-words').textContent.includes('a system of 2 equations')")
            assert panel.locator(".sv-unknowns input").count() == 2
            panel.locator(".sv-solve").click()
            page.wait_for_selector(".se-addon-solver .sv-list li")
            assert "linsolve" in panel.locator(".sv-method").inner_text()
            panel.locator(".sv-insert-all").click()
            page.wait_for_function("!document.querySelector('.se-source').textContent.includes('x + y')")
            assert doc.expr == And(Eq(x, 2), Eq(y, 1), Eq(z, 5))                            # one solution: its equations
            # an expression = 0 with infinitely many solutions: the set, with words
            doc.set("sin(x) - 1")
            page.evaluate(f"{ED}.send({{action: 'snapshot'}})")
            page.wait_for_function("document.querySelector('.se-addon-solver .sv-words').textContent.includes('= 0')")
            panel.locator(".sv-solve").click()
            page.wait_for_selector(".se-addon-solver .sv-item-words")
            assert "every integer n" in panel.locator(".sv-item-words").inner_text()
            assert errors == []
        finally:
            browser.close()
            srv.shutdown()
            srv.server_close()

"""The units panel in a real browser: the check and its outlines in the
formula, selecting a problem, Convert, the short-names switch, the guide.
Needs Playwright with Chromium and the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import Symbol
from sympy.physics.units import Quantity, hour, kilometer, meter, second

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_units import ADDON, ADDON_SHORT  # noqa: E402

x = Symbol("x")


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


@pytest.fixture
def served():
    servers = []

    def make(expr):
        doc = Document(expr, addons=[ADDON])
        srv = EditorServer(doc, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return doc, srv

    yield make
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        try:
            yield b
        finally:
            b.close()


def _open(browser, url):
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector(".se-addon-units .su-panel", timeout=30000)
    return page, errors


def test_the_check_outlines_the_odd_term_and_a_click_selects_it(served, browser):
    doc, srv = served(5 * meter + 2 * second + x * meter)
    page, errors = _open(browser, srv.url)
    page.wait_for_function("document.querySelector('.su-verdict').textContent.includes('does not add up')")
    odd = next(p for p in doc.snapshot()["nodes"] if p != "/" and doc.get(p) == 2 * second)
    # the outline in the formula, on the very term
    page.wait_for_function(f"!!document.querySelector('.se-view [data-path=\"{odd}\"].su-bad-node')")
    assert page.locator(".su-problem").count() == 1
    assert "2*second" in page.locator(".su-problem").inner_text()
    assert "time" in page.locator(".su-problem").inner_text() and "length" in page.locator(".su-problem").inner_text()
    page.locator(".su-problem").click()
    page.wait_for_function(f"document.querySelector('.se-view [data-path=\"{odd}\"]').classList.contains('se-selected')")
    # the selection's own dimension
    page.wait_for_function("document.querySelector('.su-dim-name').textContent === 'time'")
    assert page.locator(".su-what").inner_text() == "The selection"
    assert errors == []


def test_convert_is_a_step_and_undo_takes_it_back(served, browser):
    doc, srv = served(36 * kilometer / hour)
    page, errors = _open(browser, srv.url)
    page.wait_for_function("document.querySelector('.su-dim-name').textContent === 'velocity'")
    assert page.locator(".su-verdict").inner_text().startswith("✓")
    assert page.locator(".su-si .katex").count() == 1                     # its SI form, typeset
    page.locator(".su-target").fill("meter/second")
    page.locator(".su-target").press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === '10*meter/second'")
    assert doc.expr == 10 * meter / second
    assert doc.history_labels()["actions"][-1] == "Units: convert to meter/second"
    # a conversion that cannot be is said in the panel, nothing changes
    page.locator(".su-target").fill("kg")
    page.locator(".su-convert .su-primary").click()
    page.wait_for_function("document.querySelector('.su-note').textContent.includes('Cannot convert')")
    assert doc.expr == 10 * meter / second
    page.locator('.se-toolbar [data-cmd="undo"]').click()
    page.wait_for_function("document.querySelector('.se-source').textContent === '36*kilometer/hour'")
    # SI base units, from the panel
    page.locator(".su-convert .su-btn", has_text="SI base units").click()
    page.wait_for_function("document.querySelector('.se-source').textContent === '10*meter/second'")
    assert errors == []


def test_the_short_names_switch_and_the_guide(served, browser):
    doc, srv = served(5 * meter)
    page, errors = _open(browser, srv.url)
    page.wait_for_function("document.querySelector('.su-dim-name').textContent === 'length'")
    page.locator(".su-short-box").check()
    page.wait_for_function("document.querySelector('.su-note').textContent.includes('are units')")
    assert doc.addons["units"] is ADDON_SHORT
    doc.handle({"action": "set", "src": "3 s"})
    assert doc.expr.atoms(Quantity) == {second}
    page.locator(".se-addon-units .se-addon-help").click()
    assert "Short unit names" in page.locator(".se-help-view").inner_text()
    page.keyboard.press("Escape")
    assert errors == []

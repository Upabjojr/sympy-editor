"""The check panel in a real browser: the marks after edits, the first error,
a click that goes to a step, an edit checked by itself, the history report's
marks, the guide.  Needs Playwright with Chromium and the KaTeX CDN (skipped
otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from sympy import symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_check import CheckAddon  # noqa: E402

playwright = pytest.importorskip("playwright.sync_api")

x = symbols("x")
ED = "document.querySelector('.sympy-editor').__sympyEditor"


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


@contextmanager
def _served(doc):
    """A page showing ``doc`` with the check panel, and the server behind it,
    both stopped however the test ends."""
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
                page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
                page.wait_for_selector(".se-addon-check .chk-panel", timeout=10000)
                page.errors = errors
                yield page
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _wait_marks(page, marks):
    page.wait_for_function(
        """want => { var els = document.querySelectorAll('.se-addon-check .chk-step .chk-mark');
                     return els.length === want.length && Array.prototype.every.call(els, (e, i) => e.textContent === want[i]); }""",
        arg=marks, timeout=20000)


def _steps(page):
    return page.locator(".se-addon-check .chk-step")


def test_marks_first_error_goto_auto_and_guide():
    doc = Document((x + 1) ** 2, addons=[CheckAddon()])
    with _served(doc) as page:
        _wait_marks(page, ["•"])
        assert "nothing to compare" in page.locator(".chk-summary").inner_text()
        # Three steps made as the editor's own controls make them (labels included),
        # then "Check" - the page did not see them happen
        doc.handle({"action": "apply", "path": "/", "op": "expand"})
        doc.handle({"action": "replace", "path": "/", "src": "x**2 + x + 1"})
        doc.handle({"action": "apply", "path": "/", "op": "differentiate", "args": ["x"]})
        page.locator(".se-addon-check .chk-again").click()
        _wait_marks(page, ["•", "✓", "✗", "→"])
        assert "went wrong at step 3" in page.locator(".chk-summary").inner_text()
        wrong = _steps(page).nth(2)
        assert "chk-first-error" in wrong.get_attribute("class")
        assert "Not the same: at x = " in wrong.inner_text()
        assert "Differentiate" in _steps(page).nth(3).inner_text()
        # A click goes to that step in the formula
        wrong.click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'x**2 + x + 1'")
        assert doc.history_labels()["index"] == 2
        page.wait_for_function("document.querySelectorAll('.se-addon-check .chk-step')[2].classList.contains('chk-current')")
        # Back to the last step, and an edit made in the page is checked by itself (Auto)
        _steps(page).nth(3).click()
        page.wait_for_function("document.querySelector('.se-source').textContent === '2*x + 1'")
        page.evaluate(ED + ".send({action: 'replace', path: '/', src: '1 + x + x'})")
        _wait_marks(page, ["•", "✓", "✗", "→", "✓"])
        page.evaluate(ED + ".send({action: 'replace', path: '/', src: '2*x'})")
        _wait_marks(page, ["•", "✓", "✗", "→", "✓", "✗"])
        # The history's steps carry the marks of the steps that were checked
        hist = doc.history_labels()["steps"]
        assert [s["check"]["symbol"] for s in hist] == ["•", "✓", "✗", "→", "✓", "✗"]
        # The guide
        page.locator(".se-addon-check .se-addon-help").click()
        assert "where the maths went wrong" in page.locator(".se-help-view").inner_text()
        page.keyboard.press("Escape")
        assert page.errors == []


def test_the_history_report_carries_the_marks():
    doc = Document((x + 1) ** 2, addons=[CheckAddon()])
    doc.handle({"action": "apply", "path": "/", "op": "expand"})
    doc.handle({"action": "replace", "path": "/", "src": "x**2 + 3"})
    with _served(doc) as page:
        _wait_marks(page, ["•", "✓", "✗"])
        html = page.evaluate(ED + ".buildReport()")
        assert "chk-history chk-different" in html and "chk-history chk-equal" in html
        assert page.errors == []

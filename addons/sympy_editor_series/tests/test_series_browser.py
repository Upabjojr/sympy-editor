"""The series panel in a real browser: following the selection (and only a
new one), the order stepper, the terms, Insert as one undoable step, the
direction shown where it matters, a failure said in words, the guide.
Needs Playwright with Chromium and the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from sympy import exp, sin, sqrt, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_series import SeriesAddon  # noqa: E402

x, y = symbols("x y")

ED = "document.querySelector('.sympy-editor').__sympyEditor"
#: The TeX of what the panel shows (KaTeX keeps it in its MathML annotation).
RESULT_TEX = "((document.querySelector('.ser-result annotation') || {}).textContent || '')"


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


class Counting(SeriesAddon):
    """The add-on, counting the questions it is asked."""

    def __init__(self):
        super().__init__()
        self.asked = []

    def handle(self, doc, method, payload):
        if method == "expand":
            self.asked.append(dict(payload))
        return super().handle(doc, method, payload)


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
                page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
                yield page
                assert errors == []
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _path_of(doc, node):
    return next(p for p in doc.snapshot()["nodes"] if doc.get(p) == node)


def test_follow_order_terms_insert_and_guide():
    addon = Counting()
    doc = Document(y + sin(x), addons=[addon])
    sin_path = _path_of(doc, sin(x))
    with _page(doc) as page:
        page.wait_for_function(f"{RESULT_TEX}.includes('O')", timeout=15000)
        assert page.locator(".ser-heading").inner_text().startswith("Taylor series of y + sin(x) at x = 0")
        assert page.locator(".ser-var").input_value() == "x"                  # x is preferred among the free symbols
        assert page.locator(".ser-dir-label").is_hidden()                     # the direction does not matter here
        # the selection: expanded at once ...
        page.evaluate(f"{ED}.select({sin_path!r})")
        page.wait_for_function("document.querySelector('.ser-heading').textContent.startsWith('Taylor series of sin(x) at x = 0, order 6')")
        assert "\\frac{x^{5}}{120}" in page.evaluate(RESULT_TEX)
        n = len(addon.asked)
        # ... and the same selection drawn again asks nothing
        page.evaluate(f"{ED}.select({sin_path!r})")
        page.wait_for_timeout(600)
        assert len(addon.asked) == n
        # the order's stepper
        page.locator(".ser-more").click()
        page.wait_for_function(f"{RESULT_TEX}.includes('x^{{7}}')")
        assert addon.asked[-1]["n"] == 7, (addon.asked, page.evaluate(RESULT_TEX))
        assert page.locator(".ser-order-out").inner_text() == "7"
        # the truncation error, against the O term
        page.wait_for_function("document.querySelector('.ser-check').textContent.includes('error')")
        assert "At x = 1/10" in page.locator(".ser-check").inner_text()
        # without O, and the coefficients
        page.locator(".ser-bare").check()
        page.wait_for_function(f"!{RESULT_TEX}.includes('O')")
        page.locator(".ser-show-terms").click()
        assert page.locator(".ser-table tr").count() == 4                     # the header and x, x^3, x^5
        # Insert without O: one step, which Undo takes back
        page.locator(".ser-insert-bare").click()
        page.wait_for_function("document.querySelector('.se-source').textContent.includes('x**5/120')")
        assert doc.expr == y + x - x**3 / 6 + x**5 / 120
        assert doc.history_labels()["actions"][-1] == "Series: x → 0 (order 7, without O)"
        page.evaluate(f"{ED}.send({{action: 'undo'}})")
        page.wait_for_function("document.querySelector('.se-source').textContent.includes('sin(x)')")
        assert doc.expr == y + sin(x)
        # the guide
        page.locator(".se-addon-series .se-addon-help").click()
        assert "Puiseux" in page.locator(".se-help-view").inner_text()
        page.keyboard.press("Escape")


def test_direction_where_it_matters_asymptotic_and_failure():
    doc = Document(sqrt(x**2) + exp(x), addons=[SeriesAddon()])
    root = _path_of(doc, sqrt(x**2))
    with _page(doc) as page:
        page.evaluate(f"{ED}.select({root!r})")
        page.wait_for_function("document.querySelector('.ser-heading').textContent.includes('sqrt(x**2)')", timeout=15000)
        assert page.locator(".ser-dir-label").is_visible()                    # |x| differs from each side
        page.locator(".ser-dir").select_option("both")
        page.wait_for_selector(".ser-other:not([hidden])")
        assert "from below" in page.locator(".ser-other").inner_text()
        # the whole formula at infinity: exp(x) has no expansion there, said in words
        page.evaluate(f"{ED}.select(null)")
        page.locator(".ser-kind").select_option("asymptotic")
        assert page.locator(".ser-point").input_value() == "oo"
        page.wait_for_function("(() => { const n = document.querySelector('.ser-note');"
                               " return n.classList.contains('ser-error') && n.textContent.length > 0; })()", timeout=15000)
        assert "SymPy" in page.locator(".ser-note").inner_text()
        assert page.locator(".ser-heading").inner_text().startswith("Partial expansion of sqrt(x**2) + exp(x)")
        # one SymPy refuses outright: no result, and nothing to insert
        page.locator(".ser-kind").select_option("leading")
        page.wait_for_function("document.querySelector('.ser-note').textContent.includes('cannot expand')", timeout=15000)
        assert page.locator(".ser-insert").is_disabled() and page.locator(".ser-result").inner_text() == ""

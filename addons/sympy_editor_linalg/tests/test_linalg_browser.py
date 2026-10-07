"""The linear algebra panel in a real browser: the properties and the
spectrum on show, no question asked twice about the same matrix, the row
reduction step by step, Insert, the guide, the touch sizes.  Needs Playwright
with Chromium and the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import time
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from sympy import ImmutableMatrix, Matrix, eye

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_linalg import ADDON  # noqa: E402

ED = "document.querySelector('.sympy-editor').__sympyEditor"


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


@contextmanager
def _page(doc, **context):
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            try:
                page = browser.new_context(**context).new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(srv.url)
                page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
                yield page, errors
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_properties_spectrum_steps_insert_and_guide(monkeypatch):
    asked = []
    orig = type(ADDON).analyse

    def counting(self, doc, payload):
        asked.append(payload.get("path"))
        return orig(self, doc, payload)

    monkeypatch.setattr(type(ADDON), "analyse", counting)
    M = Matrix([[2, 1], [1, 2]])
    doc = Document(M, addons=[ADDON])
    with _page(doc) as (page, errors):
        page.wait_for_selector(".se-addon-linalg .la-props .la-row", timeout=15000)
        text = page.locator(".se-addon-linalg .la-props").inner_text()
        assert "Determinant" in text and "Trace" in text and "Characteristic polynomial" in text
        assert "2 × 2 square matrix" in page.locator(".se-addon-linalg .la-head").inner_text()
        # the spectrum follows by itself, typeset
        page.wait_for_selector(".se-addon-linalg .la-eigen", timeout=15000)
        assert page.locator(".se-addon-linalg .la-eigen tr").count() == 3          # a header and two eigenvalues
        assert "Diagonalizable" in page.locator(".se-addon-linalg .la-verdict").inner_text()
        assert page.locator(".se-addon-linalg .la-spectrum .katex").count() > 0
        # another entry of the same matrix is the same question: nothing is asked
        n = len(asked)
        for path in ("/2/0", "/2/3", "/"):
            page.evaluate("(p) => %s.select(p)" % ED, path)
            time.sleep(0.4)
        assert len(asked) == n
        # the row reduction, step by step
        page.locator('.se-addon-linalg .la-tool[data-kind="rref"]').click()
        page.wait_for_selector(".se-addon-linalg .la-steps .la-step", timeout=15000)
        ops = page.locator(".se-addon-linalg .la-step .la-optext").all_inner_texts()
        assert ops[0] == "R1 ← (1/2)·R1" and "R2 ← R2 − R1" in ops
        assert "Agrees with SymPy's Matrix.rref()" in page.locator(".se-addon-linalg .la-decomp").inner_text()
        # Insert the result: one step of the history, and the panel looks again
        page.locator(".se-addon-linalg .la-decomp > .la-row .la-insert").last.click()
        page.wait_for_function("document.querySelector('.se-source').textContent.includes('Matrix([[1, 0], [0, 1]])')")
        assert doc.expr == ImmutableMatrix(eye(2)) and doc.can_undo
        assert doc.history_labels()["actions"][-1] == "Linear algebra: reduced row echelon form"
        deadline = time.monotonic() + 15
        while len(asked) == n and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        assert len(asked) == n + 1                                                   # the new matrix, once
        page.wait_for_selector(".se-addon-linalg .la-props .la-row", timeout=15000)
        assert page.locator(".se-addon-linalg .la-steps").count() == 0               # the old steps are gone
        # the guide
        page.locator(".se-addon-linalg .se-addon-help").click()
        guide = page.locator(".se-help-view").inner_text()
        assert "Jordan" in guide and "try for longer" in guide.lower()
        page.keyboard.press("Escape")
        assert errors == []


def test_not_a_matrix_and_a_rectangular_one():
    from sympy import symbols
    x = symbols("x")
    doc = Document(x + 1, addons=[ADDON])
    with _page(doc) as (page, errors):
        page.wait_for_function("document.querySelector('.se-addon-linalg .la-head').textContent.includes('No matrix here')",
                               timeout=15000)
        assert page.locator(".se-addon-linalg .la-tools").is_hidden()
        assert errors == []
    doc = Document(Matrix([[1, 2, 3], [4, 5, 6]]), addons=[ADDON])
    with _page(doc) as (page, errors):
        page.wait_for_selector(".se-addon-linalg .la-props .la-row", timeout=15000)
        assert "need a square matrix" in page.locator(".se-addon-linalg .la-props").inner_text()
        assert page.locator('.se-addon-linalg .la-tool[data-kind="cholesky"]').is_disabled()
        page.locator('.se-addon-linalg .la-tool[data-kind="qr"]').click()
        page.wait_for_function("document.querySelector('.se-addon-linalg .la-decomp').textContent.includes('QR decomposition')",
                               timeout=15000)
        assert errors == []


def test_buttons_are_finger_sized_on_a_touch_screen():
    doc = Document(Matrix([[1, 2], [3, 4]]), addons=[ADDON])
    with _page(doc, has_touch=True, is_mobile=True, viewport={"width": 420, "height": 820}) as (page, errors):
        page.wait_for_selector(".se-addon-linalg .la-props .la-insert", timeout=15000)
        heights = page.evaluate("Array.from(document.querySelectorAll('.se-addon-linalg .la-panel button'))"
                                ".filter(b => b.offsetParent).map(b => b.getBoundingClientRect().height)")
        assert heights and min(heights) >= 44
        # nothing wider than the screen: the panel wraps
        width = page.evaluate("document.querySelector('.se-addon-linalg .la-panel').scrollWidth")
        assert width <= 420
        assert errors == []

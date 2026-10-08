"""The assumptions panel in a real browser: the table follows the selection,
an unknown explains itself, a switch retypes the symbol (undoable) and the
hint says what changed.  Needs Playwright with Chromium and the KaTeX CDN
(skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import log, sqrt, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_assumptions import ADDON  # noqa: E402

x, y = symbols("x y")
ED = "document.querySelector('.sympy-editor').__sympyEditor"
PANEL = ".se-addon-assumptions"


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def _value(page, pred):
    return page.locator(f'{PANEL} .as-fact[data-pred="{pred}"] .as-val').inner_text()


def _run(doc, body):
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
                page.wait_for_selector(f"{PANEL} .as-fact", timeout=30000)
                body(page)
                assert errors == []
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_table_selection_switch_hint_undo_and_guide():
    doc = Document(sqrt(x**2) + 1, addons=[ADDON])

    def body(page):
        assert "Whole formula" in page.locator(f"{PANEL} .as-subject").inner_text()
        assert _value(page, "positive") == "unknown"
        assert _value(page, "commutative") == "true"
        # an unknown, tapped: the reason, and the assumption that would decide it
        page.locator(f'{PANEL} .as-fact[data-pred="positive"]').click()
        why = page.locator(f"{PANEL} .as-why").inner_text()
        assert "SymPy cannot tell whether" in why and "depends on the value of x" in why
        # the selection: the constant 1
        one = next(p for p, n in doc.snapshot()["nodes"].items() if n["src"] == "1")
        page.evaluate(f"{ED}.select({one!r})")
        page.wait_for_function(f"document.querySelector('{PANEL} .as-subject').textContent.includes('Selection')")
        page.wait_for_function(f"document.querySelector('{PANEL} .as-fact[data-pred=\"odd\"] .as-val').textContent === 'true'")
        page.evaluate(f"{ED}.select(null)")
        # x's switch: positive everywhere - the formula rewrites itself
        chip = f'{PANEL} .as-sym[data-name="x"] .as-chip[data-pred="positive"]'
        page.locator(chip).click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'x + 1'")
        page.wait_for_selector(f"{PANEL} .as-hint:not([hidden]) .as-rewrote")
        assert "as-given-true" in page.locator(chip).get_attribute("class")
        assert doc.history_labels()["actions"][-1] == "Assumptions: x positive"
        page.wait_for_function(f"document.querySelector('{PANEL} .as-fact[data-pred=\"positive\"] .as-val').textContent === 'true'")
        # undo: the old symbol and the old formula
        page.locator('.se-toolbar [data-cmd="undo"]').click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'sqrt(x**2) + 1'")
        page.wait_for_function(f"!document.querySelector('{PANEL} .as-sym[data-name=\"x\"] .as-chip[data-pred=\"positive\"]').classList.contains('as-given-true')")
        assert page.locator(f"{PANEL} .as-hint").is_hidden()
        # the guide
        page.locator(f"{PANEL} .se-addon-help").click()
        assert "everywhere" in page.locator(".se-help-view").inner_text()
        page.keyboard.press("Escape")

    _run(doc, body)


def test_a_suggestion_and_the_simplification_applied():
    doc = Document(log(x) + log(y), addons=[ADDON])

    def body(page):
        # "positive" of x is unknown; the reason offers to assume it
        path = next(p for p, n in doc.snapshot()["nodes"].items() if n["src"] == "x")
        page.evaluate(f"{ED}.select({path!r})")
        page.wait_for_function(f"document.querySelector('{PANEL} .as-subject').textContent.includes('Selection')")
        page.locator(f'{PANEL} .as-fact[data-pred="positive"]').click()
        page.locator(f'{PANEL} .as-why .as-suggest[data-name="x"]').click()
        page.wait_for_selector(f"{PANEL} .as-hint:not([hidden]) .as-simpl")
        assert "log(x) + log(y)" in page.locator(f"{PANEL} .as-hint").inner_text()
        page.locator(f"{PANEL} .as-apply").click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'log(x*y)'")
        assert doc.history_labels()["actions"][-2:] == ["Assumptions: x positive", "Assumptions: simplify"]

    _run(doc, body)

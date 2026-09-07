"""The Feynman panel in a real browser: an example started, the expansion,
the drawings, a click selecting a term.  Needs Playwright with Chromium and
the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_feynman import ADDON, Diagram  # noqa: E402


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def test_the_panel_expands_draws_and_selects():
    doc = Document("PathIntegral(psi(x_1)*psibar(x_2))", addons=[ADDON])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(srv.url)
        page.wait_for_selector(".se-addon-feynman .fd-panel", timeout=30000)
        panel = page.locator(".se-addon-feynman")
        assert "No diagram yet" in panel.inner_text() or panel.locator(".fd-empty").count() == 1
        assert not panel.locator(".fd-expand").is_disabled()                    # the whole expression is the integral
        # expand to order 2: two cards, drawn
        panel.locator(".fd-order").fill("2")
        panel.locator(".fd-expand").click()
        page.wait_for_function("document.querySelectorAll('.fd-card').length === 2", timeout=15000)
        assert isinstance(doc.expr.args[1], Diagram)
        second = panel.locator(".fd-card").nth(1)
        assert second.locator("path.fd-fermion").count() == 3 and second.locator("path.fd-photon").count() == 1
        assert second.locator("circle.fd-vertex").count() == 2 and second.locator("circle.fd-external").count() == 2
        assert second.locator("polygon.fd-arrow").count() == 3
        assert "order e^2" in second.locator(".fd-caption").inner_text()
        assert "2 diagrams" in panel.locator(".fd-hint").inner_text()
        # a click on a card selects its term; the card shows it
        second.click()
        page.wait_for_function("document.querySelector('.fd-card:nth-child(2)').classList.contains('fd-selected')")
        assert page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.selected") == "/1"
        assert page.locator(".se-view .katex").count() >= 1
        # an example from the menu replaces the expression
        panel.locator(".fd-examples").select_option("PathIntegral(A(mu, x_1)*A(nu, x_2))")
        page.wait_for_function("document.querySelector('.sympy-editor').__sympyEditor.state.src === 'PathIntegral(A(mu, x_1)*A(nu, x_2))'", timeout=15000)
        panel.locator(".fd-which").select_option("all")
        panel.locator(".fd-expand").click()
        page.wait_for_function("document.querySelectorAll('.fd-card').length === 3", timeout=15000)
        assert any("−1/2" in t or "-1/2" in t for t in panel.locator(".fd-caption").all_inner_texts())
        # the guide
        page.locator(".se-addon-feynman .se-addon-help").click()
        assert "Wick" in page.locator(".se-help-view").inner_text()
        page.keyboard.press("Escape")
        assert errors == []
        browser.close()
    srv.shutdown()
    srv.server_close()

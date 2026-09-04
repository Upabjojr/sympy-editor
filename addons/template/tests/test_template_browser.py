"""The template's panel in a real browser: the tool button, the query, the
guide.  Needs Playwright with Chromium and the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_addon_template import ADDON  # noqa: E402

x = symbols("x")


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def test_panel_tool_query_and_guide():
    doc = Document(sin(x) + x, addons=[ADDON])
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
        page.wait_for_selector(".se-addon-template .tpl-panel", timeout=30000)
        assert "Hello from Python" in page.locator(".tpl-info").inner_text()          # client_options reached the panel
        page.wait_for_function("document.querySelector('.tpl-info').textContent.includes('2 argument(s)')")   # contribute()
        page.locator(".tpl-panel button").click()                                       # a query
        page.wait_for_function("document.querySelector('.tpl-out').textContent.includes('has 2 argument(s)')")
        assert not doc.can_undo
        page.locator('.se-toolbar [data-cmd="addon:template:double"]').click()          # a toolbar tool: a change
        page.wait_for_function("document.querySelector('.se-source').textContent === '2*x + 2*sin(x)'")
        assert doc.expr == 2 * (sin(x) + x) and doc.history_labels()["actions"][-1] == "Template: doubled"
        page.locator(".se-addon-template .se-addon-help").click()                       # the guide
        assert "guide" in page.locator(".se-help-view").inner_text().lower()
        page.keyboard.press("Escape")
        assert errors == []
        browser.close()
    srv.shutdown()
    srv.server_close()

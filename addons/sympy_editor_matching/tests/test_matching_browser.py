"""The rules panel in a real browser: a rule is added, edited in place,
opened in the formula editor and saved back.  Needs Playwright with
Chromium, the KaTeX CDN and sympy-matching (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import cos, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("sympy_matching")
playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_matching import ADDON, RewriteRule  # noqa: E402

x = symbols("x")


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def test_a_rule_can_be_edited_as_text_and_in_the_editor():
    doc = Document(sin(x) ** 2, addons=[ADDON])
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
        page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
        page.wait_for_selector(".se-addon-matching .mt-field", timeout=10000)
        page.locator(".mt-field").fill("sin(a_)**2 -> 1 - cos(a_)**2")
        page.locator(".mt-field").press("Enter")
        page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 1")
        # in place: the pencil shows the text form, Enter saves it
        page.locator(".mt-rules li .mt-edit").click()
        field = page.locator(".mt-rules li input")
        assert field.input_value() == "sin(a_)**2 -> 1 - cos(a_)**2"
        field.fill("sin(a_)**2 -> 1/2 - cos(2*a_)/2")
        field.press("Enter")
        page.wait_for_function("document.querySelector('.mt-rules li .mt-formula') !== null")
        page.wait_for_function("document.querySelector('.mt-hit .mt-result') && document.querySelector('.mt-hit .mt-result').textContent.includes('cos(2*x)')")
        assert "cos(2*a_)" in ADDON.rules(doc)[0].__str__()
        # in the editor: the rule becomes the formula, its side is edited there, Save puts it back
        page.locator(".mt-rules li .mt-open").click()
        page.wait_for_function("document.querySelector('.se-source').textContent.startsWith('Rule(')")
        assert isinstance(doc.expr, RewriteRule)
        page.wait_for_function("document.querySelector('.se-addon-matching .mt-head button').textContent === 'Save as rule 1'")
        page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.send({action: 'set', src: 'Rule(sin(a_)**2, 1 - cos(a_)**2)'})")
        page.wait_for_function("document.querySelector('.se-source').textContent === 'Rule(sin(a_)**2, 1 - cos(a_)**2)'")
        page.locator(".se-addon-matching .mt-head button", has_text="Save as rule 1").click()
        page.wait_for_function("document.querySelector('.se-addon-matching .mt-head button').textContent === 'Use selection as rule'")
        assert str(ADDON.rules(doc)[0]) == "Rule(sin(a_)**2, 1 - cos(a_)**2)"
        page.locator('.se-toolbar [data-cmd="undo"]').click()   # the formula comes back (two steps: open, set)
        page.wait_for_function("document.querySelector('.se-source').textContent.startsWith('Rule(sin(a_)**2, 1/2')")
        page.locator('.se-toolbar [data-cmd="undo"]').click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'sin(x)**2'")
        # the panel's "?" opens the add-on's guide in the editor's help overlay
        page.locator(".se-addon-matching .se-addon-help").click()
        guide = page.locator(".se-help-view")
        assert guide.is_visible() and "wildcard" in guide.inner_text().lower()
        page.keyboard.press("Escape")
        assert page.locator(".se-help-view").count() == 0
        assert errors == []
        browser.close()
    srv.shutdown()
    srv.server_close()

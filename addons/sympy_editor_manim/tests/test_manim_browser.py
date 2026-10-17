"""The Manim panel in a real browser: the script shown, following the
settings and the history, saved as a file; the render note; the guide.
Needs Playwright with Chromium and the KaTeX CDN (skipped otherwise); the
render itself only with Manim installed and SYMPY_EDITOR_SLOW_TESTS=1."""
import os
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
from sympy_editor_manim import ADDON  # noqa: E402
from sympy_editor_manim import render as rendering  # noqa: E402

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
                page.wait_for_selector(".se-addon-manim .mn-panel", timeout=10000)
                page.errors = errors
                yield page
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _code(page):
    return page.locator(".se-addon-manim .mn-code").inner_text()


def test_the_script_follows_the_settings_and_the_history_and_is_saved():
    doc = Document((x + 1) ** 2, addons=[ADDON])
    doc.handle({"action": "apply", "path": "/", "op": "expand"})
    doc.handle({"action": "replace", "path": "/0", "src": "x**3"})
    with _served(doc) as page:
        panel = page.locator(".se-addon-manim")
        page.wait_for_function("document.querySelector('.se-addon-manim .mn-count').textContent === '3 steps'")
        assert panel.locator(".mn-from option").count() == 3
        assert panel.locator(".mn-from").input_value() == "0" and panel.locator(".mn-to").input_value() == "2"
        assert panel.locator(".mn-code").is_hidden() and panel.locator(".mn-copy").is_disabled()
        # Script: the file appears, with every step
        panel.locator(".mn-script").click()
        page.wait_for_function("document.querySelector('.se-addon-manim .mn-code').textContent.includes('class FormulaSteps')")
        code = _code(page)
        assert 'r"Transform: Expand"' in code and "x^{3}" in code and "STYLE = 'matching'" in code
        assert panel.locator(".mn-copy").is_enabled() and panel.locator(".mn-save").is_enabled()
        # A setting changed rewrites it
        panel.locator(".mn-style").select_option("write")
        page.wait_for_function("document.querySelector('.se-addon-manim .mn-code').textContent.includes(\"STYLE = 'write'\")")
        panel.locator(".mn-to").select_option(index=1)
        page.wait_for_function("!document.querySelector('.se-addon-manim .mn-code').textContent.includes('x^{3}')")
        panel.locator(".mn-title").fill("Worked")
        page.wait_for_function("document.querySelector('.se-addon-manim .mn-code').textContent.includes(\"TITLE = 'Worked'\")")
        # An edit in the page: a fourth step, and the last step follows the history's end
        page.evaluate(ED + ".send({action: 'apply', path: '/', op: 'factor'})")
        page.wait_for_function("document.querySelector('.se-addon-manim .mn-count').textContent === '4 steps'")
        assert panel.locator(".mn-to").input_value() == "1"            # a chosen last step stays
        panel.locator(".mn-to").select_option(index=3)
        page.wait_for_function("document.querySelector('.se-addon-manim .mn-code').textContent.includes('Transform: Factor')")
        # Save: the file goes out as a download on a plain page
        with page.expect_download() as dl:
            panel.locator(".mn-save").click()
        download = dl.value
        assert download.suggested_filename == "formula_steps.py"
        saved = Path(download.path()).read_text(encoding="utf-8")
        assert "class FormulaSteps(Scene)" in saved and "Transform: Factor" in saved
        # The render row says whether Manim is here
        page.wait_for_function("!document.querySelector('.se-addon-manim .mn-note').textContent.startsWith('Checking')")
        note = panel.locator(".mn-note").inner_text()
        if rendering.status()["available"]:
            assert "is here" in note and panel.locator(".mn-render").is_enabled()
        else:
            assert "No video here" in note and panel.locator(".mn-render").is_disabled()
        # The guide
        panel.locator(".se-addon-help").click()
        assert "TransformMatchingTex" in page.locator(".se-help-view").inner_text()
        page.keyboard.press("Escape")
        assert page.errors == []


@pytest.mark.skipif(not rendering.status()["available"] or not os.environ.get("SYMPY_EDITOR_SLOW_TESTS"),
                    reason="Manim is not installed here, or SYMPY_EDITOR_SLOW_TESTS is not set")
def test_a_render_plays_in_the_panel():
    doc = Document((x + 1) ** 2, addons=[ADDON])
    doc.handle({"action": "apply", "path": "/", "op": "expand"})
    with _served(doc) as page:
        panel = page.locator(".se-addon-manim")
        page.wait_for_function("document.querySelector('.se-addon-manim .mn-render') && !document.querySelector('.se-addon-manim .mn-render').disabled",
                               timeout=30000)
        panel.locator(".mn-render").click()
        page.wait_for_selector(".se-addon-manim video.mn-video", timeout=300000)
        assert panel.locator(".mn-save-video").get_attribute("download") == "formula_steps.mp4"
        assert "Rendered" in panel.locator(".mn-note").inner_text()
        assert page.errors == []

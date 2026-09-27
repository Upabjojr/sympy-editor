"""The console's panel in a real browser, on the local server: typing at
``In [n]:``, the typeset ``Out[n]``, an unfinished block, the formula changed
from Python and taken back with Undo, the selection as ``editor.selection``,
a script, and the guide.  Needs Playwright with Chromium and the KaTeX CDN
(skipped otherwise)."""
import os
import sys
import threading
import time
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from sympy import cos, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_console import ADDON  # noqa: E402

x, y = symbols("x y")
ED = "document.querySelector('.sympy-editor').__sympyEditor"


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


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
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-console .pc-input", timeout=30000)
            page.wait_for_function(f"{ED} && {ED}.state && !{ED}.busy")
            yield page
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _enter(page, code):
    """Type `code` at the prompt and press Enter, and wait for the answer."""
    field = page.locator(".se-addon-console .pc-input")
    field.click()
    field.fill(code)
    before = page.locator(".se-addon-console .pc-entry").count()
    field.press("Enter")
    page.wait_for_function(f"document.querySelectorAll('.se-addon-console .pc-entry').length > {before}")
    page.wait_for_function(f"!{ED}.busy")
    return page.locator(".se-addon-console .pc-entry").last


def _source(page):
    return page.locator(".se-source").inner_text().strip()


def test_in_and_out_as_ipython():
    doc = Document(sin(x) + x, addons=[ADDON])
    with _page(doc) as page:
        assert page.locator(".se-addon-console .pc-input-row .pc-prompt").inner_text() == "In [1]:"
        entry = _enter(page, "a = 2\nprint('a is', a)\na*x")
        assert entry.locator(".pc-prompt-in").inner_text() == "In [1]:"
        assert entry.locator(".pc-stdout").inner_text().strip() == "a is 2"
        assert entry.locator(".pc-prompt-out").inner_text() == "Out[1]:"
        page.wait_for_function("document.querySelector('.se-addon-console .pc-entry:last-child .pc-math .katex') !== null")   # typeset
        assert page.locator(".se-addon-console .pc-input-row .pc-prompt").inner_text() == "In [2]:"
        entry = _enter(page, "_ + 1")
        assert entry.locator(".pc-math").get_attribute("title") == "2*x + 1"
        entry = _enter(page, "1/0")
        assert "ZeroDivisionError" in entry.locator(".pc-error").inner_text()
        assert not doc.can_undo                                             # nothing of it touched the formula


def test_an_unfinished_block_asks_for_more():
    doc = Document(x, addons=[ADDON])
    with _page(doc) as page:
        field = page.locator(".se-addon-console .pc-input")
        field.click()
        field.fill("for i in range(3):")
        field.press("Enter")
        page.wait_for_function("document.querySelector('.se-addon-console .pc-input').value === 'for i in range(3):\\n    '")
        assert page.locator(".se-addon-console .pc-entry").count() == 0
        field.press_sequentially("print(i)")
        field.press("Enter")                                                # a body wants an empty line
        page.wait_for_function("document.querySelector('.se-addon-console .pc-input').value.endsWith('print(i)\\n    ')")
        field.press("Enter")
        page.wait_for_selector(".se-addon-console .pc-entry")
        assert page.locator(".se-addon-console .pc-entry .pc-stdout").inner_text().split() == ["0", "1", "2"]
        # ↑ brings it back
        field.press("ArrowUp")
        assert page.evaluate("document.querySelector('.se-addon-console .pc-input').value").startswith("for i in range(3):")


def test_the_formula_from_python_and_back_with_undo():
    doc = Document((x + 1) ** 2, addons=[ADDON])
    with _page(doc) as page:
        _enter(page, "editor.expr = expand(editor.expr)")
        page.wait_for_function("document.querySelector('.se-source').textContent.trim() === 'x**2 + 2*x + 1'")
        assert doc.history_labels()["actions"][-1] == "Console: editor.expr = expand(editor.expr)"
        page.locator('.se-toolbar [data-cmd="undo"]').click()
        page.wait_for_function("document.querySelector('.se-source').textContent.trim() === '(x + 1)**2'")


def test_the_selection_is_editor_selection():
    doc = Document(sin(x) + cos(y), addons=[ADDON])
    with _page(doc) as page:
        path = next(p for p, info in doc.snapshot()["nodes"].items() if info["src"] == "cos(y)")
        page.evaluate(f"p => {ED}.select(p)", path)
        entry = _enter(page, "editor.selection")
        assert entry.locator(".pc-math").get_attribute("title") == "cos(y)"
        _enter(page, "editor.selection = editor.selection.rewrite(exp)")
        page.wait_for_function("document.querySelector('.se-source').textContent.includes('exp(I*y)')")
        assert doc.expr.has(sin(x)) and not doc.expr.has(cos(y))
        # Use: an Out[n] into the formula - the whole of it, with nothing selected
        page.evaluate(f"{ED}.select(null)")
        entry = _enter(page, "factor(x**2 - 1)")
        entry.locator(".pc-use").click()
        page.wait_for_function("document.querySelector('.se-source').textContent.trim() === '(x - 1)*(x + 1)'")


def test_a_script_runs_and_leaves_its_names():
    doc = Document(sin(x) ** 2 + cos(x) ** 2, addons=[ADDON])
    with _page(doc) as page:
        page.locator(".se-addon-console .pc-tab[data-mode='script']").click()
        assert page.locator(".se-addon-console .pc-script").is_visible()
        assert not page.locator(".se-addon-console .pc-input").is_visible()
        page.locator(".se-addon-console .pc-script").fill(
            "def twice(e):\n    return 2*e\n\nprint(__name__)\neditor.expr = simplify(editor.expr)\n")
        page.locator(".se-addon-console .pc-scripting .pc-run").click()
        page.wait_for_function("document.querySelector('.se-addon-console .pc-script-out .pc-note') !== null")
        assert page.locator(".se-addon-console .pc-script-out .pc-stdout").inner_text().strip() == "__main__"
        page.wait_for_function("document.querySelector('.se-source').textContent.trim() === '1'")
        assert doc.history_labels()["actions"][-1] == "Script: script.py"
        page.locator(".se-addon-console .pc-tab[data-mode='console']").click()
        entry = _enter(page, "twice(x)")                                    # the script's function, in the console
        assert entry.locator(".pc-math").get_attribute("title") == "2*x"


def test_the_guide():
    doc = Document(x, addons=[ADDON])
    with _page(doc) as page:
        page.locator(".se-addon-console .se-addon-help").click()
        text = page.locator(".se-help-view").inner_text()
        assert "editor.selection" in text and "%time" in text
        page.keyboard.press("Escape")


def test_a_runaway_loop_is_interrupted():
    doc = Document(x, addons=[ADDON])
    with _page(doc) as page:
        field = page.locator(".se-addon-console .pc-input")
        field.click()
        field.fill("n = 0\nwhile True:\n    n += 1\n")
        field.press("Control+Enter")
        page.locator(".se-interrupt").wait_for(state="visible", timeout=15000)
        page.locator(".se-interrupt").click()
        page.wait_for_selector(".se-addon-console .pc-entry .pc-error", timeout=15000)
        assert "Interrupted" in page.locator(".se-addon-console .pc-entry .pc-error").inner_text()
        page.wait_for_function(f"!{ED}.busy")
        entry = _enter(page, "n > 0")                                        # the console goes on, variables and all
        assert entry.locator(".pc-math").inner_text() == "True"


@pytest.mark.skipif(not os.environ.get("SYMPY_EDITOR_SLOW_TESTS"), reason="set SYMPY_EDITOR_SLOW_TESTS=1")
def test_a_standalone_page_runs_the_console_in_pyodide(tmp_path):
    from sympy_editor import save_html
    path = save_html((x + 1) ** 2, tmp_path / "console.html", addons=[ADDON])
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(path.as_uri())
        page.wait_for_selector(".se-addon-console .pc-input", timeout=60000)
        page.wait_for_function("document.querySelector('.se-loading').hidden", timeout=240000)
        entry = _enter(page, "import sys\nprint(sys.platform)\neditor.expr = expand(editor.expr)")
        assert entry.locator(".pc-stdout").inner_text().strip() == "emscripten"      # Python in the page itself
        page.wait_for_function("document.querySelector('.se-source').textContent.trim() === 'x**2 + 2*x + 1'")
        page.locator(".se-addon-console .pc-tab[data-mode='script']").click()
        page.locator(".se-addon-console .pc-script").fill("print(editor.expr.coeff(x))\n")
        page.locator(".se-addon-console .pc-scripting .pc-run").click()
        page.wait_for_function("document.querySelector('.se-addon-console .pc-script-out .pc-stdout')", timeout=60000)
        assert page.locator(".se-addon-console .pc-script-out .pc-stdout").inner_text().strip() == "2"
        assert errors == []
        browser.close()

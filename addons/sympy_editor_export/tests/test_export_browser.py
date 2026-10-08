"""The export panel in a real browser, on the local server: the whole
formula and the selection, the tabs and their options, a function by
codegen, a printer's refusal in words, Copy and Save through the host app
and through the browser, the guide.  Needs Playwright with Chromium and the
KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from sympy import Matrix, besselj, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_export import ADDON  # noqa: E402

x, y = symbols("x y")
ED = "document.querySelector('.sympy-editor').__sympyEditor"
CODE = "Array.from(document.querySelectorAll('.se-addon-export .ex-code')).map(e => e.textContent)"


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
            ctx = browser.new_context(**context)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-export .ex-panel", timeout=30000)
            page.wait_for_function(f"{ED} && {ED}.state && !{ED}.busy")
            yield page
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _code_is(page, text, index=0):
    page.wait_for_function(f"({CODE})[{index}] === {text!r}", timeout=10000)


def _tab(page, key):
    page.locator(f'.se-addon-export .ex-tab[data-format="{key}"]').click()


def test_formats_selection_and_options():
    doc = Document(sin(x) / y + x**2, addons=[ADDON])
    with _page(doc) as page:
        _code_is(page, r"x^{2} + \frac{\sin{\left(x \right)}}{y}")          # LaTeX of the whole formula first
        assert "whole formula" in page.locator(".se-addon-export .ex-target").inner_text()
        tabs = page.locator(".se-addon-export .ex-tab").all_inner_texts()
        assert tabs == ["LaTeX", "MathML", "Python", "C", "Fortran", "JavaScript", "Octave/MATLAB", "Julia", "Rust", "Function"]
        _tab(page, "c")
        _code_is(page, "pow(x, 2) + sin(x)/y")
        page.locator('.se-addon-export [data-option="assign"]').fill("r")
        _code_is(page, "r = pow(x, 2) + sin(x)/y;")
        path = page.evaluate(f"Object.keys({ED}.state.nodes).find(p => {ED}.state.nodes[p].src === 'x**2')")
        page.evaluate(f"{ED}.select({path!r})")                              # the selection only
        _code_is(page, "r = pow(x, 2);")
        assert "x**2" in page.locator(".se-addon-export .ex-target").inner_text()
        page.evaluate(f"{ED}.select(null)")
        _tab(page, "python")
        _code_is(page, "import math\n\nx**2 + math.sin(x)/y")
        page.locator('.se-addon-export select[data-option="module"]').select_option("numpy")
        _code_is(page, "import numpy\n\nx**2 + numpy.sin(x)/y")
        _tab(page, "c")                                                     # the options were kept per format
        assert page.locator('.se-addon-export [data-option="assign"]').input_value() == "r"
        _tab(page, "function")
        page.wait_for_function(f"({CODE}).length === 2")
        assert page.locator(".se-addon-export .ex-name").all_inner_texts() == ["f.c", "f.h"]
        page.locator('.se-addon-export [data-option="name"]').fill("area")
        page.wait_for_function(f"({CODE})[0].includes('double area(double x, double y)')")
        page.locator('.se-addon-export [data-option="header"]').uncheck()
        page.wait_for_function(f"({CODE}).length === 1")
        page.locator('.se-addon-export [data-option="name"]').fill("2bad")
        page.wait_for_selector(".se-addon-export .ex-error:not([hidden])")
        assert "plain name" in page.locator(".se-addon-export .ex-error").inner_text()
        assert page.locator(".se-addon-export .ex-code").count() == 0
        assert not doc.can_undo                                             # nothing changed the formula


def test_unsupported_and_refused():
    with _page(Document(besselj(1, x), addons=[ADDON])) as page:
        _tab(page, "javascript")
        page.wait_for_function(f"({CODE})[0] && ({CODE})[0].startsWith('// Not supported in JavaScript:')")
        assert "besselj" in page.locator(".se-addon-export .ex-notes").inner_text()
    with _page(Document(Matrix([[x, y]]), addons=[ADDON])) as page:
        _tab(page, "rust")
        page.wait_for_selector(".se-addon-export .ex-error:not([hidden])")
        assert page.locator(".se-addon-export .ex-error").inner_text().startswith("Rust cannot write this:")
        _tab(page, "c")
        _code_is(page, "M[0] = x;\nM[1] = y;")
        assert page.locator(".se-addon-export .ex-error").is_hidden()


def test_copy_and_save_through_the_host_app():
    with _page(Document(x**2, addons=[ADDON])) as page:
        _code_is(page, "x^{2}")
        page.evaluate("window.__got = []; window.SympyEditorApp = {"
                      " copyText: function (t) { window.__got.push(['copy', t]); },"
                      " saveFile: function (n, m, t) { window.__got.push(['save', n, m, t]); } }")
        page.locator(".se-addon-export .ex-copy").click()
        page.locator(".se-addon-export .ex-save").click()
        page.wait_for_function("window.__got.length === 2")
        assert page.evaluate("window.__got") == [["copy", "x^{2}"], ["save", "formula.tex", "application/x-tex", "x^{2}"]]
        page.evaluate("delete window.SympyEditorApp")


def test_copy_and_save_in_a_browser():
    with _page(Document(x**2, addons=[ADDON]), permissions=["clipboard-read", "clipboard-write"], accept_downloads=True) as page:
        _tab(page, "octave")
        _code_is(page, "x.^2")
        page.locator(".se-addon-export .ex-copy").click()
        page.wait_for_function("navigator.clipboard.readText().then(t => t === 'x.^2')")
        page.wait_for_function("document.querySelector('.se-status').textContent.includes('Copied formula.m')")
        with page.expect_download() as dl:
            page.locator(".se-addon-export .ex-save").click()
        assert dl.value.suggested_filename == "formula.m"
        assert Path(dl.value.path()).read_text() == "x.^2"


def test_closed_box_asks_nothing_and_the_guide():
    with _page(Document(x + 1, addons=[ADDON])) as page:
        _code_is(page, "x + 1")
        page.evaluate("document.querySelector('.se-addon-export').open = false")
        page.evaluate(f"window.__calls = 0; var ed = {ED}, s = ed.send.bind(ed);"
                      " ed.send = function (msg) { if (msg && msg.action === 'addon') window.__calls++; return s.apply(null, arguments); }")
        page.evaluate(f"{ED}.send({{action: 'set', src: 'x + 2'}})")
        page.wait_for_function(f"{ED}.state.src === 'x + 2' && !{ED}.busy")
        assert page.evaluate("window.__calls") == 0
        page.evaluate("document.querySelector('.se-addon-export').open = true")
        _code_is(page, "x + 2")
        page.locator(".se-addon-export .se-addon-help").click()
        assert "codegen" in page.locator(".se-help-view").inner_text()
        page.keyboard.press("Escape")

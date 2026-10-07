"""The transforms panel in a real browser: the variable list follows the
selection, Compute shows the result and its conditions without changing
the formula, Apply replaces the selection (unevaluated with the editor's
toggle on), the Transform menu's op asks for the variables, the guide.
Needs Playwright with Chromium and the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import Eq, Symbol, exp, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_transforms import ADDON  # noqa: E402

t, s, y = symbols("t s y")


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def _select(page, path):
    """Click the node at ``path``, then walk up to it with the arrow key."""
    el = page.locator(f'[data-path="{path}"]')
    box = el.bounding_box()
    el.click(force=True, position={"x": box["width"] / 2, "y": box["height"] / 2})
    for _ in range(10):
        sel = page.locator(".se-selected[data-path]")
        if sel.count() and sel.first.get_attribute("data-path") == path:
            return
        page.keyboard.press("ArrowUp")
    raise AssertionError(f"could not select {path}")


def _source(page, text):
    page.wait_for_function("t => document.querySelector('.se-source').textContent === t", arg=text)


@pytest.fixture
def served():
    def start(expr):
        doc = Document(expr, addons=[ADDON])
        srv = EditorServer(doc, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return doc, srv
    servers = []
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        try:
            yield browser, start
        finally:
            browser.close()
            for srv in servers:
                srv.shutdown()
                srv.server_close()


def _open(browser, srv):
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(srv.url)
    page.wait_for_selector(".se-addon-transforms .tf-panel", timeout=30000)
    return page, errors


def test_compute_apply_unevaluated_and_the_guide(served):
    browser, start = served
    doc, srv = start(Eq(y, exp(-2 * t)))
    page, errors = _open(browser, srv)
    panel = page.locator(".se-addon-transforms")
    _select(page, "/1")
    page.wait_for_function("document.querySelector('.tf-var').value === 't'")      # the selection's variable, chosen
    assert panel.locator(".tf-new").input_value() == "s"
    panel.locator(".tf-compute").click()                                            # a query
    page.wait_for_function("document.querySelector('.tf-conds').textContent.includes('Re(s) > -2')")
    assert panel.locator(".tf-result .katex").count() == 1                          # drawn by KaTeX
    assert not doc.can_undo
    panel.locator(".tf-apply").click()                                              # a step
    _source(page, "Eq(y, 1/(s + 2))")
    assert doc.history_labels()["actions"][-1] == "Transforms: Laplace transform, t → s"
    page.wait_for_function("document.querySelector('.tf-note').textContent.includes('converges for Re(s) > -2')")
    # back, and this time unevaluated: the editor's toggle decides
    page.locator('.se-toolbar [data-cmd="undo"]').click()
    _source(page, "Eq(y, exp(-2*t))")
    page.locator(".se-lazy-box").check()
    _select(page, "/1")
    panel.locator(".tf-apply").click()
    _source(page, "Eq(y, LaplaceTransform(exp(-2*t), t, s))")
    assert doc.history_labels()["actions"][-1].endswith("(unevaluated)")
    panel.locator(".se-addon-help").click()                                         # the guide
    assert "z-transform" in page.locator(".se-help-view").inner_text()
    page.keyboard.press("Escape")
    assert errors == []


def test_the_variable_list_follows_the_transform_and_errors_are_said(served):
    browser, start = served
    doc, srv = start(1 / (s + 3))
    page, errors = _open(browser, srv)
    panel = page.locator(".se-addon-transforms")
    panel.locator(".tf-kind").select_option("inverse_laplace")
    assert panel.locator(".tf-new").input_value() == "t"
    assert panel.locator(".tf-var").input_value() == "s"
    panel.locator(".tf-compute").click()
    page.wait_for_function("document.querySelector('.tf-conds').textContent.includes('t > 0')")
    # a new variable that is no name: said in the panel, nothing changed
    panel.locator(".tf-new").fill("2*t")
    panel.locator(".tf-apply").click()
    page.wait_for_function("document.querySelector('.tf-note').classList.contains('tf-error')")
    assert "must be a name" in panel.locator(".tf-note").inner_text()
    assert not doc.can_undo
    # Hankel asks for its order, the others do not
    assert panel.locator(".tf-extra-box").is_hidden()
    panel.locator(".tf-kind").select_option("hankel")
    assert panel.locator(".tf-extra-box").is_visible() and panel.locator(".tf-extra").input_value() == "0"
    assert errors == []


def test_the_transform_menu_asks_for_the_variables(served):
    browser, start = served
    doc, srv = start(exp(-2 * t))
    page, errors = _open(browser, srv)
    page.locator(".se-ops").click()
    page.locator('.se-pick-menu[data-for="se-ops"] >> text=Laplace transform…').first.click()
    form = page.locator(".se-fn-form")
    form.wait_for(state="visible")
    assert form.locator("select").input_value() == "t"                             # the variable, from the free symbols
    form.locator(".se-fn-apply").click()
    _source(page, "1/(s + 2)")
    page.wait_for_function("document.querySelector('.se-status').textContent.includes('Re(s) > -2')")
    assert errors == []

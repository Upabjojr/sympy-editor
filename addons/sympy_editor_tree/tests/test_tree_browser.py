"""The tree panel in a real browser: a click selects in the formula, a
double-click edits, the node menu offers the editor's tools.  Needs
Playwright with Chromium and the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import Mul, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_tree import ADDON  # noqa: E402

playwright = pytest.importorskip("playwright.sync_api")

x, y, z = symbols("x y z")


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


@pytest.fixture
def page_and_doc():
    doc = Document(x + y * z, addons=[ADDON])
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
        page.wait_for_selector(".se-addon-tree .tree-node", timeout=10000)
        page.errors = errors
        yield page, doc
        browser.close()
    srv.shutdown()
    srv.server_close()


def _node(page, label):
    """The tree node whose box says ``label``."""
    return page.locator(".tree-node").filter(has_text=label).first


def test_click_selects_in_the_formula_and_double_click_edits(page_and_doc):
    page, doc = page_and_doc
    _node(page, "Mul").click()
    page.wait_for_function("document.querySelector('.se-selected[data-path]') !== null")
    sel = page.locator(".se-selected[data-path]").first.get_attribute("data-path")
    assert doc.get(sel) == y * z                                  # the same node in the formula
    assert page.locator(".tree-node.tree-selected text").text_content() == "Mul"
    # the other way round: selecting in the formula marks the tree
    page.locator('.se-toolbar [data-cmd="parent"]').click()      # the enclosing expression, in the editor
    page.wait_for_function("document.querySelector('.tree-node.tree-selected text').textContent === 'Add'")
    # a double-click on a leaf edits its value
    _node(page, "y").dblclick()
    field = page.locator(".tree-edit")
    assert field.is_visible() and field.input_value() == "y"
    field.fill("2")
    field.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x + 2*z'")
    assert doc.expr == x + 2 * z
    # a double-click on a head changes it
    _node(page, "Mul").dblclick()
    page.locator(".tree-edit").fill("Add")
    page.locator(".tree-edit").press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x + z + 2'")
    assert doc.expr == x + z + 2
    assert page.errors == []


def test_the_node_menu_offers_the_editors_tools(page_and_doc):
    page, doc = page_and_doc
    _node(page, "Mul").click(button="right")
    menu = page.locator(".tree-menu")
    assert menu.is_visible()
    text = menu.inner_text()
    assert "Change head" in text and "Delete" in text and "Transform" in text and "Simplify" in text
    assert "Methods of Mul" in text
    menu.locator(".tree-item", has_text="Negate").click()          # a transformation, through the editor
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x - y*z'")
    assert doc.expr == x - y * z
    # the Node button opens the same menu for the selected node; Delete removes it
    _node(page, "x").click()
    page.locator(".tree-node-btn").click()
    page.locator(".tree-menu .tree-item", has_text="Delete").click()
    page.wait_for_function("document.querySelector('.se-source').textContent === '-y*z'")
    assert doc.expr == -y * z
    assert page.errors == []


def test_the_factors_of_a_fraction_select_its_pieces():
    """cos(x)**2 + sin(x)**2/x: Pow(sin(x), 2) is the numerator, Pow(x, -1)
    is drawn as the denominator x - clicking them selects those pieces, not
    the whole fraction."""
    from sympy import cos
    doc = Document(cos(x) ** 2 + sin(x) ** 2 / x, addons=[ADDON])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        page = browser.new_page()
        page.goto(srv.url)
        page.wait_for_selector(".se-addon-tree .tree-node", timeout=30000)
        pows = page.locator(".tree-node").filter(has_text="Pow")
        sel = lambda: page.locator(".se-selected[data-path]").first.get_attribute("data-path")
        for i in range(pows.count()):
            node = pows.nth(i)
            src = node.locator("title").text_content()
            node.click()
            page.wait_for_function("document.querySelector('.se-selected[data-path]') !== null")
            if src == "sin(x)**2":
                assert doc.get(sel()) == sin(x) ** 2 and sel().endswith("/n")
            elif src == "1/x":
                assert doc.get(sel()) == x and sel().endswith("/d")
            page.keyboard.press("Escape")
        browser.close()
    srv.shutdown()
    srv.server_close()


def test_the_history_view_shows_a_tree_under_every_step(page_and_doc):
    """The History view (a self-contained page in a frame) carries the tree
    of each step in a collapsible box, the new nodes marked."""
    page, doc = page_and_doc
    page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.send({action: 'set', src: 'x + y*z + 1'})")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x + y*z + 1'")
    page.locator('.se-toolbar [data-cmd="history"]').click()
    frame = page.frame_locator(".se-history-frame")
    frame.locator("section.step").nth(1).wait_for(timeout=15000)
    assert frame.locator("section.step").count() == 2
    assert frame.locator("section.step details.tree-history svg").count() == 2
    assert frame.locator("section.step details.tree-history summary").first.text_content() == "Expression tree"
    # the second step's tree marks what the first did not have: the 1
    added = frame.locator("section.step").nth(1).locator(".tree-node.tree-added text")
    assert "1" in [t.text_content() for t in added.all()]
    assert frame.locator("section.step").nth(0).locator(".tree-node.tree-added").count() == 0
    # a click on a box's heading folds it and does not open the step (the view stays)
    frame.locator("section.step details.tree-history summary").first.click()
    page.wait_for_timeout(300)
    assert page.locator(".se-history-view").count() == 1
    assert frame.locator("section.step details.tree-history").first.get_attribute("open") is None
    # the strip's buttons do all of them at once
    page.locator(".se-history-head .tree-expand-all").click()
    page.wait_for_function("Array.from(document.querySelector('.se-history-frame').contentDocument.querySelectorAll('details.tree-history')).every(d => d.open)")
    page.locator(".se-history-head .tree-collapse-all").click()
    page.wait_for_function("Array.from(document.querySelector('.se-history-frame').contentDocument.querySelectorAll('details.tree-history')).every(d => !d.open)")
    page.keyboard.press("Escape")
    assert page.errors == []

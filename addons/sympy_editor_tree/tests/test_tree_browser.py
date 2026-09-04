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

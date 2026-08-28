"""End-to-end tests of the JavaScript front end in a real (headless) browser.

Requirements (dev only, never shipped):  ``pip install playwright`` and
``python -m playwright install chromium``.  The page loads KaTeX from its CDN,
so network access is needed.  Everything is skipped automatically when these
are unavailable.  Set ``SYMPY_EDITOR_SLOW_TESTS=1`` to also exercise the
self-contained Pyodide page (downloads Pyodide + SymPy, ~30 s).
"""

import os
import socket
import threading
import urllib.request
from contextlib import closing

import pytest
from sympy import Array, Matrix, sin, symbols

from sympy_editor import Document, to_html
from sympy_editor.html import default_urls
from sympy_editor.server import EditorServer

playwright = pytest.importorskip("playwright.sync_api")

x, y, z = symbols("x y z")


def _online(url: str) -> bool:
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


@pytest.fixture(scope="module")
def browser():
    try:
        with playwright.sync_playwright() as p:
            try:
                b = p.chromium.launch()
            except Exception as exc:  # browser binary not installed
                pytest.skip(f"chromium not available: {exc}")
            yield b
            b.close()
    except Exception as exc:
        pytest.skip(f"playwright unavailable: {exc}")


@pytest.fixture
def serve_expr():
    """Factory: serve_expr(expr) -> (server, document); servers stop at teardown."""
    servers = []

    def _serve(expr):
        doc = Document(expr)
        srv = EditorServer(doc, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv, doc

    yield _serve
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def served():
    """A running EditorServer for x**2/y - sin(x); yields (server, document)."""
    doc = Document(x**2 / y - sin(x))
    srv = EditorServer(doc, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, doc
    srv.shutdown()
    srv.server_close()


def _click(page, path):
    """Click the centre of the annotated element for ``path``.

    ``force=True`` skips Playwright's actionability check, which otherwise
    refuses because KaTeX stacks an empty ``.vlist`` strut over glyphs in
    fractions; the editor resolves the hit with ``elementsFromPoint``."""
    page.locator(f'[data-path="{path}"]').click(force=True)


def _open(browser, url):
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    page.errors = errors
    return page


def test_render_select_and_walk_up(browser, served):
    srv, doc = served
    page = _open(browser, srv.url)
    assert page.locator(".se-source").inner_text() == "x**2/y - sin(x)"
    assert page.locator(".se-status").inner_text().startswith("Click")
    _click(page, "/1/1/0")                            # y in the denominator
    assert page.locator(".se-status").inner_text() == "Symbol: y"
    assert page.locator(".se-selected").get_attribute("data-path") == "/1/1/0"
    _click(page, "/1/1/0")                            # same spot again -> parent
    assert page.locator(".se-status").inner_text().startswith("Mul:")
    page.keyboard.press("ArrowUp")
    assert page.locator(".se-status").inner_text().startswith("Add:")
    page.keyboard.press("Escape")
    assert page.locator(".se-selected").count() == 0
    assert page.errors == []


def test_in_place_edit_commits_to_python(browser, served):
    srv, doc = served
    page = _open(browser, srv.url)
    _click(page, '/1/1/0')
    page.keyboard.type("z")                           # typing starts an in-place edit
    field = page.locator(".se-inline")
    assert field.count() == 1
    assert field.evaluate("e => e.parentNode.getAttribute('data-path')") == "/1/1/0"
    assert field.input_value() == "z"
    page.keyboard.type("**2")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('z**2')")
    assert doc.expr == x**2 / z**2 - sin(x)
    assert page.locator(".se-inline").count() == 0
    assert page.locator(".se-selected").get_attribute("data-path") == "/1/1/0"
    # Escape restores the rendering without changes
    page.keyboard.press("Enter")
    assert page.locator(".se-inline").input_value() == "z"
    page.keyboard.press("Escape")
    assert page.locator(".se-inline").count() == 0
    assert page.locator(".se-view .katex").count() == 1
    assert doc.expr == x**2 / z**2 - sin(x)
    assert page.errors == []


def test_ops_undo_redo_delete_and_errors(browser, served):
    srv, doc = served
    page = _open(browser, srv.url)
    _click(page, '/1')          # the fraction
    page.locator(".se-ops").select_option("negate")
    page.locator('[data-cmd="apply"]').click()
    page.wait_for_function("document.querySelector('.se-source').textContent.startsWith('-x**2')")
    assert doc.expr == -(x**2) / y - sin(x)
    page.keyboard.press("Control+z")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x**2/y - sin(x)'")
    page.keyboard.press("Control+Shift+z")
    page.wait_for_function("document.querySelector('.se-source').textContent.startsWith('-x**2')")
    _click(page, '/0')          # -sin(x) term
    page.keyboard.press("Delete")
    page.wait_for_function("!document.querySelector('.se-source').textContent.includes('sin')")
    assert doc.expr == -(x**2) / y
    # a parse error is shown and leaves the state alone
    page.keyboard.press("ArrowDown")                  # select root
    page.keyboard.press("Enter")
    page.keyboard.press("Control+a")
    page.keyboard.type("x +")
    page.keyboard.press("Enter")
    page.wait_for_selector(".se-error:not([hidden])")
    assert "parse" in page.locator(".se-error").inner_text().lower()
    assert doc.expr == -(x**2) / y
    assert page.errors == []


def test_done_button_closes_session(browser, served):
    srv, doc = served
    page = _open(browser, srv.url)
    page.locator('[data-cmd="finish"]').click()
    page.wait_for_selector(".sympy-editor.se-closed")
    assert page.locator('[data-cmd="edit"]').is_disabled()


def test_readonly_page(browser, tmp_path):
    path = tmp_path / "ro.html"
    path.write_text(to_html(x + y, editable=False), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    assert page.locator('[data-cmd="edit"]').count() == 0
    _click(page, '/0')
    assert page.locator(".se-selected").count() == 1


def test_matrix_element_edit(browser, serve_expr):
    srv, doc = serve_expr(Matrix([[x, y], [z, 1]]))
    page = _open(browser, srv.url)
    assert page.locator(".se-view .katex [data-path]").count() == 5   # root + 4 elements
    _click(page, "/2/1")                                              # y
    assert page.locator(".se-status").inner_text() == "Symbol: y"
    page.keyboard.type("y**2")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('y**2')")
    assert doc.expr == Matrix([[x, y**2], [z, 1]])
    assert page.errors == []


def test_ndim_array_element_edit(browser, serve_expr):
    arr = Array([[[x, 1], [y, 2]], [[z, 3], [1, 4]]])
    srv, doc = serve_expr(arr)
    page = _open(browser, srv.url)
    path = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "3")
    _click(page, path)
    assert page.locator(".se-status").inner_text() == "Integer: 3"
    page.keyboard.type("w")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('w')")
    assert doc.expr[1, 0, 1] == symbols("w") and doc.expr.shape == (2, 2, 2)
    assert page.errors == []


@pytest.mark.skipif(not os.environ.get("SYMPY_EDITOR_SLOW_TESTS"), reason="set SYMPY_EDITOR_SLOW_TESTS=1")
def test_pyodide_page_edits_in_browser(browser, tmp_path):
    path = tmp_path / "py.html"
    path.write_text(to_html(x**2 + y), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    _click(page, '/0')          # y
    page.keyboard.type("sin(x)")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('sin(x)')", timeout=180000)
    assert page.locator(".se-error").is_hidden()

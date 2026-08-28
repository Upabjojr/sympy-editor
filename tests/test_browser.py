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
import re
import time

from sympy import Array, Matrix, MatrixSymbol, Symbol, sin, symbols

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


def _gap_between(page, left_path, right_path):
    """Viewport point midway between two rendered nodes (on the same line)."""
    return page.evaluate(
        """([l, r]) => {
            const a = document.querySelector(`[data-path="${l}"]`).getBoundingClientRect();
            const b = document.querySelector(`[data-path="${r}"]`).getBoundingClientRect();
            return [(a.right + b.left) / 2, (a.top + a.bottom) / 2];
        }""",
        [left_path, right_path],
    )


def _next_state(page, action):
    """Run `action` and wait until the editor has applied the next snapshot."""
    seq = int(page.locator(".sympy-editor").first.get_attribute("data-seq") or 0)
    action()
    page.wait_for_function("s => +document.querySelector('.sympy-editor').getAttribute('data-seq') > s", arg=seq)


def _wait(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_click_between_terms_inserts(browser, serve_expr):
    srv, doc = serve_expr(x + y)                       # Add(x, y): "/0" is x, "/1" is y
    page = _open(browser, srv.url)
    gx, gy = _gap_between(page, "/0", "/1")            # on the "+"
    page.mouse.click(gx, gy)
    assert page.locator(".se-caret").count() == 1
    assert page.locator(".se-status").inner_text().startswith("Insert into Add")
    assert page.locator(".se-selected").count() == 0
    page.keyboard.type("z**2")                         # typing at the caret opens the field there
    assert page.locator(".se-inline").count() == 1
    assert page.locator(".se-inline").get_attribute("placeholder") == "term"
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('z**2')")
    assert doc.expr == x + y + z**2
    assert page.locator(".se-caret").count() == 0 and page.locator(".se-inline").count() == 0
    # Escape removes the caret; Tab puts it after the selection, Shift+Tab before
    gx, gy = _gap_between(page, "/0", "/1")
    page.mouse.click(gx, gy)
    assert page.locator(".se-caret").count() == 1
    page.keyboard.press("Escape")
    assert page.locator(".se-caret").count() == 0
    _click(page, "/0")
    assert page.locator(".se-status").inner_text() == "Symbol: x"
    page.keyboard.press("Tab")
    assert page.locator(".se-caret").count() == 1
    page.keyboard.press("ArrowRight")                  # move to the next gap
    assert page.locator(".se-caret").count() == 1
    page.keyboard.press("Enter")                       # open an empty field
    page.keyboard.type("3*w")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('3*w')")
    assert doc.expr == x + y + z**2 + 3 * symbols("w")
    # clicking a glyph still selects it (no caret)
    _click(page, "/1")
    assert page.locator(".se-status").inner_text() == "Symbol: y"
    assert page.locator(".se-caret").count() == 0
    assert page.errors == []


def test_insert_factor_in_nested_product(browser, serve_expr):
    srv, doc = serve_expr(x * y + 1)                   # Add(1, Mul(x, y)); the Mul is "/1"
    page = _open(browser, srv.url)
    _click(page, "/1/0")                               # x
    page.keyboard.press("Tab")                         # caret after x inside the Mul
    assert page.locator(".se-status").inner_text().startswith("Insert into Mul")
    page.keyboard.type("2")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.startsWith('2*x*y')")
    assert doc.expr == 2 * x * y + 1
    assert page.errors == []


def test_symbols_panel_declare_and_assumptions(browser, serve_expr):
    srv, doc = serve_expr(x + 1)
    page = _open(browser, srv.url)
    page.locator(".se-symbols summary").click()
    new = page.locator(".se-sym-new")
    new.locator("input.se-sym-name").fill("M")
    new.locator("select").select_option("MatrixSymbol")
    new.locator('input[title="Rows"]').fill("3")
    new.locator('input[title="Columns"]').fill("3")
    new.locator("button", has_text="Add").click()
    page.wait_for_function("[...document.querySelectorAll('.se-sym code')].some(c => c.textContent === 'M')")
    assert doc.declared["M"] == MatrixSymbol("M", 3, 3)
    assert "declared, not used" in page.locator(".se-symbols").inner_text()
    # the declared name resolves when typed into the formula
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")                   # select the whole expression
    page.keyboard.press("Enter")
    page.keyboard.press("Control+a")
    page.keyboard.type("M*x + M.T")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('M.T')")
    M = MatrixSymbol("M", 3, 3)
    assert doc.expr == M * x + M.T
    assert "declared, not used" not in page.locator(".se-symbols").inner_text()
    # assumptions on an existing symbol propagate into the expression
    row = page.locator(".se-sym").filter(has=page.locator("code", has_text=re.compile(r"^x$")))
    row.locator("input.se-sym-assume").fill("positive")
    _next_state(page, lambda: row.locator("button", has_text="Set").click())
    assert all(s.is_positive for s in doc.expr.atoms(Symbol) if s.name == "x")
    assert page.locator(".se-error").is_hidden()
    # a declared, unused name can be removed
    new = page.locator(".se-sym-new")
    new.locator("input.se-sym-name").fill("q")
    _next_state(page, lambda: new.locator("button", has_text="Add").click())
    assert "q" in doc.declared
    row = page.locator(".se-sym").filter(has=page.locator("code", has_text=re.compile(r"^q$")))
    _next_state(page, lambda: row.locator("button", has_text="Remove").click())
    assert "q" not in doc.declared
    assert page.errors == []


@pytest.mark.skipif(not os.environ.get("SYMPY_EDITOR_SLOW_TESTS"), reason="set SYMPY_EDITOR_SLOW_TESTS=1")
def test_pyodide_runtime_is_shared_and_matrix_names_survive(browser, tmp_path):
    A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
    body = to_html(A * B, full_page=False) + to_html(x + y, full_page=False)
    path = tmp_path / "two.html"
    path.write_text("<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>" + body + "</body></html>", encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.wait_for_function("document.querySelectorAll('.se-view .katex').length === 2", timeout=30000)
    editors = page.locator(".sympy-editor")
    editors.nth(0).locator('[data-path="/1"]').click(force=True)     # B
    page.keyboard.type("A.T")
    page.keyboard.press("Enter")
    page.wait_for_function(
        "document.querySelectorAll('.sympy-editor')[0].querySelector('.se-source').textContent === 'A*A.T'",
        timeout=180000)
    editors.nth(0).locator('[data-path="/0"]').click(force=True)
    assert editors.nth(0).locator(".se-status").inner_text() == "MatrixSymbol: A"   # not "Str"
    editors.nth(1).locator('[data-path="/0"]').click(force=True)
    page.keyboard.type("z")
    page.keyboard.press("Enter")
    page.wait_for_function(
        "document.querySelectorAll('.sympy-editor')[1].querySelector('.se-source').textContent.includes('z')",
        timeout=180000)
    assert page.evaluate("Object.keys(window.__sympyEditorPyodide.runtimes).length") == 1
    assert page.evaluate("window.__sympyEditorPyodide.docs") == 2
    assert page.evaluate("performance.getEntriesByType('resource').filter(e => e.name.endsWith('pyodide.asm.js')).length") == 1
    assert page.evaluate("document.querySelectorAll('.se-error:not([hidden])').length") == 0


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

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

from sympy import Array, Matrix, MatrixSymbol, Symbol, pi, sin, symbols

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


def test_caret_and_selection_are_exclusive(browser, serve_expr):
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    _click(page, "/0")                                 # select x
    page.keyboard.press("Tab")                         # caret after x: the selection is gone
    assert page.locator(".se-caret").count() == 1
    assert page.locator(".se-selected").count() == 0
    assert page.locator('[data-cmd="delete"]').is_disabled()
    page.keyboard.press("Delete")                      # nothing to delete with a caret
    page.keyboard.press("Backspace")
    page.wait_for_timeout(300)
    assert doc.expr == x + y
    assert page.locator(".se-caret").count() == 1
    page.keyboard.type("2")                            # keys at a caret insert
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x + y + 2'")
    assert doc.expr == x + y + 2
    gx, gy = _gap_between(page, "/1", "/2")
    _click(page, "/1")                                 # select x
    page.mouse.click(gx, gy)                           # clicking a gap replaces the selection by a caret
    assert page.locator(".se-selected").count() == 0 and page.locator(".se-caret").count() == 1
    _click(page, "/1")                                 # selecting removes the caret
    assert page.locator(".se-caret").count() == 0 and page.locator(".se-selected").count() == 1
    page.keyboard.type("+")                            # any printable key replaces the selection
    assert page.locator(".se-inline").input_value() == "+"
    page.keyboard.press("Escape")
    assert doc.expr == x + y + 2
    assert page.errors == []


def test_latex_shortcuts_in_the_field(browser, serve_expr):
    theta, lam = symbols("theta lamda")
    srv, doc = serve_expr(theta + 1)
    page = _open(browser, srv.url)
    path = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "theta")
    _click(page, path)
    page.keyboard.press("Enter")                       # the field shows the Greek letter
    assert page.locator(".se-inline").input_value() == "θ"
    page.keyboard.press("Control+a")
    page.keyboard.type("\\theta")                      # expands as soon as the command is complete
    assert page.locator(".se-inline").input_value() == "θ"
    page.keyboard.type("**2 + \\lambda*\\pi + \\sin(\\alpha)")
    assert page.locator(".se-inline").input_value() == "θ**2 + λ*π + sin(α)"
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('sin(alpha)')")
    assert doc.expr == theta**2 + lam * pi + sin(symbols("alpha")) + 1
    assert theta in doc.expr.free_symbols               # the same symbol as before, not a new "θ"
    # a command that is a prefix of another waits for the next character
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "1"))
    page.keyboard.type("\\in")
    assert page.locator(".se-inline").input_value() == "\\in"
    page.keyboard.type("fty")
    assert page.locator(".se-inline").input_value() == "∞"
    page.keyboard.press("Escape")
    assert page.errors == []


def _display_children(page, parent):
    """Annotated children of `parent`, left to right as displayed."""
    return page.evaluate(
        """p => { const els = [...document.querySelectorAll('.se-view [data-path]')];
                 const kids = els.filter(e => { const q = e.getAttribute('data-path'); if (q === p || !(p === '/' ? q.startsWith('/') : q.startsWith(p + '/'))) return false;
                   const anc = e.parentElement.closest('[data-path]'); return anc && anc.getAttribute('data-path') === p; });
                 return kids.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left).map(e => e.getAttribute('data-path')); }""", parent)


def _center(page, path):
    return page.evaluate("p => { const b = document.querySelector(`[data-path=\"${p}\"]`).getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; }", path)


def test_shift_arrows_select_ranges(browser, serve_expr):
    a, b, c, d = symbols("a b c d")
    srv, doc = serve_expr(a + b + c + d)
    page = _open(browser, srv.url)
    kids = _display_children(page, "/")
    assert len(kids) == 4
    _click(page, kids[1])                              # b
    page.keyboard.press("Shift+ArrowRight")
    assert page.locator(".se-selected").count() == 2
    assert page.locator(".se-status").inner_text() == "Add range: b + c"
    page.keyboard.press("Shift+ArrowRight")
    assert page.locator(".se-selected").count() == 3
    page.keyboard.press("Shift+ArrowLeft")             # shrink back
    assert page.locator(".se-selected").count() == 2
    assert page.locator('[data-cmd="delete"]').is_enabled()
    page.locator(".se-ops").select_option("negate")    # an op acts on the range only
    _next_state(page, lambda: page.locator('[data-cmd="apply"]').click())
    assert doc.expr == a - b - c + d
    assert page.locator(".se-selected").count() == 0   # a new state drops the range
    kids = _display_children(page, "/")
    _click(page, kids[0])                              # a
    page.keyboard.press("Shift+ArrowRight")            # a and -b
    page.keyboard.type("q")                            # typing replaces the whole range
    assert page.locator(".se-inline").input_value() == "q"
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('q')")
    assert doc.expr == -c + d + symbols("q")
    kids = _display_children(page, "/")
    _click(page, kids[0])
    page.keyboard.press("Shift+ArrowRight")
    page.keyboard.press("Escape")                      # Escape clears the range
    assert page.locator(".se-selected").count() == 0
    _click(page, kids[0])
    page.keyboard.press("Shift+ArrowRight")
    page.keyboard.press("Enter")                       # Enter edits the range, prefilled with its source
    assert "+" in page.locator(".se-inline").input_value()
    page.keyboard.press("Escape")                      # cancel restores the rendering
    page.wait_for_function("document.querySelectorAll('.se-view [data-path]').length > 1")
    assert page.locator(".se-inline").count() == 0
    _click(page, kids[0])
    page.keyboard.press("Shift+ArrowRight")
    _next_state(page, lambda: page.keyboard.press("Delete"))
    assert len(doc.expr.args) == 1 or doc.expr.is_Symbol or doc.expr.is_Mul
    assert page.errors == []


def test_range_in_nested_product_and_arrow_down_reduces(browser, serve_expr):
    a, b, c = symbols("a b c")
    srv, doc = serve_expr(a * b * c + 1)              # Add(1, Mul(a, b, c))
    page = _open(browser, srv.url)
    mul = next(k for k, v in doc.snapshot()["nodes"].items() if v["type"] == "Mul")
    kids = _display_children(page, mul)
    _click(page, kids[0])
    page.keyboard.press("Shift+ArrowRight")
    assert page.locator(".se-status").inner_text() == "Mul range: a*b"
    page.keyboard.press("ArrowUp")                     # the range's parent
    assert page.locator(".se-status").inner_text() == "Mul: a*b*c"
    page.keyboard.press("ArrowDown")                   # reduce to the first child
    assert page.locator(".se-status").inner_text() == "Symbol: a"
    page.keyboard.press("Shift+ArrowRight")
    page.keyboard.press("ArrowRight")                  # collapse the range onto its focus end
    assert page.locator(".se-status").inner_text() == "Symbol: b"
    assert page.errors == []


def test_mouse_drag_selects_a_range(browser, serve_expr):
    a, b, c, d = symbols("a b c d")
    srv, doc = serve_expr(a + b + c + d)
    page = _open(browser, srv.url)
    kids = _display_children(page, "/")
    x0, y0 = _center(page, kids[0])
    x2, y2 = _center(page, kids[2])
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x2, y2, steps=6)
    page.mouse.up()
    assert page.locator(".se-selected").count() == 3
    assert page.locator(".se-status").inner_text() == "Add range: a + b + c"
    page.mouse.move(x2, y2)                            # the click ending the drag does not clear it
    assert page.locator(".se-selected").count() == 3
    _click(page, kids[3])                              # a plain click selects again
    assert page.locator(".se-status").inner_text() == "Symbol: d"
    assert page.errors == []


def test_touch_drag_selects_a_range(browser, serve_expr):
    a, b, c = symbols("a b c")
    srv, doc = serve_expr(a + b + c)
    page = _open(browser, srv.url)
    kids = _display_children(page, "/")
    page.evaluate(
        """([p0, p1]) => {
            const at = p => { const b = document.querySelector(`[data-path="${p}"]`).getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; };
            const fire = (type, p) => { const [x, y] = at(p); const el = document.elementFromPoint(x, y);
              el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, pointerType: 'touch', pointerId: 7, isPrimary: true, buttons: 1 })); };
            fire('pointerdown', p0); fire('pointermove', p1); fire('pointerup', p1);
        }""", [kids[0], kids[1]])
    assert page.locator(".se-selected").count() == 2
    assert page.locator(".se-status").inner_text() == "Add range: a + b"
    assert page.evaluate("getComputedStyle(document.querySelector('.se-view')).touchAction").startswith("pan-y")
    assert page.errors == []


def test_selection_box_covers_tall_content(browser, serve_expr):
    srv, doc = serve_expr(Matrix([[x, y], [z, 1]]))
    page = _open(browser, srv.url)
    _click(page, "/2/0")                               # the x entry, then up to the matrix
    page.keyboard.press("ArrowUp")
    assert page.locator(".se-status").inner_text().startswith("ImmutableDenseMatrix")
    box = page.locator(".se-box-select").bounding_box()
    glyphs = page.evaluate("""() => {
        const el = document.querySelector('.se-selected');
        let top = Infinity, bottom = -Infinity, left = Infinity, right = -Infinity;
        for (const g of el.querySelectorAll('.mord, .mopen, .mclose, .delimsizing')) {
            const b = g.getBoundingClientRect(); if (!b.height) continue;
            top = Math.min(top, b.top); bottom = Math.max(bottom, b.bottom); left = Math.min(left, b.left); right = Math.max(right, b.right);
        }
        return { top, bottom, left, right }; }""")
    assert box["y"] <= glyphs["top"] + 1 and box["y"] + box["height"] >= glyphs["bottom"] - 1
    assert box["x"] <= glyphs["left"] + 1 and box["x"] + box["width"] >= glyphs["right"] - 1
    assert box["height"] > 1.5 * page.locator(".se-selected").bounding_box()["height"]   # taller than the inline span
    # hover box follows the same rule, and disappears when leaving
    x0, y0 = _center(page, "/2/1")
    page.mouse.move(x0, y0)
    assert page.locator(".se-box-hover").count() == 1
    page.mouse.move(5, 5)
    assert page.locator(".se-box-hover").count() == 0
    assert page.errors == []


# ---------------------------------------------------------------------------
# Scenario helper: write graphical-edit tests as a sequence of user actions
# and read the resulting expression back, on either backend.
# ---------------------------------------------------------------------------

class Scenario:
    """Drive one editor like a user.  `source` waits for the editor to
    apply the next state and returns the SymPy source it shows, so the
    same scenario runs against the HTTP backend and the Pyodide page."""

    def __init__(self, page, expr, timeout=180000):
        self.page = page
        self.expr = expr
        self.timeout = timeout

    # -- selection --
    def click(self, path):
        _click(self.page, path)
        return self

    def select(self, path):
        """Select a node by path, walking up from a glyph if needed."""
        _click(self.page, path)
        for _ in range(20):
            if self.page.locator(".se-selected").first.get_attribute("data-path") == path:
                return self
            self.page.keyboard.press("ArrowUp")
        raise AssertionError(f"could not select {path}")

    def caret_after(self, path):
        """A caret right after the rendering of `path` (a mouse click there)."""
        r = self.page.locator(f'[data-path="{path}"]').bounding_box()
        self.page.mouse.click(r["x"] + r["width"] + 2, r["y"] + r["height"] / 2)
        assert self.page.locator(".se-caret").count() == 1, "no caret appeared"
        return self

    def caret_between(self, left, right):
        gx, gy = _gap_between(self.page, left, right)
        self.page.mouse.click(gx, gy)
        assert self.page.locator(".se-caret").count() == 1, "no caret appeared"
        return self

    def drag(self, a, b):
        x0, y0 = _center(self.page, a)
        x1, y1 = _center(self.page, b)
        self.page.mouse.move(x0, y0)
        self.page.mouse.down()
        self.page.mouse.move(x1, y1, steps=6)
        self.page.mouse.up()
        return self

    # -- keys --
    def type(self, text):
        self.page.keyboard.type(text)
        return self

    def key(self, *keys):
        for k in keys:
            self.page.keyboard.press(k)
        return self

    def enter(self):
        seq = int(self.page.locator(".sympy-editor").first.get_attribute("data-seq") or 0)
        self.page.keyboard.press("Enter")
        self.page.wait_for_function("s => +document.querySelector('.sympy-editor').getAttribute('data-seq') > s",
                                    arg=seq, timeout=self.timeout)
        return self

    # -- results --
    @property
    def source(self):
        return self.page.locator(".se-source").inner_text()

    @property
    def status(self):
        return self.page.locator(".se-status").inner_text()

    @property
    def error(self):
        return self.page.locator(".se-error").inner_text()

    def path_of(self, src):
        """Path of the first rendered node whose source is `src` (from the
        editor's current state)."""
        nodes = self.page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.state.nodes")
        for path in sorted(nodes, key=len):
            if nodes[path]["src"] == src:
                return path
        raise AssertionError(f"no node {src!r}; have {sorted(v['src'] for v in nodes.values())}")


@pytest.fixture(params=["http", pytest.param("pyodide", marks=pytest.mark.skipif(
    not os.environ.get("SYMPY_EDITOR_SLOW_TESTS"), reason="set SYMPY_EDITOR_SLOW_TESTS=1"))])
def scenario(request, browser, serve_expr, tmp_path):
    """scenario(expr) -> Scenario on the HTTP backend or on a Pyodide page."""
    pages = []

    def make(expr):
        if request.param == "http":
            srv, doc = serve_expr(expr)
            page = _open(browser, srv.url)
        else:
            path = tmp_path / "scenario.html"
            path.write_text(to_html(expr), encoding="utf-8")
            page = browser.new_page()
            page.errors = []
            page.on("pageerror", lambda e: page.errors.append(str(e)))
            page.goto(path.as_uri())
            page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
        pages.append(page)
        return Scenario(page, expr)

    yield make
    for page in pages:
        assert page.errors == []
        page.close()


def test_arrow_navigation_remembers_and_crosses_levels(scenario):
    A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
    s = scenario(A**2 * B)                              # MatMul(MatPow(A, 2), B), displayed A²B
    s.click(s.path_of("2"))
    assert s.status == "Integer: 2"
    s.key("ArrowRight")                                 # leaves the power and reaches B
    assert s.status == "MatrixSymbol: B"
    s.key("ArrowLeft")                                  # back to the power as a whole
    assert s.status == "MatPow: A**2"
    s.key("ArrowDown")                                  # into it: first child
    assert s.status == "MatrixSymbol: A"
    s.key("ArrowRight")
    assert s.status == "Integer: 2"
    s.key("ArrowUp", "ArrowUp")                         # up to the product...
    assert s.status.startswith("MatMul")
    s.key("ArrowDown", "ArrowDown")                     # ...and ↓ returns where ↑ came from, twice
    assert s.status == "Integer: 2"
    x, y = symbols("x y")
    s2 = scenario(x**2 + x + 1)                         # args (1, x, x**2) but displayed x² + x + 1
    s2.click(s2.path_of("1")).key("ArrowLeft")
    assert s2.status == "Symbol: x"
    s2.key("ArrowLeft")
    assert s2.status == "Pow: x**2"
    s2.key("ArrowLeft")                                 # nothing further left: stays
    assert s2.status == "Pow: x**2"


def test_type_menu_shows_the_selection_type_operations(browser, serve_expr):
    from sympy import Integral, Matrix
    srv, doc = serve_expr(Matrix([[x, y], [z, 1]]))
    page = _open(browser, srv.url)
    menu = page.locator(".se-typemenu")
    assert menu.is_visible()                              # nothing selected: the whole (matrix) expression is the target
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")                      # select it explicitly
    assert menu.is_visible() and menu.locator("option").first.inner_text().startswith("Matrix")
    labels = menu.locator("option").all_inner_texts()
    assert "Transpose" in labels and "Determinant" in labels
    assert "Transpose" not in page.locator(".se-ops option").all_inner_texts()   # not in the general dropdown
    _click(page, "/2/0")                                  # the x entry: a plain scalar, no type menu
    assert page.locator(".se-status").inner_text() == "Symbol: x"
    assert not menu.is_visible()
    page.keyboard.press("ArrowUp")                        # the matrix itself
    assert page.locator(".se-status").inner_text().startswith("ImmutableDenseMatrix")
    _next_state(page, lambda: menu.select_option("determinant"))   # picking applies at once
    assert doc.expr == x - y * z
    assert not menu.is_visible()                          # the result is a scalar: no type menu
    srv2, doc2 = serve_expr(Integral(x**2, (x, 0, 1)) + y)
    page = _open(browser, srv2.url)
    _click(page, next(k for k, v in doc2.snapshot()["nodes"].items() if v["type"] == "Integral"))
    menu = page.locator(".se-typemenu")
    assert menu.locator("option").first.inner_text().startswith("Integral")
    _next_state(page, lambda: menu.select_option("evaluate"))
    assert doc2.expr == y + symbols("x") ** 0 / 3 or str(doc2.expr) == "y + 1/3"
    assert page.errors == []


def _rect(page, path):
    return page.evaluate("p => { const b = document.querySelector(`[data-path=\"${p}\"]`).getBoundingClientRect(); return {l: b.left, r: b.right, y: b.top + b.height / 2}; }", path)


def test_edges_give_a_caret_and_the_middle_selects(browser, serve_expr):
    srv, doc = serve_expr(x**2 + y)                      # Add(y, x**2) displayed x² + y
    page = _open(browser, srv.url)
    px = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x**2")
    ry = _rect(page, "/0")                                # y
    page.mouse.click((ry["l"] + ry["r"]) / 2, ry["y"])    # middle: select
    assert page.locator(".se-status").inner_text() == "Symbol: y"
    page.mouse.click(ry["l"] + 1, ry["y"])                # left edge: caret before y, nothing selected
    assert page.locator(".se-caret").count() == 1 and page.locator(".se-selected").count() == 0
    assert page.locator(".se-status").inner_text().startswith("Insert into Add")
    page.keyboard.type("3")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x**2 + y + 3'")
    px = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x**2")   # paths changed with the sum
    rx = _rect(page, px + "/0")                            # the x of x²: its left edge is the term's edge
    page.mouse.click(rx["l"] + 1, rx["y"])
    assert page.locator(".se-caret").count() == 1 and page.locator(".se-status").inner_text().startswith("Insert into Add")
    page.mouse.click((rx["l"] + rx["r"]) / 2, rx["y"])    # its middle selects x itself
    assert page.locator(".se-status").inner_text() == "Symbol: x"
    r2 = _rect(page, px + "/1")                            # the exponent: right edge = end of the term
    page.mouse.click(r2["r"] - 1, r2["y"])
    assert page.locator(".se-status").inner_text().startswith("Insert into Add")
    page.keyboard.type("+ w")                              # a leading + means a term wherever the caret is
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('w')")
    assert doc.expr == x**2 + y + 3 + symbols("w")
    assert page.errors == []


def test_plus_term_typed_after_a_product_is_added_not_multiplied(scenario):
    A, B = MatrixSymbol("A", 2, 2), MatrixSymbol("B", 2, 2)
    s = scenario(A * B + 2 * A.T)
    ab = s.path_of("A*B")
    s.caret_after(ab).type("+ B * A").enter()          # the caret sits in the product's trailing gap
    assert s.source == "A*B + B*A + 2*A.T"
    assert s.error == ""
    s2 = scenario(A * B + 2 * A.T)
    s2.select(s2.path_of("A*B")).key("Enter", "End").type(" + B*A").enter()   # editing the block itself
    assert s2.source == "A*B + B*A + 2*A.T"


def test_scenario_helper_covers_the_other_gestures(scenario):
    a, b, c = symbols("a b c")
    s = scenario(a * b + c)
    s.caret_between(s.path_of("a*b"), s.path_of("c")).type("- 1").enter()
    assert s.source == "a*b + c - 1"
    s.drag(s.path_of("a"), s.path_of("b")).type("q").enter()
    assert s.source == "c + q - 1"


def test_touch_tap_again_edits_and_caret_tap_inserts(browser, serve_expr):
    srv, doc = serve_expr(x + y)
    ctx = browser.new_context(has_touch=True, is_mobile=True, viewport={"width": 420, "height": 800})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    x0, y0 = _center(page, "/0")
    page.touchscreen.tap(x0, y0)                        # tap: select
    assert page.locator(".se-status").inner_text() == "Symbol: x"
    page.touchscreen.tap(x0, y0)                        # tap again: edit, no keyboard needed
    assert page.locator(".se-inline").count() == 1
    assert page.evaluate("document.activeElement.className") == "se-inline"
    page.keyboard.type("z")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'y + z'")
    gx, gy = _gap_between(page, "/0", "/1")
    page.touchscreen.tap(gx, gy)                        # tap a gap: caret
    assert page.locator(".se-caret").count() == 1
    page.touchscreen.tap(gx, gy)                        # tap it again: insertion field
    assert page.locator(".se-inline").count() == 1
    page.keyboard.type("2")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'y + z + 2'")
    # the keyboard button: visible on touch devices, opens a field for the selection
    kb = page.locator('[data-cmd="keyboard"]')
    assert kb.is_visible()
    x0, y0 = _center(page, "/0")
    page.touchscreen.tap(x0, y0)
    kb.tap()
    assert page.locator(".se-inline").count() == 1 and page.evaluate("document.activeElement.className") == "se-inline"
    page.keyboard.press("Escape")                       # cancel the field...
    page.keyboard.press("Escape")                       # ...and clear the selection
    kb.tap()                                             # nothing selected: the whole expression
    assert page.locator(".se-inline").input_value() == "y + z + 2"
    page.keyboard.press("Escape")
    assert errors == []
    ctx.close()


def test_keyboard_button_hidden_with_a_mouse(browser, serve_expr):
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    assert page.locator('[data-cmd="keyboard"]').count() == 1
    assert not page.locator('[data-cmd="keyboard"]').is_visible()
    # double-clicking a gap opens the insertion field, not an edit of the whole expression
    gx, gy = _gap_between(page, "/0", "/1")
    page.mouse.dblclick(gx, gy)
    assert page.locator(".se-status").inner_text().startswith("Inserting into Add")
    page.keyboard.press("Escape")
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
def test_pyodide_page_preloads_the_runtime(browser, tmp_path):
    path = tmp_path / "pre.html"
    path.write_text(to_html(x + y), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    page.wait_for_function("document.querySelector('.se-status').textContent.includes('Python')", timeout=30000)
    page.wait_for_function("window.__sympyEditorPyodide && window.__sympyEditorPyodide.docs === 1", timeout=180000)
    assert page.locator(".se-status").inner_text() == ""      # status cleared once ready, no edit happened
    page.locator('[data-path="/0"]').click(force=True)
    page.keyboard.type("z")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('z')", timeout=10000)   # fast: already loaded
    # preload can be turned off
    path2 = tmp_path / "lazy.html"
    path2.write_text(to_html(x + y, options={"preload": False}), encoding="utf-8")
    page2 = browser.new_page()
    page2.goto(path2.as_uri())
    page2.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    page2.wait_for_timeout(1500)
    assert page2.evaluate("!window.__sympyEditorPyodide")


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

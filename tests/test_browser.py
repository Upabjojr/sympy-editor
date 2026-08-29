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
    assert page.locator(".se-view .katex:not(.se-ghost *)").count() == 1
    assert doc.expr == x**2 / z**2 - sin(x)
    assert page.errors == []


def test_ops_undo_redo_delete_and_errors(browser, served):
    srv, doc = served
    page = _open(browser, srv.url)
    _click(page, '/1')          # the fraction
    page.locator(".se-ops").select_option("negate")  # picking applies at once
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
    page.locator('.se-toolbar [data-cmd="finish"]').click()
    page.wait_for_selector(".sympy-editor.se-closed")
    assert page.locator('.se-toolbar [data-cmd="edit"]').is_disabled()


def test_readonly_page(browser, tmp_path):
    path = tmp_path / "ro.html"
    path.write_text(to_html(x + y, editable=False), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    assert page.locator('.se-toolbar [data-cmd="edit"]').count() == 0
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
    assert page.locator(".se-status").inner_text().startswith("Type into Add")
    assert page.locator(".se-selected").count() == 0
    page.keyboard.type("+ z**2")                       # typing at the caret opens the field there; "+" adds a term
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
    page.keyboard.type("3*w")                          # no operator at the junction: juxtaposed with the neighbour
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('w')")
    assert doc.expr.has(symbols("w")) and doc.expr != x + y + z**2 + 3 * symbols("w")
    # clicking a glyph still selects it (no caret)
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "y"))
    assert page.locator(".se-status").inner_text() == "Symbol: y"
    assert page.locator(".se-caret").count() == 0
    assert page.errors == []


def test_insert_factor_in_nested_product(browser, serve_expr):
    srv, doc = serve_expr(x * y + 1)                   # Add(1, Mul(x, y)); the Mul is "/1"
    page = _open(browser, srv.url)
    _click(page, "/1/0")                               # x
    page.keyboard.press("Tab")                         # caret after x inside the Mul
    assert page.locator(".se-status").inner_text().startswith("Type into Mul")
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
    _next_state(page, lambda: page.keyboard.press("Enter"))   # the source line applies on Enter
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
    assert page.locator('.se-toolbar [data-cmd="delete"]').is_disabled()
    page.keyboard.press("Delete")                      # nothing to delete with a caret
    page.keyboard.press("Backspace")
    page.wait_for_timeout(300)
    assert doc.expr == x + y
    assert page.locator(".se-caret").count() == 1
    page.keyboard.type("2")                            # keys at a caret insert: juxtaposed with x
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === '2*x + y'")
    assert doc.expr == 2 * x + y
    gx, gy = _gap_between(page, "/0", "/1")
    _click(page, "/1")                                 # select y
    page.mouse.click(gx, gy)                           # clicking a gap replaces the selection by a caret
    assert page.locator(".se-selected").count() == 0 and page.locator(".se-caret").count() == 1
    _click(page, "/1")                                 # selecting removes the caret
    assert page.locator(".se-caret").count() == 0 and page.locator(".se-selected").count() == 1
    page.keyboard.type("+")                            # any printable key replaces the selection
    assert page.locator(".se-inline").input_value() == "+"
    page.keyboard.press("Escape")
    assert doc.expr == 2 * x + y
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
    assert page.locator('.se-toolbar [data-cmd="delete"]').is_enabled()
    _next_state(page, lambda: page.locator(".se-ops").select_option("negate"))   # an op acts on the range only
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
            page.wait_for_function("document.querySelector('.se-loading').hidden", timeout=240000)
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
    s.key("ArrowDown")                                  # ↓ on an atom: a caret after it
    assert s.page.locator(".se-caret").count() == 1
    s.key("ArrowUp")                                    # ↑ from the caret: the atom again
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
    assert page.locator(".se-status").inner_text().startswith("Type into Add")
    page.keyboard.type("3")                                # juxtaposed: 3*y
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x**2 + 3*y'")
    px = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x**2")   # paths changed with the sum
    rx = _rect(page, px + "/0")                            # the x of x²: its left edge is the term's edge
    page.mouse.click(rx["l"] + 1, rx["y"])
    assert page.locator(".se-caret").count() == 1 and page.locator(".se-status").inner_text().startswith("Type into Add")
    page.mouse.click((rx["l"] + rx["r"]) / 2, rx["y"])    # its middle selects x itself
    assert page.locator(".se-status").inner_text() == "Symbol: x"
    r2 = _rect(page, px + "/1")                            # the exponent: right edge = end of the term
    page.mouse.click(r2["r"] - 1, r2["y"])
    assert page.locator(".se-status").inner_text().startswith("Type into Add")
    page.keyboard.type("+ w")                              # a leading + means a term wherever the caret is
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('w')")
    assert doc.expr == x**2 + 3 * y + symbols("w")
    assert page.errors == []


def test_edge_of_a_matrix_entry_extends_it(browser, serve_expr):
    from sympy import Matrix
    srv, doc = serve_expr(Matrix([[x, y], [z, 1]]))
    page = _open(browser, srv.url)
    entry = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "y")
    r = _rect(page, entry)
    page.mouse.click(r["r"] - 1, r["y"])                  # right edge of the entry: a caret to type next to it
    assert page.locator(".se-caret").count() == 1 and page.locator(".se-selected").count() == 0
    assert page.locator(".se-status").inner_text().startswith("Type after Symbol y")
    page.keyboard.type("+ 1")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('y + 1')")
    assert doc.expr[0, 1] == y + 1
    entry = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "z")
    r = _rect(page, entry)
    page.mouse.click(r["l"] + 1, r["y"])                  # left edge: typing "2" multiplies
    assert page.locator(".se-status").inner_text().startswith("Type before Symbol z")
    page.keyboard.type("2")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('2*z')")
    assert doc.expr[1, 0] == 2 * z
    zpath = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "z")
    r = _rect(page, zpath)
    page.mouse.click((r["l"] + r["r"]) / 2, r["y"])       # the middle of a glyph still selects it
    assert page.locator(".se-status").inner_text() == "Symbol: z" and page.locator(".se-caret").count() == 0
    # ↓ on an atom gives a caret after it and lifts the selection entirely; ↑ from a caret selects that atom first
    page.keyboard.press("ArrowDown")
    assert page.locator(".se-caret").count() == 1 and page.locator(".se-selected").count() == 0
    assert page.locator(".se-box-select").count() == 0 and page.locator(".se-source mark").count() == 0
    assert not page.locator(".se-actions").is_visible()
    page.keyboard.press("ArrowUp")
    assert page.locator(".se-status").inner_text() == "Symbol: z"
    assert page.errors == []


def test_caret_aligns_with_the_previous_character(browser, serve_expr):
    from sympy import Integral
    srv, doc = serve_expr(y + Integral(x, x) + z**2)   # a short symbol next to a tall integral and a superscript
    page = _open(browser, srv.url)
    kids = _display_children(page, "/")
    for left, right in zip(kids, kids[1:]):
        gx, gy = _gap_between(page, left, right)
        page.mouse.click(gx, gy)
        assert page.locator(".se-caret").count() == 1
        caret = page.locator(".se-caret").bounding_box()
        prev = page.evaluate("p => { const ed = document.querySelector('.sympy-editor').__sympyEditor; const r = ed._visualRect(document.querySelector('[data-path=\"' + p + '\"]')); return [r.top, r.bottom]; }", left)
        assert abs(caret["y"] - prev[0]) <= 1.5, (left, caret, prev)
        assert abs(caret["y"] + caret["height"] - prev[1]) <= 1.5, (left, caret, prev)
    assert page.errors == []


def test_source_line_is_linked_to_the_rendering(browser, serve_expr):
    srv, doc = serve_expr(x**2 + sin(y) / 3)
    page = _open(browser, srv.url)
    src = page.locator(".se-source")
    assert src.get_attribute("contenteditable") in ("plaintext-only", "true")
    text = src.inner_text()
    # selecting "sin(y)" in the source selects that node in the rendering
    start = text.index("sin(y)")
    src.focus()                                               # a user selection: the line has focus
    page.evaluate("""([s, e]) => { const t = document.querySelector('.se-source').firstChild; window.getSelection().setBaseAndExtent(t, s, t, e); }""", [start, start + 6])
    page.wait_for_function("document.querySelector('.se-status').textContent === 'sin: sin(y)'")
    assert page.locator(".se-selected").count() == 1
    # selecting in the rendering highlights the source text
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x**2") + "/0")   # the x of x**2
    assert src.locator("mark").inner_text() == "x"
    page.keyboard.press("ArrowUp")                            # the whole power
    assert src.locator("mark").inner_text() == "x**2"
    assert page.evaluate("document.activeElement.className") == "se-view"   # focus stays in the formula
    # editing the source: Enter applies, Esc reverts; the rendering is never replaced by code
    page.keyboard.press("Escape")
    assert page.locator(".se-selected").count() == 0 and src.locator("mark").count() == 0
    page.keyboard.press("Enter")                              # nothing selected: edit the whole expression...
    assert page.evaluate("document.activeElement.className").startswith("se-source")   # ...in the source line
    assert page.locator(".se-inline").count() == 0
    page.keyboard.press("Control+a")
    page.keyboard.type("x*y + 1")
    assert src.evaluate("e => e.classList.contains('se-dirty')")
    page.keyboard.press("Escape")                             # revert
    assert src.inner_text() == text and not src.evaluate("e => e.classList.contains('se-dirty')")
    src.click()
    page.keyboard.press("Control+a")
    page.keyboard.type("x*y + 1")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x*y + 1'")
    assert doc.expr == x * y + 1
    assert page.locator(".se-view .katex:not(.se-ghost *)").count() == 1       # still rendered
    assert page.errors == []


def test_backspace_unwraps_and_delete_removes(browser, serve_expr):
    from sympy import cos, sqrt
    t = symbols("theta")
    srv, doc = serve_expr(x * cos(t) + sqrt(y))
    page = _open(browser, srv.url)
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "theta"))
    page.keyboard.press("ArrowUp")                        # cos(theta)
    assert page.locator(".se-status").inner_text() == "cos: cos(theta)"
    assert page.locator('.se-toolbar [data-cmd="unwrap"]').is_enabled()
    _next_state(page, lambda: page.keyboard.press("Backspace"))   # keep theta, drop cos
    assert doc.expr == x * t + sqrt(y)
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "sqrt(y)"))
    _next_state(page, lambda: page.locator('.se-toolbar [data-cmd="unwrap"]').click())
    assert doc.expr == x * t + y
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x"))
    page.keyboard.press("ArrowUp")                        # x*theta
    _next_state(page, lambda: page.keyboard.press("Backspace"))   # keeps x, the term ↑ came from
    assert doc.expr == x + y
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "y"))
    _next_state(page, lambda: page.keyboard.press("Delete"))     # Delete removes entirely
    assert doc.expr == x
    assert page.errors == []


def test_floating_action_bar_click_and_tap(browser, serve_expr):
    from sympy import cos
    t = symbols("theta")
    srv, doc = serve_expr(x * cos(t) + y)
    page = _open(browser, srv.url)
    bar = page.locator(".se-actions")
    assert not bar.is_visible()
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "theta"))
    page.keyboard.press("ArrowUp")
    assert bar.is_visible()
    box = page.locator(".se-box-select").bounding_box()
    bb = bar.bounding_box()
    assert bb["y"] >= box["y"] + box["height"]                # right under the selection
    bar.locator('[data-cmd="child"]').click()                 # ↓ button: back into theta
    assert page.locator(".se-status").inner_text() == "Symbol: theta"
    bar.locator('[data-cmd="child"]').click()                 # ↓ on an atom: a caret after it, bar gone
    assert page.locator(".se-caret").count() == 1 and not bar.is_visible()
    page.keyboard.press("ArrowUp")                            # back on theta
    page.locator('.se-toolbar [data-cmd="parent"]').click()   # toolbar ↑ to cos(theta)
    assert page.locator(".se-status").inner_text() == "cos: cos(theta)"
    _next_state(page, lambda: bar.locator('[data-cmd="unwrap"]').click())
    assert doc.expr == x * t + y
    assert bar.is_visible()                                   # the selection survives on the changed node
    page.keyboard.press("Escape")
    assert not bar.is_visible()                               # nothing selected: no bar
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "y"))
    _next_state(page, lambda: bar.locator('[data-cmd="delete"]').click())
    assert doc.expr == x * t
    # by finger
    ctx = browser.new_context(has_touch=True, is_mobile=True, viewport={"width": 420, "height": 800})
    tp = ctx.new_page()
    tp.goto(srv.url)
    tp.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    cx, cy = _center(tp, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "theta"))
    tp.touchscreen.tap(cx, cy)
    tp.locator(".se-actions [data-cmd=\"parent\"]").tap()   # x*theta
    assert tp.locator(".se-status").inner_text().startswith("Mul")
    tp.locator(".se-actions [data-cmd=\"unwrap\"]").tap()   # keeps theta: the factor ↑ came from
    tp.wait_for_function("document.querySelector('.se-source').textContent === 'theta'")
    assert doc.expr == t
    ctx.close()
    assert page.errors == []


def test_copy_cut_paste_of_a_selection(browser, serve_expr):
    from sympy import cos
    t = symbols("theta")
    srv, doc = serve_expr(x * cos(t) + y)
    page = _open(browser, srv.url)
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "theta"))
    page.keyboard.press("ArrowUp")                        # cos(theta)
    # Ctrl+C: the copy event carries the SymPy source of the selection
    copied = page.evaluate("""() => { const dt = new DataTransfer(); const ev = new ClipboardEvent('copy', {clipboardData: dt, bubbles: true, cancelable: true});
        document.activeElement.dispatchEvent(ev); return dt.getData('text/plain'); }""")
    assert copied == "cos(theta)"
    # Ctrl+V on another selection replaces it with the pasted text
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "y"))
    _next_state(page, lambda: page.evaluate("""() => { const dt = new DataTransfer(); dt.setData('text/plain', 'cos(theta)');
        document.activeElement.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }"""))
    assert doc.expr == x * cos(t) + cos(t)
    # Ctrl+X: copies and removes
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x"))
    _next_state(page, lambda: page.evaluate("""() => { const dt = new DataTransfer();
        document.activeElement.dispatchEvent(new ClipboardEvent('cut', {clipboardData: dt, bubbles: true, cancelable: true})); window.__cut = dt.getData('text/plain'); }"""))
    assert page.evaluate("window.__cut") == "x" and doc.expr == 2 * cos(t)
    # paste at a caret splices like typing
    kids = _display_children(page, "/")
    gx, gy = _gap_between(page, kids[0], kids[1]) if len(kids) > 1 else _center(page, kids[0])
    page.mouse.click(gx, gy) if len(kids) > 1 else page.keyboard.press("Tab")
    assert page.locator(".se-caret").count() == 1
    _next_state(page, lambda: page.evaluate("""() => { const dt = new DataTransfer(); dt.setData('text/plain', '+ y');
        document.activeElement.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }"""))
    assert doc.expr.has(y)
    # the action bar's Copy button (system clipboard, permission granted here)
    ctx = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    p2 = ctx.new_page()
    p2.goto(srv.url)
    p2.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    _click(p2, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "y"))
    p2.locator(".se-actions [data-cmd=\"copy\"]").click()
    assert p2.evaluate("navigator.clipboard.readText()") == "y"
    ctx.close()
    assert page.errors == []


def test_function_box_search_prompt_and_paste_button(browser, serve_expr):
    from sympy import FiniteSet, cos
    srv, doc = serve_expr(sin(x) * cos(y))
    ctx = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    assert not page.locator(".se-loading").is_visible()     # no overlay on the HTTP backend
    fn = page.locator(".se-fn")
    fn.click()
    page.wait_for_function("document.querySelectorAll('.se-fn-item').length > 0")   # the list appears on focus
    fn.fill("sol")
    menu = page.locator(".se-fn-menu")
    assert menu.is_visible() and menu.locator(".se-fn-item").first.get_attribute("data-name") == "solve"
    page.keyboard.press("Enter")                              # pick solve: it needs a symbol -> a prompt
    form = page.locator(".se-fn-form")
    assert form.is_visible() and "solve(" in form.locator(".se-fn-title").inner_text()
    sel = form.locator("select")
    assert sel.locator("option").all_inner_texts() == ["x", "y"]   # the free symbols of the selection
    sel.select_option("y")
    _next_state(page, lambda: form.locator(".se-fn-apply").click())
    assert isinstance(doc.expr, FiniteSet) and all(not e.has(y) for e in doc.expr)
    assert not form.is_visible() and fn.input_value() == ""
    # a function without required parameters applies at once
    srv2, doc2 = serve_expr(x**2 - 1)
    page.goto(srv2.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    fn = page.locator(".se-fn")
    fn.click()
    assert _wait(lambda: page.evaluate("document.querySelector('.sympy-editor').__sympyEditor._functionsLoaded"), timeout=10)   # the list is fetched on focus
    fn.fill("factor")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc2.expr == (x - 1) * (x + 1) and not page.locator(".se-fn-form").is_visible()
    # a prompt applied with the default choice (Enter in the form)
    fn.click()
    fn.fill("diff")
    page.keyboard.press("Enter")
    assert page.locator(".se-fn-form").is_visible()
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc2.expr == 2 * x
    # typed with arguments: applied as written; errors are reported
    fn.click()
    fn.fill("bogus(")
    page.keyboard.press("Enter")
    page.wait_for_selector(".se-error:not([hidden])")
    # Paste button over a selection
    page.evaluate("navigator.clipboard.writeText('z + 1')")
    _click(page, next(k for k, v in doc2.snapshot()["nodes"].items() if v["src"] == "2"))
    _next_state(page, lambda: page.locator('.se-toolbar [data-cmd="paste"]').click())
    assert doc2.expr == (z + 1) * x
    assert page.errors == []
    ctx.close()


def test_paste_over_the_whole_expression_applies_at_once(browser, serve_expr):
    srv, doc = serve_expr(x**2 + sin(y))
    page = _open(browser, srv.url)
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")                          # the whole expression is selected
    paste = """(text) => { const dt = new DataTransfer(); dt.setData('text/plain', text);
        document.activeElement.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }"""
    # Ctrl+V replaces it right away (typing there would go to the source line, a paste is complete)
    _next_state(page, lambda: page.evaluate(paste, "θ + 1"))
    assert doc.expr == symbols("theta") + 1
    assert page.locator(".se-inline").count() == 0 and page.evaluate("document.activeElement.className") == "se-view"
    # the Paste button too (the last copy made here stands in for the system clipboard)
    page.keyboard.press("Escape")
    page.keyboard.press("ArrowDown")
    page.evaluate("() => { navigator.clipboard = undefined; document.querySelector('.sympy-editor').__sympyEditor._clip = 'z**3'; }")
    _next_state(page, lambda: page.locator('.se-toolbar [data-cmd="paste"]').click())
    assert doc.expr == z**3
    # destroy() unhooks the editor from the document (clipboard and selection
    # listeners), so disposed notebook outputs do not pile up
    removed = page.evaluate("""() => { const ed = document.querySelector('.sympy-editor').__sympyEditor;
        const removed = [], orig = document.removeEventListener.bind(document);
        document.removeEventListener = (k, fn, o) => { removed.push(k); orig(k, fn, o); };
        ed.destroy(); document.removeEventListener = orig; return removed.sort(); }""")
    assert removed == ["copy", "cut", "paste", "selectionchange"]
    assert page.locator(".sympy-editor").count() == 0
    assert page.errors == []


def _view_font_px(page):
    return page.evaluate("parseFloat(getComputedStyle(document.querySelector('.se-view')).fontSize)")


def test_zoom_buttons_wheel_keys_and_pinch(browser, serve_expr):
    srv, doc = serve_expr(x**2 + sin(y))
    page = _open(browser, srv.url)
    base = _view_font_px(page)
    zoom = lambda: page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.zoom")
    label = page.locator('.se-toolbar [data-cmd="zoomreset"]')
    assert zoom() == 1 and label.inner_text() == "100%" and label.is_disabled()
    # buttons: the formula grows, the selection box follows it
    _click(page, "/0")
    page.locator('.se-toolbar [data-cmd="zoomin"]').click()
    assert zoom() == 1.2 and abs(_view_font_px(page) - base * 1.2) < 0.5 and label.inner_text() == "120%"
    box = page.evaluate("document.querySelector('.se-box-select').getBoundingClientRect().toJSON()")
    node = page.evaluate("document.querySelector('.se-selected').getBoundingClientRect().toJSON()")   # the innermost node under the click
    assert box["left"] <= node["left"] + 1 and box["right"] >= node["right"] - 1 and box["top"] <= node["top"] + 1
    assert page.locator(".se-selected").count() == 1
    page.locator('.se-toolbar [data-cmd="zoomout"]').click()
    assert zoom() == 1 and label.is_disabled()
    # Ctrl+wheel over the formula zooms (a trackpad pinch arrives the same way)
    page.evaluate("""() => { const v = document.querySelector('.se-view'), r = v.getBoundingClientRect();
        v.dispatchEvent(new WheelEvent('wheel', {deltaY: -100, ctrlKey: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, bubbles: true, cancelable: true})); }""")
    assert zoom() > 1
    # keys: Ctrl+0 resets, Ctrl+minus shrinks below 1
    page.locator(".se-view").focus()
    page.keyboard.press("Control+0")
    assert zoom() == 1
    page.keyboard.press("Control+-")
    assert abs(zoom() - 1 / 1.2) < 0.01
    page.keyboard.press("Control+0")
    # pinch: two touch pointers moving apart
    page.evaluate("""() => { const v = document.querySelector('.se-view'), r = v.getBoundingClientRect();
        const y = r.top + r.height / 2, cx = r.left + r.width / 2;
        const ev = (type, id, x) => v.dispatchEvent(new PointerEvent(type, {bubbles: true, cancelable: true, clientX: x, clientY: y, pointerType: 'touch', pointerId: id, isPrimary: id === 1, buttons: 1}));
        ev('pointerdown', 1, cx - 50); ev('pointerdown', 2, cx + 50);
        ev('pointermove', 1, cx - 100); ev('pointermove', 2, cx + 100);
        ev('pointerup', 1, cx - 100); ev('pointerup', 2, cx + 100); }""")
    assert abs(zoom() - 2) < 0.01
    assert page.locator(".se-selected").count() == 1   # the pinch selected nothing new (no range, no click)
    assert page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.range") is None
    assert doc.expr == x**2 + sin(y) and page.errors == []


def test_long_formula_scrolls_sideways_and_fits_a_phone(browser, serve_expr):
    terms = symbols("a0:24")
    srv, doc = serve_expr(sum(t**2 for t in terms))
    page = browser.new_page(viewport={"width": 400, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    view = page.locator(".se-view")
    scroll_left = lambda: page.evaluate("document.querySelector('.se-view').scrollLeft")
    assert page.evaluate("(() => { const v = document.querySelector('.se-view'); return v.scrollWidth > v.clientWidth; })()")
    # the page itself does not overflow: the editor fits the screen, the tools wrap onto several rows
    assert page.evaluate("document.documentElement.scrollWidth") <= 400
    assert page.evaluate("getComputedStyle(document.querySelector('.se-tools')).flexWrap") == "wrap"
    rows = page.evaluate("(() => { const tops = new Set([...document.querySelectorAll('.se-tools > button')].map(b => Math.round(b.getBoundingClientRect().top))); return tops.size; })()")
    assert rows >= 2 and page.evaluate("(() => { const t = document.querySelector('.se-tools'); return t.scrollWidth <= t.clientWidth; })()")
    # the action bar under a selection wraps as well instead of running off the screen
    _click(page, "/0")
    bar = page.locator(".se-actions").bounding_box()
    root = page.locator(".sympy-editor").bounding_box()
    assert bar["x"] >= root["x"] and bar["x"] + bar["width"] <= root["x"] + root["width"] + 1
    assert page.evaluate("(() => { const tops = new Set([...document.querySelectorAll('.se-actions button')].map(b => Math.round(b.getBoundingClientRect().top))); return tops.size; })()") >= 2
    page.keyboard.press("Escape")
    # a plain wheel over the formula scrolls it sideways instead of the page
    r = view.bounding_box()
    page.mouse.move(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
    page.mouse.wheel(0, 120)
    page.wait_for_timeout(100)
    assert scroll_left() > 0 and page.evaluate("window.scrollY") == 0
    # dragging empty space (the padding above the glyphs) pans it back, without selecting anything
    before = scroll_left()
    x0, y0 = r["x"] + 100, r["y"] + 3
    assert page.evaluate("([x, y]) => !document.elementFromPoint(x, y).closest('[data-path]')", [x0, y0])
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x0 + 60, y0, steps=4)
    page.mouse.up()
    assert before - scroll_left() >= 50
    assert page.locator(".se-selected").count() == 0 and page.locator(".se-caret").count() == 0
    assert errors == []


def test_source_line_previews_while_typing(browser, serve_expr):
    srv, doc = serve_expr(x**2 + sin(y))
    page = _open(browser, srv.url)
    src = page.locator(".se-source")
    src.click()
    page.keyboard.press("Control+a")
    page.keyboard.type("cos(x)*3")
    _wait(lambda: page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.state.src") == "3*cos(x)")
    assert page.locator('.se-view [data-path="/"]').count() == 1
    assert "3" in page.locator(".se-view .katex").inner_text()     # rendered
    assert doc.expr == x**2 + sin(y)                                 # not committed
    assert src.inner_text() == "cos(x)*3"                            # the line is left alone (no normalisation while typing)
    assert "Previewing" in page.locator(".se-status").inner_text()
    # a string that does not parse marks the line and keeps the rendering
    page.keyboard.type("+(")
    page.wait_for_timeout(500)
    assert "se-invalid" in src.get_attribute("class")
    assert page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.state.src") == "3*cos(x)"
    page.keyboard.press("Backspace")
    page.keyboard.press("Backspace")
    _wait(lambda: "se-invalid" not in src.get_attribute("class"))
    # Esc reverts to what is committed
    page.keyboard.press("Escape")
    _wait(lambda: page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.state.src") == "x**2 + sin(y)")
    assert src.inner_text() == "x**2 + sin(y)" and doc.expr == x**2 + sin(y)
    # Enter commits the previewed text
    src.click()
    page.keyboard.press("Control+a")
    page.keyboard.type("y**3")
    _wait(lambda: page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.state.src") == "y**3")
    assert doc.expr == x**2 + sin(y)
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == y**3 and "se-dirty" not in src.get_attribute("class")
    assert page.errors == []


def test_long_computation_shows_spinner_and_can_be_interrupted(browser):
    import time
    from sympy_editor.ops import Op, get_ops

    def forever(expr):
        while True:
            time.sleep(0.001)

    ops = get_ops()
    ops["forever"] = Op("forever", "Take forever", forever)
    doc = Document(x + 1, ops=ops)
    srv = EditorServer(doc, port=0, options={"workingAfter": 100, "interruptAfter": 600})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        page = _open(browser, srv.url)
        page.select_option(".se-ops", "forever")
        overlay = page.locator(".se-loading")
        _wait(lambda: overlay.is_visible())
        assert "Take forever" in overlay.inner_text() and page.locator(".se-spinner").is_visible()
        button = page.locator(".se-interrupt")
        assert not button.is_visible()                         # not yet: only after interruptAfter
        _wait(lambda: button.is_visible(), timeout=5)
        _next_state(page, lambda: button.click())
        assert "Interrupted" in page.locator(".se-error").inner_text()
        assert not overlay.is_visible() and doc.expr == x + 1
        # the editor works normally afterwards; a transformation that changes nothing says so
        _next_state(page, lambda: page.select_option(".se-ops", "expand"))
        assert page.locator(".se-error").is_hidden()
        assert _wait(lambda: page.locator(".se-status").inner_text().startswith("No change: Expand"))
        assert page.errors == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_delete_button_empties_the_whole_expression(browser, serve_expr):
    srv, doc = serve_expr(x**2 + sin(y))
    page = _open(browser, srv.url)
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")                              # the whole expression
    page.locator('.se-toolbar [data-cmd="delete"]').click()        # the button (a phone has no Delete key)
    # the focus stays in the source line, which is empty; the formula is hidden
    assert page.evaluate("document.activeElement.className").startswith("se-source")
    assert page.locator(".se-source").inner_text() == ""
    assert "se-empty" in page.locator(".se-view").get_attribute("class")
    assert page.locator(".se-view .katex").is_hidden()
    page.keyboard.type("z")
    _wait(lambda: "se-empty" not in page.locator(".se-view").get_attribute("class"))   # previewed as it is typed
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == z
    assert page.errors == []


def test_arrow_buttons_move_the_selection_and_the_caret(browser, serve_expr):
    a, b, c = symbols("a b c")
    srv, doc = serve_expr(a + b + c)
    page = _open(browser, srv.url)
    kids = _display_children(page, "/")
    _click(page, kids[0])
    page.locator('.se-toolbar [data-cmd="right"]').click()
    assert page.locator(".se-selected").get_attribute("data-path") == kids[1]
    page.locator('.se-actions [data-cmd="right"]').click()
    assert page.locator(".se-selected").get_attribute("data-path") == kids[2]
    page.locator('.se-actions [data-cmd="left"]').click()
    assert page.locator(".se-selected").get_attribute("data-path") == kids[1]
    # with a caret, the buttons move it between the terms
    page.keyboard.press("Tab")                                     # caret after b
    assert page.locator(".se-caret").count() == 1
    gap = "(() => { const c = document.querySelector('.sympy-editor').__sympyEditor.caret; return [c.index, document.querySelector('.se-caret').getBoundingClientRect().left]; })()"
    i1, x1 = page.evaluate(gap)
    page.locator('.se-toolbar [data-cmd="right"]').click()
    i2, x2 = page.evaluate(gap)
    assert i2 == i1 + 1 and x2 > x1
    page.locator('.se-toolbar [data-cmd="left"]').click()
    i3, x3 = page.evaluate(gap)
    assert i3 == i1 and x3 < x2                                    # back in the previous gap (at its near end, like the ← key)
    # with a caret, ↓ is disabled and ↑ selects the atom the caret is attached to
    assert page.locator('.se-toolbar [data-cmd="child"]').is_disabled()
    assert page.locator('.se-toolbar [data-cmd="parent"]').is_enabled()
    page.locator('.se-toolbar [data-cmd="parent"]').click()
    assert page.locator(".se-caret").count() == 0 and page.locator(".se-selected").get_attribute("data-path") == kids[1]
    assert page.errors == []


def test_caret_walks_through_atoms_across_levels(browser, serve_expr):
    from sympy import sin
    a, b, c = symbols("a b c")
    srv, doc = serve_expr(a + sin(b) + c)                          # printed a + c + sin(b): |a|c|sin(|b|)|
    page = _open(browser, srv.url)
    assert doc.snapshot()["src"] == "a + c + sin(b)"
    nodes = doc.snapshot()["nodes"]
    pb = next(k for k, v in nodes.items() if v["src"] == "b")
    kids = _display_children(page, "/")
    _click(page, kids[0])                                          # a
    page.keyboard.press("Tab")                                     # caret after a (the gap before c)
    caret = "(() => { const c = document.querySelector('.sympy-editor').__sympyEditor.caret; return c && {path: c.path, extend: c.extend || null, index: c.index, x: document.querySelector('.se-caret').getBoundingClientRect().left}; })()"
    start = page.evaluate(caret)
    assert start["path"] == "/" and start["index"] == 1
    right = page.locator('.se-toolbar [data-cmd="right"]')
    steps = []
    for _ in range(4):
        assert right.is_enabled()
        right.click()
        steps.append(page.evaluate(caret))
    # gap before sin(b), into sin: before b, after b, out of sin: the end of the sum, where → is disabled
    assert [(c["path"], c["extend"], c["index"]) for c in steps] == [("/", None, 2), (pb, "before", 0), (pb, "after", 0), ("/", None, 3)]
    assert all(steps[i]["x"] > steps[i - 1]["x"] for i in range(1, 4)) and steps[0]["x"] > start["x"]
    assert right.is_disabled()
    # back: the same positions in reverse, strictly leftwards, down to the start of the sum, where ← is disabled
    left = page.locator('.se-toolbar [data-cmd="left"]')
    back = []
    for _ in range(5):
        assert left.is_enabled()
        left.click()
        back.append(page.evaluate(caret))
    assert [(c["path"], c["extend"], c["index"]) for c in back] == [(pb, "after", 0), (pb, "before", 0), ("/", None, 2), ("/", None, 1), ("/", None, 0)]
    assert all(back[i]["x"] < back[i - 1]["x"] for i in range(1, 5))
    assert left.is_disabled() and right.is_enabled()
    # ↑ from the caret before b selects b
    for _ in range(3):
        right.click()
    assert page.evaluate(caret)["extend"] == "before"
    page.locator('.se-toolbar [data-cmd="parent"]').click()
    assert page.locator(".se-selected").get_attribute("data-path") == pb and page.locator(".se-caret").count() == 0
    assert page.errors == []


def test_caret_enters_a_rational_and_its_parts_are_editable(browser, serve_expr):
    from sympy import Rational
    srv, doc = serve_expr(x + Rational(1, 2))                       # printed x + 1/2
    page = _open(browser, srv.url)
    nodes = doc.snapshot()["nodes"]
    pr = next(k for k, v in nodes.items() if v["src"] == "1/2")
    assert pr + "/n" in nodes and pr + "/d" in nodes
    caret = "(() => { const c = document.querySelector('.sympy-editor').__sympyEditor.caret; return c && [c.path, c.extend || null]; })()"
    # ←/→ from nothing: a caret at the start / the end
    page.locator(".se-view").focus()
    page.locator('.se-toolbar [data-cmd="right"]').click()
    assert page.evaluate(caret) == ["/", None] and page.locator(".se-caret").count() == 1
    assert page.locator('.se-toolbar [data-cmd="right"]').is_disabled()       # nothing further right
    assert page.locator('.se-toolbar [data-cmd="left"]').is_enabled()
    # walking left enters the number: after the denominator, before it, after the numerator, before it
    left = page.locator('.se-toolbar [data-cmd="left"]')
    seen = []
    for _ in range(4):
        left.click()
        seen.append(page.evaluate(caret))
    assert seen == [[pr + "/d", "after"], [pr + "/d", "before"], [pr + "/n", "after"], [pr + "/n", "before"]]
    # the numerator is selectable and editable
    page.locator('.se-toolbar [data-cmd="parent"]').click()
    assert page.locator(".se-selected").get_attribute("data-path") == pr + "/n"
    page.keyboard.type("3")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == x + Rational(3, 2)
    _click(page, pr + "/d")
    page.keyboard.type("y")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == x + 3 / y
    page.keyboard.press("Escape")
    page.locator('.se-toolbar [data-cmd="left"]').click()                    # from nothing: the start
    assert page.evaluate(caret) == ["/", None] and page.locator('.se-toolbar [data-cmd="left"]').is_disabled()
    assert page.errors == []


def test_empty_formula_keeps_the_cursor_and_zoom_block_stays_together(browser, serve_expr):
    srv, doc = serve_expr(x**2 + sin(y))
    page = browser.new_page(viewport={"width": 420, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    # the zoom buttons share one row even when the toolbar wraps
    tops = page.evaluate("[...document.querySelectorAll('.se-zoom button')].map(b => Math.round(b.getBoundingClientRect().top))")
    assert len(tops) == 3 and len(set(tops)) == 1
    # delete everything, then click the (empty) formula area: the cursor goes to the empty line, nothing comes back
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")
    page.locator('.se-toolbar [data-cmd="delete"]').click()
    assert "se-empty" in page.locator(".se-view").get_attribute("class")
    r = page.locator(".se-view").bounding_box()
    page.mouse.click(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
    assert page.evaluate("document.activeElement.className").startswith("se-source")
    assert page.locator(".se-source").inner_text() == "" and "se-empty" in page.locator(".se-view").get_attribute("class")
    page.keyboard.type("q + 1")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == symbols("q") + 1
    # Esc from the empty state still brings the previous expression back
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Delete")
    page.keyboard.press("Escape")
    assert page.locator(".se-source").inner_text() == "q + 1" and "se-empty" not in page.locator(".se-view").get_attribute("class")
    assert errors == []


def test_change_animation_red_to_green(browser, serve_expr):
    from sympy import cos
    srv, doc = serve_expr(x**2 + sin(y))
    page = _open(browser, srv.url)
    nodes = doc.snapshot()["nodes"]
    ps = next(k for k, v in nodes.items() if v["src"] == "sin(y)")
    _click(page, ps)
    page.keyboard.press("ArrowUp")                                # sin(y) (the click lands on y)
    assert page.locator(".se-selected").get_attribute("data-path") == ps
    page.keyboard.type("cos(y)")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    # right after the change: a red ghost of the old part, a green ghost of the new one, the real rendering hidden but present
    assert page.locator(".se-ghost").count() == 2
    assert page.locator(".se-ghost-old .se-removed").count() >= 1 and "sin" in page.locator(".se-ghost-old").inner_text()
    assert page.locator(".se-ghost-new .se-added").count() >= 1 and "cos" in page.locator(".se-ghost-new .se-added").first.inner_text()
    assert page.evaluate("getComputedStyle(document.querySelector('.se-view .se-changing')).opacity") == "0"
    assert page.locator('.se-view [data-path="/"]').count() == 1     # the ghosts carry no paths: hit-testing is untouched
    red = page.evaluate("getComputedStyle(document.querySelector('.se-ghost-old .se-removed')).color")
    green = page.evaluate("getComputedStyle(document.querySelector('.se-ghost-new .se-added')).color")
    assert red != green
    # when it is over: no ghosts, the new part stays green in the real rendering
    assert _wait(lambda: page.locator(".se-ghost").count() == 0, timeout=5)
    assert page.locator(".se-view .se-added").count() >= 1
    assert page.evaluate("getComputedStyle(document.querySelector('.se-view .se-added')).color") == green
    assert doc.expr == x**2 + cos(y)
    # ... until the formula is touched
    _click(page, "/0")
    assert page.locator(".se-view .se-added").count() == 0
    # a preview does not animate - the commit does, from the last committed formula
    page.locator(".se-source").click()
    page.keyboard.press("End")
    page.keyboard.type(" + 1")
    _wait(lambda: page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.state.src") == "x**2 + cos(y) + 1")
    assert page.locator(".se-ghost").count() == 0
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert page.locator(".se-ghost").count() == 2 and "1" in page.locator(".se-ghost-new .se-added").first.inner_text()
    assert _wait(lambda: page.locator(".se-ghost").count() == 0, timeout=5)
    # a reverted preview animates nothing
    page.locator(".se-source").click()
    page.keyboard.press("End")
    page.keyboard.type(" + 2")
    _wait(lambda: page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.state.src") == "x**2 + cos(y) + 3")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert page.locator(".se-ghost").count() == 0 and doc.expr == x**2 + cos(y) + 1
    assert page.errors == []


def test_change_animation_term_typed_at_a_caret_merges_green(browser, serve_expr):
    srv, doc = serve_expr(2 * x + 3 * y)
    page = _open(browser, srv.url)
    nodes = doc.snapshot()["nodes"]
    p3y = next(k for k, v in nodes.items() if v["src"] == "3*y")
    _click(page, next(k for k, v in nodes.items() if v["src"] == "y"))
    page.keyboard.press("ArrowUp")                                # 3*y
    page.keyboard.press("Tab")                                    # caret after it
    assert page.locator(".se-caret").count() == 1
    page.keyboard.type("+x")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == 3 * x + 3 * y
    # the old 2x goes red as a whole and the new 3x comes green as a whole (not a red 2 beside a green 3)
    assert page.locator(".se-ghost").count() == 2
    red = [t.strip() for t in page.locator(".se-ghost-old .se-removed").all_inner_texts()]
    green = [t.strip() for t in page.locator(".se-ghost-new .se-added").all_inner_texts()]
    assert any("2" in t and "x" in t for t in red), red
    assert any("3" in t and "x" in t for t in green) and not any("3" in t and "y" in t for t in green), green
    assert _wait(lambda: page.locator(".se-ghost").count() == 0, timeout=5)
    p3x = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "3*x")
    assert "se-added" in page.locator(f'.se-view [data-path="{p3x}"]').first.get_attribute("class")
    assert page.errors == []


def test_replacing_the_whole_expression(browser, serve_expr):
    srv, doc = serve_expr(x**2 + sin(y))
    page = _open(browser, srv.url)
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")                          # the whole expression
    page.keyboard.type("z")                                   # typing: starts over in the source line, never a field over the formula
    assert page.locator(".se-inline").count() == 0
    assert page.evaluate("document.activeElement.className").startswith("se-source")
    assert page.locator(".se-source").inner_text() == "z"
    page.keyboard.type("+1")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == z + 1
    # Delete with everything selected: the source line is emptied, new text replaces the expression
    page.locator(".se-view").focus()
    page.keyboard.press("Escape")
    page.keyboard.press("ArrowDown")
    assert page.locator('.se-toolbar [data-cmd="delete"]').is_enabled()
    page.keyboard.press("Delete")
    assert page.locator(".se-source").inner_text() == "" and "removed" in page.locator(".se-status").inner_text()
    page.keyboard.type("q**2")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == symbols("q") ** 2
    # an empty line left behind restores the expression, and says so
    page.locator(".se-source").click()
    page.keyboard.press("Control+a")
    page.keyboard.press("Backspace")
    page.evaluate("document.querySelector('.se-source').blur()")   # leave the line empty
    assert page.locator(".se-source").inner_text() == "q**2" and "Empty" in page.locator(".se-status").inner_text()
    assert doc.expr == symbols("q") ** 2
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
    page.keyboard.type("+ 2")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'y + z + 2'")
    # the keyboard button: visible on touch devices, opens a field for the selection
    kb = page.locator('.se-toolbar [data-cmd="keyboard"]')
    assert kb.is_visible()
    x0, y0 = _center(page, "/0")
    page.touchscreen.tap(x0, y0)
    kb.tap()
    assert page.locator(".se-inline").count() == 1 and page.evaluate("document.activeElement.className") == "se-inline"
    page.keyboard.press("Escape")                       # cancel the field...
    page.keyboard.press("Escape")                       # ...and clear the selection
    kb.tap()                                             # nothing selected: the whole expression, in the source line
    assert page.locator(".se-inline").count() == 0
    assert page.evaluate("document.activeElement.className").startswith("se-source")
    assert page.evaluate("window.getSelection().toString()") == "y + z + 2"
    page.keyboard.press("Escape")
    assert errors == []
    ctx.close()


def test_keyboard_button_hidden_with_a_mouse(browser, serve_expr):
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    assert page.locator('.se-toolbar [data-cmd="keyboard"]').count() == 1
    assert not page.locator('.se-toolbar [data-cmd="keyboard"]').is_visible()
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
    page.wait_for_function("[...document.querySelectorAll('.se-loading')].every(o => o.hidden)", timeout=240000)
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
    # one runtime: the page itself fetched Pyodide at most once (in a worker, not at all)
    assert page.evaluate("performance.getEntriesByType('resource').filter(e => e.name.endsWith('pyodide.asm.js')).length") <= 1
    assert page.evaluate("document.querySelectorAll('.se-error:not([hidden])').length") == 0


@pytest.mark.skipif(not os.environ.get("SYMPY_EDITOR_SLOW_TESTS"), reason="set SYMPY_EDITOR_SLOW_TESTS=1")
def test_pyodide_page_preloads_the_runtime(browser, tmp_path):
    path = tmp_path / "pre.html"
    path.write_text(to_html(x + y), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    page.wait_for_function("document.querySelector('.se-status').textContent.includes('Python')", timeout=30000)
    assert page.locator(".se-loading").is_visible()            # a blocking overlay while Python loads
    assert "Python" in page.locator(".se-loading-text").inner_text()
    page.wait_for_function("window.__sympyEditorPyodide && window.__sympyEditorPyodide.docs === 1", timeout=180000)
    page.wait_for_function("document.querySelector('.se-loading').hidden", timeout=30000)
    assert page.locator(".se-status").inner_text().startswith("Click to select")   # back to the idle hint, no edit happened
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
    page.wait_for_function("document.querySelector('.se-loading').hidden", timeout=240000)   # the overlay blocks input while loading
    _click(page, '/0')          # y
    page.keyboard.type("sin(x)")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('sin(x)')", timeout=180000)
    assert page.locator(".se-error").is_hidden()


@pytest.mark.skipif(not os.environ.get("SYMPY_EDITOR_SLOW_TESTS"), reason="set SYMPY_EDITOR_SLOW_TESTS=1")
def test_pyodide_worker_interrupt_and_sessions(browser, tmp_path):
    from sympy import Integer
    from sympy_editor import save_html
    big = Integer(3 * 10**6)                        # its factorial takes a minute or more (and 7 MB, not gigabytes)
    path = save_html(big, tmp_path / "sessions.html", options={"sessions": True, "workingAfter": 100, "interruptAfter": 500})
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"file://{path}")
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    if page.evaluate("typeof Worker === 'undefined'"):
        pytest.skip("no Worker")
    # a file:// page cannot spawn a worker in Chromium: serve the page instead
    import http.server, functools
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        page.goto(f"http://127.0.0.1:{httpd.server_address[1]}/sessions.html")
        page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
        _wait(lambda: page.locator(".se-loading").is_hidden(), timeout=180)
        ed = "document.querySelector('.sympy-editor').__sympyEditor"
        assert page.evaluate(f"{ed}.backend.canInterrupt()")
        # the long computation: the page stays alive (the spinner animates in the DOM), Interrupt stops it
        page.locator(".se-fn").fill("factorial()")     # with parentheses: applied as written, one request
        page.keyboard.press("Enter")
        assert _wait(lambda: page.locator(".se-interrupt").is_visible(), timeout=10)
        assert page.evaluate("document.querySelector('.se-spinner') !== null")
        _next_state(page, lambda: page.locator(".se-interrupt").click())
        assert "Interrupted" in page.locator(".se-error").inner_text()
        # Python restarts on the next request; the document is back from its last state
        _next_state(page, lambda: page.select_option(".se-ops", "expand"))
        assert _wait(lambda: page.locator(".se-loading").is_hidden(), timeout=180)
        assert page.evaluate(f"{ed}.state.src") == str(big)
        # the drawer (☰) lists the sessions - the first one so far - and the history of the current one
        assert page.locator(".se-drawer").is_hidden()
        page.locator('.se-toolbar [data-cmd="drawer"]').click()
        assert _wait(lambda: page.locator(".se-drawer").is_visible())
        assert page.locator(".se-session").count() == 2                # one session + the "new" row
        assert _wait(lambda: page.locator(".se-step").count() >= 1, timeout=10)
        # the history is a sub-tab nested inside the current session's card, collapsed by default
        assert page.locator(".se-drawer-pane[data-pane=history]").is_hidden()
        assert page.locator(".se-session-current .se-subtab[data-tab=history]").count() == 1
        page.locator('.se-session-current .se-subtab[data-tab="history"]').click()
        assert page.locator(".se-session-current .se-drawer-pane[data-pane=history]").is_visible()
        page.locator('.se-session-current .se-subtab[data-tab="history"]').click()          # toggles
        assert page.locator(".se-drawer-pane[data-pane=history]").is_hidden()
        # "New session…" offers an empty formula (default), a copy, and the examples
        page.locator(".se-session-new").click()
        picker = page.locator(".se-session-picker")
        assert picker.is_visible() and "se-choice-default" in picker.locator('.se-choice[data-start="empty"]').get_attribute("class")
        assert picker.locator(".se-choice").count() >= 2 + 8
        assert picker.locator(".se-choice", has_text="Quadratic formula").count() == 1
        # an example starts a session with that expression
        _next_state(page, lambda: picker.locator(".se-choice", has_text="Quadratic formula").click())
        assert _wait(lambda: page.locator(".se-session").count() == 3, timeout=30)
        assert page.evaluate(f"{ed}.state.src") == "Eq(x, (-b + sqrt(-4*a*c + b**2))/(2*a))"
        page.keyboard.press("Escape")                                  # closes the drawer
        assert page.locator(".se-drawer").is_hidden()
        # an empty session: the formula area is empty and the cursor is in the source line
        page.locator('.se-toolbar [data-cmd="drawer"]').click()
        page.locator(".se-session-new").click()
        page.locator('.se-choice[data-start="empty"]').click()
        assert _wait(lambda: page.locator(".se-session").count() == 4, timeout=30)
        assert _wait(lambda: page.locator(".se-drawer").is_hidden() and "se-empty" in page.locator(".se-view").get_attribute("class"), timeout=10)
        assert page.evaluate("document.activeElement.className").startswith("se-source") and page.locator(".se-source").inner_text() == ""
        page.keyboard.type("x + 1")
        _next_state(page, lambda: page.keyboard.press("Enter"))
        assert _wait(lambda: page.evaluate("JSON.parse(localStorage.getItem('sympy-editor:sessions')).list.some(s => s.name === 'x + 1')"), timeout=10)
        # the history of this session has two steps (the placeholder, then x + 1); the first one can be jumped to
        page.locator('.se-toolbar [data-cmd="drawer"]').click()
        page.locator('.se-session-current .se-subtab[data-tab="history"]').click()
        assert _wait(lambda: page.locator(".se-step").count() == 2 and "se-step-current" in page.locator(".se-step").nth(1).get_attribute("class"), timeout=10)
        assert "(2)" in page.locator('.se-session-current .se-subtab[data-tab="history"]').inner_text()
        # each step is rendered as a diff: the previous formula (0, red) -> this one (x + 1, green)
        step2 = page.locator(".se-step").nth(1)
        assert _wait(lambda: step2.locator(".se-step-formulas .katex").count() == 2, timeout=10)
        assert step2.locator(".se-step-before .se-diff-removed").count() >= 1 and step2.locator(".se-step-after .se-diff-added").count() >= 1
        assert "0" in step2.locator(".se-step-before").inner_text() and "x+1" in step2.locator(".se-step-after").inner_text().replace(" ", "")
        assert page.locator(".se-drawer [data-path]").count() == 0     # history formulas carry no live paths
        assert page.locator(".se-step").first.locator(".se-step-formulas .katex").count() == 1   # the first step: just the formula
        _next_state(page, lambda: page.locator(".se-step").first.click())
        assert page.evaluate(f"{ed}.state.src") == "0" and page.evaluate(f"{ed}.state.can_redo")
        # switching back to the first session: tapping its row (not only its Open button) opens it
        page.locator('.se-session[role="button"]', has_text=str(big)).first.locator(".se-session-row code").click()
        assert _wait(lambda: page.evaluate(f"{ed}.state.src") == str(big) and page.locator(".se-session-current .se-session-row code").inner_text() == str(big), timeout=60)
        page.locator(".se-drawer-close").click()
        assert page.locator(".se-drawer").is_hidden()
        assert errors == []
    finally:
        httpd.shutdown()
        httpd.server_close()

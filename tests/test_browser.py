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
    fractions; the editor resolves the hit with ``elementsFromPoint``.
    ``position`` pins the click to the centre of the whole box: without it
    Playwright aims at the first client rect, which for a node broken into
    fragments ("-sin(x)") is its first glyph - the "-", an operator."""
    el = page.locator(f'[data-path="{path}"]')
    box = el.bounding_box()
    el.click(force=True, position={"x": box["width"] / 2, "y": box["height"] / 2})
    if page.locator(".se-caret").count() and not page.locator(".se-selected").count():
        page.keyboard.press("ArrowUp")   # the centre fell between the node's own glyphs: select beside the caret


def _select(page, path):
    """Select exactly ``path``: click it, then walk up from whatever leaf,
    caret or junction the click landed on."""
    _click(page, path)
    for _ in range(20):
        sel = page.locator(".se-selected[data-path]")
        if sel.count() and sel.first.get_attribute("data-path") == path:
            return
        page.keyboard.press("ArrowUp")
    raise AssertionError(f"could not select {path}")


def _settled(read, tries=40):
    """The value of `read()` once it stops changing (a smooth scroll)."""
    last = read()
    for _ in range(tries):
        time.sleep(0.05)
        now = read()
        if now == last:
            return now
        last = now
    return last


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
    _click(page, "/1/d")                              # y, the denominator (the tree's Pow(y, -1))
    assert page.locator(".se-status").inner_text() == "Symbol: y"
    assert page.locator(".se-selected").get_attribute("data-path") == "/1/d"
    _click(page, "/1/d")                              # same spot again -> parent
    assert page.locator(".se-status").inner_text().startswith("Mul:")
    page.keyboard.press("ArrowUp")
    assert page.locator(".se-status").inner_text().startswith("Add:")
    page.keyboard.press("Escape")
    assert page.locator(".se-selected").count() == 0
    assert page.errors == []


def test_in_place_edit_commits_to_python(browser, served):
    srv, doc = served
    page = _open(browser, srv.url)
    _click(page, '/1/d')
    page.keyboard.type("z")                           # typing starts an in-place edit
    field = page.locator(".se-inline")
    assert field.count() == 1
    assert field.evaluate("e => e.parentNode.getAttribute('data-path')") == "/1/d"
    assert field.input_value() == "z"
    page.keyboard.type("**2")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent.includes('z**2')")
    assert doc.expr == x**2 / z**2 - sin(x)
    assert page.locator(".se-inline").count() == 0
    assert page.locator(".se-selected").get_attribute("data-path") == "/1/d"    # the denominator, now z**2
    # Escape restores the rendering without changes
    page.keyboard.press("Enter")
    assert page.locator(".se-inline").input_value() == "z**2"
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
    """Viewport point in the gap between two rendered nodes (on the same line),
    beside the operator glyph if one is there: clicking the glyph itself
    selects the operator, the space next to it gives a caret."""
    return page.evaluate(
        """([l, r]) => {
            const a = document.querySelector(`[data-path="${l}"]`).getBoundingClientRect();
            const b = document.querySelector(`[data-path="${r}"]`).getBoundingClientRect();
            // Either can be displayed first: the printer reorders terms.
            const [lo, hi] = a.right <= b.left ? [a.right, b.left] : [b.right, a.left];
            // Whole pixels: the browser rounds click coordinates, so a
            // fraction of a pixel beside the glyph would land back on it.
            let x = Math.round((lo + hi) / 2);
            const y = (a.top + a.bottom) / 2;
            const onOp = (x) => (document.elementsFromPoint(x, y) || []).some(el => {
                const t = (el.textContent || '').trim();
                return t.length <= 1 && '+-\u2212\u22c5\u00b7\u00d7=<>\u2264\u2265\u2227\u2228'.includes(t) && t && !el.querySelector('[data-path]');
            });
            if (onOp(x)) {
                let lx = x, rx = x;
                while (onOp(lx) && lx - 1 > lo) lx -= 1;
                while (onOp(rx) && rx + 1 < hi) rx += 1;
                x = !onOp(rx) ? rx : lx;      // the glyph usually sits flush against the left node
            }
            return [x, y];
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
        """Select a node by path, walking up from a glyph (or from the caret
        that a click landing between two glyphs leaves) if needed."""
        _click(self.page, path)
        for _ in range(20):
            sel = self.page.locator(".se-selected[data-path]")
            if sel.count() and sel.first.get_attribute("data-path") == path:
                return self
            self.page.keyboard.press("ArrowUp")
        raise AssertionError(f"could not select {path}")

    def caret_after(self, path):
        """A caret right after the rendering of `path` (a mouse click there,
        on the node's right edge so it cannot land on an operator glyph)."""
        r = self.page.locator(f'[data-path="{path}"]').bounding_box()
        self.page.mouse.click(r["x"] + r["width"] - 1, r["y"] + r["height"] / 2)
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
    _select(page, next(k for k, v in doc2.snapshot()["nodes"].items() if v["type"] == "Integral"))
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
    # sqrt(y) is Pow(y, 1/2): two arguments, so Unwrap asks which one to leave
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "sqrt(y)"))
    page.locator('.se-toolbar [data-cmd="unwrap"]').click()
    keep = page.locator(".se-keep")
    keep.wait_for(state="visible")
    assert [b.strip() for b in keep.locator("button").all_inner_texts()][:2] == ["y", "1/2"]
    _next_state(page, lambda: keep.locator("button", has_text="y").first.click())
    assert doc.expr == x * t + y
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x"))
    page.keyboard.press("ArrowUp")                        # x*theta
    page.keyboard.press("Backspace")                      # two factors: which one to keep?
    keep.wait_for(state="visible")
    _next_state(page, lambda: page.keyboard.press("Enter"))       # x, the factor ↑ came from, is focused
    assert doc.expr == x + y
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "y"))
    _next_state(page, lambda: page.keyboard.press("Delete"))     # Delete removes entirely
    assert doc.expr == x
    assert page.errors == []


def test_array_tools_ask_for_their_axes(browser, serve_expr):
    """The array type menu offers the tools; the ones that take axes ask."""
    from sympy import Array
    srv, doc = serve_expr(Array([[1, 2], [3, 4]]))
    page = _open(browser, srv.url)
    _click(page, "/")
    menu = page.locator(".se-typemenu")
    menu.wait_for(state="visible")
    labels = menu.locator("option").all_inner_texts()
    assert any("Permute axes" in t for t in labels) and any("Contract axes" in t for t in labels)
    assert any("As matrix" in t for t in labels)

    menu.select_option("permutedims")               # asks before doing anything
    form = page.locator(".se-fn-form")
    form.wait_for(state="visible")
    assert doc.expr == Array([[1, 2], [3, 4]])
    form.locator("input").first.fill("(1, 0)")
    _next_state(page, lambda: form.locator(".se-fn-apply").click())
    assert doc.expr == Array([[1, 3], [2, 4]])

    _click(page, "/")
    _next_state(page, lambda: page.locator(".se-typemenu").select_option("tomatrix"))
    assert doc.expr == Matrix([[1, 3], [2, 4]])
    _click(page, "/")
    _next_state(page, lambda: page.locator(".se-typemenu").select_option("to_array"))
    assert doc.expr == Array([[1, 3], [2, 4]])
    assert page.errors == []


def test_unwrap_asks_which_argument_to_keep(browser, serve_expr):
    """x**2 has no natural argument to leave: the editor asks for the base or
    the exponent instead of silently keeping the base."""
    srv, doc = serve_expr(x ** 2 + y)
    page = _open(browser, srv.url)
    _click(page, next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "x"))
    page.keyboard.press("ArrowUp")                     # the power itself
    assert page.locator(".se-status").inner_text() == "Pow: x**2"
    keep = page.locator(".se-keep")
    assert not keep.is_visible()
    page.keyboard.press("Backspace")
    keep.wait_for(state="visible")
    assert [b.strip() for b in keep.locator("button").all_inner_texts()][:2] == ["x", "2"]
    assert doc.expr == x ** 2 + y                      # nothing has happened yet
    page.keyboard.press("Escape")                      # Escape leaves the expression alone
    keep.wait_for(state="hidden")
    assert doc.expr == x ** 2 + y
    page.keyboard.press("Backspace")
    keep.wait_for(state="visible")
    _next_state(page, lambda: keep.locator("button", has_text="2").first.click())
    assert doc.expr == 2 + y                           # the exponent, not the base
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
    tp.locator(".se-actions [data-cmd=\"unwrap\"]").tap()   # two factors: the chooser asks
    tp.locator(".se-keep").wait_for(state="visible")
    tp.locator(".se-keep button", has_text="theta").first.tap()   # keep theta
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
    rows = page.evaluate("(() => { const tops = new Set([...document.querySelectorAll('.se-tools [data-cmd]')].map(b => Math.round(b.getBoundingClientRect().top))); return tops.size; })()")
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
    # the focus is in a field where the formula was; the source line is empty; the formula is hidden
    assert page.evaluate("document.activeElement.className") == "se-inline se-inline-empty"
    assert page.locator(".se-view .se-inline-empty").count() == 1
    assert page.locator(".se-source").inner_text() == ""
    assert "se-empty" in page.locator(".se-view").get_attribute("class")
    assert page.locator(".se-view .katex").is_hidden()
    page.keyboard.type("z")
    _wait(lambda: "se-empty" not in page.locator(".se-view").get_attribute("class"))   # previewed as it is typed
    assert page.locator(".se-source").inner_text() == "z"                              # the line follows
    assert page.evaluate("document.activeElement.className") == "se-inline se-inline-empty"   # still typing there
    page.keyboard.type("**2")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == z**2 and page.locator(".se-inline-empty").count() == 0
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


def test_shown_parts_are_clickable_and_editable(browser, serve_expr):
    """What is shown is what is selected, where SymPy's tree differs from the
    rendering: the 1 of 1/n (the tree's Pow(n, -1)); the 2, the e and the
    whole 2e under the bar of 1/(2e) (the tree's exp(-1)/2); the 2 of x - 2y."""
    from sympy import E
    n = symbols("n")
    srv, doc = serve_expr(1 / n)
    page = _open(browser, srv.url)
    _click(page, "/n")
    assert page.locator(".se-status").inner_text() == "One: 1"
    page.keyboard.type("x")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == x / n
    page.keyboard.press("Escape")
    srv, doc = serve_expr(1 / (2 * E))
    page = _open(browser, srv.url)
    _click(page, "/d/0")
    assert page.locator(".se-status").inner_text() == "Integer: 2"
    page.keyboard.press("ArrowUp")                                    # the denominator as a whole
    assert page.locator(".se-selected").get_attribute("data-path") == "/d" and page.locator(".se-status").inner_text() == "Mul: 2*E"
    page.keyboard.press("ArrowUp")                                    # the fraction
    assert page.locator(".se-selected").get_attribute("data-path") == "/"
    _click(page, "/d/0")
    page.keyboard.type("3")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == 1 / (3 * E)
    _select(page, "/d")                                               # replacing the whole denominator
    page.keyboard.type("y")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == 1 / y
    page.keyboard.press("Escape")
    srv, doc = serve_expr(x - 2 * y)
    page = _open(browser, srv.url)
    _click(page, "/1/neg/0")
    assert page.locator(".se-status").inner_text() == "Integer: 2"
    page.keyboard.press("ArrowUp")
    assert page.locator(".se-status").inner_text() == "Mul: 2*y"     # the product after the sign
    page.keyboard.press("ArrowUp")
    assert page.locator(".se-status").inner_text() == "Mul: -2*y"    # the signed term
    page.keyboard.press("Escape")                                     # (a click inside the selection walks up)
    _click(page, "/1/neg/0")
    page.keyboard.type("3")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == x - 3 * y
    _select(page, "/1/neg")
    _next_state(page, lambda: page.keyboard.press("Delete"))          # the signed term goes with it
    assert doc.expr == x
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
    # delete everything, then click the (empty) formula area: a field to type in, nothing comes back
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")
    page.locator('.se-toolbar [data-cmd="delete"]').click()
    assert "se-empty" in page.locator(".se-view").get_attribute("class")
    page.locator(".se-view").focus()                                                   # away from the field
    r = page.locator(".se-view").bounding_box()
    page.mouse.click(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
    assert page.evaluate("document.activeElement.className") == "se-inline se-inline-empty"
    assert page.locator(".se-source").inner_text() == "" and "se-empty" in page.locator(".se-view").get_attribute("class")
    page.keyboard.type("q + 1")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == symbols("q") + 1
    # Esc from the empty state still brings the previous expression back; so does typing straight into the empty view
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Delete")
    page.keyboard.press("Escape")
    assert page.locator(".se-source").inner_text() == "q + 1" and "se-empty" not in page.locator(".se-view").get_attribute("class")
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Delete")
    page.keyboard.press("Escape")                                                      # the field goes, the view keeps the focus
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Delete")
    page.evaluate("document.querySelector('.se-view').focus()")
    page.keyboard.type("w")                                                            # typing into the empty view opens the field with it
    assert page.locator(".se-inline-empty").input_value() == "w"
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert doc.expr == symbols("w")
    assert errors == []


def test_change_animation_red_to_green(browser, serve_expr):
    from sympy import cos
    srv, doc = serve_expr(x**2 + sin(y))
    page = _open(browser, srv.url)
    nodes = doc.snapshot()["nodes"]
    ps = next(k for k, v in nodes.items() if v["src"] == "sin(y)")
    _click(page, ps)
    for _ in range(3):                                            # up from the glyph the click landed on
        if page.locator(".se-selected[data-path]").first.get_attribute("data-path") == ps:
            break
        page.keyboard.press("ArrowUp")
    assert page.locator(".se-selected[data-path]").first.get_attribute("data-path") == ps
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


def test_diff_colours_only_what_changed(browser, serve_expr):
    """Unwrapping cos(x)**2 in sin(x)**2 + cos(x)**2 = 1: cos(x)**2 goes red and cos(x)
    comes green; sin(x)**2 and the sum around it are not coloured (animation, view, report)."""
    from sympy import Eq, cos
    srv, doc = serve_expr(Eq(sin(x)**2 + cos(x)**2, 1))
    page = _open(browser, srv.url)
    nodes = doc.snapshot()["nodes"]
    pcos = next(k for k, v in nodes.items() if v["src"] == "cos(x)**2")
    _next_state(page, lambda: page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.send({action: 'unwrap', path: '%s', keep: 0})" % pcos))
    assert doc.expr == Eq(sin(x)**2 + cos(x), 1)
    assert _wait(lambda: page.locator(".se-ghost").count() == 0, timeout=5)
    green = [t.strip() for t in page.locator(".se-view .se-added").all_inner_texts()]
    assert green and all("sin" not in t for t in green) and any("cos" in t for t in green), green
    page.locator('.se-toolbar [data-cmd="history"]').click()
    frame = page.frame_locator(".se-history-frame")
    frame.locator(".step").nth(1).wait_for()
    red = [t.strip() for t in frame.locator(".transition .before .rep-removed").all_inner_texts()]
    assert red and all("sin" not in t for t in red) and any("cos" in t for t in red), red
    assert "2" in red                                                     # the exponent went too
    assert any("sin" in t for t in frame.locator(".transition .before .rep-kept").all_inner_texts())
    green = [t.strip() for t in frame.locator('.step[data-index="1"] .rep-added').all_inner_texts()]
    assert green and all("sin" not in t for t in green) and any("cos" in t for t in green), green
    assert page.errors == []


def test_unevaluated_toggle(browser, serve_expr):
    """With "unevaluated" on, the Determinant of a matrix is built, not computed."""
    from sympy import Determinant, Matrix
    srv, doc = serve_expr(Matrix([[1, 2], [3, 4]]))
    page = _open(browser, srv.url)
    page.locator(".se-lazy-box").check()
    page.locator(".se-view").focus()
    page.keyboard.press("ArrowDown")                                          # the whole matrix
    menu = page.locator(".se-typemenu")                                       # the matrix tools
    _next_state(page, lambda: menu.select_option("determinant"))
    assert isinstance(doc.expr, Determinant)
    assert doc.snapshot()["latex_plain"].startswith("\\left|")                    # |M|, not -2
    _next_state(page, lambda: page.select_option(".se-ops", "doit"))
    assert doc.expr == -2
    page.locator(".se-lazy-box").uncheck()
    page.locator(".se-view").focus()
    _next_state(page, lambda: page.keyboard.press("Control+z"))
    _next_state(page, lambda: page.keyboard.press("Control+z"))
    page.keyboard.press("Escape")                                             # (the selection followed the undo)
    page.keyboard.press("ArrowDown")
    _next_state(page, lambda: menu.select_option("determinant"))
    assert doc.expr == -2
    assert page.errors == []


def test_selection_follows_the_change(browser, serve_expr):
    """After an operation the selection is what replaced the selected node,
    not whatever now sits at its old path (SymPy reorders arguments)."""
    from sympy import Eq, cos, expand
    srv, doc = serve_expr(Eq(sin(x)**2 + cos(x)**2, 1))
    page = _open(browser, srv.url)
    nodes = doc.snapshot()["nodes"]
    pcos = next(k for k, v in nodes.items() if v["src"] == "cos(x)**2")
    _click(page, pcos + "/0/0")                                                       # the x of cos(x)
    page.keyboard.press("ArrowUp")
    page.keyboard.press("ArrowUp")
    assert page.locator(".se-status").inner_text() == "Pow: cos(x)**2"
    _next_state(page, lambda: page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.send({action: 'unwrap', path: '%s', keep: 0})" % pcos))
    assert doc.expr == Eq(sin(x)**2 + cos(x), 1)
    sel = page.locator(".se-selected").get_attribute("data-path")
    assert doc.snapshot()["nodes"][sel]["src"] == "cos(x)"                           # not sin(x)**2, which took its path
    _next_state(page, lambda: page.select_option(".se-ops", "expand_trig"))          # no change of cos(x): still selected
    assert doc.snapshot()["nodes"][page.locator(".se-selected").get_attribute("data-path")]["src"] == "cos(x)"
    # a replacement that SymPy moves elsewhere in the sum is followed
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    nodes = doc.snapshot()["nodes"]
    py = next(k for k, v in nodes.items() if v["src"] == "y")
    _click(page, py)
    page.keyboard.type("a")
    _next_state(page, lambda: page.keyboard.press("Enter"))                          # y -> a: now the first term
    sel = page.locator(".se-selected").get_attribute("data-path")
    assert doc.snapshot()["nodes"][sel]["src"] == "a" and sel != py
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
    # only what changed is coloured: the 2 of 2x goes red, the 3 of 3x comes green; x and 3y are untouched
    assert page.locator(".se-ghost").count() == 2
    red = [t.strip() for t in page.locator(".se-ghost-old .se-removed").all_inner_texts()]
    green = [t.strip() for t in page.locator(".se-ghost-new .se-added").all_inner_texts()]
    assert red == ["2"], red
    assert green == ["3"], green
    assert _wait(lambda: page.locator(".se-ghost").count() == 0, timeout=5)
    p3x = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "3*x")
    assert "se-added" in page.locator(f'.se-view [data-path="{p3x}/0"]').first.get_attribute("class")
    assert "se-added" not in page.locator(f'.se-view [data-path="{p3x}"]').first.get_attribute("class")
    assert page.errors == []


def test_history_report_is_self_contained_and_works_offline(browser, serve_expr, tmp_path):
    from sympy import cos
    srv, doc = serve_expr(x**2 + sin(y))
    page = _open(browser, srv.url)
    nodes = doc.snapshot()["nodes"]
    _click(page, next(k for k, v in nodes.items() if v["src"] == "y"))
    page.keyboard.press("ArrowUp")
    page.keyboard.type("cos(y)")
    _next_state(page, lambda: page.keyboard.press("Enter"))
    _next_state(page, lambda: page.select_option(".se-ops", "factor"))
    assert doc.expr == x**2 + cos(y)
    html = page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.buildReport()")
    assert html.startswith("<!DOCTYPE html>") and "data:font/woff2;base64," in html
    assert "<script src" not in html and "<link" not in html      # nothing is fetched: only the inline player
    assert html.count("<script>") == 1
    # Every KaTeX @font-face must survive the inlining: with only the first
    # one left, \left[ fell back to a normal-height bracket (document.fonts
    # .check() is true for an undeclared family, so count the rules).
    assert html.count("@font-face") >= 15
    assert "Edit: sin(y) → cos(y)" in html and "Transform: Factor" in html
    assert html.count('<section class="step"') == 3 and 'rep-added' in html and 'rep-removed' in html
    assert "cdn.jsdelivr.net" not in html.split("</style>")[1]      # no external resource in the body either
    # the file works offline, fonts included
    path = tmp_path / "report.html"
    path.write_text(html, encoding="utf-8")
    ctx = browser.new_context()
    ctx.route("**/*", lambda route: route.abort() if not route.request.url.startswith("file:") else route.continue_())
    rp = ctx.new_page()
    errors = []
    rp.on("pageerror", lambda e: errors.append(str(e)))
    rp.goto(path.as_uri())
    assert rp.locator(".step .katex").count() == 3 and rp.locator(".transition").count() == 2
    # ...and it plays there too, offline
    where = """() => {
        const r = document.querySelector('.player .play').getBoundingClientRect();
        const m = document.querySelector('main').getBoundingClientRect();
        return [Math.round(r.x), Math.round(m.x), Math.round(m.width)];
    }"""
    at_rest = rp.evaluate(where)
    rp.locator(".player .play").click()
    assert rp.evaluate("document.body.classList.contains('slides')")
    assert rp.locator(".slide-on").count() == 1
    # the bar holds its place from slide to slide: the slides are of very
    # different widths, and "main" used to shrink to whichever was on show
    seen = {tuple(rp.evaluate(where))}
    for _ in range(4):
        rp.locator(".player .next").click()
        seen.add(tuple(rp.evaluate(where)))
    assert seen == {tuple(at_rest)}, seen
    assert rp.evaluate("document.fonts.ready.then(() => document.fonts.check('12px KaTeX_Main'))")
    assert rp.locator(".transition .what").first.inner_text() == "Edit: sin(y) → cos(y)"
    assert rp.locator('.step[data-current="1"] h2').inner_text().startswith("STEP 3") or "current" in rp.locator('.step[data-current="1"] h2').inner_text().lower()
    green = rp.evaluate("getComputedStyle(document.querySelector('.rep-added')).color")
    red = rp.evaluate("getComputedStyle(document.querySelector('.rep-removed')).color")
    assert green != red and errors == []
    ctx.close()
    # the toolbar button shows it in the page: the steps, a click on one opens it
    page.locator('.se-toolbar [data-cmd="history"]').click()
    frame = page.frame_locator(".se-history-frame")
    frame.locator(".step").nth(2).wait_for()
    assert frame.locator(".step").count() == 3 and frame.locator(".transition .what").first.inner_text() == "Edit: sin(y) → cos(y)"
    assert frame.locator('.step[data-current="1"] h2').inner_text().startswith("STEP 3")
    # saved from there as the web page or as a Python script that rebuilds every step
    save = page.locator(".se-history-head .se-head-save")      # one control, both ways out
    assert save.locator("option").all_inner_texts() == ["Save \u25be", "as a web page", "as a Python script"]
    with page.expect_download() as dl:
        save.select_option("html")
    assert dl.value.suggested_filename.startswith("sympy-editor-history-") and dl.value.suggested_filename.endswith(".html")
    assert save.input_value() == ""                            # and it goes back to "Save"
    with page.expect_download() as dl:
        save.select_option("py")
    assert dl.value.suggested_filename.endswith(".py")
    script = tmp_path / "history.py"
    dl.value.save_as(script)
    ns = {}
    exec(script.read_text(encoding="utf-8"), ns)
    assert ns["steps"] == [x**2 + sin(y), x**2 + cos(y), x**2 + cos(y)] and "Transform: Factor" in script.read_text(encoding="utf-8")
    _next_state(page, lambda: frame.locator(".step").nth(0).click())
    assert page.locator(".se-history-view").count() == 0 and doc.expr == x**2 + sin(y)
    page.locator('.se-toolbar [data-cmd="history"]').click()
    page.wait_for_selector(".se-history-view")
    page.keyboard.press("Escape")
    assert page.locator(".se-history-view").count() == 0
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
        assert page.evaluate("document.activeElement.className") == "se-inline se-inline-empty" and page.locator(".se-source").inner_text() == ""
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
        # switching back to the first session: tapping its row (not only its Open button) opens it, and the drawer closes
        page.locator('.se-session[role="button"]', has_text=str(big)).first.locator(".se-session-row code").click()
        assert _wait(lambda: page.evaluate(f"{ed}.state.src") == str(big) and page.locator(".se-drawer").is_hidden(), timeout=60)
        page.locator('.se-toolbar [data-cmd="drawer"]').click()
        assert _wait(lambda: page.locator(".se-drawer").is_visible())
        assert page.locator(".se-session-current .se-session-row code").inner_text() == str(big)
        page.locator(".se-drawer-close").click()
        assert page.locator(".se-drawer").is_hidden()
        assert errors == []
    finally:
        httpd.shutdown()
        httpd.server_close()


ED = "document.querySelector('.sympy-editor').__sympyEditor"


def test_clicking_an_operator_selects_it_and_a_key_changes_it(browser, serve_expr):
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    view = page.locator(".se-view")
    # Clicking the "+" glyph selects the operator (not a caret), with a palette.
    view.locator(".katex :text-is('+')").last.click(force=True)
    assert _wait(lambda: page.locator(".se-status").inner_text().startswith("Operator +"))
    assert page.locator(".se-opbar").is_visible()
    _next_state(page, lambda: page.keyboard.press("*"))
    assert str(doc.expr) == "x*y"
    # What the change left behind is selected, not an unrelated node.
    assert page.evaluate(ED + ".selected") == "/"
    # The "-" shown before a negative term is the sum's operator there.
    _next_state(page, lambda: page.evaluate(ED + ".send({action: 'set', src: 'x - y'})"))
    view.locator(".katex :text-is('\u2212')").last.click(force=True)
    assert _wait(lambda: page.locator(".se-status").inner_text().startswith("Operator \u2212"))
    # The palette's Delete removes the operator: side by side, the two multiply.
    _next_state(page, lambda: page.locator(".se-opbar button[data-op='']").click())
    assert str(doc.expr) == "x*y"
    assert page.errors == []


def test_caret_enters_and_leaves_a_matrix(browser, serve_expr):
    from sympy import Matrix, cos, sin
    t = Symbol("theta")
    srv, doc = serve_expr(Matrix([[cos(t), -sin(t)], [sin(t), cos(t)]]) * Matrix([x, y]))
    page = _open(browser, srv.url)
    # A caret at the very start: before the whole matrix, outside of it.
    page.locator(".se-view").click(position={"x": 4, "y": 4})
    page.keyboard.press("Escape")
    page.keyboard.press("ArrowLeft")
    assert _wait(lambda: page.evaluate("!!" + ED + ".caret"))
    assert page.evaluate(ED + ".caret.path") == "/"
    assert page.evaluate(ED + ".caret.extend") == "before"
    # -> goes to the left of x in "x cos(theta)", not between x and cos(theta).
    page.keyboard.press("ArrowRight")
    caret = page.evaluate(ED + ".caret")
    node = page.evaluate(ED + ".state.nodes[" + ED + ".caret.path]")
    assert not caret.get("extend")
    assert node["type"] == "Mul" and caret["index"] == 0
    # <- leaves the matrix again.
    page.keyboard.press("ArrowLeft")
    assert page.evaluate(ED + ".caret.path") == "/"
    assert page.evaluate(ED + ".caret.extend") == "before"
    # Walking right passes through both rows and comes out after the matrix.
    for _ in range(60):
        page.keyboard.press("ArrowRight")
    assert page.evaluate(ED + ".caret.path") == "/"
    assert page.evaluate(ED + ".caret.extend") == "after"
    assert page.errors == []


def test_methods_menu_lists_and_calls_class_methods(browser, serve_expr):
    from sympy import Matrix
    srv, doc = serve_expr(Matrix([[1, 2], [3, 4]]))
    page = _open(browser, srv.url)
    menu = page.locator(".se-methods")
    # Nothing selected: the root expression's class, fetched once, then shown.
    assert _wait(lambda: menu.is_visible())
    assert menu.locator("option").first.inner_text().startswith("Methods")
    opts = menu.locator("option").all_inner_texts()
    assert ".det()" in opts and ".T" in opts and ".rank()" in opts
    assert not any(o.startswith(".is_") for o in opts) and ".args" not in opts
    # A method without required parameters is applied at once.
    menu.select_option("det")
    page.wait_for_function("document.querySelector('.se-source').textContent.trim() === '-2'")
    assert str(doc.expr) == "-2"
    # The result is another type: its list is fetched and shown in turn.
    assert _wait(lambda: menu.is_visible())
    opts = menu.locator("option").all_inner_texts()
    assert ".det()" not in opts and ".round()" in opts       # the Integer's list, not the matrix's
    assert page.errors == []


def test_help_button_shows_the_guide(browser, serve_expr):
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    page.locator('.se-toolbar [data-cmd="help"]').click()
    view = page.locator(".se-help-view")
    assert view.is_visible()
    text = view.inner_text().lower()
    for expected in ("selecting", "unwrap", "operators", "unevaluated", "methods", "history", "phone"):
        assert expected in text, expected
    # the guide is the whole tool: everything the editor grew is in it
    for expected in ("full screen", "slideshow", "save", "sessions", "( ) apply",
                     "the same thing seen twice", "container"):
        assert expected in text, expected
    page.keyboard.press("Escape")                     # Esc closes it
    assert page.locator(".se-help-view").count() == 0
    page.locator('.se-toolbar [data-cmd="help"]').click()
    page.locator(".se-help-view .se-history-close").click()   # so does the X
    assert page.locator(".se-help-view").count() == 0
    assert page.errors == []


# --- graphical regressions ----------------------------------------------
# One test per visual defect reported against the editor, each measuring the
# rendering itself (geometry or pixels), since none of them broke a feature.

INK_BOX = """(sel) => {
    // The visual extent of a KaTeX sub-tree: the union of its glyph spans.
    // (An inline element's own rect is its line box, which says nothing
    // about how tall the fraction or matrix inside it is.)
    const node = document.querySelector(sel);
    let top = Infinity, bot = -Infinity, left = Infinity, right = -Infinity;
    (function walk(el) {
        if (!el.children.length && (el.textContent || '').trim()) {
            const r = el.getBoundingClientRect();
            if (r.width && r.height) {
                top = Math.min(top, r.top); bot = Math.max(bot, r.bottom);
                left = Math.min(left, r.left); right = Math.max(right, r.right);
            }
        }
        for (const c of el.children) walk(c);
    })(node);
    const own = node.getBoundingClientRect();
    return {ink: {x: left, y: top, width: right - left, height: bot - top},
            box: {x: own.x, y: own.y, width: own.width, height: own.height},
            bg: getComputedStyle(node).backgroundColor};
}"""


def _white_share(page, rect, pad=1):
    """The share of page-background (white) pixels inside `rect` of the page."""
    from io import BytesIO
    from PIL import Image
    clip = {"x": rect["x"] + pad, "y": rect["y"] + pad,
            "width": max(1, rect["width"] - 2 * pad), "height": max(1, rect["height"] - 2 * pad)}
    img = Image.open(BytesIO(page.screenshot(clip=clip))).convert("RGB")
    px = list(img.getdata())
    white = sum(1 for r, g, b in px if r > 250 and g > 250 and b > 250)
    return white / len(px)


def test_change_tint_covers_the_whole_changed_area(browser, serve_expr):
    """The red/green background must cover the changed sub-expression whole.

    A background on an inline element paints its *line box*: a tall fraction
    or matrix kept a band across the middle and poked out above and below
    (and the per-level tints made a patchwork of overlapping rectangles).
    The outermost mark now carries one inline-block box - the node's whole
    visual extent - so the tint is a single rectangle, and no other part of
    the formula moves because of it.
    """
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    ed = "document.querySelector('.sympy-editor').__sympyEditor"
    before = page.evaluate(INK_BOX, ".se-view .katex")["ink"]
    _next_state(page, lambda: page.evaluate(ed + ".send({action: 'set', src: 'Matrix([[1/(x+1), y], [y**2, x*y]])'})"))
    assert _wait(lambda: page.locator(".se-ghost").count() == 0, timeout=8)   # the animation is over
    # In the editor: one box, as tall as the matrix, and an opaque tint (a
    # translucent one would stack darker where two marks overlap).
    m = page.evaluate(INK_BOX, ".se-view .se-added-box")
    assert m["box"]["height"] >= 0.9 * m["ink"]["height"], m
    assert "rgba(" not in m["bg"] and "/" not in m["bg"], m["bg"]    # opaque: "rgb(...)" / "color(srgb ...)"
    assert _white_share(page, m["box"]) < 0.08
    # The formula around a mark does not move: the same matrix, unmarked,
    # has the same ink box (the marks are cleared by touching the formula).
    page.keyboard.press("Escape")
    page.evaluate(ed + "._clearChangeMarks()")
    page.wait_for_timeout(100)
    plain = page.evaluate(INK_BOX, ".se-view .katex")["ink"]
    marked_ink = m["ink"]
    assert abs(plain["height"] - marked_ink["height"]) < 1.5 and abs(plain["y"] - marked_ink["y"]) < 1.5
    assert plain["width"] > before["width"]      # (the expression really did change)
    # The same in the saved history report.
    html = page.evaluate(ed + ".buildReport()")
    import tempfile, pathlib
    path = pathlib.Path(tempfile.mkdtemp()) / "report.html"
    path.write_text(html, encoding="utf-8")
    rp = browser.new_page()
    rp.goto(path.as_uri())
    rp.wait_for_timeout(500)
    r = rp.evaluate(INK_BOX, ".step[data-current] .rep-box")
    assert r["box"]["height"] >= 0.9 * r["ink"]["height"], r
    assert _white_share(rp, r["box"]) < 0.08
    rp.close()
    assert page.errors == []


def test_history_report_brackets_keep_their_full_height(browser, serve_expr):
    """A matrix's [ ] in the report must be as tall as the matrix.

    Inlining the KaTeX stylesheet once dropped every @font-face rule but the
    first, and without KaTeX_Size* the sized delimiters fell back to a
    normal-height bracket beside a two-row matrix.
    """
    from sympy import Matrix
    import tempfile, pathlib
    srv, doc = serve_expr(Matrix([[x, y], [1, x * y]]))
    page = _open(browser, srv.url)
    ed = "document.querySelector('.sympy-editor').__sympyEditor"
    _next_state(page, lambda: page.evaluate(ed + ".send({action: 'replace', path: '/2/0', src: 'x**2'})"))
    html = page.evaluate(ed + ".buildReport()")
    assert html.count("@font-face") >= 15                      # every face survived the inlining
    path = pathlib.Path(tempfile.mkdtemp()) / "report.html"
    path.write_text(html, encoding="utf-8")
    rp = browser.new_page()
    rp.goto(path.as_uri())
    rp.wait_for_timeout(500)
    ink = rp.evaluate(INK_BOX, ".step[data-current] .formula")["ink"]
    tallest = rp.evaluate("""() => Math.max(...[...document.querySelectorAll('.step[data-current] .delimsizing')]
        .map(e => e.getBoundingClientRect().height), 0)""")
    assert tallest >= 0.75 * ink["height"], (tallest, ink)     # a fallback bracket is ~40% of it
    rp.close()
    assert page.errors == []


def test_status_line_names_the_selection_on_its_own_line(browser, serve_expr):
    """The label naming the selection must stay visible at every width.

    It shared the row with the tools; once those were grouped into three
    full-width rows there was no room left and it collapsed to nothing on a
    wide screen (it had its own line only on a phone).
    """
    srv, doc = serve_expr(x**2 / y - sin(x))
    path = next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == "y")
    for width in (1100, 400):
        page = browser.new_page(viewport={"width": width, "height": 800})
        page.goto(srv.url)
        page.wait_for_selector(".se-view .katex [data-path]")
        _select(page, path)
        status = page.locator(".se-status")
        assert status.is_visible()
        assert status.inner_text() == "Symbol: y"
        box = status.bounding_box()
        assert box["width"] > 120 and box["height"] >= 12, (width, box)
        # its own line: under every tool, so their text can never squeeze it
        below = page.evaluate("""() => {
            const s = document.querySelector('.se-status').getBoundingClientRect();
            const tools = [...document.querySelectorAll('.se-tools > *')].map(e => e.getBoundingClientRect());
            return tools.every(t => !t.height || s.top >= t.bottom - 1);
        }""")
        assert below, width
        page.close()


def test_the_tools_are_laid_out_in_columns(browser, serve_expr):
    """The tools sit in blocks, and the blocks in three columns: the left one
    starts at the left edge, the right one ends at the right edge, the middle
    one is centred.  One long strip of buttons, or rows each ending wherever
    their content happens to stop, read as a mess."""
    srv, doc = serve_expr(x + y)
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    blocks = page.evaluate("""() => {
        const strip = document.querySelector('.se-tools').getBoundingClientRect();
        const out = [];
        for (const el of document.querySelectorAll('.se-tools > .se-block')) {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            out.push({name: el.getAttribute('data-block'),
                      left: Math.round(r.left - strip.left), right: Math.round(strip.right - r.right),
                      top: Math.round(r.top), wide: el.classList.contains('se-block-wide')});
        }
        return out;
    }""")
    by = {b["name"]: b for b in blocks}
    assert {"session", "zoom", "nav", "edit", "clip", "apply"} <= set(by), blocks
    rows = sorted({b["top"] for b in blocks})
    assert len(rows) == 3, blocks                                  # two rows of three, then the wide one
    # a block never breaks apart: what belongs together stays on one line
    assert by["session"]["top"] == by["zoom"]["top"] == by["nav"]["top"]
    assert by["edit"]["top"] == by["clip"]["top"]
    assert by["apply"]["top"] == rows[2] and by["apply"]["wide"]
    # left column flush left, right column flush right, middle centred
    assert by["session"]["left"] <= 1 and by["edit"]["left"] <= 1, blocks
    assert by["nav"]["right"] <= 1, blocks
    assert abs(by["zoom"]["left"] - by["zoom"]["right"]) <= 2, blocks
    assert abs(by["clip"]["left"] - by["clip"]["right"]) <= 2, blocks
    assert by["apply"]["left"] <= 1 and by["apply"]["right"] <= 1, blocks
    page.close()


def test_the_tools_stay_in_blocks_on_a_narrow_screen(browser, serve_expr):
    """No room for three columns on a phone: the blocks spread across each
    line instead, one against the left edge and one against the right, so
    the strip still reads as a grid and nothing hangs in the middle."""
    srv, doc = serve_expr(x + y)
    page = browser.new_page(viewport={"width": 384, "height": 780})
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    lines = page.evaluate("""() => {
        const strip = document.querySelector('.se-tools').getBoundingClientRect();
        const by = {};
        for (const el of document.querySelectorAll('.se-tools > .se-block')) {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            const key = Math.round(r.top);
            (by[key] = by[key] || []).push({left: r.left - strip.left, right: strip.right - r.right});
        }
        return Object.keys(by).sort((a, b) => a - b).map(k => by[k]);
    }""")
    assert len(lines) >= 3, lines
    for line in lines:
        assert line[0]["left"] <= 1, lines                          # every line starts at the left edge
        if len(line) > 1:
            assert line[-1]["right"] <= 1, lines                    # and, with something to spread, ends at the right
    assert any(len(line) > 1 for line in lines), lines
    assert page.evaluate("document.documentElement.scrollWidth") <= 384   # nothing overflows
    page.close()


def test_navigation_arrows_are_one_uniform_set(browser, serve_expr):
    """The four arrows are one drawing rotated, so they match everywhere.

    As text glyphs they came from whichever installed font had them: the
    horizontal pair is twice as wide as the vertical one in most UI fonts,
    which made those two buttons wider than the others (and a fallback font
    gives them another weight and baseline as well).
    """
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    MEASURE = """(root) => {
        const out = {};
        for (const cmd of ['parent', 'child', 'left', 'right', 'edit']) {
            const b = document.querySelector(root + ' [data-cmd="' + cmd + '"]');
            if (!b) continue;
            const r = b.getBoundingClientRect(), svg = b.querySelector('svg.se-icon');
            const s = svg && svg.getBoundingClientRect();
            out[cmd] = {w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10,
                        icon: s ? [Math.round(s.width * 10) / 10, Math.round(s.height * 10) / 10] : null,
                        pad: s ? Math.round((s.top - r.top) * 10) / 10 - Math.round((r.bottom - s.bottom) * 10) / 10 : null};
        }
        return out;
    }"""
    for root in (".se-toolbar", ".se-actions"):
        if root == ".se-actions":
            _select(page, "/0")                                   # the floating bar needs a selection
        m = page.evaluate(MEASURE, root)
        arrows = [m[c] for c in ("parent", "child", "left", "right")]
        assert all(a["icon"] for a in arrows), (root, m)          # drawn, not typed
        assert len({a["w"] for a in arrows}) == 1, (root, m)      # one width
        assert len({a["h"] for a in arrows}) == 1, (root, m)
        assert len({tuple(a["icon"]) for a in arrows}) == 1, (root, m)
        assert all(abs(a["pad"]) <= 0.6 for a in arrows), (root, m)   # centred in the button
        assert arrows[0]["h"] == m["edit"]["h"], (root, m)        # as tall as the text buttons beside them
    assert page.errors == []


def test_caret_enters_and_leaves_an_ndim_array(browser, serve_expr):
    """Like a matrix, an N-dim array must not trap the caret."""
    arr = Array([[[x, 1], [y, 2]], [[z, 3], [1, 4]]])
    srv, doc = serve_expr(arr)
    page = _open(browser, srv.url)
    page.locator(".se-view").click(position={"x": 4, "y": 4})
    page.keyboard.press("Escape")
    page.keyboard.press("ArrowLeft")
    assert _wait(lambda: page.evaluate("!!" + ED + ".caret"))
    assert page.evaluate(ED + ".caret.path") == "/" and page.evaluate(ED + ".caret.extend") == "before"
    page.keyboard.press("ArrowRight")                     # into the array
    assert page.evaluate(ED + ".caret.path") != "/" or not page.evaluate(ED + ".caret.extend")
    page.keyboard.press("ArrowLeft")                      # and out again
    assert page.evaluate(ED + ".caret.path") == "/" and page.evaluate(ED + ".caret.extend") == "before"
    for _ in range(120):                                  # right through every element, and out
        page.keyboard.press("ArrowRight")
    assert page.evaluate(ED + ".caret.path") == "/" and page.evaluate(ED + ".caret.extend") == "after"
    assert page.errors == []


def test_native_backend_talks_to_the_host_application(browser, serve_expr):
    """The backend the mobile app uses: Python runs in the host, not the page.

    The app injects `window.SympyEditorPy` (MainActivity.PythonBridge, which
    calls CPython through Chaquopy); here a stub with the same three methods
    answers from a real Document over HTTP, so the page's half of the bridge
    - request ids, `window.__sympyEditorNative`, warmup, and an error coming
    back from the host - is exercised exactly as it is on the device.
    """
    from sympy_editor.html import build_config
    srv, doc = serve_expr(x + y)
    cfg = build_config(doc, backend="native",
                       options={"katexJs": default_urls()["katexJs"], "katexCss": default_urls()["katexCss"]})
    page = _open(browser, srv.url)                 # the same origin, for the stub's fetch()
    page.evaluate("""([api, token]) => {
        const host = document.createElement('div');
        host.id = 'native-host';
        document.body.appendChild(host);
        const post = (body) => fetch(api, {method: 'POST', body: JSON.stringify(body),
                                           headers: {'Content-Type': 'application/json', 'X-SymPy-Editor-Token': token}
                                          }).then(r => r.text());
        window.__nativeCalls = [];
        window.SympyEditorPy = {
            newDoc(req, id, srepr, settings) {
                window.__nativeCalls.push(['newDoc', id]);
                post({action: 'snapshot'}).then(t => window.__sympyEditorNative(req, true, t),
                                                e => window.__sympyEditorNative(req, false, String(e)));
            },
            handle(req, id, message) {
                window.__nativeCalls.push(['handle', JSON.parse(message).action]);
                post(JSON.parse(message)).then(t => window.__sympyEditorNative(req, true, t),
                                               e => window.__sympyEditorNative(req, false, String(e)));
            },
            version(req) { window.__sympyEditorNative(req, true, '{"python": "3.12.7", "sympy": "1.14.0"}'); }
        };
    }""", [srv.url.rstrip("/") + "/api", srv.token])
    page.evaluate("(cfg) => { window.__nativeEditor = SympyEditor.mount(document.getElementById('native-host'), cfg); }", cfg)
    page.wait_for_selector("#native-host .se-view .katex [data-path]", timeout=30000)
    ed = "window.__nativeEditor"
    assert page.evaluate(ed + ".state.src") == "x + y"
    assert page.evaluate("window.__nativeCalls[0][0]") == "newDoc"       # started through the host
    seq = page.evaluate(ed + ".state.seq")
    page.evaluate(ed + ".send({action: 'replace', path: '/0', src: 'z**2'})")
    page.wait_for_function("s => %s.state.seq > s" % ed, arg=seq, timeout=30000)
    assert str(doc.expr) == "y + z**2"                                   # the host's Document did the edit
    assert page.evaluate("window.__nativeCalls.some(c => c[0] === 'handle' && c[1] === 'replace')")
    # An error from the host reaches the page instead of hanging it
    page.evaluate("window.SympyEditorPy.handle = (req) => window.__sympyEditorNative(req, false, 'Python is gone');")
    page.evaluate(ed + ".send({action: 'undo'})")
    assert _wait(lambda: "Python is gone" in page.locator("#native-host .se-error").inner_text())
    assert page.errors == []


def test_full_screen_button_gives_the_formula_the_window(browser, serve_expr):
    """A quasi-transparent button in the corner of the editing area makes the
    formula fill the window; Esc (or the button) comes back."""
    srv, doc = serve_expr(x**2 / y - sin(x))
    page = browser.new_page(viewport={"width": 900, "height": 620})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)

    btn = page.locator(".se-fullbtn")
    assert btn.count() == 1
    # barely there over the formula until the pointer asks for it
    assert float(page.evaluate("getComputedStyle(document.querySelector('.se-fullbtn')).opacity")) <= 0.5
    view = page.locator(".se-view").bounding_box()
    box = btn.bounding_box()
    assert box["y"] >= view["y"] - 1 and box["y"] < view["y"] + view["height"] / 2      # top...
    assert view["x"] + view["width"] - (box["x"] + box["width"]) < 12                   # ...right corner
    size = lambda: float(page.evaluate("parseFloat(getComputedStyle(document.querySelector('.se-view')).fontSize)"))
    small, short = size(), view["height"]
    # the host application (the Android app) is asked for its own full screen
    page.evaluate("window.SympyEditorApp = { calls: [], setFullscreen(on) { this.calls.push(on); } };")

    btn.click()
    assert "se-full" in page.locator(".sympy-editor").get_attribute("class")
    assert page.evaluate("window.SympyEditorApp.calls") == [True]
    page.wait_for_function("!!document.fullscreenElement", timeout=10000)   # and the browser, for real
    # nothing but the formula is left: the tools, the source line and the
    # Symbols panel all step aside
    assert not page.locator(".se-toolbar").is_visible()
    assert not page.locator(".se-source").is_visible()
    assert not page.locator(".se-symbols").is_visible()
    assert page.locator(".se-fullbtn").is_visible()          # the way back stays
    root = page.locator(".sympy-editor").bounding_box()
    assert (round(root["x"]), round(root["y"])) == (0, 0)                # the page behind is covered
    assert (round(root["width"]), round(root["height"])) == (900, 620)
    tall = page.locator(".se-view").bounding_box()["height"]
    assert tall > short * 1.5, (short, tall)                            # the editing area took the room
    assert size() > small                                               # and the formula is drawn larger

    _select(page, "/1/d")                                               # selecting still works, and lands right
    sel = page.locator(".se-view .se-selected[data-path]").bounding_box()
    glyph = page.locator('.se-view [data-path="/1/d"]').bounding_box()
    assert abs(sel["x"] - glyph["x"]) < 6 and abs(sel["y"] - glyph["y"]) < 12, (sel, glyph)

    page.keyboard.press("Escape")                                       # first Esc drops the selection
    assert "se-full" in page.locator(".sympy-editor").get_attribute("class")
    page.keyboard.press("Escape")                                       # then leaves full screen
    assert "se-full" not in page.locator(".sympy-editor").get_attribute("class")
    assert page.evaluate("window.SympyEditorApp.calls") == [True, False]
    page.wait_for_function("!document.fullscreenElement", timeout=10000)
    assert page.locator(".se-toolbar").is_visible()
    assert page.locator(".se-source").is_visible()
    assert round(page.locator(".se-view").bounding_box()["height"]) == round(short)
    assert errors == []
    page.close()


def test_no_box_capitalises_what_is_typed(browser, serve_expr):
    """Formula text is code: on a touch keyboard nothing comes up shifted
    (x, not X), autocorrected or predicted - in every box of the editor."""
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    page.locator(".se-symbols summary").click()          # the name / shape / assumption boxes
    _select(page, "/0")
    page.keyboard.press("Enter")                         # the in-place editor over the formula
    page.wait_for_selector(".se-view input.se-inline")
    page.locator(".se-fn").click()                       # the function box and its parameter form
    boxes = page.evaluate("""() => {
        const out = [];
        for (const el of document.querySelectorAll('.sympy-editor input, .sympy-editor .se-source')) {
            if (el.type && el.type !== 'text') continue;
            out.push([el.className || el.tagName, el.getAttribute('autocapitalize'),
                      el.getAttribute('autocorrect'), el.getAttribute('spellcheck')]);
        }
        return out;
    }""")
    assert len(boxes) >= 4, boxes
    assert all(b[1] == "off" and b[2] == "off" and b[3] == "false" for b in boxes), boxes
    assert page.errors == []


def test_a_lambda_can_be_applied_to_arguments(browser, serve_expr):
    """Lambda is the one SymPy object that is itself a function: the methods
    menu offers to apply it, since `__call__` is a dunder and shows up in no
    listing of its methods."""
    from sympy import Lambda
    srv, doc = serve_expr(Lambda(x, x**2))
    page = _open(browser, srv.url)
    menu = page.locator(".se-methods")
    assert _wait(lambda: menu.is_visible())
    assert "( ) apply" in menu.locator("option").all_inner_texts()
    menu.select_option("__call__")
    form = page.locator(".se-fn-form")
    form.wait_for(state="visible")
    form.locator("input").first.fill("3")
    _next_state(page, lambda: form.locator(".se-fn-apply").click())
    assert doc.expr == 9
    assert page.errors == []


def test_the_history_viewer_runs_without_any_editor(browser, tmp_path):
    """A page built from a list of expressions - nobody edited anything -
    shows every step with its diff, and saves itself as one file."""
    from sympy import Integral, cos
    from sympy_editor import History, to_history_html

    hist = History([
        Integral(x * sin(x), x),
        (-x * cos(x) + Integral(cos(x), x), "by parts"),
        (-x * cos(x) + sin(x), "the last integral"),
    ], title="By parts")
    path = tmp_path / "steps.html"
    path.write_text(to_history_html(hist), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1000, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    page.wait_for_selector(".se-history-page .se-history-frame", timeout=30000)
    assert page.locator(".se-view").count() == 0              # no editor on the page at all
    frame = page.frame_locator(".se-history-frame")
    frame.locator(".step").first.wait_for(timeout=30000)
    assert frame.locator(".step").count() == 3
    assert "By parts" in frame.locator("h1").inner_text()
    # what each step brought is green, what the one before lost is red...
    assert frame.locator(".step .rep-added").count() > 0
    assert frame.locator(".transition .rep-removed").count() > 0
    # ...over its whole extent, one box per changed region (as in the editor)
    assert frame.locator(".step .rep-added.rep-box").count() > 0
    # ...and the action that produced each step is named
    whats = frame.locator(".transition .what").all_inner_texts()
    assert whats == ["by parts", "the last integral"]
    # no step is "current": nothing is being edited here
    assert frame.locator('.step[data-current="1"]').count() == 0
    # the fonts travelled with the page: the tall integral sign is drawn, not
    # a fallback glyph (the same regression the editor's report had)
    assert frame.locator(".step .delimsizing, .step .mop").count() > 0
    assert page.locator(".se-history-head button").last.inner_text() == "Save as web page"
    assert page.locator(".se-history-head .se-play").is_visible()      # and the player, in the fixed strip
    assert errors == []
    page.close()


def test_a_radical_that_disappears_is_marked(browser, tmp_path):
    """sqrt(x) is Pow(x, 1/2) drawn as a radical: the exponent has no place
    of its own in the view, so the node hides one argument.  Turned into
    x**(3/2) it hides none - the radical sign is gone - and that has to show
    in red, even though it is still the same Pow over the same radicand,
    which the diff used to call unchanged."""
    from sympy import Rational, sqrt
    from sympy_editor import History, to_history_html

    hist = History([sqrt(x), (x ** Rational(3, 2), "the exponent becomes 3/2")], title="sqrt")
    path = tmp_path / "sqrt.html"
    path.write_text(to_history_html(hist), encoding="utf-8")
    page = browser.new_page(viewport={"width": 900, "height": 700})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    page.wait_for_selector(".se-history-frame", timeout=30000)
    frame = page.frame_locator(".se-history-frame")
    frame.locator(".step").first.wait_for(timeout=30000)
    removed = frame.locator(".transition .before .rep-removed")
    assert removed.count() == 1                       # the Pow itself: the radical
    colours = page.frames[1].evaluate("""() => {
        const out = {};
        const red = document.querySelector('.transition .before .rep-removed');
        const surd = red.querySelector('svg path');            // the radical is drawn, not typed
        const rad = red.querySelector('.rep-kept');            // the x under it, unchanged
        out.mark = getComputedStyle(red).color;
        out.surd = surd ? getComputedStyle(surd).fill : null;
        out.radicand = rad ? getComputedStyle(rad).color : null;
        out.kept = !!rad;
        return out;
    }""")
    assert colours["mark"] == colours["surd"] != colours["radicand"], colours
    assert colours["kept"] is True                    # the radicand stays as it was, in black
    # and on the new side the exponent it grew is green
    assert frame.locator('.step[data-index="1"] .rep-added').count() > 0
    assert errors == []
    page.close()


def test_the_sessions_button_sits_on_the_side_the_drawer_opens(browser, tmp_path):
    """The drawer slides in from the right, so its ☰ belongs at the right
    end of its row - not at the far left, across the toolbar from it."""
    path = tmp_path / "sessions.html"
    path.write_text(to_html(x + y, options={"sessions": True}), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    page.goto(path.as_uri())
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    row = page.evaluate("""() => {
        const drawer = document.querySelector('.se-tools [data-cmd="drawer"]').getBoundingClientRect();
        const strip = document.querySelector('.se-tools').getBoundingClientRect();
        const mine = [], mid = (drawer.top + drawer.bottom) / 2;
        for (const el of document.querySelectorAll('.se-tools > .se-block')) {
            const r = el.getBoundingClientRect();
            if (r.height && r.width && Math.abs((r.top + r.bottom) / 2 - mid) < 9) mine.push(r.right);
        }
        return {drawer: drawer.right, edge: strip.right, blocks: mine.length,
                rightmost: Math.max(...mine), block: document.querySelector('.se-tools [data-cmd="drawer"]').closest('.se-block').getAttribute('data-block')};
    }""")
    assert row["block"] == "sessions"                           # a block of its own...
    assert row["blocks"] >= 3                                   # ...on the row with the timeline and the zoom
    assert abs(row["drawer"] - row["edge"]) <= 1, row           # and it ends that row, at the strip's right edge
    assert row["drawer"] == row["rightmost"], row
    # and on a phone, where the blocks pack into lines instead of columns,
    # it moves up to end the first line rather than starting the second
    page.set_viewport_size({"width": 384, "height": 780})
    page.wait_for_timeout(100)
    narrow = page.evaluate("""() => {
        const strip = document.querySelector('.se-tools').getBoundingClientRect();
        const blocks = [...document.querySelectorAll('.se-tools > .se-block')].filter(el => el.getBoundingClientRect().height);
        const tops = blocks.map(el => Math.round(el.getBoundingClientRect().top));
        const first = Math.min(...tops);
        const drawer = document.querySelector('.se-tools [data-cmd="drawer"]').closest('.se-block').getBoundingClientRect();
        return {onFirstLine: Math.round(drawer.top) === first, fromRight: Math.round(strip.right - drawer.right)};
    }""")
    assert narrow["onFirstLine"] and narrow["fromRight"] <= 1, narrow
    # the drawer really does come from the right
    assert page.evaluate("getComputedStyle(document.querySelector('.se-drawer')).right") == "0px"
    page.close()


def test_the_full_screen_button_is_a_finger_sized_target(browser, serve_expr):
    """On a touch screen the corner button must be a target, not a glyph:
    a 27px icon in the corner of the formula is easy to miss, and missing it
    selects whatever is under it instead."""
    srv, doc = serve_expr(x + y)
    page = browser.new_page(viewport={"width": 420, "height": 780},
                            has_touch=True, is_mobile=True)
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    box = page.locator(".se-fullbtn").bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44, box
    icon = page.locator(".se-fullbtn svg").bounding_box()
    assert icon["width"] < 25, icon                       # the drawing itself stays small
    page.locator(".se-fullbtn").tap()
    assert "se-full" in page.locator(".sympy-editor").get_attribute("class")
    page.close()


def test_a_history_plays_as_a_slideshow(browser, tmp_path):
    """A history can be watched, not only read.  The engine ships inside the
    report - so a saved file plays on its own - but the controls belong in
    the strip above it, which does not scroll away with the steps."""
    from sympy import Integral, cos
    from sympy_editor import History, to_history_html

    hist = History([
        Integral(x * sin(x), x),
        (-x * cos(x) + Integral(cos(x), x), "by parts"),
        (-x * cos(x) + sin(x), "the last integral"),
    ], title="By parts")
    path = tmp_path / "play.html"
    path.write_text(to_history_html(hist), encoding="utf-8")
    page = browser.new_page(viewport={"width": 900, "height": 700})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    page.wait_for_selector(".se-history-frame", timeout=30000)
    frame = page.frame_locator(".se-history-frame")
    frame.locator(".step").first.wait_for(timeout=30000)
    doc = page.frames[1]

    # the controls are in the fixed strip, not inside the scrolling report
    head = page.locator(".se-history-head")
    assert _wait(lambda: head.locator(".se-play").is_visible())
    assert not frame.locator(".player").is_visible()          # the report's own bar steps aside
    strip = head.bounding_box()
    for sel in (".se-play", ".se-play-count"):
        box = page.locator(sel).bounding_box()
        assert strip["y"] <= box["y"] and box["y"] + box["height"] <= strip["y"] + strip["height"] + 1
    # one slide per step - three of them - and the change that produced a
    # step is shown with it, not on a slide of its own
    assert page.locator(".se-play-count").inner_text() == "1 / 3"
    assert doc.evaluate("document.querySelectorAll('.slide-on').length") == 0   # everything shown until asked
    assert not page.locator(".se-play-all").is_visible()

    page.locator(".se-play").click()
    assert doc.evaluate("document.body.classList.contains('slides')")
    assert doc.evaluate("document.querySelectorAll('.slide-on').length") == 1   # the first step, alone
    assert not frame.locator("h1").is_visible()               # the slide has the frame to itself
    assert "Pause" in page.locator(".se-play").inner_text()
    assert page.locator(".se-play-all").is_visible()
    assert _wait(lambda: page.locator(".se-play-count").inner_text() == "2 / 3", timeout=10)
    # the change and its result are on screen together, in that order
    shown = doc.evaluate("""() => [...document.querySelectorAll('.slide-on')]
        .map(el => el.className.split(' ')[0] + ':' + (el.querySelector('.what') || {}).textContent)""")
    assert len(shown) == 2 and shown[0].startswith("transition:by parts") and shown[1].startswith("step:")
    assert frame.locator(".transition.slide-on .rep-removed").count() > 0   # what it was, in red
    assert frame.locator(".step.slide-on .rep-added").count() > 0           # what it became, in green

    page.locator(".se-play").click()                           # pause where it stands
    at = page.locator(".se-play-count").inner_text()
    assert "Play" in page.locator(".se-play").inner_text()
    page.locator(".se-play-step").last.click()
    assert page.locator(".se-play-count").inner_text() != at
    page.locator(".se-play-step").first.click()
    assert page.locator(".se-play-count").inner_text() == at
    page.locator(".se-play-all").click()                       # back to the whole history
    assert not doc.evaluate("document.body.classList.contains('slides')")
    assert doc.evaluate("document.querySelectorAll('.slide-on').length") == 0
    assert frame.locator("h1").is_visible()
    assert errors == []
    page.close()


def test_the_three_menus_are_one_size(browser, serve_expr):
    """Transform, the type menu and Methods are one set of controls, so they
    are one size.  A select takes the width of its widest option, which made
    the three of them three different widths sitting side by side."""
    from sympy import Matrix

    srv, doc = serve_expr(Matrix([[1, 2], [3, 4]]))       # a type with a menu of its own
    page = _open(browser, srv.url)
    assert _wait(lambda: page.locator(".se-methods").is_visible() and page.locator(".se-typemenu").is_visible())
    widths = page.evaluate("""() => ['.se-ops', '.se-typemenu', '.se-methods']
        .map(s => Math.round(document.querySelector(s).getBoundingClientRect().width))""")
    assert len(set(widths)) == 1, widths
    assert widths[0] > 100, widths                        # and wide enough to read
    assert page.errors == []


def test_up_and_down_walk_into_a_determinant_s_matrix(browser, serve_expr):
    """|M| is drawn by the Determinant around the matrix's contents, so the
    matrix had no place in the view tree: ↓ from the determinant landed on an
    entry and ↑ came straight back, skipping the matrix itself."""
    from sympy import Determinant, Matrix

    srv, doc = serve_expr(Determinant(Matrix([[x]])))
    page = _open(browser, srv.url)
    _select(page, "/")
    assert page.locator(".se-status").inner_text().startswith("Determinant")
    page.keyboard.press("ArrowDown")                       # into the matrix
    assert page.locator(".se-selected").get_attribute("data-path") == "/0"
    assert "Matrix" in page.locator(".se-status").inner_text()
    page.keyboard.press("ArrowDown")                       # and on to the entry
    assert page.locator(".se-selected").get_attribute("data-path") == "/0/2/0"
    page.keyboard.press("ArrowUp")                         # back up through the matrix
    assert page.locator(".se-selected").get_attribute("data-path") == "/0"
    page.keyboard.press("ArrowUp")
    assert page.locator(".se-selected").get_attribute("data-path") == "/"
    assert page.errors == []


def test_the_history_can_be_resized(browser, tmp_path):
    """The formulas are the point of a history, so they take the size the
    reader wants - in the listing and in the slideshow alike: the buttons in
    the strip, Ctrl+wheel, or two fingers."""
    from sympy import Integral, cos
    from sympy_editor import History, to_history_html

    hist = History([
        Integral(x * sin(x), x),
        (-x * cos(x) + Integral(cos(x), x), "by parts"),
        (-x * cos(x) + sin(x), "the last integral"),
    ], title="By parts")
    path = tmp_path / "zoom.html"
    path.write_text(to_history_html(hist), encoding="utf-8")
    page = browser.new_page(viewport={"width": 900, "height": 700}, has_touch=True)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    page.wait_for_selector(".se-history-frame", timeout=30000)
    frame = page.frame_locator(".se-history-frame")
    frame.locator(".step").first.wait_for(timeout=30000)
    doc = page.frames[1]
    size = lambda sel=".step": doc.evaluate(
        "s => parseFloat(getComputedStyle(document.querySelector(s)).fontSize)", sel)

    assert _wait(lambda: page.locator(".se-play-zoom").count() == 2)
    smaller, larger = page.locator(".se-play-zoom").first, page.locator(".se-play-zoom").last
    level = page.locator(".se-play-level")
    listed = size()
    assert level.inner_text() == "100%"                     # and it says where it stands
    larger.click()
    larger.click()
    assert size() > listed * 1.3, (listed, size())
    assert level.inner_text() == "144%"
    grown = size()
    smaller.click()
    assert listed < size() < grown and level.inner_text() == "120%"
    level.click()                                           # the readout is the way back
    assert level.inner_text() == "100%" and size() == listed
    for _ in range(10):
        smaller.click()                                     # and it goes well below half
    assert int(level.inner_text().rstrip("%")) <= 30, level.inner_text()
    assert size() < listed / 2
    level.click()

    # the slideshow scales with it, instead of pinning its own size
    page.locator(".se-play").click()
    slide = size(".step.slide-on")
    assert slide > size(".transition")                      # a slide is drawn larger to start with
    larger.click()
    assert size(".step.slide-on") > slide
    page.locator(".se-play-all").click()

    # Ctrl+wheel over the report, as everywhere else in the editor
    at = size()
    box = page.locator(".se-history-frame").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.keyboard.down("Control")
    page.mouse.wheel(0, -240)
    page.keyboard.up("Control")
    assert _wait(lambda: size() > at)

    # and two fingers
    at = size()
    doc.evaluate("""() => {
        const mk = (x, y) => ({clientX: x, clientY: y, identifier: x, target: document.body});
        const fire = (type, pts) => {
            const e = new Event(type, {bubbles: true, cancelable: true});
            e.touches = pts; e.changedTouches = pts;
            document.dispatchEvent(e);
        };
        fire('touchstart', [mk(100, 100), mk(200, 100)]);
        fire('touchmove', [mk(60, 100), mk(260, 100)]);
        fire('touchend', []);
    }""")
    assert _wait(lambda: size() > at)

    assert level.inner_text() != "100%"                      # the wheel and the fingers move it too
    # the panel itself is resizable where it sits in a page
    assert page.evaluate("getComputedStyle(document.querySelector('.se-history-page')).resize") == "vertical"
    assert errors == []
    page.close()


def test_the_slideshow_runs_at_the_speed_it_is_told(browser, tmp_path):
    """A slideshow at one pace suits nobody: the strip halves and doubles it.
    The buttons say \u00bd\u00d7 and 2\u00d7 rather than - and +, which two groups
    along already mean the size of the formulas."""
    from sympy import Integral, cos
    from sympy_editor import History, to_history_html

    hist = History([
        Integral(x * sin(x), x),
        (-x * cos(x) + Integral(cos(x), x), "by parts"),
        (-x * cos(x) + sin(x), "the last integral"),
    ], title="By parts")
    path = tmp_path / "speed.html"
    path.write_text(to_history_html(hist), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1100, "height": 700})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    page.wait_for_selector(".se-history-frame", timeout=30000)
    page.frame_locator(".se-history-frame").locator(".step").first.wait_for(timeout=30000)
    page.wait_for_function("() => !document.querySelector('.se-play').disabled", timeout=30000)

    slower, faster = page.locator(".se-play-speed").first, page.locator(".se-play-speed").last
    rate, count = page.locator(".se-play-rate"), page.locator(".se-play-count")
    # the two buttons are dials, with no word or figure on them: the speed is
    # written once, on the button between them
    assert (slower.inner_text().strip(), faster.inner_text().strip()) == ("", "")
    assert page.locator(".se-play-speed svg").count() == 2
    assert rate.inner_text() == "1\u00d7"                       # and it says where it stands
    # and the size is a clear step away from the speed, not in the same row of
    # buttons: a percentage and a factor are not the same kind of thing
    gap = page.evaluate("""() => {
        const speed = document.querySelectorAll('.se-play-speed')[1].getBoundingClientRect();
        const zoom = document.querySelector('.se-play-rate').getBoundingClientRect();
        const size = document.querySelectorAll('.se-play-zoom')[0].getBoundingClientRect();
        return [Math.round(size.left - speed.right), Math.round(speed.left - zoom.right)];
    }""")
    assert gap[0] > gap[1] * 2, gap                          # wider than the gap inside a group
    speed = lambda: page.frames[1].evaluate("() => window.sympyHistoryPlayer.state().speed")

    faster.click()
    assert rate.inner_text() == "2\u00d7" and speed() == 2
    faster.click()
    faster.click()
    assert rate.inner_text() == "4\u00d7" and speed() == 4     # and no faster than that
    slower.click()
    assert rate.inner_text() == "2\u00d7"
    rate.click()                                             # the readout is the way back
    assert rate.inner_text() == "1\u00d7" and speed() == 1
    for _ in range(4):
        slower.click()
    assert rate.inner_text() == "0.25\u00d7" and speed() == 0.25

    # a quarter of the speed really is slower: nothing moves in a second and
    # a half, where four times the speed reaches the end inside it
    page.locator(".se-play").click()
    page.wait_for_timeout(1500)
    assert count.inner_text() == "1 / 3", count.inner_text()
    page.locator(".se-play").click()                         # pause
    rate.click()
    faster.click()
    faster.click()
    assert rate.inner_text() == "4\u00d7"
    page.locator(".se-play").click()
    assert _wait(lambda: count.inner_text() == "3 / 3", 3.0)
    # and it stops there, one interval later - a short one, at this speed
    assert _wait(lambda: page.locator(".se-play").inner_text().endswith("Play"), 2.0)
    assert errors == []
    page.close()


def test_dragging_over_the_source_line_selects_in_the_formula(browser, serve_expr):
    """The source line is linked to the rendering both ways.  Selecting text
    in it used to come apart: the floating action bar popped up under the
    pointer as soon as a node matched, the pointer left the line, and the
    browser dropped the selection being made - so nothing was ever selected
    and the formula never followed."""
    srv, doc = serve_expr(x**2 / y - sin(x))
    page = _open(browser, srv.url)
    assert page.locator(".se-source").inner_text() == "x**2/y - sin(x)"
    # where "sin(x)" sits on the screen, to the pixel
    where = page.evaluate("""() => {
        const src = document.querySelector('.se-source'), text = src.textContent;
        const i = text.indexOf('sin(x)');
        const r = document.createRange();
        r.setStart(src.firstChild, i);
        r.setEnd(src.firstChild, i + 'sin(x)'.length);
        const b = r.getBoundingClientRect();
        return {left: b.left, right: b.right, y: (b.top + b.bottom) / 2};
    }""")
    page.mouse.move(where["left"] + 1, where["y"])
    page.mouse.down()
    for k in range(1, 7):
        page.mouse.move(where["left"] + 1 + k * (where["right"] - where["left"] - 2) / 6, where["y"])
    page.mouse.up()

    assert page.evaluate("String(getSelection())") == "sin(x)"      # the text really is selected
    assert page.locator(".se-status").inner_text() == "sin: sin(x)"  # and the formula followed
    assert page.locator(".se-view .se-selected").count() >= 1
    assert not page.locator(".se-actions").is_visible()              # the bar keeps out of the way
    # a caret in the line hints at the node it falls in, without selecting
    page.mouse.click(where["left"] + 2, where["y"])
    assert page.evaluate("String(getSelection())") == ""
    assert page.errors == []


def test_every_control_is_finger_sized_on_a_touch_screen(browser, serve_expr):
    """A coarse pointer makes the controls bigger - but only those the rule
    listed, so the menus added later stayed 12px next to a 14px Transform,
    visibly smaller and harder to hit."""
    from sympy import Matrix

    srv, doc = serve_expr(Matrix([[1, 2], [3, 4]]))
    page = browser.new_page(viewport={"width": 420, "height": 800}, has_touch=True, is_mobile=True)
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    assert _wait(lambda: page.locator(".se-methods").is_visible() and page.locator(".se-typemenu").is_visible())
    sizes = page.evaluate("""() => {
        const out = {};
        for (const s of ['.se-ops', '.se-typemenu', '.se-methods', '.se-fn', '[data-cmd="edit"]']) {
            const el = document.querySelector(s), cs = getComputedStyle(el), r = el.getBoundingClientRect();
            out[s] = [cs.fontSize, Math.round(r.height)];
        }
        return out;
    }""")
    assert len({v[0] for v in sizes.values()}) == 1, sizes          # one type size for all of them
    assert all(v[1] >= 32 for v in sizes.values()), sizes           # and a target a finger can hit
    page.close()


def test_the_history_strip_wraps_by_groups(browser, tmp_path):
    """The strip's controls come in groups - playing, the size, saving - and
    a group wraps as a whole.  A "- 100% +" split over two lines stops being
    a control."""
    from sympy import Integral, cos
    from sympy_editor import History, to_history_html

    hist = History([
        Integral(x * sin(x), x),
        (-x * cos(x) + Integral(cos(x), x), "by parts"),
        (-x * cos(x) + sin(x), "the last integral"),
    ], title="By parts")
    path = tmp_path / "groups.html"
    path.write_text(to_history_html(hist), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1000, "height": 640})
    page.goto(path.as_uri())
    page.wait_for_selector(".se-history-frame", timeout=30000)
    assert _wait(lambda: page.locator(".se-play").is_visible())
    page.locator(".se-play").click()                       # every control visible
    MEASURE = """() => [...document.querySelectorAll('.se-history-head .se-head-group')].map(g => {
        const kids = [...g.children].filter(c => !c.hidden).map(c => c.getBoundingClientRect());
        const mids = new Set(kids.map(r => Math.round((r.top + r.bottom) / 2)));
        return {label: g.textContent.replace(/\\s+/g, ' ').trim().slice(0, 16),
                lines: mids.size, height: Math.round(g.getBoundingClientRect().height),
                tallest: Math.round(Math.max(...kids.map(r => r.height)))};
    })"""
    for width in (1000, 640, 420, 360, 320):
        page.set_viewport_size({"width": width, "height": 640})
        page.wait_for_timeout(60)
        groups = page.evaluate(MEASURE)
        assert len(groups) >= 3, (width, groups)
        for g in groups:
            assert g["lines"] == 1, (width, g)                   # every child on the same line...
            assert g["height"] <= g["tallest"] + 2, (width, g)   # ...so the group is one row high
        zoom = next(g for g in groups if "100%" in g["label"])
        assert zoom["lines"] == 1, (width, zoom)
    page.close()


def test_nothing_in_the_history_strip_moves(browser, tmp_path):
    """Pressing Play used to shift the buttons beside it: "Show all" appeared
    out of nowhere and Pause is wider than Play.  Every control keeps its
    exact place, whatever the player is doing."""
    from sympy import Integral, cos
    from sympy_editor import History, to_history_html

    hist = History([
        Integral(x * sin(x), x),
        (-x * cos(x) + Integral(cos(x), x), "by parts"),
        (-x * cos(x) + sin(x), "the last integral"),
    ], title="By parts")
    path = tmp_path / "steady.html"
    path.write_text(to_history_html(hist), encoding="utf-8")
    page = browser.new_page(viewport={"width": 420, "height": 640})
    page.goto(path.as_uri())
    page.wait_for_selector(".se-history-frame", timeout=30000)
    assert _wait(lambda: page.locator(".se-play").is_visible())
    WHERE = """() => {
        const out = {};
        for (const el of document.querySelectorAll('.se-history-head button, .se-history-head .se-play-count')) {
            const r = el.getBoundingClientRect();
            out[el.className.split(' ')[0] + '|' + (el.getAttribute('aria-label') || '')] =
                [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
        }
        return out;
    }"""
    at_rest = page.evaluate(WHERE)
    assert len(at_rest) >= 6, at_rest

    page.locator(".se-play").click()                        # Play -> Pause, "Show all" wakes up
    assert page.evaluate(WHERE) == at_rest
    assert page.locator(".se-play-all").is_visible()
    assert _wait(lambda: page.locator(".se-play-count").inner_text() == "2 / 3", timeout=10)
    assert page.evaluate(WHERE) == at_rest                  # and while it plays
    page.locator(".se-play-zoom").last.click()              # 100% -> 120%
    assert page.evaluate(WHERE) == at_rest
    page.locator(".se-play-all").click()                    # back to the listing
    assert page.evaluate(WHERE) == at_rest
    page.close()


def test_the_arrows_walk_the_listing_when_nothing_is_playing(browser, tmp_path):
    """With everything shown, the two arrows are not slideshow controls with
    nothing to do: they walk the steps of the listing, scrolling to each and
    marking it, and Play then starts from where they left off."""
    from sympy import Integral, cos
    from sympy_editor import History, to_history_html

    hist = History([
        Integral(x * sin(x), x),
        (-x * cos(x) + Integral(cos(x), x), "by parts"),
        (-x * cos(x) + sin(x), "the last integral"),
        ((-x * cos(x) + sin(x)).expand(), "expand"),
    ], title="By parts")
    path = tmp_path / "walk.html"
    path.write_text(to_history_html(hist), encoding="utf-8")
    page = browser.new_page(viewport={"width": 900, "height": 520})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    page.wait_for_selector(".se-history-frame", timeout=30000)
    assert _wait(lambda: page.locator(".se-play").is_visible())
    doc = page.frames[1]
    scroll = lambda: doc.evaluate("document.scrollingElement.scrollTop")
    playing = lambda: doc.evaluate("document.body.classList.contains('slides')")
    prev, next_ = page.locator(".se-play-step").first, page.locator(".se-play-step").last

    assert page.locator(".se-play-count").inner_text() == "1 / 4" and scroll() == 0
    next_.click()
    assert not playing()                                     # still the whole history, not a slideshow
    assert page.locator(".se-play-count").inner_text() == "2 / 4"
    assert _wait(lambda: scroll() > 0)                       # it moved to the step
    assert doc.evaluate("document.querySelectorAll('.slide-here').length") == 2   # the change and its result
    assert _wait(lambda: scroll() > 0)
    next_.click()
    assert _wait(lambda: page.locator(".se-play-count").inner_text() == "3 / 4")
    at3 = _settled(scroll)
    prev.click()                                             # and back up the listing
    assert _wait(lambda: page.locator(".se-play-count").inner_text() == "2 / 4")
    assert _wait(lambda: scroll() < at3)
    assert not playing()

    page.locator(".se-play").click()                         # Play carries on from there
    assert playing() and page.locator(".se-play-count").inner_text() == "2 / 4"
    page.locator(".se-play-all").click()                     # and Show all lands on the step it was on
    assert not playing()
    assert doc.evaluate("document.querySelectorAll('.slide-here').length") == 2
    assert page.locator(".se-play-count").inner_text() == "2 / 4"
    assert errors == []
    page.close()


def test_a_caret_in_the_source_line_is_a_caret_in_the_formula(browser, serve_expr):
    """Putting the text cursor in the source line drops whatever was selected
    and puts the formula's caret in the same place - the two lines are one
    document seen twice, not two."""
    srv, doc = serve_expr(x**2 / y - sin(x))
    page = _open(browser, srv.url)
    text = page.locator(".se-source").inner_text()
    assert text == "x**2/y - sin(x)"
    _select(page, "/1/n/0")                                  # something selected in the formula
    assert page.locator(".se-view .se-selected").count() >= 1

    def caret_at(offset):
        page.evaluate("""(off) => {
            const src = document.querySelector('.se-source');
            src.focus();
            const r = document.createRange();
            r.setStart(src.firstChild, off);
            r.collapse(true);
            const s = getSelection(); s.removeAllRanges(); s.addRange(r);
        }""", offset)
        # selectionchange is delivered asynchronously: wait for the caret to follow
        state = lambda: page.evaluate("""() => {
            const e = document.querySelector('.sympy-editor').__sympyEditor;
            return {selected: e.selected, range: !!e.range,
                    caret: e.caret ? [e.caret.path, e.caret.extend || null] : null};
        }""")
        _wait(lambda: state()["caret"] is not None)
        return state()

    at = caret_at(text.index("y"))                            # right before the denominator
    assert at["selected"] is None and not at["range"]         # the selection is lifted...
    assert page.locator(".se-caret").count() == 1             # ...and the formula has a caret
    assert at["caret"] == ["/1/d", "before"]                  # exactly where the text cursor is
    assert page.locator(".se-status").inner_text().startswith("Type before Symbol y")

    assert caret_at(text.index("y") + 1)["caret"] == ["/1/d", "after"]
    assert caret_at(0)["caret"][0] == "/"                     # the head of the formula
    assert caret_at(len(text))["caret"][0] == "/"             # and its tail
    assert page.evaluate("(() => { const e = document.querySelector('.sympy-editor').__sympyEditor; return e.caret.index; })()") == 2
    assert page.errors == []


def test_the_history_close_button_sits_in_the_corner(browser, serve_expr):
    """However the strip wraps, closing is in the top right corner."""
    srv, doc = serve_expr((x + y) ** 2)
    page = browser.new_page(viewport={"width": 420, "height": 700})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(srv.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    _next_state(page, lambda: page.select_option(".se-ops", "expand"))   # a second step, so there is a player
    page.locator('.se-toolbar [data-cmd="history"]').click()
    page.wait_for_selector(".se-history-view")
    assert _wait(lambda: page.locator(".se-history-head .se-play").is_visible())
    where = page.evaluate("""() => {
        const head = document.querySelector('.se-history-head').getBoundingClientRect();
        const close = document.querySelector('.se-history-close').getBoundingClientRect();
        const rows = new Set([...document.querySelectorAll('.se-history-head .se-head-group')]
            .map(g => Math.round(g.getBoundingClientRect().top)));
        return {fromRight: head.right - close.right, fromTop: close.top - head.top, rows: rows.size};
    }""")
    assert where["rows"] > 1, where                       # the strip really does wrap here
    assert where["fromRight"] < 16 and where["fromTop"] < 12, where
    assert errors == []
    page.close()


def test_a_session_can_be_given_a_name(browser, tmp_path):
    """A session was labelled with its formula, which is no help once there
    are several: the name can be the user's own, and then nothing overwrites
    it - not even editing the formula."""
    path = tmp_path / "sessions.html"
    path.write_text(to_html(x + y, options={"sessions": True}), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1000, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    page.locator('[data-cmd="drawer"]').click()
    row = page.locator(".se-session").first
    assert _wait(lambda: row.locator(".se-session-row > code").inner_text() == "x + y")

    row.locator(".se-session-rename").first.click()
    field = row.locator("input.se-session-name")
    field.wait_for()
    field.fill("Simplifying the Hamiltonian")
    field.press("Enter")
    assert _wait(lambda: row.locator(".se-session-row > code").inner_text() == "Simplifying the Hamiltonian")

    # the formula changes; the name the user gave stays
    page.keyboard.press("Escape")                            # close the drawer (its backdrop covers the tools)
    assert _wait(lambda: page.locator(".se-drawer").is_hidden())
    _next_state(page, lambda: page.select_option(".se-ops", "expand"))
    page.locator('[data-cmd="drawer"]').click()
    assert _wait(lambda: page.locator(".se-session").first.locator(".se-session-row > code").inner_text() == "Simplifying the Hamiltonian")
    # it survives a reload, like the sessions themselves
    page.reload()
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    page.locator('[data-cmd="drawer"]').click()
    assert _wait(lambda: page.locator(".se-session").first.locator(".se-session-row > code").inner_text() == "Simplifying the Hamiltonian")

    # emptying the name hands the session back to its formula
    page.locator(".se-session").first.locator(".se-session-rename").first.click()
    field = page.locator(".se-session").first.locator("input.se-session-name")
    field.wait_for()
    field.fill("")
    field.press("Enter")
    assert _wait(lambda: page.locator(".se-session").first.locator(".se-session-row > code").inner_text() != "Simplifying the Hamiltonian")
    assert errors == []
    page.close()


def test_the_history_strip_opens_in_its_final_shape(browser, serve_expr):
    """The player's controls used to appear only once the report inside the
    iframe answered, so opening the view showed a strip that rearranged
    itself a moment later.  They are built in place, already counting the
    steps, and merely become usable."""
    srv, doc = serve_expr((x + y) ** 2)
    page = _open(browser, srv.url)
    _next_state(page, lambda: page.select_option(".se-ops", "expand"))     # two steps: there is a player
    GEOM = """() => {
        const out = {};
        for (const el of document.querySelectorAll('.se-history-head button, .se-history-head select, .se-history-head .se-play-count')) {
            const r = el.getBoundingClientRect();
            out[(el.className || el.tagName).split(' ')[0]] = [Math.round(r.x), Math.round(r.y), Math.round(r.width)];
        }
        return out;
    }"""
    page.locator('.se-toolbar [data-cmd="history"]').click()
    page.wait_for_selector(".se-history-head .se-play")                    # the strip's first paint
    at_first = page.evaluate(GEOM)
    assert page.locator(".se-play-count").inner_text() == "1 / 2"          # it already knows the steps
    assert page.locator(".se-play-level").inner_text() == "100%"
    page.wait_for_function("() => !document.querySelector('.se-history-head .se-play').disabled", timeout=30000)
    assert page.evaluate(GEOM) == at_first                                 # and nothing moved
    assert len(at_first) >= 6, at_first
    page.keyboard.press("Escape")

    # a history of one step has nothing to play, and shows no player at all
    srv2, doc2 = serve_expr(x + y)
    page.goto(srv2.url)
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    page.locator('.se-toolbar [data-cmd="history"]').click()
    page.wait_for_selector(".se-history-view")
    assert page.locator(".se-history-head .se-play").count() == 0
    assert page.locator(".se-history-head .se-head-save").count() == 1
    assert page.errors == []


def test_the_formula_and_the_source_line_show_the_same_place(browser, serve_expr):
    """The two views are one document.  What is selected in the formula is
    marked in the source text, and the formula's caret shows there as a
    cursor of its own - the other half of putting the text cursor in the
    line and getting a caret in the formula."""
    srv, doc = serve_expr(x**2 / y - sin(x))
    page = _open(browser, srv.url)
    text = page.locator(".se-source").inner_text()
    marker_at = """() => {
        const src = document.querySelector('.se-source');
        const c = src.querySelector('.se-source-caret');
        if (!c) return null;
        let off = 0;
        for (const n of src.childNodes) { if (n === c) break; off += (n.textContent || '').length; }
        return off;
    }"""

    _select(page, "/1/d")                                    # the y of x**2/y
    assert page.locator(".se-source mark").inner_text() == "y"
    assert page.evaluate(marker_at) is None                  # a selection, not a caret

    _select(page, "/0")                                      # a whole term: - sin(x)
    assert page.locator(".se-source mark").inner_text() == "- sin(x)"

    _select(page, "/1/d")
    page.keyboard.press("ArrowDown")                         # into a caret beside it
    assert page.locator(".se-caret").count() == 1
    assert page.locator(".se-source mark").count() == 0      # no selection any more...
    assert page.evaluate(marker_at) == text.index("y") + 1   # ...a cursor where the caret is
    page.keyboard.press("ArrowLeft")                         # the cursor follows the caret
    assert page.evaluate(marker_at) == text.index("y")

    page.keyboard.press("Escape")                            # and goes when nothing is marked
    assert page.evaluate(marker_at) is None
    assert page.locator(".se-source").inner_text() == text
    assert page.errors == []


def test_naming_a_session_owns_the_row_until_it_is_done(browser, tmp_path):
    """While a session is being named the row is the field and its two
    answers - the pencil, Open and Delete step aside, so a stray tap cannot
    open or delete what is being renamed - and a snapshot arriving in the
    background must not rebuild the list under the typing."""
    path = tmp_path / "naming.html"
    path.write_text(to_html(x + y, options={"sessions": True}), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1000, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script("""localStorage.setItem('sympy-editor:sessions', JSON.stringify({
        current: 's1',
        list: [{id: 's1', name: 'x + y', updated: 2, state: null, empty: false},
               {id: 's2', name: 'an older one', updated: 1, state: null, empty: false, title: true}]
    }));""")
    page.goto(path.as_uri())
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    page.locator('[data-cmd="drawer"]').click()
    row = page.locator(".se-session[data-id]").last               # not the current session
    assert _wait(lambda: row.locator("code").first.inner_text() == "an older one")
    assert row.locator("[data-delete]").is_enabled()              # deleting needs a second session
    assert row.locator("[data-delete]").inner_text() == "Delete"

    row.locator(".se-session-rename").first.click()
    field = page.locator("input.se-session-name")
    field.wait_for()
    assert field.input_value() == "an older one"                  # a name of its own is there to edit
    assert row.locator("button").all_inner_texts() == ["✓", "✕"]
    assert row.locator("[data-open]").count() == 0 and row.locator("[data-delete]").count() == 0
    # the editor stores a session in the background; the field must survive it
    page.evaluate("document.querySelector('.sympy-editor').__sympyEditor._fillSessions()")
    page.wait_for_timeout(600)
    assert page.locator("input.se-session-name").count() == 1

    field.fill("Hamiltonian")
    row.locator(".se-session-drop").click()                       # ✕ leaves it as it was
    assert _wait(lambda: row.locator("code").first.inner_text() == "an older one")
    assert row.locator("[data-open]").count() == 1                # and the row comes back whole

    row.locator(".se-session-rename").first.click()
    page.locator("input.se-session-name").fill("Hamiltonian")
    row.locator(".se-session-keep").click()                       # ✓ keeps it
    assert _wait(lambda: row.locator("code").first.inner_text() == "Hamiltonian")
    assert errors == []
    page.close()

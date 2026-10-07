"""Interaction bugs of the front end: grid moves, operators, the two views'
carets, the keep chooser, ranges of connectives, stale pointers, the empty
view and refused edits.  Driven by Playwright like tests/test_browser.py,
whose fixtures and helpers these tests share."""

import pytest
from sympy import And, Matrix, symbols

from test_browser import (  # noqa: F401  (fixtures are used by name)
    _close_what_the_test_opened,
    _click,
    _display_children,
    _next_state,
    _open,
    _select,
    _wait,
    browser,
    pytestmark,
    serve_expr,
)

x, y, z = symbols("x y z")
ED = "document.querySelector('.sympy-editor').__sympyEditor"

PLUS = """() => { const v = document.querySelector('.se-view');
    for (const el of v.querySelectorAll('*')) {
        if (el.querySelector('[data-path]')) continue;
        if ((el.textContent || '').trim() === '+') {
            const r = el.getBoundingClientRect();
            return [r.left + r.width / 2, r.top + r.height / 2]; } }
    return null; }"""


def _path(doc, src):
    return next(k for k, v in doc.snapshot()["nodes"].items() if v["src"] == src)


def _selected(page):
    return page.evaluate(f"(() => {ED}.selected)()")


def _junction(page):
    plus = page.evaluate(PLUS)
    assert plus, "no + glyph"
    page.mouse.click(plus[0], plus[1])
    assert _wait(lambda: page.evaluate(f"(() => !!{ED}.junction)()"))


# 1 ---------------------------------------------------------------------------

def test_sideways_in_a_grid_keeps_to_the_drawn_row(browser, serve_expr):
    """→ from the last cell of a row steps out of the matrix (here: nowhere,
    the matrix is the whole formula), never onto the next row's cell; ←
    from inside a cell's expression goes to the cell beside it in its row."""
    srv, doc = serve_expr(Matrix([[x, y], [z, 1]]))
    page = _open(browser, srv.url)
    _select(page, "/2/1")                                  # y, end of the first row
    page.locator(".se-view").press("ArrowRight")
    assert _selected(page) == "/2/1"                       # not 1, the cell below
    assert page.locator('.se-keyrow [data-cmd="right"]').is_disabled()

    srv, doc = serve_expr(Matrix([[x, y + 1], [z, 1]]))
    page = _open(browser, srv.url)
    _select(page, _path(doc, "y"))
    page.locator(".se-view").press("ArrowLeft")
    assert _selected(page) == "/2/0"                       # x, not z
    _select(page, _path(doc, "y"))
    page.locator('.se-keyrow [data-cmd="left"]').click()
    assert _selected(page) == "/2/0"
    assert page.errors == []


# 2 ---------------------------------------------------------------------------

def test_down_from_an_operator_stays_on_its_line(browser, serve_expr):
    srv, doc = serve_expr((x + y) / z)
    page = _open(browser, srv.url)
    _junction(page)
    page.locator(".se-view").press("ArrowDown")
    assert _wait(lambda: page.evaluate(f"(() => !!{ED}.caret)()"))
    page.locator(".se-view").type("5", delay=40)
    page.keyboard.press("Enter")
    assert _wait(lambda: doc.expr == (x + 5 * y) / z, timeout=20), str(doc.expr)
    assert page.errors == []


# 3 ---------------------------------------------------------------------------

def _source_before_caret(page):
    return page.evaluate("""() => { const s = document.querySelector('.se-source');
        const c = s.querySelector('.se-source-caret'); if (!c) return null;
        const r = document.createRange(); r.selectNodeContents(s); r.setEndBefore(c);
        return r.toString(); }""")


@pytest.mark.parametrize("expr_src, left_src, before", [
    ("x + y + z", "x", "x"),
    ("sqrt(x) + 1", "sqrt(x)", "sqrt(x)"),
])
def test_a_caret_attached_left_is_at_the_end_of_the_left_term(browser, serve_expr, expr_src, left_src, before):
    from sympy import sympify
    srv, doc = serve_expr(sympify(expr_src))
    page = _open(browser, srv.url)
    _select(page, _path(doc, left_src))
    page.evaluate(f"(() => {ED}.caretAtSelection(false))()")   # right after it, attached to it
    assert page.evaluate(f"(() => {ED}.caret.attach)()") == "left"
    assert _wait(lambda: _source_before_caret(page) == before), _source_before_caret(page)


# The caret between `left` and `right` in the node `parent`, with no side of
# its own - as a click in the gap or an arrow key leaves it - drawn at one
# end of the gap: "a" is against the left term, "b" after the operator,
# against the right one.
_CARET_IN_GAP = """([parent, left, right, end]) => {
    const ed = document.querySelector('.sympy-editor').__sympyEditor;
    const gap = ed._gapsOf(parent).find(g => g.leftEl && g.rightEl
        && g.leftEl.getAttribute('data-path') === left && g.rightEl.getAttribute('data-path') === right);
    if (!gap) return null;
    ed._showCaret(gap, gap[end]);
    return {a: gap.a, b: gap.b, y: (gap.top + gap.bottom) / 2, attach: ed.caret.attach || null};
}"""


@pytest.mark.parametrize("expr_src, parent, left, right, end, typed, expected", [
    # the reported bug: in front of the 1, r must join the 1 (x + r*1), not the x
    ("(x + 1)/(t - z + 3)", "x + 1", "x", "1", "b", "r", "(x + r)/(t - z + 3)"),
    ("(x + 1)/(t - z + 3)", "x + 1", "x", "1", "a", "r", "(r*x + 1)/(t - z + 3)"),
    ("(x + 1)/(t - z + 3)", "x + 1", "x", "1", "b", "2", "(x + 2)/(t - z + 3)"),
    ("(x + 1)/(t - z + 3)", "t - z + 3", "-z", "3", "b", "r", "(x + 1)/(t - z + 3*r)"),
    ("x + y", "x + y", "x", "y", "b", "r", "x + r*y"),
    ("x + y", "x + y", "x", "y", "a", "r", "r*x + y"),
    ("x + y + z", "x + y + z", "y", "z", "b", "r", "x + y + r*z"),
    ("x + y + z", "x + y + z", "x", "y", "b", "r", "x + r*y + z"),
    ("x**2 + y", "x**2 + y", "x**2", "y", "b", "r", "x**2 + r*y"),     # stored as Add(y, x**2)
    ("x**2 + y", "x**2 + y", "x**2", "y", "a", "r", "r*x**2 + y"),
    ("Eq(x + y, z)", "x + y", "x", "y", "b", "r", "Eq(x + r*y, z)"),
    # an operator typed with the text still decides for itself
    ("x + y", "x + y", "x", "y", "b", "r +", "x + r + y"),
    ("x + y", "x + y", "x", "y", "a", "+ r", "x + r + y"),
    ("x + y", "x + y", "x", "y", "b", "+ r", "x + r + y"),             # a new term wherever in the gap
    ("x + y", "x + y", "x", "y", "b", "- r", "x - r + y"),
    ("x + y", "x + y", "x", "y", "a", "r +", "r*x + y"),
])
def test_text_typed_at_a_caret_joins_the_term_the_caret_is_drawn_against(
        browser, serve_expr, expr_src, parent, left, right, end, typed, expected):
    """A caret is a point in the written formula: after the plus of `x + 1`,
    in front of the 1, what is typed belongs with the 1.  It used to join the
    term on the left whatever the caret showed - `r` there gave `r*x + 1`."""
    from sympy import sympify
    srv, doc = serve_expr(sympify(expr_src))
    page = _open(browser, srv.url)
    where = page.evaluate(_CARET_IN_GAP, [_path(doc, parent), _path(doc, left), _path(doc, right), end])
    assert where and where["b"] - where["a"] > 2 and where["attach"] is None, where
    page.locator(".se-view").focus()
    _next_state(page, lambda: (page.keyboard.type(typed), page.keyboard.press("Enter")))
    assert doc.expr == sympify(expected), (doc.expr, expected)
    assert page.errors == []


@pytest.mark.parametrize("expr_src, where, expected", [
    ("f(x, y)", "before", "r*f(x, y)"),          # left of the f: beside the call, not in front of x
    ("f(x, y)", "after", "r*f(x, y)"),           # right of the closing parenthesis
    ("f(x, y)", "inside-first", "f(r*x, y)"),    # just in front of x, inside
    ("f(x, y)", "inside-last", "f(x, r*y)"),
    ("Max(x, y)", "before", "r*Max(x, y)"),
    ("Max(x, y)", "after", "r*Max(x, y)"),
    ("x + y", "before", "r*x + y"),              # a sum starts with its first term: nothing changed
    ("x + y", "after", "x + r*y"),
])
def test_a_caret_outside_a_function_call_is_beside_the_call(browser, serve_expr, expr_src, where, expected):
    """With `f(x, y)` as the whole formula, a click left of the `f` or right
    of the `)` shows the caret outside the call - and what was typed went
    inside it, onto the first or the last argument (`f(r*x, y)`)."""
    from sympy import sympify
    srv, doc = serve_expr(sympify(expr_src))
    page = _open(browser, srv.url)
    box = page.evaluate(f"""(() => {{ const ed = {ED}; const kids = ed._readingChildren('/').map(p => ed._visualRect(ed._els(p)[0]));
        const r = ed._visualRect(ed._els('/')[0]);
        return {{left: r.left, right: r.right, y: (kids[0].top + kids[0].bottom) / 2,
                 first: kids[0].left, last: kids[kids.length - 1].right}}; }})()""")
    x = {"before": box["left"] - 8, "after": box["right"] + 8,
         "inside-first": box["first"] - 1, "inside-last": box["last"] + 1}[where]
    page.mouse.click(x, box["y"])
    assert page.locator(".se-caret").count() == 1
    _next_state(page, lambda: (page.keyboard.type("r"), page.keyboard.press("Enter")))
    assert doc.expr == sympify(expected), (doc.expr, expected)
    assert page.errors == []


def test_a_click_in_front_of_a_term_types_onto_that_term(browser, serve_expr):
    """The same by hand: a click in the gap of `x + 1`, after the plus, then
    the keys; and with the arrow keys from the 1."""
    from sympy import sympify
    expr = sympify("(x + 1)/(t - z + 3)")
    srv, doc = serve_expr(expr)
    page = _open(browser, srv.url)
    one = page.evaluate("p => { const r = document.querySelector(`[data-path=\"${p}\"]`).getBoundingClientRect();"
                        " return {left: r.left, y: (r.top + r.bottom) / 2}; }", _path(doc, "1"))
    page.mouse.click(one["left"] - 1, one["y"])                  # just in front of the 1
    assert page.locator(".se-caret").count() == 1
    _next_state(page, lambda: (page.keyboard.type("r"), page.keyboard.press("Enter")))
    assert doc.expr == sympify("(x + r)/(t - z + 3)"), doc.expr
    # from the 3 of the denominator: ↓ gives a caret after it, ← steps in front of it
    _click(page, _path(doc, "3"))
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowLeft")
    assert page.locator(".se-caret").count() == 1
    _next_state(page, lambda: (page.keyboard.type("w"), page.keyboard.press("Enter")))
    assert doc.expr == sympify("(x + r)/(t - z + 3*w)"), doc.expr
    assert page.errors == []


def test_the_source_cursor_after_a_term_puts_the_caret_after_it(browser, serve_expr):
    srv, doc = serve_expr(x + y + z)
    page = _open(browser, srv.url)
    page.evaluate("""() => { const s = document.querySelector('.se-source'); s.focus();
        const t = s.firstChild; const r = document.createRange(); r.setStart(t, 1); r.collapse(true);
        const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); }""")
    assert _wait(lambda: page.evaluate(f"(() => !!{ED}.caret)()"))
    left = page.evaluate(f"(() => {{ const c = {ED}.caret; return c.leftEl ? c.leftEl.getAttribute('data-path') : null; }})()")
    assert left == _path(doc, "x")                         # after x, not before it


# 4 ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["Backspace", "ArrowUp"])
def test_the_keep_chooser_keeps_the_focused_choice(browser, serve_expr, key):
    srv, doc = serve_expr(x ** 2 + y)
    page = _open(browser, srv.url)
    _click(page, _path(doc, "x"))
    page.keyboard.press("ArrowUp")                         # the power, coming from x
    page.keyboard.press("Backspace")
    keep = page.locator(".se-keep")
    keep.wait_for(state="visible")
    assert page.evaluate("(() => document.activeElement.textContent.trim())()") == "x"
    page.keyboard.press(key)
    assert _wait(lambda: doc.expr == x + y, timeout=10), str(doc.expr)
    assert page.errors == []


# 5 ---------------------------------------------------------------------------

def test_toolbar_arrows_work_on_an_operator(browser, serve_expr):
    srv, doc = serve_expr(x + y + z)
    page = _open(browser, srv.url)
    _junction(page)
    parent = page.locator('.se-keyrow [data-cmd="parent"]')
    assert _wait(lambda: parent.is_enabled())
    parent.click()
    assert _wait(lambda: _selected(page) == "/")
    for cmd, side in (("right", "rightIndex"), ("left", "leftIndex")):
        _junction(page)
        want = page.evaluate(f"(() => {ED}._displayChildren({ED}.junction.path)[{ED}.junction.{side}])()")
        page.locator(f'.sympy-editor [data-cmd="{cmd}"]').click()
        assert _wait(lambda: _selected(page) == want), (cmd, _selected(page), want)
        assert page.evaluate(f"(() => {ED}.caret)()") is None
    assert page.errors == []


# 6 ---------------------------------------------------------------------------

def test_editing_a_range_of_a_conjunction(browser, serve_expr):
    expr = And(x > 1, y < 2, z > 0)
    srv, doc = serve_expr(expr)
    page = _open(browser, srv.url)
    first = _display_children(page, "/")[0]
    _select(page, first)
    page.keyboard.press("Shift+ArrowRight")
    assert page.evaluate(f"(() => !!{ED}.range)()")
    page.keyboard.press("Enter")
    field = page.locator(".se-view input.se-inline")
    field.wait_for()
    page.evaluate("""() => { const i = document.querySelector('.se-view input.se-inline');
        i.value = i.value.replace(/[012]/g, '7'); }""")
    field.press("Enter")
    assert _wait(lambda: doc.expr != expr, timeout=10), page.locator(".se-error").inner_text()
    assert len(doc.expr.args) == 3
    assert sum(1 for a in doc.expr.args if a.rhs == 7) == 2
    assert page.errors == []


# 7 ---------------------------------------------------------------------------

FIRE = """([type, pid, ptype, target]) => {
    const v = document.querySelector('.se-view');
    const el = target === 'body' ? document.body : v;
    const r = v.getBoundingClientRect();
    el.dispatchEvent(new PointerEvent(type, { pointerId: pid, pointerType: ptype, isPrimary: true,
        button: 0, buttons: type === 'pointerdown' ? 1 : 0, bubbles: true, cancelable: true,
        clientX: r.left + 5, clientY: r.top + 5 })); }"""


@pytest.mark.parametrize("release", ["outside", "never"])
def test_a_mouse_released_elsewhere_does_not_make_a_tap_a_pinch(browser, serve_expr, release):
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    page.evaluate(f"a => ({FIRE})(a)", ["pointerdown", 1, "mouse", "view"])
    if release == "outside":
        page.evaluate(f"a => ({FIRE})(a)", ["pointerup", 1, "mouse", "body"])
    page.evaluate(f"a => ({FIRE})(a)", ["pointerdown", 7, "touch", "view"])
    state = page.evaluate(f"(() => ({{pinch: !!{ED}._pinch, n: Object.keys({ED}._pointers).length}}))()")
    assert state == {"pinch": False, "n": 1}, state
    page.evaluate(f"a => ({FIRE})(a)", ["pointerup", 7, "touch", "view"])
    assert page.errors == []


# 8 ---------------------------------------------------------------------------

def test_a_refused_expression_in_the_empty_view_keeps_its_text(browser, serve_expr):
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    _select(page, "/")
    page.keyboard.press("Backspace")                       # everything removed
    field = page.locator(".se-view input.se-inline-empty")
    field.wait_for()
    page.keyboard.type("x +")                              # (an open bracket is closed for you now: not refused)
    page.keyboard.press("Enter")
    assert _wait(lambda: page.locator(".se-error").is_visible(), timeout=10)
    assert field.count() == 1 and field.input_value() == "x +"
    assert page.evaluate("(() => document.activeElement.classList.contains('se-inline-empty'))()")
    assert doc.expr == x + y
    page.keyboard.press("Escape")                          # still alive: Esc brings the expression back
    assert _wait(lambda: field.count() == 0)
    page.wait_for_selector(".se-view .katex [data-path]")
    assert page.errors == []


# 9 ---------------------------------------------------------------------------

def test_down_on_a_fraction_takes_the_numerator_first(browser, serve_expr):
    srv, doc = serve_expr(x / (y + z + 1))
    page = _open(browser, srv.url)
    page.evaluate(f"(() => {{ {ED}._cameFrom = {{}}; {ED}.select('/'); }})()")
    page.locator(".se-view").press("ArrowDown")
    assert _selected(page) == "/n"


def test_a_refused_insertion_keeps_the_caret(browser, serve_expr):
    srv, doc = serve_expr(x + y)
    page = _open(browser, srv.url)
    _select(page, _path(doc, "x"))
    page.evaluate(f"(() => {ED}.caretAtSelection(false))()")
    assert page.evaluate(f"(() => !!{ED}.caret)()")
    page.locator(".se-view").type("((", delay=40)
    _next_state(page, lambda: page.keyboard.press("Enter"))
    assert page.locator(".se-error").is_visible()
    assert _wait(lambda: page.evaluate(f"(() => !!{ED}.caret)()"))
    left = page.evaluate(f"(() => {{ const c = {ED}.caret; return c.leftEl ? c.leftEl.getAttribute('data-path') : null; }})()")
    assert left == _path(doc, "x")
    assert page.locator(".se-error").is_visible()          # the caret coming back does not hide the error
    # the next change of selection takes the stale error away
    _click(page, _path(doc, "y"))
    assert _wait(lambda: not page.locator(".se-error").is_visible())
    assert page.errors == []

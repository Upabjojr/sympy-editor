"""The tree panel in a real browser: a click selects in the formula, a
double-click edits, the node menu offers the editor's tools.  Needs
Playwright with Chromium and the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing, contextmanager
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


@contextmanager
def _served(doc):
    """A page showing ``doc`` with its tree, and the server behind it: both
    stopped however the test ends - a failure or a skip included, which
    used to leave the server serving for the rest of the run."""
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(srv.url)
                page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
                page.wait_for_selector(".se-addon-tree .tree-node", timeout=10000)
                page.errors = errors
                yield page
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def page_and_doc():
    doc = Document(x + y * z, addons=[ADDON])
    with _served(doc) as page:
        yield page, doc


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
    page.locator('.se-keyrow [data-cmd="parent"]').click()      # the enclosing expression, in the editor
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
    with _served(doc) as page:
        pows = page.locator(".tree-node").filter(has_text="Pow")
        sel = lambda: page.locator(".se-selected[data-path]").first.get_attribute("data-path")
        seen = set()
        for i in range(pows.count()):
            node = pows.nth(i)
            src = node.locator("title").text_content()
            node.click()
            page.wait_for_function("document.querySelector('.se-selected[data-path]') !== null")
            if src == "sin(x)**2":
                assert doc.get(sel()) == sin(x) ** 2 and sel().endswith("/n")
            elif src == "1/x":
                assert doc.get(sel()) == x and sel().endswith("/d")
            seen.add(src)
            page.keyboard.press("Escape")
        assert {"sin(x)**2", "1/x"} <= seen, seen      # both pieces were there to click
        assert page.errors == []


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
    # every box starts shut: a history is a list of steps, and a tree opened
    # beside each of them would bury it
    assert frame.locator("section.step details.tree-history[open]").count() == 0
    # a click on a box's heading opens that one, and does not open the step
    # (the view stays where it is)
    frame.locator("section.step details.tree-history summary").first.click()
    page.wait_for_timeout(300)
    assert page.locator(".se-history-view").count() == 1
    assert frame.locator("section.step details.tree-history").first.get_attribute("open") is not None
    frame.locator("section.step details.tree-history summary").first.click()   # and shuts it again
    page.wait_for_timeout(300)
    assert frame.locator("section.step details.tree-history").first.get_attribute("open") is None
    # the strip's buttons do all of them at once
    page.locator(".se-history-head .tree-expand-all").click()
    page.wait_for_function("Array.from(document.querySelector('.se-history-frame').contentDocument.querySelectorAll('details.tree-history')).every(d => d.open)")
    page.locator(".se-history-head .tree-collapse-all").click()
    page.wait_for_function("Array.from(document.querySelector('.se-history-frame').contentDocument.querySelectorAll('details.tree-history')).every(d => !d.open)")
    page.keyboard.press("Escape")
    assert page.errors == []


def _drag(page, src, dst):
    a, b = src.bounding_box(), dst.bounding_box()
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(a["x"] + a["width"] / 2 + 8, a["y"] + a["height"] / 2 + 8)
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=10)


def test_quick_actions_drag_verdicts_and_the_red_flicker():
    """A click shows a bar of quick actions; a drag lights the target green
    or red before the drop; a refused transformation shows its error and
    flickers the panel red; Delete on a needed node is refused."""
    from sympy import cos
    doc = Document(sin(x) + y * z, addons=[ADDON])
    with _served(doc) as page:
        # the quick actions
        _node(page, "Mul").click()
        bar = page.locator(".tree-quick")
        assert bar.is_visible()
        labels = [b.text_content() for b in bar.locator("button").all()]
        assert labels[:3] == ["Head", "Delete", "Wrap"] and "+ arg" in labels and "⋯" in labels
        assert bar.locator("button", has_text="Delete").is_enabled()
        # the x of sin(x) is needed there: Delete disabled, and the key refused with a flicker
        _node(page, "x").click()
        assert bar.locator("button", has_text="Delete").is_disabled()
        page.keyboard.press("Delete")
        page.wait_for_selector(".tree-panel.tree-flash", timeout=2000)
        page.wait_for_function("!document.querySelector('.se-error').hidden && document.querySelector('.se-error').textContent.includes('cannot be taken out of sin(x)')")
        assert doc.expr == sin(x) + y * z
        # a drag: red over a forbidden target (a leaf), green over an allowed one, and the move
        _drag(page, _node(page, "y"), _node(page, "z"))
        page.wait_for_selector(".tree-node.tree-drop-no", timeout=2000)
        page.mouse.up()
        page.wait_for_selector(".tree-panel.tree-flash", timeout=2000)
        page.wait_for_function("document.querySelector('.se-error').textContent.includes('leaf')")
        assert doc.expr == sin(x) + y * z
        _drag(page, _node(page, "sin"), _node(page, "Mul"))             # sin(x) becomes a factor of y*z
        page.wait_for_selector(".tree-node.tree-drop", timeout=2000)
        page.mouse.up()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'y*z*sin(x)'")
        assert doc.expr == y * z * sin(x)
        # a drop Python refuses (sin takes one argument) flickers too: the flash
        # of the drop before gone first, so that this one is not taken for it
        page.wait_for_selector(".tree-panel.tree-flash", state="detached", timeout=5000)
        page.wait_for_function("!document.querySelector('.sympy-editor').__sympyEditor.busy")   # a drop sent while busy waits
        page.evaluate("document.querySelector('.se-error').textContent = ''")
        # the flash lasts 600 ms, which a busy machine can see through: it is
        # recorded as it happens rather than looked for afterwards
        page.evaluate("""() => { window.__flashed = false;
            new MutationObserver(() => { if (document.querySelector('.tree-panel.tree-flash')) window.__flashed = true; })
                .observe(document.body, {subtree: true, attributes: true, attributeFilter: ['class']}); }""")
        _drag(page, _node(page, "z"), _node(page, "sin"))
        page.wait_for_selector(".tree-node.tree-drop, .tree-node.tree-drop-no", timeout=2000)   # over the target
        page.mouse.up()
        page.wait_for_function("window.__flashed", timeout=5000)
        page.wait_for_function("!document.querySelector('.se-error').hidden && document.querySelector('.se-error').textContent !== ''")
        assert doc.expr == y * z * sin(x)
        # the quick bar's Delete does delete
        _node(page, "z").click()
        page.locator(".tree-quick button", has_text="Delete").click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'y*sin(x)'")
        assert page.errors == []


def _gesture_page(p, doc, **ctx_args):
    """A touch browser on a page showing ``doc``'s tree, and the server behind
    it.  The gesture tests each want their own viewport, so they do not share
    the fixture above."""
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        browser = p.chromium.launch()
    except Exception as exc:
        srv.shutdown(); srv.server_close()
        pytest.skip(f"chromium not available: {exc}")
    ctx = browser.new_context(**ctx_args)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(srv.url)
    page.wait_for_selector(".se-addon-tree .tree-node", timeout=30000)
    # The panel sits under the formula, off the bottom of a phone-sized
    # window: fingers sent to a box that is not on the screen land nowhere.
    page.locator(".se-addon-tree .tree-scroll").scroll_into_view_if_needed()
    page.wait_for_timeout(100)
    return page, ctx, browser, srv


# Deep and wide enough that the drawing is bigger than the panel: something
# to zoom into, and somewhere to scroll to.
BUSHY = sin(x * y + z) ** 2 + Mul(x, y, z, evaluate=False) - sin(x) / (y + z)

DRAWN = """() => { const s = document.querySelector('.se-addon-tree .tree-svg');
    const b = s.getAttribute('viewBox').split(' ').map(Number);
    return {w: +s.getAttribute('width'), h: +s.getAttribute('height'),
            box: s.getAttribute('viewBox'), vw: b[2], vh: b[3]}; }"""
SCROLL = """() => { const s = document.querySelector('.se-addon-tree .tree-scroll');
    return {left: s.scrollLeft, top: s.scrollTop}; }"""


def test_two_fingers_pinch_the_tree():
    """Pinch to zoom, on a touch screen, as in the plot's picture - but a tree
    is a drawing rather than a pair of axes, so it scales evenly and keeps its
    own coordinates: the viewBox stays the layout's size and only the drawn
    size changes.  What is under the middle of the fingers stays there."""
    with playwright.sync_playwright() as p:
        page, ctx, browser, srv = _gesture_page(
            p, Document(BUSHY, addons=[ADDON]),
            has_touch=True, is_mobile=True, viewport={"width": 420, "height": 820})
        try:
            # one finger is still the browser's, to scroll the box with; two
            # come to the panel rather than magnifying the whole page
            assert page.evaluate("() => getComputedStyle(document.querySelector('.tree-scroll')).touchAction") == "pan-x pan-y"

            first = page.evaluate(DRAWN)
            assert abs(first["w"] - first["vw"]) <= 1 and abs(first["h"] - first["vh"]) <= 1   # life size

            cdp = ctx.new_cdp_session(page)
            box = page.locator(".se-addon-tree .tree-scroll").bounding_box()
            hold = (box["x"] + box["width"] * 0.5, box["y"] + min(box["height"], 260) / 2)
            # what the drawing has under the middle of the fingers, in its own
            # units: this is what a pinch has to keep where it is
            under = """([cx, cy]) => { const s = document.querySelector('.tree-scroll');
                const b = s.getBoundingClientRect(), z = +document.querySelector('.tree-svg').getAttribute('width');
                const k = z / +document.querySelector('.tree-svg').getAttribute('viewBox').split(' ')[2];
                return [(cx - b.left + s.scrollLeft) / k, (cy - b.top + s.scrollTop) / k]; }"""
            held = page.evaluate(under, list(hold))

            ids = [0]

            def pinch(d0, d1):
                ids[0] += 2                      # fresh ids: a reused one loses a finger
                a, b = ids[0], ids[0] + 1
                pt = lambda d, i: {"x": hold[0] + (-d if i == a else d), "y": hold[1], "id": i}
                cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [pt(d0, a)]})
                cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [pt(d0, a), pt(d0, b)]})
                for i in range(1, 11):
                    d = d0 + (d1 - d0) * i / 10.0
                    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [pt(d, a), pt(d, b)]})
                    page.wait_for_timeout(40)
                cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                page.wait_for_timeout(120)

            pinch(30, 120)                                   # apart: a closer look
            big = page.evaluate(DRAWN)
            assert big["w"] > first["w"] * 1.8, (first["w"], big["w"])
            assert big["box"] == first["box"]                # the layout is untouched
            assert abs(big["h"] / big["w"] - first["h"] / first["w"]) < 0.01   # evenly, not stretched
            now = page.evaluate(under, list(hold))
            assert abs(now[0] - held[0]) < 12 and abs(now[1] - held[1]) < 12, (held, now)

            pinch(120, 30)                                   # together: back out
            small = page.evaluate(DRAWN)
            assert small["w"] < big["w"] * 0.6, (big["w"], small["w"])
            assert small["box"] == first["box"]
            assert page.errors == []
        finally:
            browser.close(); srv.shutdown(); srv.server_close()


def test_two_fingers_drag_the_tree_along():
    """The middle of a pinch carries the point it started on, so two fingers
    moved together scroll the drawing without changing its size."""
    with playwright.sync_playwright() as p:
        page, ctx, browser, srv = _gesture_page(
            p, Document(BUSHY, addons=[ADDON]),
            has_touch=True, is_mobile=True, viewport={"width": 420, "height": 820})
        try:
            cdp = ctx.new_cdp_session(page)
            box = page.locator(".se-addon-tree .tree-scroll").bounding_box()
            cx, cy = box["x"] + box["width"] * 0.6, box["y"] + min(box["height"], 260) / 2
            # room to scroll into: start from a magnified tree
            page.evaluate("""() => { const s = document.querySelector('.tree-scroll');
                s.scrollLeft = s.scrollWidth / 3; }""")
            page.wait_for_timeout(60)
            before = page.evaluate(SCROLL)
            size = page.evaluate(DRAWN)

            pt = lambda dx, i: {"x": cx + dx + (-25 if i == 1 else 25), "y": cy, "id": i}
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [pt(0, 1)]})
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [pt(0, 1), pt(0, 2)]})
            for i in range(1, 11):
                dx = 8 * i                                   # both fingers to the right, unchanged apart
                cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [pt(dx, 1), pt(dx, 2)]})
                page.wait_for_timeout(40)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            page.wait_for_timeout(120)

            after = page.evaluate(SCROLL)
            assert after["left"] < before["left"] - 40, (before, after)   # the tree followed the fingers
            assert page.evaluate(DRAWN)["w"] == size["w"]                 # and did not change size
            assert page.errors == []
        finally:
            browser.close(); srv.shutdown(); srv.server_close()


def test_a_trackpad_pinch_zooms_the_tree_and_a_plain_wheel_scrolls_it():
    """A pinch on a laptop's trackpad reaches the page as a wheel event with
    ctrlKey set - that is how the browser reports it, and how it would magnify
    the whole page if nobody took it.  A plain wheel is left alone: over a tall
    drawing a wheel should scroll."""
    with playwright.sync_playwright() as p:
        page, ctx, browser, srv = _gesture_page(p, Document(BUSHY, addons=[ADDON]))
        try:
            wheel = """([n, dy, ctrl]) => { const s = document.querySelector('.tree-scroll');
                const b = s.getBoundingClientRect();
                for (let i = 0; i < n; i++) s.dispatchEvent(new WheelEvent('wheel',
                    {deltaY: dy, ctrlKey: ctrl, bubbles: true, cancelable: true,
                     clientX: b.left + b.width / 2, clientY: b.top + b.height / 2}));
                return null; }"""
            first = page.evaluate(DRAWN)
            page.evaluate(wheel, [6, -100, True])            # a pinch out on the trackpad
            page.wait_for_timeout(120)
            closer = page.evaluate(DRAWN)
            assert closer["w"] > first["w"] * 1.4, (first["w"], closer["w"])
            assert closer["box"] == first["box"]

            page.evaluate(wheel, [6, 100, True])             # and back in
            page.wait_for_timeout(120)
            assert page.evaluate(DRAWN)["w"] < closer["w"] * 0.8

            size = page.evaluate(DRAWN)
            page.evaluate(wheel, [3, 100, False])            # a plain wheel is not a zoom
            page.wait_for_timeout(120)
            assert page.evaluate(DRAWN)["w"] == size["w"]
            assert page.errors == []
        finally:
            browser.close(); srv.shutdown(); srv.server_close()


def test_the_mouse_drags_the_tree_from_empty_space():
    """With a mouse there is no pinch, and the scrollbars alone are a poor way
    about a drawing wider than the panel.  Empty space is where it is pushed
    along from; a press on a node still starts the drag that moves it, and a
    double-click on empty space gives the tree back its life size."""
    with playwright.sync_playwright() as p:
        page, ctx, browser, srv = _gesture_page(p, Document(BUSHY, addons=[ADDON]))
        try:
            # magnified first: at life size a tree this wide fits a desktop
            # window, and a drawing with nowhere to go cannot be pushed along
            page.evaluate("""() => { const s = document.querySelector('.tree-scroll');
                const b = s.getBoundingClientRect();
                for (let i = 0; i < 8; i++) s.dispatchEvent(new WheelEvent('wheel',
                    {deltaY: -100, ctrlKey: true, bubbles: true, cancelable: true,
                     clientX: b.left + b.width / 2, clientY: b.top + 40})); }""")
            page.wait_for_timeout(150)
            page.evaluate("""() => { const s = document.querySelector('.tree-scroll');
                s.scrollLeft = s.scrollWidth / 3; }""")
            page.wait_for_timeout(60)
            before = page.evaluate(SCROLL)
            assert before["left"] > 40, before
            box = page.locator(".se-addon-tree .tree-scroll").bounding_box()
            # the bottom strip of the box: below the deepest row, so empty
            y = box["y"] + box["height"] - 4
            page.mouse.move(box["x"] + box["width"] * 0.7, y)
            page.mouse.down()
            for i in range(1, 9):
                page.mouse.move(box["x"] + box["width"] * 0.7 + 10 * i, y)
            page.mouse.up()
            after = page.evaluate(SCROLL)
            assert after["left"] < before["left"] - 40, (before, after)

            # a double-click on empty space is life size again
            big = page.evaluate(DRAWN)
            page.mouse.dblclick(box["x"] + box["width"] * 0.7, y)
            page.wait_for_timeout(120)
            back = page.evaluate(DRAWN)
            assert back["w"] < big["w"] and abs(back["w"] - back["vw"]) <= 1
            assert page.errors == []
        finally:
            browser.close(); srv.shutdown(); srv.server_close()


def test_switching_the_tree_off_takes_its_page_listener_away(page_and_doc):
    """The panel listens on the whole page - for pointerdown, to close its
    menus, and for click, to tell a double click; switched off, it must stop -
    or every off/on cycle leaves one more listener, and a dead panel's,
    behind."""
    page, doc = page_and_doc
    page.add_init_script("""(() => {
      const add = document.addEventListener.bind(document), rem = document.removeEventListener.bind(document);
      const live = new Set(), ours = (t) => t === 'pointerdown' || t === 'click';
      window.__listeners = () => live.size;
      document.addEventListener = (t, f, o) => { if (ours(t)) live.add(f); return add(t, f, o); };
      document.removeEventListener = (t, f, o) => { if (ours(t)) live.delete(f); return rem(t, f, o); };
    })()""")
    page.reload()
    page.wait_for_selector(".se-addon-tree .tree-node", timeout=30000)
    before = page.evaluate("window.__listeners()")
    ed = "document.querySelector('.sympy-editor').__sympyEditor"
    for _ in range(3):
        page.evaluate(ed + ".send({action: 'addons', disable: ['tree']})")
        page.wait_for_selector(".se-addon-tree", state="detached", timeout=10000)
        page.evaluate(ed + ".send({action: 'addons', enable: ['tree']})")
        page.wait_for_selector(".se-addon-tree .tree-node", timeout=10000)
    assert page.evaluate("window.__listeners()") == before
    assert page.errors == []


# What the page sends to Python, as it sends it: a gesture that must change
# nothing is one that sends nothing.
RECORD = """() => { const ed = document.querySelector('.sympy-editor').__sympyEditor;
    window.__sent = []; const send = ed.backend.send.bind(ed.backend);
    ed.backend.send = function (m, r) { window.__sent.push(m); return send(m, r); }; }"""
CHANGES = "() => window.__sent.filter(m => m.action === 'addon').map(m => m.method)"
IDLE = "!document.querySelector('.sympy-editor').__sympyEditor.busy"


def _wide_sum():
    """F(a) + G(b) + ... : a row of nodes much wider than a phone."""
    from sympy import Function
    names, args = "F G H K L M N P Q R S T".split(), symbols("a b c d e g h k m n p q")
    return sum(Function(n)(s) for n, s in zip(names, args))


def test_a_finger_that_scrolls_the_tree_from_a_node_moves_nothing():
    """A finger put down on a node and drawn sideways scrolls the tree - and
    used to edit the expression as well: the press started a drag as a
    mouse's does, the browser took the gesture for its scroll and sent
    ``pointercancel``, and the cancel was handled as a drop on whatever node
    the finger had last passed over (``F(a) + G(b) + ...``, a finger on ``K``
    drawn to the left, became ``... + H(c, K(d)) + ...``).  A finger does not
    drag nodes: it scrolls, as the guide says."""
    doc = Document(_wide_sum(), addons=[ADDON])
    with playwright.sync_playwright() as p:
        page, ctx, browser, srv = _gesture_page(p, doc, has_touch=True, viewport={"width": 390, "height": 700})
        try:
            page.wait_for_function(IDLE)
            page.evaluate(RECORD)
            before, steps = doc.expr, len(doc.history_labels()["actions"])
            # the heads in the row under the root, left to right; the finger
            # goes down on the last that is wholly on the screen and is drawn
            # over the ones before it
            heads = page.evaluate("""() => [...document.querySelectorAll('.tree-node.tree-head-node')]
                .filter(g => g.getAttribute('data-key').indexOf('/') < 0 && g.getAttribute('data-key') !== '')
                .map(g => { const r = g.getBoundingClientRect();
                            return {left: r.left, right: r.right, y: (r.top + r.bottom) / 2}; })
                .filter(n => n.left > 0 && n.right < innerWidth - 4).sort((a, b) => a.left - b.left)""")
            assert len(heads) >= 4, heads
            start = heads[-1]
            x0, y0 = start["left"] + 4, start["y"]
            assert x0 > 220, heads                               # room for the finger to travel
            # what the finger's events are, and whether a node was ever
            # shown as dragged or as a place to drop - while it happens,
            # since none of it is left to see afterwards
            page.evaluate("""() => { window.__pointer = []; window.__dragged = false;
                const svg = document.querySelector('.tree-svg');
                ['pointerdown', 'pointermove', 'pointerup', 'pointercancel'].forEach(t =>
                    svg.addEventListener(t, e => window.__pointer.push(t + ':' + e.pointerType), true));
                new MutationObserver(() => {
                    if (svg.querySelector('.tree-dragging, .tree-drop, .tree-drop-no')) window.__dragged = true;
                }).observe(svg, {subtree: true, attributes: true, attributeFilter: ['class']}); }""")
            cdp = ctx.new_cdp_session(page)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
            for i in range(1, 41):
                cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x0 - 5 * i, "y": y0}]})
                page.wait_for_timeout(10)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            page.wait_for_timeout(600)
            page.wait_for_function(IDLE)

            seen = page.evaluate("window.__pointer")
            assert "pointerdown:touch" in seen and "pointercancel:touch" in seen, seen   # the gesture this is about
            assert page.evaluate(SCROLL)["left"] > 40                                   # the tree scrolled
            assert page.evaluate(CHANGES) == []                                         # and that is all it did
            assert not page.evaluate("window.__dragged")                                # no node was picked up on the way
            assert doc.expr == before and len(doc.history_labels()["actions"]) == steps
            assert page.locator(".tree-node.tree-dragging, .tree-node.tree-drop, .tree-node.tree-drop-no").count() == 0
            assert page.errors == []
        finally:
            browser.close(); srv.shutdown(); srv.server_close()


def test_a_drag_that_is_cancelled_drops_nothing():
    """``pointercancel`` went to the same function as ``pointerup``, which
    lets go of the subtree where it is: a drag the browser took away - a pen
    whose stroke became a scroll, a window that lost the pointer - was
    committed as a drop on the node under it.  A cancelled gesture is given
    up, whatever kind of pointer made it."""
    doc = Document(sin(x) + y * z, addons=[ADDON])
    with _served(doc) as page:
        page.wait_for_function(IDLE)
        page.evaluate(RECORD)
        lit = page.evaluate("""() => {
            const node = (src) => [...document.querySelectorAll('.tree-node')].find(g => g.querySelector('title').textContent === src);
            const at = (g) => { const r = g.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
            const from = node('sin(x)'), to = node('y*z'), a = at(from), b = at(to);
            const send = (type, target, p) => target.dispatchEvent(new PointerEvent(type, {pointerId: 7, pointerType: 'pen',
                isPrimary: true, button: type === 'pointermove' ? -1 : 0, buttons: 1, clientX: p[0], clientY: p[1],
                bubbles: true, cancelable: true}));
            send('pointerdown', from, a);
            send('pointermove', from, [a[0] + 8, a[1] + 8]);
            send('pointermove', to, b);
            const lit = to.classList.contains('tree-drop') && from.classList.contains('tree-dragging');
            send('pointercancel', to, b);
            return lit; }""")
        assert lit                                                   # the drag was under way, over a node that would take it
        page.wait_for_timeout(500)
        page.wait_for_function(IDLE)
        assert page.evaluate(CHANGES) == []
        assert doc.expr == sin(x) + y * z
        assert page.locator(".tree-node.tree-dragging, .tree-node.tree-drop, .tree-node.tree-drop-no").count() == 0
        assert page.errors == []


def test_the_first_double_click_on_a_node_opens_the_field(page_and_doc):
    """On a fresh page, with nothing selected, a double-click on a node did
    nothing: its first click selects in the formula, the formula's box grows
    for the selection's tools and the panel moves down, so the second click
    - the pointer has not moved - lands beside the node, and the browser's
    ``dblclick`` with it.  Later double-clicks worked, the panel having
    moved already, which is how the test above never saw it: it clicks a
    node first.  The panel tells a double click by itself now: a second
    click soon after the first, where the first was."""
    page, doc = page_and_doc
    assert page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.selected") is None
    _node(page, "y").dblclick()
    field = page.locator(".tree-edit")
    page.wait_for_timeout(300)                     # what closes the field again would have by now
    assert field.is_visible() and field.input_value() == "y"
    assert page.evaluate("document.activeElement === document.querySelector('.tree-edit')")
    # the field is where the node is now, not where it was
    box, node = field.bounding_box(), _node(page, "y").bounding_box()
    assert abs(box["x"] - node["x"]) < 3 and abs(box["y"] - node["y"]) < 3, (box, node)
    field.fill("2")
    field.press("Enter")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x + 2*z'")
    assert doc.expr == x + 2 * z
    # two clicks far apart in time are two clicks: the second selects again
    _node(page, "z").click()
    page.wait_for_timeout(700)
    _node(page, "z").click()
    page.wait_for_timeout(200)
    assert not field.is_visible() and page.locator(".tree-quick").is_visible()
    # and so are two clicks on two nodes, however quick
    a, b = _node(page, "x").bounding_box(), _node(page, "z").bounding_box()
    page.mouse.click(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
    page.wait_for_timeout(200)
    assert not field.is_visible()
    assert page.locator(".tree-node.tree-selected title").text_content() == "z"
    assert page.errors == []


def test_a_double_tap_edits_the_node_that_was_tapped():
    """On a phone the formula's box grows by more than a row of the tree when
    the first tap selects, so the second tap found another node under the
    finger - the parent, ``Mul``, for a tap on ``y`` - and the field opened
    for that one: the head of the product, where the value of a leaf was
    asked for.  The node is the one the first tap was on."""
    doc = Document(x + y * z + sin(x), addons=[ADDON])
    with playwright.sync_playwright() as p:
        page, ctx, browser, srv = _gesture_page(
            p, doc, has_touch=True, is_mobile=True, viewport={"width": 420, "height": 820})
        try:
            page.wait_for_function(IDLE)
            page.evaluate(RECORD)
            cdp = ctx.new_cdp_session(page)
            box = _node(page, "y").bounding_box()
            at = [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2]
            for dx in (0, 7):                                    # a finger does not land twice on one spot
                cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": at[0] + dx, "y": at[1]}]})
                cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                page.wait_for_timeout(120)
            page.wait_for_timeout(300)
            field = page.locator(".tree-edit")
            assert field.is_visible() and field.input_value() == "y"
            # and nothing else was done with the second tap, whatever it landed on
            assert page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.selected") is not None
            assert doc.get(page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.selected")) == y
            assert page.evaluate(CHANGES) == []
            assert page.errors == []
        finally:
            browser.close(); srv.shutdown(); srv.server_close()


def test_a_field_left_as_it_was_is_not_a_step(page_and_doc):
    """Looking at a node and clicking away added a step to the history each
    time - "Tree: /1/0 → y" over and over, the expression the same, Undo lit
    with nothing to undo: the field sent its text when it lost the focus
    without asking whether it had changed.  The editor's own field does
    nothing then, and neither does this one."""
    page, doc = page_and_doc
    page.evaluate(RECORD)
    steps = len(doc.history_labels()["actions"])
    field = page.locator(".tree-edit")
    for label in ("y", "Mul"):                                   # a leaf's value, an inner node's head
        for leave in ("away", "Enter"):
            _node(page, label).dblclick()
            field.wait_for(state="visible", timeout=3000)
            assert field.input_value() == label
            if leave == "away":
                page.locator(".tree-hint").click()               # elsewhere: the field loses the focus
            else:
                field.press("Enter")
            field.wait_for(state="hidden", timeout=3000)
            page.wait_for_timeout(300)
            page.wait_for_function(IDLE)
    assert page.evaluate(CHANGES) == []
    assert len(doc.history_labels()["actions"]) == steps and doc.expr == x + y * z
    assert not page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.state.can_undo")
    # spaces around the same text are the same text; another text is a step
    _node(page, "y").dblclick()
    field.fill("  y ")
    page.locator(".tree-hint").click()
    field.wait_for(state="hidden", timeout=3000)
    page.wait_for_timeout(300)
    assert page.evaluate(CHANGES) == []
    _node(page, "y").dblclick()
    field.fill("2")
    page.locator(".tree-hint").click()
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x + 2*z'")
    assert page.evaluate(CHANGES) == ["replace"] and doc.expr == x + 2 * z
    assert page.errors == []


def test_a_piece_of_the_formula_with_no_node_stands_for_the_node_around_it():
    """The formula has pieces the tree has no node for - the ``2`` of
    ``x - 2*y`` is part of a ``-2`` there, the product after the minus is
    nothing at all.  With one of them selected the panel marked the root, or
    whichever node had been clicked last, and its fields acted on that:
    ``sin`` typed in "wrap in…" with the ``2`` selected gave
    ``sin(x - 2*y)``.  The node is the one around the piece, as the guide
    says: the nearest piece above it that the tree does have."""
    doc = Document(x - 2 * y, addons=[ADDON])
    with _served(doc) as page:
        ed = "document.querySelector('.sympy-editor').__sympyEditor"
        marked = "[...document.querySelectorAll('.tree-node.tree-selected title')].map(t => t.textContent)"
        term = [p for p in doc.snapshot()["nodes"] if doc.get(p) == -2 * y][0]
        assert doc.get(term + "/neg/0") == 2
        # a node clicked before must not be what the fields act on afterwards
        _node(page, "x").click()
        page.keyboard.press("Escape")
        for piece in (term + "/neg/0", term + "/neg", term):
            page.evaluate("p => %s.select(p)" % ed, piece)
            page.wait_for_function("%s.length === 1 && %s[0] === '-2*y'" % (marked, marked), timeout=3000)
        page.evaluate("p => %s.select(p)" % ed, term + "/neg/1")           # the y has a node of its own
        page.wait_for_function("%s[0] === 'y'" % marked, timeout=3000)
        page.evaluate("p => %s.select(p)" % ed, term + "/neg/0")
        page.locator(".tree-field[placeholder^='wrap']").fill("sin")
        page.locator(".tree-field[placeholder^='wrap']").press("Enter")
        page.wait_for_function("document.querySelector('.se-source').textContent === 'x - sin(2*y)'")
        assert doc.expr == x + sin(-2 * y)
        assert page.errors == []

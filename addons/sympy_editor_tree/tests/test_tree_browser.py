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
        page.wait_for_selector(".se-addon-tree .tree-node", timeout=30000)
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
        # a drop Python refuses (sin takes one argument) flickers too
        _drag(page, _node(page, "z"), _node(page, "sin"))
        page.mouse.up()
        page.wait_for_selector(".tree-panel.tree-flash", timeout=5000)
        assert doc.expr == y * z * sin(x)
        # the quick bar's Delete does delete
        _node(page, "z").click()
        page.locator(".tree-quick button", has_text="Delete").click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'y*sin(x)'")
        assert errors == []
        browser.close()
    srv.shutdown()
    srv.server_close()


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

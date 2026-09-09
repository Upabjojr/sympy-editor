"""The plot panel in a real browser: the range fields, the refusal to guess
values, the zoom, the guide.  Needs Playwright with Chromium and the KaTeX
and Plotly CDNs (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_plot import ADDON, PLOTLY_JS  # noqa: E402

x, a = symbols("x a")


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not (_online(default_urls()["katexJs"]) and _online(PLOTLY_JS)), reason="a CDN is not reachable")


def test_fields_values_zoom_and_guide():
    doc = Document(a * sin(x), addons=[ADDON])
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
        # two free symbols: no curve, a message naming the one that needs a value
        page.wait_for_selector(".se-addon-plot .plot-note.error", timeout=15000)
        note = page.locator(".se-addon-plot .plot-note").inner_text()
        assert "2 free symbols" in note and "give a value to x" in note      # a is first alphabetically: on the axis
        assert page.locator(".plot-area *").count() == 0
        assert page.locator(".plot-sliders label.plot-unset").count() == 1
        # the range fields are text: selectable like any text
        frm = page.locator(".se-addon-plot .plot-bar .plot-num").first
        frm.click()
        page.keyboard.press("Control+a")
        assert page.evaluate("(e => [e.selectionStart, e.selectionEnd])(document.querySelector('.se-addon-plot .plot-bar .plot-num'))") == [0, 2]
        # the variable can be changed, and a value given to the other one: then it draws
        page.locator(".se-addon-plot select").select_option("x")
        page.wait_for_function("document.querySelector('.plot-sliders label') && document.querySelector('.plot-sliders label').getAttribute('data-sym') === 'a'")
        page.locator(".plot-sliders .plot-value").fill("2")
        page.wait_for_selector(".plot-area svg.main-svg, .plot-area svg.plot-svg", timeout=30000)
        page.wait_for_function("document.querySelector('.plot-shown').textContent.includes('-6')")
        assert page.locator(".plot-sliders label.plot-unset").count() == 0
        # a zoom in the picture: the fields follow, the readout says what is shown
        page.wait_for_function("document.querySelector('.plot-area')._seRelayout === true")   # Plotly's event API is up
        page.evaluate("Plotly.relayout(document.querySelector('.plot-area'), {'xaxis.range': [0, 1]})")
        page.wait_for_function("document.querySelector('.se-addon-plot .plot-bar .plot-num').value === '0'")
        assert page.locator(".se-addon-plot .plot-bar .plot-num").nth(1).input_value() == "1"
        page.wait_for_function("document.querySelector('.plot-shown').textContent === 'visible range: 0 … 1'")
        # the gestures a person uses: a box dragged in the picture, and the wheel
        box = page.locator(".plot-area .nsewdrag").first.bounding_box()
        x0, x1, ym = box["x"] + box["width"] * 0.3, box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5
        page.mouse.move(x0, ym); page.mouse.down(); page.mouse.move(x0 + 5, ym + 5); page.mouse.move(x1, ym + 40, steps=8); page.mouse.up()
        near = "(() => { const v = parseFloat(document.querySelector('.se-addon-plot .plot-bar .plot-num').value); return Math.abs(v - %s) < 0.06; })()"
        page.wait_for_function(near % 0.3)                                    # the box's left edge, 30% of [0, 1]
        shown = page.locator(".plot-shown").inner_text()
        assert shown.startswith("visible range: 0.2") or shown.startswith("visible range: 0.3")
        page.mouse.move(box["x"] + box["width"] * 0.5, ym)
        for _ in range(3):                                                     # three notches: zooms in around the pointer
            page.mouse.wheel(0, -120)
            page.wait_for_timeout(200)
        page.wait_for_function("parseFloat(document.querySelector('.se-addon-plot .plot-bar .plot-num').value) > 0.31")
        assert page.locator(".plot-shown").inner_text() != shown
        page.mouse.dblclick(x0, ym)                                            # back to the whole span
        page.wait_for_function("document.querySelector('.plot-shown').textContent === 'visible range: -6 … 6'")
        # a plot cleared for a missing value and drawn again still follows a zoom
        # (Plotly's purge took the listener away once, and the label stayed at -6 … 6)
        page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.send({action: 'set', src: 'a*sin(x) + b'})")
        page.wait_for_selector(".se-addon-plot .plot-note.error", timeout=15000)
        page.locator('.plot-sliders [data-sym="b"] .plot-value').fill("1")
        page.wait_for_selector(".plot-area svg.main-svg", timeout=30000)
        page.wait_for_function("document.querySelector('.plot-area')._seRelayout === true")
        box = page.locator(".plot-area .nsewdrag").first.bounding_box()
        x0, x1, ym = box["x"] + box["width"] * 0.3, box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5
        page.mouse.move(x0, ym); page.mouse.down(); page.mouse.move(x0 + 5, ym + 5); page.mouse.move(x1, ym + 40, steps=8); page.mouse.up()
        page.wait_for_function("document.querySelector('.plot-shown').textContent !== 'visible range: -6 … 6'")
        assert page.locator(".se-addon-plot .plot-bar .plot-num").first.input_value().startswith("-2.")
        # the guide
        page.locator(".se-addon-plot .se-addon-help").click()
        assert "zoom" in page.locator(".se-help-view").inner_text().lower()
        page.keyboard.press("Escape")
        assert errors == []
        browser.close()
    srv.shutdown()
    srv.server_close()


def test_two_fingers_pinch_the_axis():
    """Pinch to zoom, on a touch screen.  Plotly reads a two-finger drag as
    the box zoom it uses for a mouse, which lands the range wherever the
    fingers finished rather than around what they were holding; the panel
    takes the gesture first (a capture listener that stops it going on) and
    scales the span itself, keeping the point under the middle of the pinch
    where it is."""
    doc = Document(sin(x), addons=[ADDON])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(has_touch=True, is_mobile=True, viewport={"width": 420, "height": 820})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-plot .plot-area", timeout=30000)
            page.wait_for_function("() => { const a = document.querySelector('.plot-area'); return a && a._fullLayout; }", timeout=60000)
            # one finger still scrolls the page; the pinch is the panel's
            assert page.evaluate("() => getComputedStyle(document.querySelector('.plot-area')).touchAction") == "pan-y"

            cdp = ctx.new_cdp_session(page)
            box = page.locator(".plot-area").bounding_box()
            cy = box["y"] + box["height"] / 2
            hold = box["x"] + box["width"] * 0.62        # off-centre, both fingers still on the picture
            span = lambda: page.evaluate("() => { const r = document.querySelector('.plot-area')._fullLayout.xaxis.range; return r[1] - r[0]; }")
            at_hold = lambda: page.evaluate("""(cx) => { const a = document.querySelector('.plot-area');
                const b = a.getBoundingClientRect(); const ax = a._fullLayout.xaxis;
                const f = (cx - (b.left + ax._offset)) / ax._length;
                return ax.range[0] + f * (ax.range[1] - ax.range[0]); }""", hold)

            ids = [0]

            def pinch(d0, d1):
                ids[0] += 2                              # fresh ids: a gesture that reuses them loses a finger
                a, b = ids[0], ids[0] + 1
                pt = lambda d, i: {"x": hold + (-d if i == a else d), "y": cy, "id": i}
                cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [pt(d0, a)]})
                cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [pt(d0, a), pt(d0, b)]})
                for i in range(1, 11):
                    d = d0 + (d1 - d0) * i / 10.0
                    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [pt(d, a), pt(d, b)]})
                    page.wait_for_timeout(40)
                cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                page.wait_for_timeout(1200)

            wide, held = span(), at_hold()
            pinch(30, 110)                               # fingers apart: a closer look
            close = span()
            assert close < wide * 0.6, (wide, close)
            assert abs(at_hold() - held) < close * 0.06, (held, at_hold())   # what was held stayed put
            # the fields and the label follow the pinch, as they do a wheel zoom
            assert abs(float(page.locator(".se-addon-plot .plot-bar .plot-num").first.input_value())
                       - page.evaluate("() => document.querySelector('.plot-area')._fullLayout.xaxis.range[0]")) < 0.2
            assert page.locator(".plot-shown").inner_text().startswith("visible range:")

            pinch(110, 30)                               # fingers together: back out
            assert span() > close * 1.8, (close, span())
            assert abs(at_hold() - held) < span() * 0.06, (held, at_hold())
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_one_finger_drags_the_plot_along():
    """A finger on a picture is expected to push it along; Plotly would draw
    a zoom box instead.  Sideways is the panel's - the span moves with the
    finger, at the same width - and up or down is left to the browser, which
    scrolls the page as it does everywhere else."""
    doc = Document(sin(x), addons=[ADDON])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(has_touch=True, is_mobile=True, viewport={"width": 420, "height": 820})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-plot .plot-area", timeout=30000)
            page.wait_for_function("() => { const a = document.querySelector('.plot-area'); return a && a._fullLayout; }", timeout=60000)
            cdp = ctx.new_cdp_session(page)
            box = page.locator(".plot-area").bounding_box()
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            rng = lambda: page.evaluate("() => document.querySelector('.plot-area')._fullLayout.xaxis.range.slice()")
            ids = [40]

            def swipe(dx, dy, steps=10):
                ids[0] += 1
                i = ids[0]
                cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": cx, "y": cy, "id": i}]})
                for k in range(1, steps + 1):
                    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints":
                        [{"x": cx + dx * k / steps, "y": cy + dy * k / steps, "id": i}]})
                    page.wait_for_timeout(40)
                cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                page.wait_for_timeout(1400)

            start = rng()
            wide = start[1] - start[0]
            swipe(-110, 0)                            # push the picture left: further along the axis
            moved = rng()
            assert moved[0] > start[0] + wide * 0.15, (start, moved)
            assert abs((moved[1] - moved[0]) - wide) < wide * 0.02, (start, moved)   # scrolled, not zoomed
            swipe(110, 0)
            assert abs(rng()[0] - start[0]) < wide * 0.1, (start, rng())             # and back
            here = rng()
            swipe(0, -160)                            # straight down: the page's gesture
            assert abs(rng()[0] - here[0]) < wide * 0.05, (here, rng())
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()

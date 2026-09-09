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
        # the gestures a person uses: a drag moves the picture, the wheel zooms
        box = page.locator(".plot-area .nsewdrag").first.bounding_box()
        x0, ym = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5
        span = page.evaluate("() => { const ax = document.querySelector('.plot-area')._fullLayout.xaxis; return [ax._length, ax.range[1] - ax.range[0]]; }")
        by = box["width"] * 0.2
        page.mouse.move(x0, ym); page.mouse.down(); page.mouse.move(x0 - by, ym, steps=10); page.mouse.up()
        # pushed left by that many pixels, and the picture goes with it: the
        # left end moves right by exactly what those pixels are worth
        want = by / span[0] * span[1]
        near = "(() => { const v = parseFloat(document.querySelector('.se-addon-plot .plot-bar .plot-num').value); return Math.abs(v - %s) < 0.06; })()"
        page.wait_for_function(near % round(want, 4))
        assert page.locator(".plot-shown").inner_text().startswith("visible range:")
        shown = page.locator(".plot-shown").inner_text()
        wide = page.evaluate("() => { const r = document.querySelector('.plot-area')._fullLayout.xaxis.range; return r[1] - r[0]; }")
        page.mouse.move(box["x"] + box["width"] * 0.5, ym)
        for _ in range(3):                                                     # three notches: zooms in around the pointer
            page.mouse.wheel(0, -120)
            page.wait_for_timeout(200)
        page.wait_for_function("(w) => { const r = document.querySelector('.plot-area')._fullLayout.xaxis.range; return (r[1] - r[0]) < w * 0.9; }", arg=wide)
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
        x0, ym = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5
        page.mouse.move(x0, ym); page.mouse.down(); page.mouse.move(x0 - box["width"] * 0.25, ym, steps=10); page.mouse.up()
        page.wait_for_function("document.querySelector('.plot-shown').textContent !== 'visible range: -6 … 6'")
        assert float(page.locator(".se-addon-plot .plot-bar .plot-num").first.input_value()) > -6
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
            # every gesture on the picture is the panel's: nothing is left for
            # the browser to scroll or magnify over it
            assert page.evaluate("() => getComputedStyle(document.querySelector('.plot-area')).touchAction") == "none"

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

            height = lambda: page.evaluate("() => { const r = document.querySelector('.plot-area')._fullLayout.yaxis.range; return r[1] - r[0]; }")
            wide, held, tall = span(), at_hold(), height()
            pinch(30, 110)                               # apart, sideways: a closer look along the axis
            close = span()
            assert close < wide * 0.6, (wide, close)
            assert abs(at_hold() - held) < close * 0.06, (held, at_hold())   # what was held stayed put
            # fingers that barely separate up the screen say nothing about the
            # height, so a sideways pinch leaves it alone
            assert abs(height() - tall) < tall * 0.2, (tall, height())
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
    a zoom box instead.  Both directions move it, at the size it has: the
    span sideways, the height up and down."""
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

            yrng = lambda: page.evaluate("() => document.querySelector('.plot-area')._fullLayout.yaxis.range.slice()")
            start, ystart = rng(), yrng()
            wide, tall = start[1] - start[0], ystart[1] - ystart[0]
            swipe(-110, 0)                            # push the picture left: further along the axis
            moved = rng()
            assert moved[0] > start[0] + wide * 0.15, (start, moved)
            assert abs((moved[1] - moved[0]) - wide) < wide * 0.02, (start, moved)   # scrolled, not zoomed
            swipe(110, 0)
            assert abs(rng()[0] - start[0]) < wide * 0.1, (start, rng())             # and back
            # up and down moves the picture too: pushed up, what was below it
            # comes into view, which is lower down the axis
            here = yrng()
            swipe(0, -90)
            after = yrng()
            assert after[0] < here[0] - tall * 0.05, (here, after)
            assert abs((after[1] - after[0]) - tall) < tall * 0.03, (here, after)    # moved, not zoomed
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_trackpad_pinch_zooms_both_axes():
    """A pinch on a laptop's trackpad reaches the page as a wheel event with
    ctrlKey set - that is how the browser reports it, and how it would zoom
    the page if nobody took it.  Plotly's wheel zoom wants a plain wheel and
    ignores it, so the panel takes it and zooms both axes about the pointer."""
    doc = Document(sin(x), addons=[ADDON])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-plot .plot-area", timeout=30000)
            page.wait_for_function("() => { const a = document.querySelector('.plot-area'); return a && a._fullLayout; }", timeout=60000)
            axis = lambda name: page.evaluate("(n) => { const r = document.querySelector('.plot-area')._fullLayout[n].range; return r[1] - r[0]; }", name)

            def trackpad(delta, times=4):
                page.evaluate("""([dy, n]) => { const el = document.querySelector('.plot-area');
                    const r = el.getBoundingClientRect();
                    for (let i = 0; i < n; i++) el.dispatchEvent(new WheelEvent('wheel',
                        {deltaY: dy, deltaMode: 0, ctrlKey: true, bubbles: true, cancelable: true,
                         clientX: r.left + r.width / 2, clientY: r.top + r.height / 2})); }""", [delta, times])
                page.wait_for_timeout(1200)

            wide, tall = axis("xaxis"), axis("yaxis")
            trackpad(-120)                                   # towards you: a closer look
            assert axis("xaxis") < wide * 0.85, (wide, axis("xaxis"))
            assert axis("yaxis") < tall * 0.85, (tall, axis("yaxis"))
            close = axis("xaxis")
            trackpad(120)                                    # and back out
            assert axis("xaxis") > close * 1.15, (close, axis("xaxis"))
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()

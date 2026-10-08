"""The plot panel in a real browser: the range fields, the refusal to guess
values, the zoom, the guide.  Needs Playwright with Chromium and the KaTeX
and Plotly CDNs (skipped otherwise)."""
import math
import sys
import threading
import time
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from sympy import Symbol, cos, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_plot import ADDON, PLOTLY_JS  # noqa: E402

x, y, a = symbols("x y a")

#: The editor of the page, and the picture's element.
ED = "document.querySelector('.sympy-editor').__sympyEditor"
AREA = "document.querySelector('.plot-area')"


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not (_online(default_urls()["katexJs"]) and _online(PLOTLY_JS)), reason="a CDN is not reachable")



#: Where the picture sits below the plot bar, in pixels: a wobble changes it,
#: page scrolling does not.
LAYOUT = """(() => { const a = document.querySelector('.plot-area').getBoundingClientRect(),
    b = document.querySelector('.se-addon-plot .plot-bar').getBoundingClientRect();
    return Math.round(a.top - b.top); })()"""

def _launch(p):
    """Chromium, or a skip when it is not installed - as every other
    browser module does; an error here said nothing about the plot."""
    try:
        return p.chromium.launch()
    except Exception as exc:
        pytest.skip(f"chromium not available: {exc}")


def test_fields_values_zoom_and_guide():
    doc = Document(a * sin(x), addons=[ADDON])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            browser = _launch(p)
            try:
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
                # and every number field - the range's, a slider's value - asks for the
                # ordinary keyboard: the phones' decimal and numeric pads have no
                # minus sign, and "from" is negative to begin with
                modes = page.evaluate("Array.from(document.querySelectorAll('.se-addon-plot .plot-num')).map(e => e.getAttribute('inputmode'))")
                assert len(modes) >= 3 and all(m in (None, "text") for m in modes), modes
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
                assert page.locator(".se-addon-plot .plot-bar .plot-num").first.input_value() == "-6"
                assert page.locator(".plot-sliders label.plot-unset").count() == 0
                # a zoom in the picture: the fields follow
                page.wait_for_function("document.querySelector('.plot-area')._seRelayout === true")   # Plotly's event API is up
                page.evaluate("Plotly.relayout(document.querySelector('.plot-area'), {'xaxis.range': [0, 1]})")
                page.wait_for_function("document.querySelector('.se-addon-plot .plot-bar .plot-num').value === '0'")
                assert page.locator(".se-addon-plot .plot-bar .plot-num").nth(1).input_value() == "1"
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
                fields = lambda: [page.locator(".se-addon-plot .plot-bar .plot-num").nth(i).input_value() for i in (0, 1)]
                shown = fields()
                lay = page.evaluate(LAYOUT)
                wide = page.evaluate("() => { const r = document.querySelector('.plot-area')._fullLayout.xaxis.range; return r[1] - r[0]; }")
                page.mouse.move(box["x"] + box["width"] * 0.5, ym)
                for _ in range(3):                                                     # three notches: zooms in around the pointer
                    page.mouse.wheel(0, -120)
                    page.wait_for_timeout(200)
                page.wait_for_function("(w) => { const r = document.querySelector('.plot-area')._fullLayout.xaxis.range; return (r[1] - r[0]) < w * 0.9; }", arg=wide)
                page.wait_for_function("(s) => document.querySelector('.se-addon-plot .plot-bar .plot-num').value !== s", arg=shown[0])
                assert fields() != shown
                assert page.evaluate(LAYOUT) == lay                                   # the zoom moved nothing on the page
                page.mouse.dblclick(x0, ym)                                            # back to the whole span
                page.wait_for_function("(() => { const f = document.querySelectorAll('.se-addon-plot .plot-bar .plot-num'); return f[0].value === '-6' && f[1].value === '6'; })()")
                # a plot cleared for a missing value and drawn again still follows a zoom
                # (Plotly's purge took the listener away once, and the fields stayed at -6 … 6)
                page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.send({action: 'set', src: 'a*sin(x) + b'})")
                page.wait_for_selector(".se-addon-plot .plot-note.error", timeout=15000)
                page.locator('.plot-sliders [data-sym="b"] .plot-value').fill("1")
                page.wait_for_selector(".plot-area svg.main-svg", timeout=30000)
                page.wait_for_function("document.querySelector('.plot-area')._seRelayout === true")
                box = page.locator(".plot-area .nsewdrag").first.bounding_box()
                x0, ym = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5
                page.mouse.move(x0, ym); page.mouse.down(); page.mouse.move(x0 - box["width"] * 0.25, ym, steps=10); page.mouse.up()
                page.wait_for_function("document.querySelector('.se-addon-plot .plot-bar .plot-num').value !== '-6'")
                assert float(page.locator(".se-addon-plot .plot-bar .plot-num").first.input_value()) > -6
                # the guide
                page.locator(".se-addon-plot .se-addon-help").click()
                assert "zoom" in page.locator(".se-help-view").inner_text().lower()
                page.keyboard.press("Escape")
                assert errors == []
            finally:
                browser.close()
    finally:
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
            browser = _launch(p)
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
            lay0 = page.evaluate(LAYOUT)
            pinch(30, 110)                               # apart, sideways: a closer look along the axis
            close = span()
            assert close < wide * 0.6, (wide, close)
            assert abs(at_hold() - held) < close * 0.06, (held, at_hold())   # what was held stayed put
            # fingers that barely separate up the screen say nothing about the
            # height, so a sideways pinch leaves it alone
            assert abs(height() - tall) < tall * 0.2, (tall, height())
            # the fields follow the pinch, as they do a wheel zoom
            assert abs(float(page.locator(".se-addon-plot .plot-bar .plot-num").first.input_value())
                       - page.evaluate("() => document.querySelector('.plot-area')._fullLayout.xaxis.range[0]")) < 0.2
            # and nothing on the page moved: at a phone's width follow the
            # selection used to wrap onto the next line and back as the bar
            # changed width, and the picture jumped under the fingers
            assert page.evaluate(LAYOUT) == lay0, (lay0, page.evaluate(LAYOUT))
            assert page.locator(".se-addon-plot .plot-bar input[type=checkbox]").count() == 0
            assert page.locator(".se-addon-plot .plot-follow input[type=checkbox]").count() == 1

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
            browser = _launch(p)
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
            browser = _launch(p)
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


def test_a_gesture_redraws_by_the_frame_not_by_the_move():
    """A finger sends moves faster than the picture can be redrawn, and asking
    Plotly for each of them is what makes a gesture stutter.  The moves are
    collected and the last one before the frame is the only one drawn."""
    doc = Document(sin(x), addons=[ADDON])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            browser = _launch(p)
            ctx = browser.new_context(has_touch=True, is_mobile=True, viewport={"width": 420, "height": 900})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-plot .plot-area", timeout=30000)
            page.wait_for_function("() => { const a = document.querySelector('.plot-area'); return a && a._fullLayout; }", timeout=60000)
            out = page.evaluate("""async () => {
                window.__R = 0;
                const real = Plotly.relayout;
                Plotly.relayout = function () { window.__R++; return real.apply(this, arguments); };
                const a = document.querySelector('.plot-area');
                const b = a.getBoundingClientRect();
                const cx = b.left + b.width / 2, cy = b.top + b.height / 2;
                const touch = (t, x) => {
                    const p = new Touch({identifier: 9, target: a, clientX: x, clientY: cy});
                    const none = t === 'touchend';
                    a.dispatchEvent(new TouchEvent(t, {touches: none ? [] : [p], targetTouches: none ? [] : [p],
                        changedTouches: [p], bubbles: true, cancelable: true}));
                };
                touch('touchstart', cx);
                for (let i = 1; i <= 40; i++) touch('touchmove', cx - i * 3);   // all inside one frame
                const during = window.__R;
                await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
                touch('touchend', cx - 120);
                return {during: during, after: window.__R};
            }""")
            assert out["during"] == 0, out          # forty moves, nothing asked of Plotly yet
            assert out["after"] == 1, out           # and one redraw when the frame came
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_picture_stops_following_when_sampling_is_too_slow():
    """Sampling is Python's work and can be slow - an integral, a big
    expression, a phone - while a gesture asks for a new range many times a
    second.  Rather than let that pile up, the picture stops following itself
    and says so, with the way back beside it."""
    doc = Document(sin(x), addons=[ADDON])
    real = doc.handle

    def slow(message, *a, **k):
        if isinstance(message, dict) and message.get("method") == "samples":
            time.sleep(4.0)                        # well past the stall budget
        return real(message, *a, **k)

    doc.handle = slow
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-plot .plot-area", timeout=60000)
            stopped = False
            for _ in range(60):
                if "stopped following" in page.locator(".plot-note").inner_text():
                    stopped = True
                    break
                # keep asking for a new range, as a person tinkering would
                page.evaluate("""() => { const a = document.querySelector('.plot-area');
                    if (window.Plotly && a._fullLayout) Plotly.relayout(a, {'xaxis.range': [-6 + Math.random(), 6 + Math.random()]}); }""")
                page.wait_for_timeout(1000)
            assert stopped, page.locator(".plot-note").inner_text()
            assert page.locator(".plot-again").count() == 1
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


@contextmanager
def _panel(doc, held=None):
    """The plot panel of ``doc`` on a page of its own; ``page.errors`` is what
    the page threw.  The server keeps nothing (``store=False``).  With
    ``held`` (a list) Plotly does not arrive: its requests wait there until
    the test lets them go (:func:`_release`)."""
    srv = EditorServer(doc, port=0, store=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            browser = _launch(p)
            try:
                page = browser.new_page(viewport={"width": 1100, "height": 900})
                page.errors = []
                page.on("pageerror", lambda e: page.errors.append(str(e)))
                if held is not None:
                    page.route("**/plotly*.js", lambda route: held.append(route))
                page.goto(srv.url)
                page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
                page.wait_for_selector(".se-addon-plot .plot-panel", timeout=30000)
                # every message the editor sends from here on, as it was sent
                page.evaluate("""() => { const ed = %s; window.__sent = [];
                    const send = ed.backend.send.bind(ed.backend);
                    ed.backend.send = function (m, r) { window.__sent.push(JSON.parse(JSON.stringify(m))); return send(m, r); }; }""" % ED)
                yield page
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _release(held):
    while held:
        held.pop().continue_()


def _sampled(page):
    """The ``samples`` requests sent since the page was watched."""
    return page.evaluate("window.__sent.filter(m => m.method === 'samples')")


def _settle(page, ms=500):
    """Past the panel's debounce, and whatever it then asked answered."""
    page.wait_for_timeout(ms)
    page.wait_for_function("!%s.busy" % ED)
    page.wait_for_timeout(200)


def test_a_value_is_sent_as_it_was_typed():
    """The field's text went through parseFloat on its way out: ``pi/2``
    was no value at all (the panel went on asking for one), ``1/2`` was 1,
    ``-1/4`` was -1, ``3*2`` was 3 and ``2e`` was 2.  Python reads a value
    in the document's names, so the text is what it is sent."""
    doc = Document(y * sin(x), addons=[ADDON])
    with _panel(doc) as page:
        page.wait_for_selector(".se-addon-plot .plot-note.error", timeout=15000)       # x on the axis: y needs a value
        field = page.locator(".plot-sliders .plot-value")
        slider = page.locator(".plot-sliders input[type=range]")
        for typed, value in [("pi/2", math.pi / 2), ("1/2", 0.5), ("-1/4", -0.25), ("3*2", 6.0), ("0.75", 0.75)]:
            field.fill(typed)
            # the curve is the one of that value: its last point is value * sin(6)
            page.wait_for_function("(want) => { const d = %s.data; if (!d || !d[0]) return false;"
                                   " const ys = d[0].y; return Math.abs(ys[ys.length - 1] - want) < 1e-9; }" % AREA,
                                   arg=value * math.sin(6), timeout=30000)
            assert _sampled(page)[-1]["values"] == {"y": typed}
            assert "error" not in page.locator(".plot-note").get_attribute("class")
            assert field.input_value() == typed                                      # and the field is left as typed
            # the slider stands at the number the text is, as far as it reaches
            assert abs(float(slider.input_value()) - max(-3.0, min(3.0, value))) <= 0.05, (typed, slider.input_value())
        # the slider still writes its number in the field
        slider.evaluate("(e) => { e.value = '1.5'; e.dispatchEvent(new Event('input', {bubbles: true})); }")
        page.wait_for_function("(() => { const s = window.__sent.filter(m => m.method === 'samples'); return s.length && s[s.length - 1].values.y === '1.5'; })()")
        assert field.input_value() == "1.5"
        # what is no number is said so where the value was asked, not read as its first digits
        field.fill("2+")
        page.wait_for_function("document.querySelector('.plot-note').className.indexOf('error') >= 0")
        assert "value of y" in page.locator(".plot-note").inner_text()
        assert _sampled(page)[-1]["values"] == {"y": "2+"}
        # an emptied field is no value: the panel asks for one again
        field.fill("")
        page.wait_for_function("document.querySelector('.plot-note').textContent.indexOf('give a value to y') >= 0")
        assert _sampled(page)[-1]["values"] == {}
        assert page.errors == []


def test_the_latest_answer_is_the_one_drawn_when_plotly_arrives():
    """Every answer that came while Plotly was loading started a load of its
    own and drew itself when that ended, whatever had been asked since:
    with ``y*sin(x)``, ``sin(x)`` selected and then the whole, the picture
    showed ``sin(x)`` under a selection that has no curve - and the page had
    a script tag per answer."""
    doc = Document(y * sin(x), addons=[ADDON])
    part = [path for path in ("/0", "/1") if doc.get(path) == sin(x)][0]
    held = []
    with _panel(doc, held=held) as page:
        says = lambda text: page.wait_for_function(
            "(t) => document.querySelector('.plot-note').textContent.indexOf(t) === 0", arg=text, timeout=15000)
        for _ in range(3):
            page.evaluate("(p) => %s.select(p)" % ED, part)
            says("sin(x)")                                   # sampled, and waiting for Plotly to be drawn
            page.evaluate("%s.select('/')" % ED)
            says("y*sin(x) has 2 free symbols")              # the whole: y has no value, nothing to draw
        assert page.evaluate("typeof window.Plotly") == "undefined"
        assert page.evaluate("document.querySelectorAll('script[src*=plotly]').length") == 1
        _release(held)
        page.wait_for_function("typeof window.Plotly !== 'undefined'", timeout=60000)
        page.wait_for_timeout(800)
        assert page.evaluate("%s.selected" % ED) == "/"
        assert page.locator(".plot-note").inner_text().startswith("y*sin(x) has 2 free symbols")
        assert "error" in page.locator(".plot-note").get_attribute("class")
        assert page.evaluate("(%s.data || []).length" % AREA) == 0
        assert page.locator(".plot-area *").count() == 0
        # and what is asked from now on is drawn by the Plotly that arrived
        page.evaluate("(p) => %s.select(p)" % ED, part)
        page.wait_for_selector(".plot-area svg.main-svg", timeout=30000)
        assert page.evaluate("%s.data.length" % AREA) == 1
        assert page.evaluate("document.querySelectorAll('script[src*=plotly]').length") == 1
        assert page.errors == []


def test_a_name_is_any_text_and_still_has_one_row():
    """The row of a symbol was looked up with a selector made of its name:
    ``\\alpha`` never matched its own row, so every sampling added another
    one, and ``a"b`` is no selector at all - the page threw and the panel
    stayed on the answer before.  A name that an object has anyway
    (``constructor``) came with a value nobody gave."""
    for name in (r"\alpha", 'a"b', "a]b", "constructor"):
        doc = Document(x * Symbol(name), addons=[ADDON])
        with _panel(doc) as page:
            page.wait_for_selector(".se-addon-plot .plot-note.error", timeout=15000)
            page.locator(".se-addon-plot select").select_option("x")
            page.wait_for_function("(n) => { const r = document.querySelector('.plot-sliders label');"
                                   " return r && r.getAttribute('data-sym') === n; }", arg=name)
            rows = lambda: page.evaluate("[...document.querySelectorAll('.plot-sliders label')].map(l => l.getAttribute('data-sym'))")
            assert rows() == [name]
            # no value was given: the field is empty and the row says so
            assert page.locator(".plot-sliders .plot-value").input_value() == ""
            assert page.locator(".plot-sliders label.plot-unset").count() == 1
            left = page.locator(".se-addon-plot .plot-bar .plot-num").first
            for k in range(3):                               # sampled again, three times over
                before = len(_sampled(page))
                left.fill(str(-5 - k))
                left.press("Tab")
                page.wait_for_function("(n) => window.__sent.filter(m => m.method === 'samples').length > n", arg=before)
                _settle(page)
            assert rows() == [name], name
            # the row works: a value, and the curve is drawn
            page.locator(".plot-sliders .plot-value").fill("2")
            page.wait_for_selector(".plot-area svg.main-svg, .plot-area svg.plot-svg", timeout=30000)
            assert _sampled(page)[-1]["values"] == {name: "2"}
            assert rows() == [name], name
            assert page.errors == [], name


def _listeners(cdp, expression):
    """How many listeners of each event ``expression`` has."""
    obj = cdp.send("Runtime.evaluate", {"expression": expression})["result"]["objectId"]
    count = {}
    for one in cdp.send("DOMDebugger.getEventListeners", {"objectId": obj})["listeners"]:
        count[one["type"]] = count.get(one["type"], 0) + 1
    return count


def test_switched_off_the_plot_leaves_nothing_behind():
    """Switching the add-on off only stopped its timer: Plotly kept the
    picture - a listener on the window for every time the add-on had been
    on, each holding a graph no longer on the page - and what the panel had
    waiting was still asked of Python after it had gone."""
    doc = Document(sin(x) + cos(x), addons=[ADDON])
    with _panel(doc) as page:
        page.wait_for_selector(".plot-area svg.main-svg", timeout=60000)
        cdp = page.context.new_cdp_session(page)
        switch = lambda **which: page.evaluate("(w) => %s.send(Object.assign({action: 'addons'}, w))" % ED, which)
        switch(disable=["plot"])
        page.wait_for_function("!document.querySelector('.se-addon-plot')")
        _settle(page)
        clean = _listeners(cdp, "window")
        for _ in range(3):
            switch(enable=["plot"])
            page.wait_for_selector(".plot-area svg.main-svg", timeout=60000)
            area = page.evaluate_handle(AREA)
            # asked for again as it goes: a change of the span, and the switch
            # before the panel's debounce has run out
            page.evaluate("""() => { const f = document.querySelector('.se-addon-plot .plot-bar .plot-num');
                f.value = '-4'; f.dispatchEvent(new Event('change', {bubbles: true})); }""")
            page.evaluate("window.__sent.length = 0")
            switch(disable=["plot"])
            page.wait_for_function("!document.querySelector('.se-addon-plot')")
            _settle(page, 800)
            assert _sampled(page) == []
            # the picture it left is no graph of Plotly's any more
            assert area.evaluate("(a) => !a._fullLayout && !a.querySelector('svg')")
        assert _listeners(cdp, "window") == clean
        assert page.evaluate("document.querySelectorAll('script[src*=plotly]').length") == 1
        assert page.errors == []


def test_a_folded_panel_asks_nothing_until_it_is_opened():
    """With the panel folded every selection and every change was still
    sampled - Python's work, the editor busy with it each time - for a
    picture nobody could see."""
    doc = Document(sin(x) + cos(x), addons=[ADDON])
    with _panel(doc) as page:
        page.wait_for_selector(".plot-area svg.main-svg", timeout=60000)
        _settle(page)
        page.locator(".se-addon-plot > summary").click()
        assert page.evaluate("document.querySelector('.se-addon-plot').open") is False
        page.evaluate("window.__sent.length = 0")
        for path in ("/0", "/1", "/0/0", "/"):
            page.evaluate("(p) => %s.select(p)" % ED, path)
            _settle(page)
        page.evaluate("%s.send({action: 'apply', path: '/', op: 'expand'})" % ED)
        _settle(page)
        page.evaluate("%s.send({action: 'set', src: 'x**2 - 1'})" % ED)
        _settle(page)
        assert [m["action"] for m in page.evaluate("window.__sent")] == ["apply", "set"]
        # opened: asked once, for what is there now
        page.locator(".se-addon-plot > summary").click()
        page.wait_for_function("document.querySelector('.plot-note').textContent === 'x**2 - 1'", timeout=15000)
        page.wait_for_function("(() => { const d = %s.data; return d && d[0] && d[0].y[0] === 35; })()" % AREA)
        _settle(page)
        assert len(_sampled(page)) == 1
        # folded and opened with nothing changed in between: nothing to ask
        page.locator(".se-addon-plot > summary").click()
        page.locator(".se-addon-plot > summary").click()
        _settle(page)
        assert len(_sampled(page)) == 1
        assert page.evaluate("document.querySelector('.se-addon-plot').open") is True
        assert page.errors == []


def test_a_sampling_slower_than_the_overlay_asks_once():
    """On a phone a sampling took about 0.5 s, past the 0.4 s after which the
    editor shows its "Working…" overlay.  Taking the overlay down redrew the
    selection, every redraw told the add-ons the selection had changed, and
    the panel - following the selection - asked again: a request every 0.6 s
    for ever, the overlay blinking.  A selection that has not moved asks
    nothing now (the editor tells only a real change, and the panel ignores
    the target it already drew)."""
    doc = Document(sin(x) + cos(x), addons=[ADDON])
    real = doc.handle

    def slow(message, *a, **k):
        if isinstance(message, dict) and message.get("method") == "samples":
            time.sleep(0.6)                        # past workingAfter (400 ms)
        return real(message, *a, **k)

    doc.handle = slow
    with _panel(doc) as page:
        _settle(page, 1500)
        page.evaluate("window.__sent = []")
        path = page.evaluate("Object.keys(%s.state.nodes).find(p => %s.state.nodes[p].src === 'sin(x)')" % (ED, ED))
        page.evaluate("%s.select(%r)" % (ED, path))
        page.wait_for_timeout(5000)                 # eight rounds of the old loop
        asked = _sampled(page)
        assert 1 <= len(asked) <= 2, [m.get("path") for m in asked]
        assert asked[-1]["path"] == path
        _settle(page)
        page.evaluate("window.__sent = []")
        for _ in range(3):                          # the selection drawn again: a relayout, the overlay going
            page.evaluate("%s._applySelection(); %s._showLoading('Working…'); %s._hideLoading()" % (ED, ED, ED))
        page.wait_for_timeout(1500)
        assert _sampled(page) == []
        assert page.errors == []

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
        page.wait_for_function("document.querySelector('.plot-shown').textContent === 'shown: 0 … 1'")
        # the gestures a person uses: a box dragged in the picture, and the wheel
        box = page.locator(".plot-area .nsewdrag").first.bounding_box()
        x0, x1, ym = box["x"] + box["width"] * 0.3, box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5
        page.mouse.move(x0, ym); page.mouse.down(); page.mouse.move(x0 + 5, ym + 5); page.mouse.move(x1, ym + 40, steps=8); page.mouse.up()
        near = "(() => { const v = parseFloat(document.querySelector('.se-addon-plot .plot-bar .plot-num').value); return Math.abs(v - %s) < 0.06; })()"
        page.wait_for_function(near % 0.3)                                    # the box's left edge, 30% of [0, 1]
        shown = page.locator(".plot-shown").inner_text()
        assert shown.startswith("shown: 0.2") or shown.startswith("shown: 0.3")
        page.mouse.move(box["x"] + box["width"] * 0.5, ym)
        for _ in range(3):                                                     # three notches: zooms in around the pointer
            page.mouse.wheel(0, -120)
            page.wait_for_timeout(200)
        page.wait_for_function("parseFloat(document.querySelector('.se-addon-plot .plot-bar .plot-num').value) > 0.31")
        assert page.locator(".plot-shown").inner_text() != shown
        page.mouse.dblclick(x0, ym)                                            # back to the whole span
        page.wait_for_function("document.querySelector('.plot-shown').textContent === 'shown: -6 … 6'")
        # the guide
        page.locator(".se-addon-plot .se-addon-help").click()
        assert "zoom" in page.locator(".se-help-view").inner_text().lower()
        page.keyboard.press("Escape")
        assert errors == []
        browser.close()
    srv.shutdown()
    srv.server_close()

"""The Values panel in a real browser: fields, precision, exact forms, the
reasons, the table and its copies, the guide, and a selection drawn again
asking nothing.  Needs Playwright with Chromium and the KaTeX CDN (skipped
otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from sympy import sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_numeric import ADDON  # noqa: E402

x, a = symbols("x a")

ED = "document.querySelector('.sympy-editor').__sympyEditor"
P = ".se-addon-numeric "


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


@contextmanager
def _panel(doc):
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            try:
                context = browser.new_context(viewport={"width": 1100, "height": 900})
                page = context.new_page()
                page.errors = []
                page.on("pageerror", lambda e: page.errors.append(str(e)))
                page.goto(srv.url)
                page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
                page.wait_for_selector(P + ".num-panel", timeout=30000)
                page.evaluate("""() => { const ed = %s; window.__sent = [];
                    const send = ed.backend.send.bind(ed.backend);
                    ed.backend.send = function (m, r) { window.__sent.push(JSON.parse(JSON.stringify(m))); return send(m, r); }; }""" % ED)
                yield page
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _asked(page):
    return [m for m in page.evaluate("window.__sent") if m.get("action") == "addon" and m.get("addon") == "numeric"]


def _note(page):
    return page.locator(P + ".num-note").inner_text()


def _value(page):
    return page.locator(P + ".num-value").inner_text()


def _field(page, name):
    return page.locator(P + '.num-fields label[data-sym="%s"] input' % name)


def test_values_digits_exact_forms_and_reasons():
    doc = Document(a * sin(x) / x, addons=[ADDON])
    with _panel(doc) as page:
        # no value is guessed: both symbols are asked for
        page.wait_for_function("document.querySelector('.se-addon-numeric .num-note').textContent.includes('Give a value to')")
        assert "a, x" in _note(page) and page.locator(P + ".num-fields label.num-unset").count() == 2
        _field(page, "a").fill("2")
        _field(page, "x").fill("pi/2")
        page.wait_for_function("document.querySelector('.se-addon-numeric .num-value').textContent === '1.27323954473516'")
        exact = page.locator(P + ".num-exact")
        page.wait_for_function("document.querySelector('.se-addon-numeric .num-exact .katex') !== null")    # drawn by KaTeX
        assert exact.inner_text().startswith("=") and "π" in exact.inner_text()
        assert "1.5707" in page.locator(P + '.num-fields label[data-sym="x"] .num-read').inner_text()   # what pi/2 was read as
        assert page.locator(P + ".num-fields label.num-unset").count() == 0
        # thirty digits
        page.locator(P + ".num-digits").select_option("30")
        page.wait_for_function("document.querySelector('.se-addon-numeric .num-value').textContent.startsWith('1.27323954473516268615107010698')")
        # a division by zero says so
        _field(page, "x").fill("0")
        page.wait_for_function("document.querySelector('.se-addon-numeric .num-note').textContent.includes('division by zero')")
        assert "num-bad" in page.locator(P + ".num-value").get_attribute("class")
        # a complex value reads a + b i
        _field(page, "x").fill("1 + I")
        page.wait_for_function("/^[0-9.]+ [+-] [0-9.]+ i$/.test(document.querySelector('.se-addon-numeric .num-value').textContent)")
        assert doc.can_undo is False                              # nothing here changes the formula
        # a selected piece is what is evaluated: sin(x)
        _field(page, "x").fill("pi/6")
        path = page.evaluate("Object.keys(%s.state.nodes).find(p => %s.state.nodes[p].src === 'sin(x)')" % (ED, ED))
        page.evaluate("%s.select(%r)" % (ED, path))
        page.wait_for_function("document.querySelector('.se-addon-numeric .num-target').textContent.includes('sin(x)')")
        page.wait_for_function("document.querySelector('.se-addon-numeric .num-value').textContent.startsWith('0.5')")
        assert page.locator(P + '.num-fields label[data-sym="a"]').count() == 0     # a is not in sin(x)
        # ... and a selection drawn again asks nothing
        page.wait_for_timeout(600)
        page.evaluate("window.__sent = []")
        for _ in range(3):
            page.evaluate("%s._applySelection(); %s._showLoading('Working…'); %s._hideLoading()" % (ED, ED, ED))
        page.wait_for_timeout(1000)
        assert _asked(page) == []
        # the guide
        page.locator(P + ".se-addon-help").click()
        assert "division by zero" in page.locator(".se-help-view").inner_text()
        page.keyboard.press("Escape")
        assert page.errors == []


def test_the_table_and_its_copies():
    doc = Document(a / x, addons=[ADDON])
    with _panel(doc) as page:
        page.locator(P + ".num-mode-table").click()
        page.wait_for_function("document.querySelector('.se-addon-numeric .num-tablebar').hidden === false")
        page.locator(P + ".num-var").select_option("x")
        page.wait_for_function("document.querySelector('.se-addon-numeric .num-fields label[data-sym=\"a\"]') !== null")
        assert page.locator(P + '.num-fields label[data-sym="x"]').count() == 0   # x varies: no field of its own
        _field(page, "a").fill("1")
        page.locator(P + ".num-start").fill("-1")
        page.locator(P + ".num-stop").fill("1")
        page.locator(P + ".num-step").fill("0.5")
        page.wait_for_function("document.querySelectorAll('.se-addon-numeric .num-table tbody tr').length === 5")
        cells = page.evaluate("Array.from(document.querySelectorAll('.se-addon-numeric .num-table tbody tr')).map(r => Array.from(r.cells).map(c => c.textContent))")
        assert [c[0] for c in cells] == ["-1", "-0.5", "0", "0.5", "1"]
        assert cells[0][1] == "-1.0" and "division by zero" in cells[2][2]
        # copied through the app when the page runs in one ...
        page.evaluate("window.SympyEditorApp = { copyText: function (t) { window.__copied = t; } }")
        page.locator(P + ".num-copy-tsv").click()
        tsv = page.evaluate("window.__copied")
        assert tsv.splitlines()[0] == "x\tvalue\tnote" and tsv.splitlines()[1] == "-1\t-1.0\t"
        page.locator(P + ".num-copy-csv").click()
        csv = page.evaluate("window.__copied").splitlines()
        assert csv[0] == "x,value,note" and csv[3].startswith("0,complex ∞,division by zero")
        assert "Copied the table as CSV (5 rows)" in page.locator(".se-status").inner_text()
        # ... a list instead of the range, kept exact in the cell's tooltip
        page.locator(P + ".num-list").fill("1, 1/3")
        page.wait_for_function("document.querySelectorAll('.se-addon-numeric .num-table tbody tr').length === 2")
        assert page.locator(P + ".num-table tbody tr").nth(1).locator("td").first.get_attribute("title") == "1/3"
        # the cap: said, not silently cut
        page.locator(P + ".num-list").fill("")
        page.locator(P + ".num-start").fill("1")
        page.locator(P + ".num-stop").fill("10000")
        page.locator(P + ".num-step").fill("1")
        page.wait_for_function("document.querySelector('.se-addon-numeric .num-note').textContent.includes('Only the first 500 of 10000 rows')", timeout=20000)
        assert page.locator(P + ".num-table tbody tr").count() == 500
        # a step the wrong way
        page.locator(P + ".num-step").fill("-1")
        page.wait_for_function("document.querySelector('.se-addon-numeric .num-note').textContent.includes('goes the other way')")
        assert page.errors == []

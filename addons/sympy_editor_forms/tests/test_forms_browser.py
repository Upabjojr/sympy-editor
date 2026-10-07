"""The forms panel in a real browser: the cards, grouping, sorting, applying,
the time box, the guide, and the rules every panel follows (a selection drawn
again asks nothing; a folded panel runs nothing).  Needs Playwright with
Chromium and the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import time
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from sympy import cos, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

import sympy_editor_forms as forms  # noqa: E402
from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_forms import ADDON, FormsAddon  # noqa: E402

x, y = symbols("x y")
EXPR = (x**2 - 1) / (x - 1) + sin(x)**2 + cos(x)**2
ED = "document.querySelector('.sympy-editor').__sympyEditor"
DONE = "document.querySelector('.se-addon-forms .fm-progress').textContent.includes('functions')"


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def _counting(doc):
    """Count the add-on's calls by method, on the Python side."""
    calls = []
    real = doc.handle

    def handle(message, *a, **k):
        if isinstance(message, dict) and message.get("action") == "addon" and message.get("addon") == "forms":
            calls.append(message.get("method"))
        return real(message, *a, **k)
    doc.handle = handle
    return calls


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
                page = browser.new_page()
                page.errors = []
                page.on("pageerror", lambda e: page.errors.append(str(e)))
                page.goto(srv.url)
                page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
                page.wait_for_selector(".se-addon-forms .fm-panel", timeout=15000)
                yield page
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _cards(page):
    return page.evaluate("Array.from(document.querySelectorAll('.se-addon-forms button.fm-card')).map(c => "
                         "({fns: c.querySelector('.fm-fns').textContent, meta: c.querySelector('.fm-meta').textContent, "
                         "tex: !!c.querySelector('.katex')}))")


def test_cards_grouped_sorted_applied_and_undone():
    doc = Document(EXPR, addons=[ADDON])
    with _panel(doc) as page:
        page.wait_for_function(DONE, timeout=60000)
        cards = _cards(page)
        # x + 2, the fewest operations, first - and every function that gave it on the one card
        first = cards[0]
        assert {"simplify", "trigsimp", "fu"} <= set(first["fns"].split(", ")), first
        assert first["meta"].startswith("1 ops") and first["tex"]
        assert "(−9)" in first["meta"]                          # nine fewer than now
        keys = page.evaluate("Array.from(document.querySelectorAll('.se-addon-forms button.fm-card')).map(c => c.dataset.key)")
        assert len(keys) == len(set(keys))                          # one card per form
        ops = [int(c["meta"].split(" ops")[0]) for c in cards]
        assert ops == sorted(ops)
        assert "expand_trig" in page.locator(".se-addon-forms .fm-same").inner_text()
        assert "now" in page.locator(".se-addon-forms .fm-current").inner_text().lower()
        # as computed: simplify was the first function run
        page.locator(".se-addon-forms .fm-sort").select_option("order")
        assert _cards(page)[0]["fns"].startswith("simplify")
        page.locator(".se-addon-forms .fm-sort").select_option("length")
        lengths = page.evaluate("Array.from(document.querySelectorAll('.se-addon-forms button.fm-card .fm-len')).map(e => parseInt(e.textContent))")
        assert lengths == sorted(lengths)
        assert not doc.can_undo                                     # exploring changed nothing
        # a tap applies the card's form: a step of the history, named after the function
        page.locator(".se-addon-forms button.fm-card").first.click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'x + 2'")
        assert doc.history_labels()["actions"][-1] == "Forms: simplify"
        # ... and the panel explores the new formula
        page.wait_for_function("document.querySelector('.se-addon-forms .fm-current').textContent.includes('5 chars')", timeout=30000)
        page.evaluate(ED + ".send({action: 'undo'})")
        page.wait_for_function("document.querySelector('.se-source').textContent.includes('sin(x)**2')")
        # the guide
        page.locator(".se-addon-forms .se-addon-help").click()
        assert "time box" in page.locator(".se-help-view").inner_text().lower()
        page.keyboard.press("Escape")
        assert page.errors == []


def test_a_slow_function_times_out_and_the_editor_stays_free(monkeypatch):
    def forever(e, f):
        n = 0
        while True:
            n = abs(n) + 1
            (lambda: None)()
    monkeypatch.setattr(forms, "BASE_JOBS", [("slow", "slow", forever)] + forms.BASE_JOBS)
    doc = Document(EXPR, addons=[FormsAddon(timeout=1)])
    calls = _counting(doc)
    with _panel(doc) as page:
        page.wait_for_function("document.querySelector('.se-addon-forms .fm-progress').textContent.includes('Running slow')",
                               timeout=30000)
        # an edit while the slow one runs: it waits for the time box at most, and goes first
        began = time.monotonic()
        page.evaluate(ED + ".send({action: 'set', src: 'sin(x)**2 + cos(x)**2'})")
        page.wait_for_function("document.querySelector('.se-source').textContent === 'sin(x)**2 + cos(x)**2'")
        assert time.monotonic() - began < 2.5
        assert page.evaluate("!document.querySelector('.sympy-editor').classList.contains('se-busy')")
        # the new formula is explored, the slow function shown as timed out
        page.wait_for_selector(".se-addon-forms .fm-timeout-card", timeout=30000)
        assert "timed out after 1 s" in page.locator(".se-addon-forms .fm-timeout-card").first.inner_text()
        page.wait_for_function(DONE, timeout=60000)
        assert _cards(page)[0]["fns"].startswith("simplify")       # 1, the fewest operations
        assert calls.count("plan") == 2
        assert page.errors == []


def test_a_selection_drawn_again_asks_nothing_and_a_selection_explores_itself():
    doc = Document(EXPR, addons=[ADDON])
    calls = _counting(doc)
    with _panel(doc) as page:
        page.wait_for_function(DONE, timeout=60000)
        path = page.evaluate("Object.keys(%s.state.nodes).find(p => %s.state.nodes[p].src === '(x**2 - 1)/(x - 1)')" % (ED, ED))
        page.evaluate("%s.select(%r)" % (ED, path))
        page.wait_for_function("document.querySelector('.se-addon-forms .fm-current').textContent.includes('18 chars')",
                               timeout=30000)
        page.wait_for_function(DONE, timeout=60000)
        assert any(c["fns"].split(", ")[0] in ("factor", "cancel", "simplify") for c in _cards(page))
        before = len(calls)
        for _ in range(3):              # a relayout, the overlay coming and going: the same selection
            page.evaluate("%s._applySelection(); %s._showLoading('Working…'); %s._hideLoading()" % (ED, ED, ED))
        page.wait_for_timeout(1200)
        assert len(calls) == before
        # applying to the selection replaces it alone
        page.locator(".se-addon-forms .fm-sort").select_option("ops")
        page.locator(".se-addon-forms button.fm-card").first.click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'x + sin(x)**2 + cos(x)**2 + 1'")
        assert page.errors == []


def test_a_folded_panel_runs_nothing_until_it_is_opened():
    doc = Document(EXPR, addons=[ADDON])
    calls = _counting(doc)
    with _panel(doc) as page:
        page.wait_for_function(DONE, timeout=60000)
        page.evaluate("document.querySelector('.se-addon-forms').open = false")
        page.wait_for_timeout(200)
        before = len(calls)
        page.evaluate(ED + ".send({action: 'set', src: 'x*y + x'})")
        page.wait_for_function("document.querySelector('.se-source').textContent === 'x*y + x'")
        page.wait_for_timeout(1000)
        assert len(calls) == before
        page.evaluate("document.querySelector('.se-addon-forms').open = true")
        page.wait_for_function("document.querySelector('.se-addon-forms .fm-fns') && "
                               "Array.from(document.querySelectorAll('.se-addon-forms .fm-fns')).some(e => e.textContent.includes('collect(x)'))",
                               timeout=60000)
        assert page.errors == []

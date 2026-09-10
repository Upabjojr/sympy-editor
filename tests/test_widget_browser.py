"""The widget's front end (widget.js) in a real browser, against the real
widget.  anywidget itself needs a notebook server; here a stand-in for its
model carries every message to SympyEditorWidget._on_msg over HTTP and brings
the snapshot trait back, which is all widget.js ever sees of the kernel.

What this guards is the contract between the two: the answer to a message
settles that message's promise *with the snapshot*, as the HTTP and Pyodide
backends do.  It used to settle with null, which no Python-side test could
see - every add-on method then said "No answer" in a notebook: the plot drew
nothing and the LaTeX reader never read.  Needs Playwright with Chromium and
the KaTeX CDN (skipped otherwise)."""
import http.server
import json
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import cos, sin, symbols

anywidget = pytest.importorskip("anywidget")
playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.widget import SympyEditorWidget  # noqa: E402

ADDONS = Path(__file__).resolve().parent.parent / "addons"
x = symbols("x")


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def _addon(folder):
    sys.path.insert(0, str(ADDONS / folder))
    module = __import__(folder)
    return module.ADDON


PAGE = """<!doctype html><meta charset="utf-8">
<style>%(css)s</style>
<div id="host"></div>
<script type="module">
  const start = await (await fetch("/state")).json();
  const { default: widget } = await import("/esm.js");
  let snapshot = start.snapshot;
  const subs = [];
  // What widget.js uses of anywidget's model, and no more.
  const model = {
    get: (k) => (k === "options" ? start.options : k === "snapshot" ? snapshot : undefined),
    on: (ev, cb) => { if (ev === "change:snapshot") subs.push(cb); },
    off: (ev, cb) => { const i = subs.indexOf(cb); if (i >= 0) subs.splice(i, 1); },
    send: (msg) => {
      fetch("/msg", { method: "POST", body: JSON.stringify(msg) })
        .then((r) => r.text())
        .then((s) => {
          if (s === snapshot) return;              // a trait set to what it was fires nothing
          snapshot = s;
          subs.slice().forEach((cb) => cb());
        });
    },
  };
  widget.render({ model, el: document.getElementById("host") });
  window.__rendered = true;
</script>"""


@pytest.fixture
def widget_page():
    """Open a page showing a widget; ``open(widget)`` returns the page."""
    servers, browsers = [], []
    pw = playwright.sync_playwright().start()

    def open_(w):
        lock = threading.Lock()

        class Bridge(http.server.BaseHTTPRequestHandler):
            def _reply(self, body, kind):
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/esm.js":
                    self._reply(w._esm, "text/javascript")
                elif self.path == "/state":
                    self._reply(json.dumps({"options": w.options, "snapshot": w.snapshot}), "application/json")
                else:
                    self._reply(PAGE % {"css": w._css}, "text/html")

            def do_POST(self):
                msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with lock:                       # one message at a time, each with its own answer
                    w._on_msg(w, msg, [])
                    w.wait(60)
                    self._reply(w.snapshot, "application/json")

            def log_message(self, *args):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        browsers.append(browser)
        page = browser.new_page(viewport={"width": 1100, "height": 1000})
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{srv.server_address[1]}/")
        page.wait_for_function("window.__rendered === true", timeout=30000)
        page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
        return page

    yield open_
    for b in browsers:
        b.close()
    for s in servers:
        s.shutdown()
        s.server_close()
    pw.stop()


def test_an_addon_method_is_answered_in_the_widget(widget_page):
    """The plot asks Python for its samples (an add-on method): in a notebook
    that answer has to reach the plot, or it says "No answer" and draws
    nothing - which is what it did."""
    page = widget_page(SympyEditorWidget(sin(x), addons=[_addon("sympy_editor_plot")]))
    page.wait_for_selector(".se-addon-plot .plot-note", timeout=20000)
    # the variable menu is filled from the answer: empty while it was lost
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('.se-addon-plot select option')).some(o => o.value === 'x')",
        timeout=30000)
    note = page.locator(".se-addon-plot .plot-note")
    assert "No answer" not in note.inner_text()
    assert "error" not in (note.get_attribute("class") or "")
    assert page.errors == []


def test_the_latex_reader_reads_and_inserts_in_the_widget(widget_page):
    """Read and insert are both add-on methods: the reading has to come back
    to the panel, and the insert has to reach the formula."""
    w = SympyEditorWidget(sin(x), addons=[_addon("sympy_editor_latex")])
    page = widget_page(w)
    page.locator(".ltx-input").fill(r"\frac{x}{2}")
    page.locator(".ltx-read").click()
    page.wait_for_function("() => (document.querySelector('.ltx-src') || {}).textContent === 'x/2'", timeout=30000)
    page.wait_for_function("() => !document.querySelector('.ltx-insert-all').disabled", timeout=10000)
    page.locator(".ltx-insert-all").click()
    page.wait_for_function("() => document.querySelector('.se-source').textContent === 'x/2'", timeout=30000)
    assert str(w.expr) == "x/2"
    assert page.errors == []


def test_an_edit_still_reaches_the_kernel_and_the_page(widget_page):
    """The answer to an edit now goes to the sender, which applies it, rather
    than being applied by widget.js before the sender hears of it.  An edit
    has to arrive in both places all the same."""
    w = SympyEditorWidget(sin(x))
    page = widget_page(w)
    seen = []
    w.on_change(seen.append)
    source = page.locator(".se-source")
    source.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("cos(x)")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.querySelector('.se-source').textContent === 'cos(x)'", timeout=30000)
    page.wait_for_function(
        "() => !!document.querySelector('.se-view .katex') && document.querySelector('.se-view').textContent.includes('cos')",
        timeout=30000)
    assert w.expr == cos(x) and seen and seen[-1] == cos(x)
    assert page.errors == []

"""The LaTeX panel in a real browser: a reading, an ambiguity picked, a
constant switched, the reading inserted.  Needs Playwright with Chromium and
the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest
from sympy import Symbol, cos, pi, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")
pytest.importorskip("lark")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_latex import ADDON, LatexAddon  # noqa: E402

x, y = symbols("x y")


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def test_the_panel_reads_offers_choices_and_inserts():
    doc = Document(x + y, addons=[ADDON])
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
        page.wait_for_selector(".se-addon-latex .ltx-input", timeout=30000)
        box = page.locator(".ltx-input")
        box.fill(r"\sin x \cos y + \pi")
        usual, other = str(sin(x) * cos(y) + pi), str(sin(x * cos(y)) + pi)
        page.wait_for_function("s => document.querySelector('.ltx-src').textContent === s", arg=usual, timeout=15000)
        assert page.locator(".ltx-preview .katex").count() == 1                          # rendered
        # the reading shows once, typeset and as source - not a second time
        # beside the menu of each ambiguity (issue #27)
        assert page.locator(".ltx-panel .katex").count() == 1 and page.locator(".ltx-ambig .katex").count() == 0
        # the ambiguities: a menu per point with the whole under each reading, the conventional one chosen
        rows = page.locator(".ltx-point")
        assert rows.count() == 2
        row = rows.filter(has=page.locator(".ltx-fragment", has_text=r"\sin x \cos y").filter(has_not_text=r"\pi"))
        assert row.count() == 1
        choice = row.locator(".ltx-choice")
        options = choice.locator("option").all_inner_texts()
        assert usual in options and other in options
        assert choice.input_value() == str(options.index(usual))
        choice.select_option(str(options.index(other)))
        page.wait_for_function("s => document.querySelector('.ltx-src').textContent === s", arg=other)
        # the constant: pi switched off is a symbol
        const = page.locator(".ltx-const input")
        assert const.count() == 1 and const.is_checked() and "pi" in page.locator(".ltx-const").inner_text()
        const.uncheck()
        as_symbol = str(Symbol("pi") + sin(x * cos(y)))                                   # another pi, printed elsewhere
        page.wait_for_function("s => document.querySelector('.ltx-src').textContent === s", arg=as_symbol)
        assert not page.locator(".ltx-const input").is_checked()
        # nothing selected and no cursor: the first button adds after the formula
        assert page.locator(".ltx-insert").inner_text() == "Add to end"
        assert not page.locator(".ltx-insert").is_disabled() and not page.locator(".ltx-insert-all").is_disabled()
        page.locator(".ltx-insert-all").click()
        page.wait_for_function("s => document.querySelector('.se-source').textContent === s", arg=as_symbol)
        assert doc.expr == Symbol("pi") + sin(x * cos(y)) and pi not in doc.expr.atoms()
        assert doc.history_labels()["actions"][-1] == r"LaTeX: \sin x \cos y + \pi"
        # a selection: the reading replaces it; new text drops the old picks
        pi_path = next(p for p, n in doc.snapshot()["nodes"].items() if p != "/" and n["src"] == "pi")   # the pi term
        page.evaluate("p => document.querySelector('.sympy-editor').__sympyEditor.select(p)", pi_path)
        assert not page.locator(".ltx-insert").is_disabled()
        box.fill(r"\frac{d}{dx} x^2")
        page.wait_for_function("document.querySelector('.ltx-src').textContent === 'Derivative(x**2, x)'")
        assert page.locator(".ltx-choice").count() == 0
        page.locator(".ltx-insert").click()
        page.wait_for_function("document.querySelector('.se-source').textContent.includes('Derivative(x**2, x)')")
        assert Symbol("pi") not in doc.expr.free_symbols
        # \sinh is one command: one reading, one menu over it (issue #27)
        box.fill(r"\sinh xy")
        page.wait_for_function("document.querySelector('.ltx-src').textContent === 'sinh(x*y)'")
        assert page.locator(".ltx-point").count() == 1 and page.locator(".ltx-panel .katex").count() == 1
        # an error is a message in the panel, not the editor's
        box.fill(r"x^2 +* y")
        page.wait_for_function("document.querySelector('.ltx-note').classList.contains('error')")
        assert "could not be read" in page.locator(".ltx-note").inner_text()
        assert not page.locator(".se-error").is_visible()
        # the toolbar tool focuses the box; the guide opens
        page.locator('.se-toolbar [data-cmd="addon:latex:focus"]').click()
        assert page.evaluate("document.activeElement.className") == "ltx-input"
        # its "?" is the toolbar's "?", not one of the panel's own buttons (issue #27)
        page.mouse.move(0, 0)
        look = ("b => { const s = getComputedStyle(b), r = b.getBoundingClientRect(); return [s.fontSize, s.fontWeight,"
                " s.padding, s.border, s.borderRadius, s.backgroundImage, s.boxShadow, s.color, Math.round(r.width),"
                " Math.round(r.height)]; }")
        assert (page.locator(".se-addon-latex .se-addon-help").evaluate(look)
                == page.locator('.se-toolbar [data-cmd="help"]').evaluate(look))
        page.locator(".se-addon-latex .se-addon-help").click()
        assert "several ways" in page.locator(".se-help-view").inner_text().lower()
        page.keyboard.press("Escape")
        assert errors == []
        browser.close()
    srv.shutdown()
    srv.server_close()



def test_the_first_button_adds_at_the_cursor_or_at_the_end():
    """With a selection it replaces the selection; with a cursor in the
    formula it is "Add to cursor", and with neither "Add to end" - the
    reading going in as if typed there."""
    z = Symbol("z")
    doc = Document(x + y, addons=[ADDON])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-latex .ltx-input", timeout=30000)
            ed = "document.querySelector('.sympy-editor').__sympyEditor"
            first = page.locator(".se-addon-latex .ltx-insert")
            source = "document.querySelector('.se-source').textContent"
            page.locator(".ltx-input").fill("z")
            page.wait_for_function("document.querySelector('.ltx-src').textContent === 'z'")
            assert first.inner_text() == "Add to end" and not first.is_disabled()
            first.click()
            page.wait_for_function(source + " === 'z*(x + y)'")
            assert doc.expr == z * (x + y)
            page.evaluate(ed + ".send({action: 'undo'})")
            page.wait_for_function(source + " === 'x + y'")
            # a cursor between the two terms: "Add to cursor", a new term there
            page.evaluate("() => { const e = %s; const pos = e._caretPositions().find(q => !q.gap.extend && q.gap.path === '/' "
                          "&& q.gap.leftEl && q.gap.rightEl); e._showCaret(pos.gap, pos.x); }" % ed)
            page.wait_for_function("document.querySelector('.se-addon-latex .ltx-insert').textContent === 'Add to cursor'")
            first.click()
            page.wait_for_function(source + " === 'x + y + z'")
            assert doc.expr == x + y + z
            # a selection: replaced
            xp = next(path for path, n in doc.snapshot()["nodes"].items() if n["src"] == "x")
            page.evaluate("p => %s.select(p)" % ed, xp)
            page.wait_for_function("document.querySelector('.se-addon-latex .ltx-insert').textContent === 'Replace the selection'")
            # nothing selected and no cursor: the end again
            page.evaluate(ed + ".select(null)")
            page.wait_for_function("document.querySelector('.se-addon-latex .ltx-insert').textContent === 'Add to end'")
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_typing_goes_on_through_a_slow_reading_and_unfinished_text_is_no_error():
    """The first reading built the grammar - long enough for the editor's
    overlay, which covered the box and took its focus mid-word; the
    half-typed text then came back as an error.  Readings are quiet now (the
    panel shows its own progress), and a text that stops too early is only
    not finished: the last reading stays, dimmed, and cannot be inserted."""
    import time

    class Slow(LatexAddon):                 # every reading as slow as the first used to be on a phone
        def read(self, doc, payload):
            time.sleep(1.2)
            return super().read(doc, payload)

    doc = Document(x + y, addons=[Slow()])
    srv = EditorServer(doc, port=0, options={"workingAfter": 100})
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
        page.wait_for_selector(".se-addon-latex .ltx-input", timeout=30000)
        page.locator(".ltx-input").click()
        page.keyboard.type(r"\frac{x", delay=30)
        page.wait_for_function("document.querySelector('.ltx-panel').classList.contains('ltx-busy')", timeout=5000)
        page.wait_for_timeout(400)                                               # well past workingAfter
        assert not page.locator(".se-loading").is_visible()                      # nothing over the box
        assert page.evaluate("document.activeElement.className") == "ltx-input"  # still typing there
        page.wait_for_function("document.querySelector('.ltx-note').textContent.startsWith('Not finished')", timeout=10000)
        assert "error" not in page.locator(".ltx-note").get_attribute("class")
        # typing on ends in a reading
        page.keyboard.type("}{2}", delay=30)
        page.wait_for_function("document.querySelector('.ltx-src').textContent === 'x/2'", timeout=15000)
        assert page.locator(".ltx-note").inner_text() == "" and not page.locator(".ltx-insert-all").is_disabled()
        # unfinished again: x/2 stays, dimmed, and is not inserted for this text
        page.keyboard.type(" +", delay=30)
        page.wait_for_function("document.querySelector('.ltx-panel').classList.contains('ltx-stale')", timeout=15000)
        assert page.locator(".ltx-src").inner_text() == "x/2" and page.locator(".ltx-insert-all").is_disabled()
        assert "error" not in page.locator(".ltx-note").get_attribute("class")
        assert not page.locator(".se-loading").is_visible() and doc.expr == x + y
        assert errors == []
        browser.close()
    srv.shutdown()
    srv.server_close()


def test_inserting_brings_the_formula_back_into_sight():
    """The panel sits below the editor: after each way in - over the
    selection, at the end, the whole expression, Ctrl+Enter - the page is
    back at the top of the editor, wherever it had been scrolled to."""
    z = Symbol("z")
    doc = Document(x + y, addons=[ADDON])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            page = browser.new_page(viewport={"width": 760, "height": 420}, reduced_motion="reduce")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-latex .ltx-input", timeout=30000)
            # room below the panel, so the page can be scrolled away from the formula
            page.evaluate("document.body.appendChild(Object.assign(document.createElement('div'), {style: 'height: 3000px'}))")
            ed = "document.querySelector('.sympy-editor').__sympyEditor"
            source = "document.querySelector('.se-source').textContent"
            top = "Math.round(document.querySelector('.sympy-editor').getBoundingClientRect().top)"

            def scrolled_away():
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_function(top + " < -100")

            def back_at_the_formula():
                page.wait_for_function(top + " >= -1 && " + top + " <= 1", timeout=5000)

            box = page.locator(".ltx-input")
            box.fill("z")
            page.wait_for_function("document.querySelector('.ltx-src').textContent === 'z'")

            scrolled_away()                                              # Add to end
            page.locator(".se-addon-latex .ltx-insert").click()
            page.wait_for_function(source + " === 'z*(x + y)'")
            back_at_the_formula()

            xp = next(path for path, n in doc.snapshot()["nodes"].items() if n["src"] == "x")
            page.evaluate("p => %s.select(p)" % ed, xp)
            page.wait_for_function("document.querySelector('.se-addon-latex .ltx-insert').textContent === 'Replace the selection'")
            scrolled_away()                                              # over the selection
            page.locator(".se-addon-latex .ltx-insert").click()
            page.wait_for_function("() => %s.includes('z**2') || %s === 'z*(y + z)'" % (source, source))
            back_at_the_formula()

            scrolled_away()                                              # the whole expression
            page.locator(".se-addon-latex .ltx-insert-all").click()
            page.wait_for_function(source + " === 'z'")
            back_at_the_formula()
            assert doc.expr == z

            box.fill("y")
            page.wait_for_function("document.querySelector('.ltx-src').textContent === 'y'")
            scrolled_away()                                              # Ctrl+Enter in the box
            box.press("Control+Enter")
            page.wait_for_function(source + " !== 'z'")
            back_at_the_formula()
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()

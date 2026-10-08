"""The console's panel in a real browser, on the local server: typing at
``In [n]:``, the typeset ``Out[n]``, an unfinished block, the formula changed
from Python and taken back with Undo, the selection as ``editor.selection``,
a script, and the guide.  Needs Playwright with Chromium and the KaTeX CDN
(skipped otherwise)."""
import json
import os
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

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor.store import Store  # noqa: E402
from sympy_editor_console import ADDON  # noqa: E402

x, y = symbols("x y")
ED = "document.querySelector('.sympy-editor').__sympyEditor"


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


@contextmanager
def _page(doc, launch=None, store=None, **context):
    """The editor of `doc` in a page, served; `store` is where the server
    keeps what the page keeps (a test's own folder by default: conftest.py)."""
    srv = EditorServer(doc, port=0, **({} if store is None else {"store": store}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(**(launch or {}))
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            page = browser.new_page(**context)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(srv.url)
            page.wait_for_selector(".se-addon-console .pc-input", timeout=30000)
            page.wait_for_function(f"{ED} && {ED}.state && !{ED}.busy")
            yield page
            assert errors == []
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _enter(page, code):
    """Type `code` at the prompt and press Enter, and wait for the answer."""
    field = page.locator(".se-addon-console .pc-input")
    field.click()
    field.fill(code)
    before = page.locator(".se-addon-console .pc-entry").count()
    field.press("Enter")
    page.wait_for_function(f"document.querySelectorAll('.se-addon-console .pc-entry').length > {before}")
    page.wait_for_function(f"!{ED}.busy")
    return page.locator(".se-addon-console .pc-entry").last


def _source(page):
    return page.locator(".se-source").inner_text().strip()


def test_in_and_out_as_ipython():
    doc = Document(sin(x) + x, addons=[ADDON])
    with _page(doc) as page:
        assert page.locator(".se-addon-console .pc-input-row .pc-prompt").inner_text() == "In [1]:"
        entry = _enter(page, "a = 2\nprint('a is', a)\na*x")
        assert entry.locator(".pc-prompt-in").inner_text() == "In [1]:"
        assert entry.locator(".pc-stdout").inner_text().strip() == "a is 2"
        assert entry.locator(".pc-prompt-out").inner_text() == "Out[1]:"
        page.wait_for_function("document.querySelector('.se-addon-console .pc-entry:last-child .pc-math .katex') !== null")   # typeset
        assert page.locator(".se-addon-console .pc-input-row .pc-prompt").inner_text() == "In [2]:"
        entry = _enter(page, "_ + 1")
        assert entry.locator(".pc-math").get_attribute("title") == "2*x + 1"
        entry = _enter(page, "1/0")
        assert "ZeroDivisionError" in entry.locator(".pc-error").inner_text()
        assert not doc.can_undo                                             # nothing of it touched the formula


def test_the_code_is_coloured_as_python():
    """The input, the script and the transcript are coloured as the source
    line is (SympyEditor.python): a coloured copy under each field, glyph
    for glyph, the field's own text transparent over it; the bracket by
    the caret and its partner are marked."""
    doc = Document(sin(x) + x, addons=[ADDON])
    with _page(doc) as page:
        field = page.locator(".se-addon-console .pc-input")
        field.click()
        field.fill("y = Symbol('y')  # a name\nsin(y) + 2.5")
        under = page.locator(".se-addon-console .pc-input-wrap .pc-hl")
        assert under.locator(".se-py-class").first.inner_text() == "Symbol"
        assert under.locator(".se-py-fn").first.inner_text() == "sin"
        assert under.locator(".se-py-str").first.inner_text() == "'y'"
        assert under.locator(".se-py-com").first.inner_text() == "# a name"
        assert under.locator(".se-py-num").last.inner_text() == "2.5"
        # the copy is the field's text, and sits exactly under it
        assert page.evaluate("""() => {
            const f = document.querySelector('.se-addon-console .pc-input');
            const u = document.querySelector('.se-addon-console .pc-input-wrap .pc-hl');
            const a = f.getBoundingClientRect(), b = u.getBoundingClientRect();
            return u.textContent === f.value + '\\n' && Math.abs(a.left - b.left) < 1 && Math.abs(a.top - b.top) < 1
                && getComputedStyle(f).color === 'rgba(0, 0, 0, 0)';
        }""")
        # the caret after "(" of sin(: that bracket and its partner are marked
        at = "y = Symbol('y')  # a name\nsin(".__len__()
        field.evaluate("(f, at) => { f.focus(); f.setSelectionRange(at, at); }", at)
        page.wait_for_function("document.querySelectorAll('.se-addon-console .pc-input-wrap .se-py-match').length === 2")
        # set from code too (the history, a completion): coloured
        page.keyboard.press("Escape")
        field.evaluate("f => { f.value = 'Integral(x, x)'; }")
        assert under.locator(".se-py-class").first.inner_text() == "Integral"
        # the transcript
        entry = _enter(page, "factor(x**2 - 1)")
        assert entry.locator(".pc-code .se-py-fn").first.inner_text() == "factor"
        assert entry.locator(".pc-code .se-py-num").count() == 2
        # and the script
        page.locator(".se-addon-console .pc-tab[data-mode=script]").click()
        assert page.locator(".se-addon-console .pc-script-wrap .pc-hl .se-py-com").count() >= 1


def test_an_unfinished_block_asks_for_more():
    doc = Document(x, addons=[ADDON])
    with _page(doc) as page:
        field = page.locator(".se-addon-console .pc-input")
        field.click()
        field.fill("for i in range(3):")
        field.press("Enter")
        page.wait_for_function("document.querySelector('.se-addon-console .pc-input').value === 'for i in range(3):\\n    '")
        assert page.locator(".se-addon-console .pc-entry").count() == 0
        field.press_sequentially("print(i)")
        field.press("Enter")                                                # a body wants an empty line
        page.wait_for_function("document.querySelector('.se-addon-console .pc-input').value.endsWith('print(i)\\n    ')")
        field.press("Enter")
        page.wait_for_selector(".se-addon-console .pc-entry")
        assert page.locator(".se-addon-console .pc-entry .pc-stdout").inner_text().split() == ["0", "1", "2"]
        assert field.input_value() == ""                                    # the run cleared it...
        # ...and ↑ brings it back
        field.press("ArrowUp")
        assert page.evaluate("document.querySelector('.se-addon-console .pc-input').value").startswith("for i in range(3):")


def test_the_formula_from_python_and_back_with_undo():
    doc = Document((x + 1) ** 2, addons=[ADDON])
    with _page(doc) as page:
        _enter(page, "editor.expr = expand(editor.expr)")
        page.wait_for_function("document.querySelector('.se-source').textContent.trim() === 'x**2 + 2*x + 1'")
        assert doc.history_labels()["actions"][-1] == "Console: editor.expr = expand(editor.expr)"
        page.locator('.se-toolbar [data-cmd="undo"]').click()
        page.wait_for_function("document.querySelector('.se-source').textContent.trim() === '(x + 1)**2'")


def test_the_selection_is_editor_selection():
    doc = Document(sin(x) + cos(y), addons=[ADDON])
    with _page(doc) as page:
        path = next(p for p, info in doc.snapshot()["nodes"].items() if info["src"] == "cos(y)")
        page.evaluate(f"p => {ED}.select(p)", path)
        entry = _enter(page, "editor.selection")
        assert entry.locator(".pc-math").get_attribute("title") == "cos(y)"
        _enter(page, "editor.selection = editor.selection.rewrite(exp)")
        page.wait_for_function("document.querySelector('.se-source').textContent.includes('exp(I*y)')")
        assert doc.expr.has(sin(x)) and not doc.expr.has(cos(y))
        # Use: an Out[n] into the formula - the whole of it, with nothing selected
        page.evaluate(f"{ED}.select(null)")
        entry = _enter(page, "factor(x**2 - 1)")
        entry.locator(".pc-use").click()
        page.wait_for_function("document.querySelector('.se-source').textContent.trim() === '(x - 1)*(x + 1)'")


def test_a_script_runs_and_leaves_its_names():
    doc = Document(sin(x) ** 2 + cos(x) ** 2, addons=[ADDON])
    with _page(doc) as page:
        page.locator(".se-addon-console .pc-tab[data-mode='script']").click()
        assert page.locator(".se-addon-console .pc-script").is_visible()
        assert not page.locator(".se-addon-console .pc-input").is_visible()
        page.locator(".se-addon-console .pc-script").fill(
            "def twice(e):\n    return 2*e\n\nprint(__name__)\neditor.expr = simplify(editor.expr)\n")
        page.locator(".se-addon-console .pc-scripting .pc-run").click()
        page.wait_for_function("document.querySelector('.se-addon-console .pc-script-out .pc-note') !== null")
        assert page.locator(".se-addon-console .pc-script-out .pc-stdout").inner_text().strip() == "__main__"
        page.wait_for_function("document.querySelector('.se-source').textContent.trim() === '1'")
        assert doc.history_labels()["actions"][-1] == "Script: script.py"
        page.locator(".se-addon-console .pc-tab[data-mode='console']").click()
        entry = _enter(page, "twice(x)")                                    # the script's function, in the console
        assert entry.locator(".pc-math").get_attribute("title") == "2*x"


def test_run_on_a_phone_stays_at_the_prompt_and_the_transcript_scrolls():
    """Tapping Run leaves the prompt where it was on the screen, with the
    focus (and a phone's keyboard) in the field; the transcript grows until it
    scrolls in a box of its own, with a bar that stays."""
    doc = Document(sin(x) + x, addons=[ADDON])
    # Playwright hides scroll bars unless told not to: the bar is what is measured
    with _page(doc, launch={"ignore_default_args": ["--hide-scrollbars"]},
               viewport={"width": 384, "height": 437}, has_touch=True, is_mobile=True) as page:
        field = page.locator(".se-addon-console .pc-input")
        run = page.locator(".se-addon-console .pc-input-row .pc-run")
        top = "() => Math.round(document.querySelector('.se-addon-console .pc-input-row').getBoundingClientRect().top)"
        field.tap()
        field.scroll_into_view_if_needed()
        where = page.evaluate(top)
        for i in range(12):
            field.fill(f"print('line'); {i} if {i} % 3 else editor.expr * {i + 1}")
            before = page.locator(".se-addon-console .pc-entry").count()
            run.tap()
            page.wait_for_function(f"document.querySelectorAll('.se-addon-console .pc-entry').length > {before}")
            page.wait_for_function(f"!{ED}.busy")
            page.wait_for_timeout(300)
            assert abs(page.evaluate(top) - where) <= 1, f"the prompt moved after run {i}"
            assert page.evaluate("document.activeElement.classList.contains('pc-input')")
        log = page.locator(".se-addon-console .pc-log").first
        assert log.evaluate("l => l.scrollHeight > l.clientHeight + 50 && getComputedStyle(l).overflowY === 'auto'")
        assert log.evaluate("l => l.scrollTop + l.clientHeight >= l.scrollHeight - 2")          # the latest in sight
        assert log.evaluate("l => l.offsetWidth - l.clientWidth") >= 8          # a bar of its own, drawn - not a phone's overlay


def test_tapping_an_output_copies_it_into_the_input():
    doc = Document(x, addons=[ADDON])
    with _page(doc) as page:
        entry = _enter(page, "factor(x**2 - 1)")
        field = page.locator(".se-addon-console .pc-input")
        field.fill("expand()")
        field.evaluate("f => { f.selectionStart = f.selectionEnd = 7; }")          # the cursor between the parentheses
        entry.locator(".pc-math").click()
        assert field.input_value() == "expand((x - 1)*(x + 1))"
        assert page.evaluate("document.activeElement.classList.contains('pc-input')")
        entry = _enter(page, "display(x**3)")                                  # display() output too
        field.fill("")
        entry.locator(".pc-display").click()
        assert field.input_value() == "x**3"
        assert doc.expr == x                                                   # copying changes nothing


def test_the_guide():
    doc = Document(x, addons=[ADDON])
    with _page(doc) as page:
        page.locator(".se-addon-console .se-addon-help").click()
        text = page.locator(".se-help-view").inner_text()
        assert "editor.selection" in text and "%time" in text
        page.keyboard.press("Escape")


def test_a_runaway_loop_is_interrupted():
    doc = Document(x, addons=[ADDON])
    with _page(doc) as page:
        field = page.locator(".se-addon-console .pc-input")
        field.click()
        field.fill("n = 0\nwhile True:\n    n += 1\n")
        field.press("Control+Enter")
        page.locator(".se-interrupt").wait_for(state="visible", timeout=15000)
        page.locator(".se-interrupt").click()
        page.wait_for_selector(".se-addon-console .pc-entry .pc-error", timeout=15000)
        assert "Interrupted" in page.locator(".se-addon-console .pc-entry .pc-error").inner_text()
        page.wait_for_function(f"!{ED}.busy")
        entry = _enter(page, "n > 0")                                        # the console goes on, variables and all
        assert entry.locator(".pc-math").inner_text() == "True"


@pytest.mark.skipif(not os.environ.get("SYMPY_EDITOR_SLOW_TESTS"), reason="set SYMPY_EDITOR_SLOW_TESTS=1")
def test_a_standalone_page_runs_the_console_in_pyodide(tmp_path):
    from sympy_editor import save_html
    path = save_html((x + 1) ** 2, tmp_path / "console.html", addons=[ADDON])
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        try:
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(path.as_uri())
            page.wait_for_selector(".se-addon-console .pc-input", timeout=60000)
            page.wait_for_function("document.querySelector('.se-loading').hidden", timeout=240000)
            entry = _enter(page, "import sys\nprint(sys.platform)\neditor.expr = expand(editor.expr)")
            assert entry.locator(".pc-stdout").inner_text().strip() == "emscripten"      # Python in the page itself
            page.wait_for_function("document.querySelector('.se-source').textContent.trim() === 'x**2 + 2*x + 1'")
            page.locator(".se-addon-console .pc-tab[data-mode='script']").click()
            page.locator(".se-addon-console .pc-script").fill("print(editor.expr.coeff(x))\n")
            page.locator(".se-addon-console .pc-scripting .pc-run").click()
            page.wait_for_function("document.querySelector('.se-addon-console .pc-script-out .pc-stdout')", timeout=60000)
            assert page.locator(".se-addon-console .pc-script-out .pc-stdout").inner_text().strip() == "2"
            assert errors == []
        finally:
            browser.close()


def _menu(page):
    return page.locator(".se-addon-console .pc-complete")


def _menu_names(page):
    page.wait_for_selector(".se-addon-console .pc-complete:not([hidden]) .pc-comp-item")
    return page.eval_on_selector_all(".se-addon-console .pc-comp-item", "els => els.map(e => e.dataset.name)")


def test_a_dot_opens_the_menu_of_what_is_in_memory():
    with _page(Document(sin(x) + y, addons=[ADDON])) as page:
        _enter(page, "rows = [1, 2]")
        field = page.locator(".se-addon-console .pc-input")
        field.click()
        field.press_sequentially("rows.")
        names = _menu_names(page)
        assert "rows.append" in names and "rows.count" in names and not any(n.startswith("rows._") for n in names)
        field.press_sequentially("ap")                              # typing narrows it
        page.wait_for_function("document.querySelectorAll('.se-addon-console .pc-comp-item').length === 1")
        kind = page.locator(".se-addon-console .pc-comp-on .pc-comp-kind").inner_text()
        assert kind == "method"
        field.press("Enter")                                        # takes it, runs nothing
        assert field.input_value() == "rows.append" and _menu(page).is_hidden()
        assert page.locator(".se-addon-console .pc-entry").count() == 1
        field.press_sequentially("(3); rows")
        field.press("Enter")
        page.wait_for_function(f"document.querySelectorAll('.se-addon-console .pc-entry').length === 2 && !{ED}.busy")
        assert "[1, 2, 3]" in page.locator(".se-addon-console .pc-entry").last.inner_text()

        field.fill("")
        field.press_sequentially("editor.")                          # the formula's own API
        names = _menu_names(page)
        assert {"editor.expr", "editor.selection", "editor.find"} <= set(names)
        on = lambda: page.eval_on_selector_all(".se-addon-console .pc-comp-on", "els => els.map(e => e.dataset.name)")
        first = on()
        start = names.index(first[0]) if first else -1
        field.press("ArrowDown")
        field.press("ArrowDown")
        assert on() == [names[(start + 2) % len(names)]]            # ↓ moved the highlight, twice
        field.press("Escape")                                       # closes the menu, nothing else
        assert _menu(page).is_hidden() and field.input_value() == "editor."
        field.press_sequentially("ex")
        page.locator(".se-addon-console .pc-comp-item[data-name='editor.expr']").click()   # a tap takes one
        assert field.input_value() == "editor.expr"
        assert page.evaluate("document.activeElement.classList.contains('pc-input')")


def test_a_name_opens_the_menu_only_when_few_names_begin_so():
    with _page(Document(x, addons=[ADDON])) as page:
        _enter(page, "velocity = 3; volume = 2")
        field = page.locator(".se-addon-console .pc-input")
        field.click()
        field.press_sequentially("s")                               # hundreds of SymPy names: no menu
        page.wait_for_timeout(600)
        assert _menu(page).is_hidden()
        field.fill("")
        field.press_sequentially("v")
        names = _menu_names(page)
        assert names[:2] == ["velocity", "volume"]                  # the user's own first
        field.press_sequentially("el")
        page.wait_for_function("document.querySelectorAll('.se-addon-console .pc-comp-item').length === 1")
        field.press("Tab")
        assert field.input_value() == "velocity" and _menu(page).is_hidden()
        field.press("Enter")                                        # a finished name runs
        page.wait_for_function(f"document.querySelectorAll('.se-addon-console .pc-entry').length === 2 && !{ED}.busy")
        assert "Out[2]" in page.locator(".se-addon-console .pc-entry").last.inner_text()
        field.press_sequentially("'volume.")                        # inside a string: nothing
        page.wait_for_timeout(600)
        assert _menu(page).is_hidden()
        field.fill("simpl")
        field.press("Tab")                                          # Tab: as far as they agree, then the menu
        page.wait_for_function("document.querySelector('.se-addon-console .pc-input').value === 'simplify'")
        assert "simplify_logic" in _menu_names(page)


def _reload(page):
    page.reload()
    page.wait_for_selector(".se-addon-console .pc-panel", timeout=30000)     # whichever tab was kept
    page.wait_for_function(f"{ED} && {ED}.state && !{ED}.busy")


def test_the_first_input_is_the_formula_and_the_transcript_comes_back_as_text():
    """Before the console is first used its prompt offers editor.expr.  What
    was run is kept as text and shown the next time: as it was while the
    Python it ran in lives (a server outlives a reload of its page), and
    faded, "not run in this Python", once that is gone - the variables are
    not kept, no Python object is - until "Run all again" runs the inputs
    once more.  Clear forgets it."""
    with _page(Document(sin(x) + y, addons=[ADDON])) as page:
        field = page.locator(".se-addon-console .pc-input")
        page.wait_for_function("document.querySelector('.se-addon-console .pc-input').value === 'editor.expr'")
        field.press("Enter")                                            # the offer, taken as it is
        page.wait_for_selector(".se-addon-console .pc-entry .pc-out")
        assert "sin" in page.locator(".se-addon-console .pc-entry").last.inner_text()
        _enter(page, "a = 41")
        _enter(page, "a + 1")

        # the same Python after a reload: the cells as they were, live
        _reload(page)
        page.wait_for_function("document.querySelectorAll('.se-addon-console .pc-entry').length === 3")
        assert page.locator(".se-addon-console .pc-restored").count() == 0
        assert page.locator(".se-addon-console .pc-entry").last.locator(".pc-use").count() == 1
        assert field.input_value() == ""                                  # used before: no offer
        assert "42" in _enter(page, "a + 1").inner_text()

        # a new Python (Reset here; an app started afresh): faded, not defined
        page.locator(".se-addon-console .pc-btn", has_text="Reset").click()
        page.wait_for_selector(".se-addon-console .pc-note-new")
        _reload(page)
        page.wait_for_selector(".se-addon-console .pc-restored-head")
        restored = page.locator(".se-addon-console .pc-entry.pc-restored")
        assert restored.count() == 4
        assert "a + 1" in restored.last.inner_text() and "42" in restored.last.inner_text()
        assert restored.locator(".pc-use").count() == 0                   # its Out[n] is text now
        assert "NameError" in _enter(page, "a").inner_text()              # the variables did not come back
        before = page.locator(".se-addon-console .pc-entry:not(.pc-restored)").count()
        page.locator(".se-addon-console .pc-rerun").click()
        page.wait_for_function(f"document.querySelectorAll('.se-addon-console .pc-entry:not(.pc-restored)').length === {before + 4}")
        page.wait_for_function(f"!{ED}.busy")
        assert "42" in page.locator(".se-addon-console .pc-entry").last.inner_text()
        assert "42" in _enter(page, "a + 1").inner_text()                 # and they are defined again

        page.locator(".se-addon-console .pc-btn", has_text="Clear").click()
        _reload(page)
        page.wait_for_timeout(500)
        assert page.locator(".se-addon-console .pc-entry").count() == 0


def test_the_script_is_kept_as_text_and_runs_again_after_a_reload():
    """The Script tab's file - its text and its name - is kept between
    visits (the editor's keeper: the app's files, the server's store), as
    the text it is: after a reload it is there to run again."""
    with _page(Document(sin(x) + y, addons=[ADDON])) as page:
        page.locator(".se-addon-console .pc-tab", has_text="Script").click()
        script = page.locator(".se-addon-console .pc-script")
        script.fill("k = 6 * 7\nprint('k is', k)\n")
        page.locator(".se-addon-console .pc-name").fill("answer.py")
        page.wait_for_timeout(700)                                      # kept 400 ms after the last key
        _reload(page)
        assert page.locator(".se-addon-console .pc-scripting").is_visible()   # the tab last used, too
        page.wait_for_function("document.querySelector('.se-addon-console .pc-script').value.startsWith('k = 6 * 7')")
        assert page.locator(".se-addon-console .pc-name").input_value() == "answer.py"
        page.locator(".se-addon-console .pc-btn", has_text="Run script").click()
        page.wait_for_function("document.querySelector('.se-addon-console .pc-script-out').innerText.includes('k is 42')")


def _kept(store, name):
    """What the panel kept under `name` (the add-on's names are the
    editor's `addon:<name>`), read from the server's store."""
    text = Store(store).kept("addon:console-" + name)
    return None if text is None else json.loads(text)


def test_use_is_for_the_outputs_of_this_namespace():
    """The numbers start again with every namespace, and Use sent the number
    alone: after a Reset the button beside the old ``Out[1]: 42`` put the new
    ``Out[1]``, ``x**2``, in the formula.  Once the namespace is another one
    - Reset, or ``%reset`` - the outputs above are text, as last time's are,
    and their Use is gone."""
    doc = Document(sin(x) + y, addons=[ADDON])
    with _page(doc) as page:
        uses = page.locator(".se-addon-console .pc-use")
        _enter(page, "41 + 1")
        assert uses.count() == 1
        page.locator(".se-addon-console .pc-btn", has_text="Reset").click()
        page.wait_for_selector(".se-addon-console .pc-note-new")
        page.wait_for_function(f"!{ED}.busy")
        assert uses.count() == 0                                          # 42 is not this namespace's Out[1]
        entry = _enter(page, "x**2")
        assert entry.locator(".pc-prompt-out").inner_text() == "Out[1]:" and uses.count() == 1
        _enter(page, "%reset")
        assert uses.count() == 0
        entry = _enter(page, "x**3")
        entry.locator(".pc-use").click()                                  # and this namespace's still goes
        page.wait_for_function("document.querySelector('.se-source').textContent.trim() === 'x**3'")
        # a Use that did not hear of the change - a page of before this fix - is refused by Python
        stale = page.evaluate(f"{ED}._addonCall('console', 'use', {{n: 1, token: 'of-another'}}).then(() => 'taken', e => String(e.message))")
        assert "namespace that is gone" in stale and doc.expr == x ** 3


def test_a_damaged_transcript_does_not_stop_the_others(tmp_path):
    """What is kept is a file, and one cell of it that was not as the panel
    writes it - ``items: "abc"``, an item that is null or has no text -
    raised in the middle of the restore: nothing was drawn, at every visit,
    and the cell was written back with the others until sixty new ones had
    pushed it out.  A kept history holding anything but text was recalled as
    ``[object Object]``."""
    cells = [None, 5, "text", [1], {"code": 5},
             {"code": "a", "n": 1, "token": "zz", "items": "abc"},
             {"code": "b", "n": 2, "token": "zz",
              "items": [None, {"kind": "stdout"}, {"kind": "stdout", "text": 5}, {"kind": "nope", "text": "?"},
                        {"kind": "stdout", "text": "kept"}]},
             {"code": "c", "n": "3", "token": "zz", "out": "text"},
             {"code": "d", "n": 4, "token": "zz", "out": {"text": "shown", "latex": 5}},
             {"code": "e", "n": 5, "token": "zz", "items": []}]
    Store(tmp_path).keep("addon:console-transcript", json.dumps(cells))
    Store(tmp_path).keep("addon:console-history", json.dumps([{"a": 1}, 5, None, "ok"]))
    with _page(Document(sin(x) + y, addons=[ADDON]), store=tmp_path) as page:
        page.wait_for_selector(".se-addon-console .pc-restored-head")
        restored = page.locator(".se-addon-console .pc-entry.pc-restored")
        assert [e.strip() for e in restored.locator(".pc-code").all_inner_texts()] == ["a", "b", "c", "d", "e"]
        assert restored.nth(1).locator(".pc-stream").all_inner_texts() == ["kept"]
        assert restored.nth(2).locator(".pc-prompt-in").inner_text() == "In [?]:"
        assert restored.nth(3).locator(".pc-math").inner_text() == "shown"
        field = page.locator(".se-addon-console .pc-input")
        field.click()
        recalled = []
        for _ in range(3):
            field.press("ArrowUp")
            recalled.append(field.input_value())
        assert recalled == ["ok", "ok", "ok"]                             # the one input that was one
        _enter(page, "1 + 1")
        page.wait_for_timeout(300)
        kept = _kept(tmp_path, "transcript")                                # and what is written back is clean
        assert [c["code"] for c in kept] == ["a", "b", "c", "d", "e", "1 + 1"]
        assert all(isinstance(c["items"], list) and all(set(i) <= {"kind", "text", "latex"} for i in c["items"]) for c in kept)
        assert kept[1]["items"] == [{"kind": "stdout", "text": "kept"}] and kept[2]["n"] is None
        assert _kept(tmp_path, "history") == ["ok", "1 + 1"]


def test_what_is_kept_of_a_transcript_is_bounded(tmp_path):
    """Each output was cut where it was kept, but not their number, nor the
    input, nor the whole: sixty cells of a loop that displays went into one
    JSON of megabytes, written at every run (and a browser's own storage, on
    a standalone page, holds five).  An input too long to keep whole is kept
    cut, to be read - and "Run all again" does not run half an input."""
    with _page(Document(sin(x) + y, addons=[ADDON]), store=tmp_path) as page:
        _enter(page, "for i in range(150):\n    display(x**i)\n")
        page.wait_for_timeout(300)
        kept = _kept(tmp_path, "transcript")
        assert len(kept[0]["items"]) == 41 and kept[0]["items"][-1]["text"].endswith("output cut]\n")
        for _ in range(8):
            _enter(page, "print('a'*30000); display(x); print('b'*30000); display(x); print('c'*30000)")
        page.wait_for_timeout(300)
        text = Store(tmp_path).kept("addon:console-transcript")
        kept = json.loads(text)
        assert len(text) <= 400_000 and 3 <= len(kept) < 9                 # the oldest went
        assert all(sum(len(i["text"]) for i in c["items"]) <= 60_010 for c in kept)
        page.locator(".se-addon-console .pc-btn", has_text="Clear").click()
        _enter(page, "v = 7")
        long = "w = " + "1 + " * 6000 + "1"
        _enter(page, long)
        page.wait_for_timeout(300)
        kept = _kept(tmp_path, "transcript")
        assert len(kept[1]["code"]) == 20_000 and kept[1]["cut"] is True
        page.locator(".se-addon-console .pc-btn", has_text="Reset").click()
        page.wait_for_selector(".se-addon-console .pc-note-new")
        _reload(page)
        page.wait_for_selector(".se-addon-console .pc-rerun")
        page.locator(".se-addon-console .pc-rerun").click()
        page.wait_for_selector(".se-addon-console .pc-note-bad")
        assert "too long to be kept whole" in page.locator(".se-addon-console .pc-note-bad").inner_text()
        page.wait_for_function(f"!{ED}.busy")
        assert page.locator(".se-addon-console .pc-entry:not(.pc-restored) .pc-code").all_inner_texts() == ["v = 7"]
        assert "NameError" in _enter(page, "w").inner_text()                # half of it was not run


def test_the_panel_going_keeps_the_script_and_asks_nothing_more(tmp_path):
    """The script is kept 400 ms after the last key, and taking the panel
    away (the add-on switched off) only stopped that timer: what was typed
    in the last moment was lost.  The completion menu's own timer was left
    running, and asked Python for a panel that was no longer there."""
    with _page(Document(sin(x) + y, addons=[ADDON]), store=tmp_path) as page:
        asked = []
        page.on("request", lambda r: asked.append(r.post_data) if r.method == "POST" and "complete" in (r.post_data or "") else None)
        page.evaluate("""() => {
            const typed = (el, text) => { el.value = text; el.selectionStart = el.selectionEnd = text.length;
                                          el.dispatchEvent(new Event('input', {bubbles: true})); };
            typed(document.querySelector('.se-addon-console .pc-script'), 'print(6 * 7)\\n');
            const field = document.querySelector('.se-addon-console .pc-input');
            field.focus();
            typed(field, 'fac');
            %s._unmountAddon('console');
        }""" % ED)
        assert page.locator(".se-addon-console").count() == 0
        page.wait_for_timeout(800)
        assert _kept(tmp_path, "script") == {"name": "script.py", "text": "print(6 * 7)\n"}
        assert asked == []


def test_a_script_that_was_emptied_stays_empty(tmp_path):
    """The example was put in the box whenever it was empty once the kept
    script had been read - so a script the user had emptied came back as the
    example at the next visit."""
    with _page(Document(sin(x) + y, addons=[ADDON]), store=tmp_path) as page:
        page.locator(".se-addon-console .pc-tab", has_text="Script").click()
        script = page.locator(".se-addon-console .pc-script")
        page.wait_for_function("document.querySelector('.se-addon-console .pc-script').value.includes('editor.expr')")   # the example, at first
        script.fill("")
        page.wait_for_timeout(700)                                      # kept 400 ms after the last key
        assert _kept(tmp_path, "script")["text"] == ""
        _reload(page)
        page.wait_for_timeout(700)
        assert page.locator(".se-addon-console .pc-scripting").is_visible()
        assert script.input_value() == ""

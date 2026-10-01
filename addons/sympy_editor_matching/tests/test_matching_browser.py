"""The rules panel in a real browser: a rule is added, edited in place,
opened in the formula editor and saved back.  Needs Playwright with
Chromium, the KaTeX CDN and sympy-matching (skipped otherwise)."""
import json
import sys
import threading
import time
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
from sympy import cos, sin, symbols, tan

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("sympy_matching")
playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_matching import ADDON, MatchingAddon, RewriteRule, rule_text  # noqa: E402

x, y, z = symbols("x y z")

ED = "document.querySelector('.sympy-editor').__sympyEditor"


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def test_a_rule_can_be_edited_as_text_and_in_the_editor():
    doc = Document(sin(x) ** 2, addons=[ADDON])
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
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(srv.url)
                page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
                page.wait_for_selector(".se-addon-matching .mt-field", timeout=10000)
                page.locator(".mt-field").fill("sin(a_)**2 -> 1 - cos(a_)**2")
                page.locator(".mt-field").press("Enter")
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 1")
                # the rule is drawn as a formula (KaTeX), the wildcard underlined - not shown as Rule(...)
                page.wait_for_selector(".mt-rules li .mt-formula .katex", timeout=10000)
                assert "Rule(" not in page.locator(".mt-rules li .mt-formula").inner_text()
                assert page.locator(".mt-rules li .mt-formula .underline").count() >= 1
                # an optional wildcard: a dotted underline (dots set under the letter), no brackets
                page.locator(".mt-field").fill("_c_*x -> z")
                page.locator(".mt-field").press("Enter")
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 2")
                second = page.locator(".mt-rules li").nth(1).locator(".mt-formula")
                assert second.locator(".katex").count() == 1 and "…" in second.inner_text() and "[" not in second.inner_text()
                page.locator(".mt-rules li").nth(1).locator(".mt-del").click()
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 1")
                # in place: the pencil shows the text form, Enter saves it
                page.locator(".mt-rules li .mt-edit").click()
                field = page.locator(".mt-rules li input")
                assert field.input_value() == "sin(a_)**2 -> 1 - cos(a_)**2"
                field.fill("sin(a_)**2 -> 1/2 - cos(2*a_)/2")
                field.press("Enter")
                page.wait_for_function("document.querySelector('.mt-rules li .mt-formula') !== null")
                page.wait_for_function("document.querySelector('.mt-hit .mt-result') && document.querySelector('.mt-hit .mt-result').textContent.includes('cos(2*x)')")
                assert "cos(2*a_)" in ADDON.rules(doc)[0].__str__()
                # in the editor: the rule becomes the formula, its side is edited there, Save puts it back
                page.locator(".mt-rules li .mt-open").click()
                page.wait_for_function("document.querySelector('.se-source').textContent.startsWith('Rule(')")
                assert isinstance(doc.expr, RewriteRule)
                page.wait_for_function("document.querySelector('.se-addon-matching .mt-head button').textContent === 'Save as rule 1'")
                page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.send({action: 'set', src: 'Rule(sin(a_)**2, 1 - cos(a_)**2)'})")
                page.wait_for_function("document.querySelector('.se-source').textContent === 'Rule(sin(a_)**2, 1 - cos(a_)**2)'")
                page.locator(".se-addon-matching .mt-head button", has_text="Save as rule 1").click()
                page.wait_for_function("document.querySelector('.se-addon-matching .mt-head button').textContent === 'Use selection as rule'")
                assert str(ADDON.rules(doc)[0]) == "Rule(sin(a_)**2, 1 - cos(a_)**2)"
                page.locator('.se-toolbar [data-cmd="undo"]').click()   # the formula comes back (two steps: open, set)
                page.wait_for_function("document.querySelector('.se-source').textContent.startsWith('Rule(sin(a_)**2, 1/2')")
                page.locator('.se-toolbar [data-cmd="undo"]').click()
                page.wait_for_function("document.querySelector('.se-source').textContent === 'sin(x)**2'")
                # the panel's "?" opens the add-on's guide in the editor's help overlay
                page.locator(".se-addon-matching .se-addon-help").click()
                guide = page.locator(".se-help-view")
                assert guide.is_visible() and "wildcard" in guide.inner_text().lower()
                page.keyboard.press("Escape")
                assert page.locator(".se-help-view").count() == 0
                assert errors == []
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_rewrite_is_one_pass_and_rewrite_all_is_refused_when_it_never_settles():
    doc = Document(x + sin(x) / x, addons=[ADDON])
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
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(srv.url)
                page.wait_for_selector(".se-addon-matching .mt-field", timeout=30000)
                page.locator(".mt-field").fill("x -> x**2")
                page.locator(".mt-field").press("Enter")
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 1")
                buttons = page.locator(".se-addon-matching .mt-head button")
                buttons.filter(has_text="Rewrite").nth(0).click()                       # one pass: every x, once
                page.wait_for_function("document.querySelector('.se-source').textContent === 'x**2 + sin(x**2)/x**2'")
                assert doc.expr == x ** 2 + sin(x ** 2) / x ** 2
                buttons.filter(has_text="Rewrite all").click()                           # never settles: refused
                page.wait_for_function("!document.querySelector('.se-error').hidden && document.querySelector('.se-error').textContent.includes('did not settle')")
                assert doc.expr == x ** 2 + sin(x ** 2) / x ** 2
                assert errors == []
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _wait(check, timeout=5.0):
    """True once ``check`` is - a file the server writes, say."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if check():
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def test_rule_sets_are_kept_and_come_back_after_a_reload(tmp_path):
    """The sets are kept where the page is run from - the server's own store
    here, the app's storage on a phone, the browser only on a page that is
    nothing but itself (SympyEditor.keep)."""
    doc = Document(sin(x) ** 2, addons=[ADDON])
    srv = EditorServer(doc, port=0, store=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(srv.url)
                page.wait_for_selector(".se-addon-matching .mt-field", timeout=30000)
                page.locator(".mt-field").fill("sin(a_)**2 -> 1 - cos(a_)**2")
                page.locator(".mt-field").press("Enter")
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 1")
                page.locator(".mt-name").fill("trig")
                page.locator(".mt-name").press("Enter")                       # the name is the saving: no Save button
                assert page.locator(".se-addon-matching .mt-sets button", has_text="Save").count() == 0
                page.wait_for_function("document.querySelector('.mt-lib').options.length === 2")
                assert doc.addon_state["matching"]["name"] == "trig"
                kept = tmp_path / "addon_matching.json"
                assert _wait(lambda: kept.is_file())
                stored = json.loads(kept.read_text(encoding="utf-8"))
                assert stored["name"] == "trig" and list(stored["library"]) == ["trig"]
                assert [r["text"] for r in stored["library"]["trig"]] == ["sin(a_)**2 -> 1 - cos(a_)**2"]
                assert page.evaluate("localStorage.getItem('sympy-editor:addon:matching')") is None
                # the document forgets everything (a kernel restarted, say); the page is
                # loaded again: the library is there, and so is the last current set -
                # the server kept them, so another browser would find them too
                doc.addon_state["matching"] = {}
                page.goto(srv.url)
                page.wait_for_selector(".se-addon-matching .mt-field", timeout=30000)
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 1", timeout=10000)
                assert page.locator(".mt-name").input_value() == "trig"
                assert [o.text_content() for o in page.locator(".mt-lib option").all()][1:] == ["trig"]
                assert [str(r) for r in doc.addon_state["matching"]["rules"]] == ["Rule(sin(a_)**2, 1 - cos(a_)**2)"]
                # a change to the named set saves itself; Revert steps back, Restore forward
                assert page.locator(".mt-revert").is_disabled() and page.locator(".mt-restore").is_disabled()
                page.locator(".mt-field").fill("x -> x**2")
                page.locator(".mt-field").press("Enter")
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 2")
                assert _wait(lambda: json.loads(kept.read_text(encoding="utf-8"))["library"]["trig"] and
                         len(json.loads(kept.read_text(encoding="utf-8"))["library"]["trig"]) == 2)
                page.locator(".mt-revert").click()
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 1")
                assert _wait(lambda: len(json.loads(kept.read_text(encoding="utf-8"))["library"]["trig"]) == 1)
                page.locator(".mt-restore").click()
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 2")
                page.locator(".mt-revert").click()
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 1")
                page.locator(".mt-lib-del").click()                            # delete it: gone from the store too
                page.wait_for_function("document.querySelector('.mt-lib').options.length === 1")
                assert _wait(lambda: json.loads(kept.read_text(encoding="utf-8"))["library"] == {})
                assert errors == []
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_rule_sets_go_to_their_own_editor_s_keeper(tmp_path):
    """A page with a second editor made after the rules panel's - a read-only
    view beside it, which keeps nothing - still keeps the sets in the store of
    the panel's own editor, the server's: not in the browser, where the page
    used to put them once the last editor made could not keep."""
    doc = Document(sin(x) ** 2, addons=[ADDON])
    srv = EditorServer(doc, port=0, store=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(srv.url)
                page.wait_for_selector(".se-addon-matching .mt-field", timeout=30000)
                page.evaluate("""() => {
                    const host = document.createElement('div');
                    host.id = 'second';
                    document.body.appendChild(host);
                    SympyEditor.mount(host, {backend: 'readonly', snapshot: {latex: 'y', nodes: {}, spans: {}},
                                             options: {}});
                }""")
                page.wait_for_selector("#second .se-view", timeout=10000)
                page.locator(".mt-field").fill("sin(a_)**2 -> 1 - cos(a_)**2")
                page.locator(".mt-field").press("Enter")
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 1")
                page.locator(".mt-name").fill("trig")
                page.locator(".mt-name").press("Enter")
                kept = tmp_path / "addon_matching.json"
                assert _wait(lambda: kept.is_file() and "trig" in json.loads(kept.read_text(encoding="utf-8"))["library"])
                assert page.evaluate("localStorage.getItem('sympy-editor:addon:matching')") is None
                assert errors == []
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_rename_button_gives_the_saved_set_a_new_name(tmp_path):
    """Rename puts the name field in rename mode: Enter moves the saved set
    to the new name (no copy left under the old one), Esc keeps it."""
    doc = Document(sin(x) ** 2, addons=[ADDON])
    srv = EditorServer(doc, port=0, store=tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(srv.url)
                page.wait_for_selector(".se-addon-matching .mt-field", timeout=30000)
                rename = page.locator(".mt-lib-rename")
                assert rename.is_disabled()                                    # nothing saved yet
                page.locator(".mt-field").fill("sin(a_)**2 -> 1 - cos(a_)**2")
                page.locator(".mt-field").press("Enter")
                page.wait_for_function("document.querySelectorAll('.mt-rules li').length === 1")
                page.locator(".mt-name").fill("trig")
                page.locator(".mt-name").press("Enter")
                page.wait_for_function("document.querySelector('.mt-lib').options.length === 2")
                assert not rename.is_disabled()
                # Esc: the old name stays
                rename.click()
                assert page.evaluate("document.activeElement.classList.contains('mt-renaming')")
                page.keyboard.type("other")
                page.keyboard.press("Escape")
                assert page.locator(".mt-name").input_value() == "trig"
                assert sorted(doc.addon_state["matching"]["library"]) == ["trig"]
                # Enter: renamed, not copied
                rename.click()
                page.keyboard.type("identities")
                page.keyboard.press("Enter")
                page.wait_for_function("[...document.querySelectorAll('.mt-lib option')].map(o => o.value).join() === ',identities'")
                assert page.locator(".mt-name").input_value() == "identities"
                assert sorted(doc.addon_state["matching"]["library"]) == ["identities"]
                assert doc.addon_state["matching"]["name"] == "identities"
                kept = tmp_path / "addon_matching.json"
                assert _wait(lambda: kept.is_file() and list(json.loads(kept.read_text(encoding="utf-8"))["library"]) == ["identities"])
                assert not page.locator(".mt-name.mt-renaming").count()
                assert errors == []
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


@contextmanager
def _panel(doc, store, options=None):
    """The page of ``doc`` with the rules panel up, served from ``store`` (a
    folder of the test's own); ``page.srv`` is the server, whose document
    changes when a session is opened.  No page error is let through."""
    srv = EditorServer(doc, port=0, store=store, **({"options": options} if options else {}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(srv.url)
                page.wait_for_selector(".se-addon-matching .mt-field", timeout=30000)
                page.wait_for_function(f"{ED} && {ED}.state && !{ED}.busy")
                page.srv = srv
                yield page
                assert errors == []
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()


def _rows(page, n):
    page.wait_for_function(f"document.querySelectorAll('.mt-rules li').length === {n}")
    page.wait_for_function(f"!{ED}.busy")


def _add(page, text):
    n = page.locator(".mt-rules li").count()
    page.locator(".mt-row .mt-field").fill(text)
    page.locator(".mt-row .mt-field").press("Enter")
    _rows(page, n + 1)


def _rules(doc):
    return [rule_text(r) for r in doc.addon_state["matching"]["rules"]]


def _shown(page):
    return [li.get_attribute("title").split("  (")[0] for li in page.locator(".mt-rules li .mt-formula").all()]


def test_the_panel_follows_the_document_into_another_session(tmp_path):
    """The panel asked Python once, at mount.  A session opened is another
    document: the panel went on showing the rule and the name of the one
    before, × on that rule left it on the screen (Python had no such rule),
    and the library - which is there to outlive a session - was gone until
    the page was loaded again."""
    first = Document(sin(x) + sin(y), addons=[MatchingAddon()])
    with _panel(first, tmp_path, options={"sessions": True}) as page:
        page.wait_for_function(f"{ED}._sessionsReady && !{ED}.busy")
        _add(page, "sin(a_) -> cos(a_)")
        page.locator(".mt-name").fill("trig")
        page.locator(".mt-name").press("Enter")
        page.wait_for_function("document.querySelector('.mt-lib').options.length === 2")
        kept = tmp_path / "addon_matching.json"
        assert _wait(lambda: list(json.loads(kept.read_text(encoding="utf-8"))["library"]) == ["trig"])
        one = page.evaluate(f"{ED}._sessionStore.current")
        assert page.evaluate(f"{ED}.newSession('Symbol(\\'q\\')')")
        page.wait_for_function(f"{ED}.state.src === 'q' && !{ED}.busy")
        second = page.srv.document
        assert second is not first and str(second.expr) == "q"
        # the library is there in the new document, and the panel shows what Python has:
        # the set that was in use, which a document with no rules of its own starts with
        page.wait_for_function("document.querySelector('.mt-lib').options.length === 2")
        assert _wait(lambda: sorted(second.addon_state["matching"]["library"]) == ["trig"])
        _rows(page, 1)
        assert _rules(second) == ["sin(a_) -> cos(a_)"] == [rule_text(r) for r in second.addon_state["matching"]["library"]["trig"]]
        assert page.locator(".mt-name").input_value() == "trig" == second.addon_state["matching"]["name"]
        # the set gets a name of its own here, and × removes the rule: in Python, so on the screen
        page.locator(".mt-name").fill("none")
        page.locator(".mt-name").press("Enter")
        page.wait_for_function("document.querySelector('.mt-lib').options.length === 3")
        page.locator(".mt-rules li .mt-del").click()
        _rows(page, 0)
        assert _rules(second) == [] and page.locator(".se-error").is_hidden()
        # back in the first session: its own rules, as it left them
        assert page.evaluate(f"{ED}.openSession('{one}')")
        page.wait_for_function(f"{ED}.state.src === 'sin(x) + sin(y)' && !{ED}.busy")
        _rows(page, 1)
        third = page.srv.document
        assert third is not second and _rules(third) == ["sin(a_) -> cos(a_)"]
        assert page.locator(".mt-name").input_value() == "trig"
        page.wait_for_function("document.querySelector('.mt-lib').options.length === 3")      # what the other session saved too
        assert sorted(third.addon_state["matching"]["library"]) == ["none", "trig"]
        page.wait_for_function("document.querySelector('.mt-hit .mt-result') !== null || true")
        page.evaluate(f"{ED}.select('/')")
        page.locator(".se-addon-matching .mt-head button", has_text="Rewrite").first.click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'cos(x) + cos(y)'")


def test_the_rule_open_in_the_editor_is_followed_when_one_above_it_is_removed(tmp_path):
    """Rule 2 of three opened in the editor, rule 1 removed: the button went
    on reading "Save as rule 2", and saved over what had come to stand
    second - the third rule, which was gone, the opened one twice in the
    list."""
    doc = Document(sin(x), addons=[MatchingAddon()])
    with _panel(doc, tmp_path) as page:
        for text in ("sin(a_) -> 1", "cos(a_) -> 2", "tan(a_) -> 3"):
            _add(page, text)
        page.locator(".mt-rules li").nth(1).locator(".mt-open").click()
        page.wait_for_function(f"{ED}.state.src.indexOf('Rule(cos') === 0 && !{ED}.busy")
        save = page.locator(".se-addon-matching .mt-head button").first
        page.wait_for_function("document.querySelector('.se-addon-matching .mt-head button').textContent === 'Save as rule 2'")
        assert page.locator(".mt-rules li.mt-editing").get_attribute("data-index") == "1"
        page.locator(".mt-rules li").nth(0).locator(".mt-del").click()
        _rows(page, 2)
        assert save.inner_text() == "Save as rule 1"
        assert page.locator(".mt-rules li.mt-editing").get_attribute("data-index") == "0"
        page.evaluate(f"{ED}.send({{action: 'replace', path: '/1', src: '5'}})")
        page.wait_for_function(f"{ED}.state.src === 'Rule(cos(a_), 5)' && !{ED}.busy")
        page.evaluate(f"{ED}.select('/')")
        save.click()
        page.wait_for_function("document.querySelector('.se-addon-matching .mt-head button').textContent === 'Use selection as rule'")
        assert _rules(doc) == ["cos(a_) -> 5", "tan(a_) -> 3"]
        # a rule below the open one goes without moving it; the open one itself ends the saving
        page.locator(".mt-rules li").nth(0).locator(".mt-open").click()
        page.wait_for_function("document.querySelector('.se-addon-matching .mt-head button').textContent === 'Save as rule 1'")
        page.locator(".mt-rules li").nth(1).locator(".mt-del").click()
        _rows(page, 1)
        assert save.inner_text() == "Save as rule 1"
        page.locator(".mt-rules li").nth(0).locator(".mt-del").click()
        _rows(page, 0)
        assert save.inner_text() == "Use selection as rule"


def test_escape_leaves_a_rule_edited_as_text_as_it_was(tmp_path):
    """The field says "Esc cancels", and Esc saved: leaving the field draws
    the list again, the field taken out of the page fires its blur, and the
    blur saved what was typed."""
    doc = Document(sin(x), addons=[MatchingAddon()])
    with _panel(doc, tmp_path) as page:
        _add(page, "sin(a_) -> cos(a_)")
        page.locator(".mt-rules li .mt-edit").click()
        field = page.locator(".mt-rules li input")
        field.fill("sin(a_) -> tan(a_)")
        field.press("Escape")
        page.wait_for_selector(".mt-rules li .mt-formula")
        page.wait_for_timeout(400)                       # the blur's own turn, and the request it sent
        page.wait_for_function(f"!{ED}.busy")
        assert _rules(doc) == ["sin(a_) -> cos(a_)"] and _shown(page) == ["Rule(sin(a_), cos(a_))"]
        # Enter saves, and so does leaving the field - once
        page.locator(".mt-rules li .mt-edit").click()
        page.locator(".mt-rules li input").fill("sin(a_) -> tan(a_)")
        page.locator(".mt-row .mt-field").click()
        page.wait_for_function("document.querySelector('.mt-rules li .mt-formula') !== null "
                               "&& document.querySelector('.mt-rules li .mt-formula').title.indexOf('tan') > 0")
        assert _rules(doc) == ["sin(a_) -> tan(a_)"]
        # a rule that is refused leaves the field there, to be put right
        page.locator(".mt-rules li .mt-edit").click()
        field = page.locator(".mt-rules li input")
        field.fill("sin(a_) -> tan(b_)")
        field.press("Enter")
        page.wait_for_function("!document.querySelector('.se-error').hidden && document.querySelector('.se-error').textContent.includes('b_')")
        assert field.input_value() == "sin(a_) -> tan(b_)" and _rules(doc) == ["sin(a_) -> tan(a_)"]
        field.fill("sin(a_) -> 1/tan(a_)")
        field.press("Enter")
        page.wait_for_function("document.querySelector('.mt-rules li .mt-formula') !== null")
        assert _rules(doc) == ["sin(a_) -> 1/tan(a_)"]


def test_a_range_is_what_the_panel_matches_and_rewrites(tmp_path):
    """With the first two terms of ``sin(x) + sin(y) + sin(z)`` selected,
    Rewrite rewrote all three: the panel read the selection and never the
    range.  And a rule over two terms could not be applied to two terms of a
    longer sum."""
    doc = Document(sin(x) + sin(y) + sin(z), addons=[MatchingAddon()])
    with _panel(doc, tmp_path) as page:
        _add(page, "sin(a_) -> cos(a_)")
        page.evaluate(f"{ED}._setRange('/', 0, 1)")
        assert page.evaluate(f"{ED}._rangeSource({ED}._rangePaths())") == "sin(x) + sin(y)"
        page.wait_for_function("document.querySelector('.mt-hits').textContent.indexOf('sin(x) + sin(y)') >= 0")
        page.locator(".se-addon-matching .mt-head button", has_text="Rewrite").first.click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'sin(z) + cos(x) + cos(y)'")
        assert doc.expr == cos(x) + cos(y) + sin(z)
        # two terms that a rule takes together, in a sum of three
        page.evaluate(f"{ED}.send({{action: 'set', src: 'sin(x)**2 + cos(x)**2 + tan(x)'}})")
        page.wait_for_function(f"{ED}.state.src === 'sin(x)**2 + cos(x)**2 + tan(x)' && !{ED}.busy")
        _add(page, "sin(a_)**2 + cos(a_)**2 -> 1")
        kids = page.evaluate(f"{ED}._displayChildren('/').map(function (p) {{ return {ED}.state.nodes[p].src; }})")
        assert kids[:2] == ["sin(x)**2", "cos(x)**2"]
        page.evaluate(f"{ED}._setRange('/', 0, 1)")
        page.wait_for_selector(".mt-hit .mt-apply")
        assert page.locator(".mt-hit .mt-bind").inner_text() == "a = x" and page.locator(".mt-hit .mt-result").inner_text() == "→ 1"
        page.locator(".mt-hit .mt-apply").click()
        page.wait_for_function("document.querySelector('.se-source').textContent === 'tan(x) + 1'")
        assert doc.expr == tan(x) + 1
        # a range is not a rule to use
        assert page.locator(".se-addon-matching .mt-head button").first.is_disabled()


def test_apply_applies_the_match_it_stands_by(tmp_path):
    """A rule that matches in two ways was listed twice with the result of
    the first, and either Apply applied the first."""
    doc = Document(x + y, addons=[MatchingAddon()])
    with _panel(doc, tmp_path) as page:
        _add(page, "a_ + b_ -> a_ - b_")
        listed = ("[...document.querySelectorAll('.mt-hit')].map(h => h.querySelector('.mt-bind').textContent + ' ' + "
                  "h.querySelector('.mt-result').textContent).sort().join('; ') === 'a = x,  b = y → x - y; a = y,  b = x → -x + y'")
        for wanted in ("x - y", "-x + y"):
            page.evaluate(f"{ED}.select('/')")
            page.wait_for_function(listed)                        # the matches of x + y, each with its own result
            page.locator(".mt-hit", has_text="→ " + wanted).locator(".mt-apply").click()
            page.wait_for_function(f"document.querySelector('.se-source').textContent === {json.dumps(wanted)}")
            page.wait_for_function(f"!{ED}.busy")
            assert str(doc.expr) == wanted
            page.locator('.se-toolbar [data-cmd="undo"]').click()
            page.wait_for_function("document.querySelector('.se-source').textContent === 'x + y'")
            page.wait_for_function(f"!{ED}.busy")


@pytest.mark.parametrize("kept", [[1, 2], "abc", {"library": {"bad": 5}}, {"library": ["a"], "rules": 7}, 5])
def test_what_the_keeper_kept_wrong_does_not_hide_the_rules(tmp_path, kept):
    """With anything but a rule set kept under the panel's name - another
    version's, a file edited by hand - Python refused the panel's first
    question, and the panel showed no rule while Python had one: at every
    start, since nothing was ever written over what was kept."""
    (tmp_path / "addon_matching.json").write_text(json.dumps(kept), encoding="utf-8")
    doc = Document(sin(x), addons=[MatchingAddon(rules=[(sin(x), cos(x))])])
    with _panel(doc, tmp_path) as page:
        _rows(page, 1)
        assert _shown(page) == ["Rule(sin(x), cos(x))"] and _rules(doc) == ["sin(x) -> cos(x)"]
        assert page.locator(".se-error").is_hidden()
        # ... and what is kept from now on is a rule set again
        assert _wait(lambda: [r["text"] for r in json.loads((tmp_path / "addon_matching.json").read_text(encoding="utf-8"))["rules"]]
                     == ["sin(x) -> cos(x)"])


def test_a_refused_rule_and_a_refused_name_say_why(tmp_path):
    """A wildcard of the replacement alone went into the formula, a
    condition that could not be kept lost the rule at the next start, and a
    name typed over another saved set replaced that set: each is refused on
    the spot, the reason in the error line, the panel as it was."""
    doc = Document(x + 1, addons=[MatchingAddon()])
    with _panel(doc, tmp_path) as page:
        field = page.locator(".mt-row .mt-field")
        for text, word in (("x -> x + b_", "b_ in the replacement"), ("a_**2 -> a_ if Q.positive(a_)", "cannot be kept")):
            field.fill(text)
            field.press("Enter")
            page.wait_for_function(f"!document.querySelector('.se-error').hidden && document.querySelector('.se-error').textContent.includes({json.dumps(word)})")
            assert field.input_value() == text and page.locator(".mt-rules li").count() == 0       # to be put right
        _add(page, "x -> x + 2")
        page.locator(".mt-name").fill("one")
        page.locator(".mt-name").press("Enter")
        page.wait_for_function("document.querySelector('.mt-lib').options.length === 2")
        page.locator(".mt-name").fill("two")
        page.locator(".mt-name").press("Enter")
        page.wait_for_function("document.querySelector('.mt-lib').options.length === 3")
        _add(page, "x -> x + 3")
        page.locator(".mt-name").fill("one")
        page.locator(".mt-name").press("Enter")
        page.wait_for_function("!document.querySelector('.se-error').hidden && document.querySelector('.se-error').textContent.includes('saved already')")
        page.wait_for_function("document.querySelector('.mt-name').value === 'two'")
        state = doc.addon_state["matching"]
        assert state["name"] == "two" and [rule_text(r) for r in state["library"]["one"]] == ["x -> x + 2"]
        # a rewrite with nothing to do says so, and is no step of the history
        page.locator(".mt-rules li").nth(1).locator(".mt-del").click()
        _rows(page, 1)
        page.locator(".mt-rules li").nth(0).locator(".mt-del").click()
        _rows(page, 0)
        _add(page, "sin(a_) -> cos(a_)")
        page.locator(".se-addon-matching .mt-head button", has_text="Rewrite all").click()
        page.wait_for_function("!document.querySelector('.se-error').hidden && document.querySelector('.se-error').textContent.includes('No rule matches')")
        assert not doc.can_undo and page.locator('.se-toolbar [data-cmd="undo"]').is_disabled()

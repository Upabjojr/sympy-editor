"""The sessions and what the page keeps, in a real browser: opening a session
that cannot be opened, a file handed over while Python is busy, deleting and
starting sessions while busy, the storage seam (Keep) with a store that
keeps nothing or fails once, sessions with the HTTP server, the native
backend closing what it left, one keeper per editor, and the widget's
request ids.  Same requirements as test_browser.py (Playwright, Chromium,
the KaTeX CDN); skipped otherwise."""

import json
import threading
import time

import pytest
from sympy import symbols

from sympy_editor import Document
from sympy_editor.html import build_config, default_urls
from sympy_editor.ops import Op
from sympy_editor.server import EditorServer

from test_browser import (  # noqa: F401 - fixtures
    _HOST_STUB, _close_what_the_test_opened, _open, _open_hosted, _wait, browser, pytestmark)

x, y, z = symbols("x y z")

ED = "document.querySelector('.sympy-editor').__sympyEditor"
KATEX = {"katexJs": default_urls()["katexJs"], "katexCss": default_urls()["katexCss"]}


def _slow(expr):
    time.sleep(1.5)
    return expr + 1


@pytest.fixture
def serving():
    """serving(doc, **kwargs) -> a running EditorServer; stopped at teardown."""
    servers = []

    def _serve(doc, **kwargs):
        srv = EditorServer(doc, port=0, **kwargs)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv

    yield _serve
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def _kept(folder):
    path = folder / "sessions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _current(store):
    return [s for s in store["list"] if s["id"] == store["current"]][0]


def _last_step(folder):
    """The current step of the current session the store holds ("" if none)."""
    store = _kept(folder)
    state = store and _current(store).get("state")
    return state["history"][state["index"]] if state else ""


def _until(page, predicate, timeout=5.0):
    """_wait, letting the page's events (a route's handler) run meanwhile."""
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        page.wait_for_timeout(50)
    return predicate()


def _ready(page, ed=ED):
    page.wait_for_function(f"() => {ed}._sessionsReady === true", timeout=30000)


# -- the native backend: a stub host over the server's one document ----------

_NATIVE_HOST = """([api, token]) => {
    const post = (body) => fetch(api, {method: 'POST', body: JSON.stringify(body),
        headers: {'Content-Type': 'application/json', 'X-SymPy-Editor-Token': token}}).then(r => r.text());
    const created = {};
    window.__calls = [];
    window.SympyEditorPy = {
        newDoc(req, id, srepr, settings) {
            window.__calls.push(['newDoc', id]);
            const s = JSON.parse(settings);
            post(s.history ? {action: 'load', state: s} : {action: 'snapshot'}).then(t => {
                const snap = JSON.parse(t);
                if (snap.error) { window.__sympyEditorNative(req, false, snap.error); return; }
                created[id] = true;
                window.__sympyEditorNative(req, true, t);
            });
        },
        handle(req, id, message) {
            window.__calls.push(['handle', id, JSON.parse(message).action]);
            if (!created[id]) { window.__sympyEditorNative(req, false, "KeyError: Unknown document '" + id + "'"); return; }
            post(JSON.parse(message)).then(t => window.__sympyEditorNative(req, true, t));
        },
        close(req, id) { window.__calls.push(['close', id]); delete created[id]; window.__sympyEditorNative(req, true, 'null'); }
    };
    const host = document.createElement('div');
    host.id = 'native-host';
    document.body.appendChild(host);
}"""

NATIVE = "window.__nativeEditor"


def _native_page(browser, srv, doc, sessions):
    """A page with an editor on the native backend, its sessions seeded."""
    cfg = build_config(doc, backend="native", options=dict(KATEX, sessions=True))
    page = _open(browser, srv.url)                  # the same origin, for the stub's fetch()
    page.evaluate(_NATIVE_HOST, [srv.url.rstrip("/") + "/api", srv.token])
    page.evaluate("(s) => localStorage.setItem('sympy-editor:sessions', JSON.stringify(s))", sessions)
    page.evaluate("(cfg) => { window.__nativeEditor = SympyEditor.mount(document.getElementById('native-host'), cfg); }", cfg)
    page.wait_for_selector("#native-host .se-view .katex [data-path]", timeout=30000)
    _ready(page, NATIVE)
    return page


def _session(sid, name, history, updated, **extra):
    state = dict({"format": 1, "history": history, "index": len(history) - 1, "labels": [None] * len(history),
                  "symbols": []}, **extra)
    return {"id": sid, "name": name, "updated": updated, "state": state, "title": True}


def test_a_session_that_cannot_be_opened_does_not_break_the_editor(browser, serving):
    """Bug: the native backend switched to the new document's id before the
    host had made it, so one refused session (symbols it cannot read) left
    every later request on a document that never came to be - "Unknown
    document", at every launch, for good.  Now the stored session stays in
    the list, marked, the editor starts a fresh one, and editing works."""
    doc = Document(x + y)
    srv = serving(doc, store=False)
    bad = _session("bad", "unreadable", ["Symbol('x')"], 3, symbols=["garbage("])
    good = _session("good", "a good one", ["Symbol('z')", "Pow(Symbol('z'), Integer(2))"], 1)
    page = _native_page(browser, srv, doc, {"current": "bad", "list": [bad, good]})
    assert "could not be opened" in page.locator("#native-host .se-error").inner_text()
    store = page.evaluate(NATIVE + "._sessionStore")
    marked = [s for s in store["list"] if s["id"] == "bad"][0]
    assert marked.get("broken") and store["current"] not in ("bad", "good")        # a fresh one, the bad one kept
    # the editor still edits: its document is the one that was made
    page.evaluate(NATIVE + ".send({action: 'replace', path: '/', src: 'cos(x)'})")
    page.wait_for_function(NATIVE + ".state.src === 'cos(x)'", timeout=10000)
    assert page.evaluate(NATIVE + ".state.error") is None
    # opening it again fails again, and still breaks nothing
    assert page.evaluate(NATIVE + ".openSession('bad')") is False
    page.evaluate(NATIVE + ".send({action: 'replace', path: '/', src: 'sin(x)'})")
    page.wait_for_function(NATIVE + ".state.src === 'sin(x)'", timeout=10000)
    assert not any("Unknown document" in (c[2] if len(c) > 2 else "") for c in page.evaluate("window.__calls"))
    assert page.errors == []


def test_the_native_backend_closes_the_document_it_leaves(browser, serving):
    """Every session opened made a document in the host and none was ever
    dropped: the host's Python grew with every switch.  After a successful
    open the old one is closed (the host's `close`, when it has one)."""
    doc = Document(x + y)
    srv = serving(doc, store=False)
    good = _session("good", "a good one", ["Symbol('z')", "Pow(Symbol('z'), Integer(2))"], 1)
    page = _native_page(browser, srv, doc, {"current": None, "list": [good]})
    first = [c[1] for c in page.evaluate("window.__calls") if c[0] == "newDoc"][-1]
    assert page.evaluate(NATIVE + ".openSession('good')") is True
    assert page.evaluate(NATIVE + ".state.src") == "z**2"
    calls = page.evaluate("window.__calls")
    assert ["close", first] in calls, calls
    # the document now open is the new one: an edit reaches it
    page.evaluate(NATIVE + ".send({action: 'undo'})")
    page.wait_for_function(NATIVE + ".state.src === 'z'", timeout=10000)
    assert page.errors == []


# -- the HTTP server: sessions kept in its store ------------------------------

def test_the_server_keeps_sessions_and_opens_them(browser, serving, tmp_path):
    """Bug: sessions were never saved with the server (or the widget): the
    editor only started them for a backend that could open a document, and
    the server's could not.  It now opens a session by swapping its
    document ("load"); the sessions and their histories are kept in its store."""
    doc = Document(x + y)
    srv = serving(doc, options={"sessions": True}, store=tmp_path)
    page = _open(browser, srv.url)
    _ready(page)
    page.evaluate(ED + ".send({action: 'replace', path: '/', src: 'cos(x)'})")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'cos(x)'", timeout=10000)
    page.evaluate(ED + ".flush()")
    assert _wait(lambda: "cos" in _last_step(tmp_path), timeout=5)
    first = _kept(tmp_path)["current"]
    # a second session, then back to the first: its history comes back with it
    assert page.evaluate(ED + ".newSession('current')") is True
    assert _wait(lambda: _kept(tmp_path)["current"] != first)
    page.evaluate(ED + ".send({action: 'replace', path: '/', src: 'tan(x)'})")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'tan(x)'", timeout=10000)
    assert page.evaluate("(id) => %s.openSession(id)" % ED, first) is True
    assert str(srv.document.expr) == "cos(x)" and srv.document.can_undo
    page.evaluate(ED + ".send({action: 'undo'})")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'x + y'", timeout=10000)
    assert page.errors == []


def test_a_page_opened_again_keeps_the_document_it_was_given(browser, serving, tmp_path):
    """serve(expr) and a widget are handed the expression to edit: the last
    session kept must not be opened over it.  A new session holds it; the
    old sessions are still listed."""
    other = Document(z)
    other.replace("/", "z**3")
    (tmp_path / "sessions.json").write_text(json.dumps({"current": "old", "list": [
        {"id": "old", "name": "z**3", "updated": 1, "state": other.export()}]}), encoding="utf-8")
    doc = Document(x + y)
    srv = serving(doc, options={"sessions": True}, store=tmp_path)
    page = _open(browser, srv.url)
    _ready(page)
    assert page.evaluate(ED + ".state.src") == "x + y" and str(srv.document.expr) == "x + y"
    store = page.evaluate(ED + "._sessionStore")
    assert store["current"] != "old" and [s["id"] for s in store["list"]].count("old") == 1
    assert page.errors == []


def test_a_file_handed_over_while_python_is_busy_waits_its_turn(browser, serving, tmp_path):
    """Bug: a file opened with the app while a computation ran went into the
    session that was open - openSession refused silently (busy), and the
    file then replaced that session's history - and an empty session was
    left behind.  The file now waits for Python, and gets its own session."""
    doc = Document(x, ops={"slow": Op("slow", "Slow", _slow)})
    srv = serving(doc, options={"sessions": True}, store=tmp_path)
    page = _open(browser, srv.url)
    _ready(page)
    page.evaluate("() => { %s.send({action: 'apply', path: '/', op: 'slow'}); }" % ED)   # not awaited
    page.wait_for_function(ED + ".busy === true", timeout=5000)
    page.evaluate("SympyEditor.openText('f.sympy', 'y**3')")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'y**3'", timeout=15000)
    store = page.evaluate(ED + "._sessionStore")
    cur = _current(store)
    assert cur["name"] == "f", store
    left = [s for s in store["list"] if s["id"] != cur["id"]]
    assert len(left) == 1 and "Integer(1)" in left[0]["state"]["history"][-1], left      # x + 1, not the file
    assert page.errors == []


def test_sessions_are_not_deleted_or_started_while_busy(browser, serving, tmp_path):
    """Bug: deleting the current session while busy cleared the pointer and
    then could not open another - no current session, nothing saved from
    then on - and a new session while busy was added and never opened."""
    doc = Document(x, ops={"slow": Op("slow", "Slow", _slow)})
    srv = serving(doc, options={"sessions": True}, store=tmp_path)
    page = _open(browser, srv.url)
    _ready(page)
    assert page.evaluate(ED + ".newSession('current')") is True
    before = page.evaluate(ED + "._sessionStore")
    assert len(before["list"]) == 2
    page.evaluate("() => { %s.send({action: 'apply', path: '/', op: 'slow'}); }" % ED)   # not awaited
    page.wait_for_function(ED + ".busy === true", timeout=5000)
    assert page.evaluate("(id) => %s.deleteSession(id)" % ED, before["current"]) is False
    assert page.evaluate(ED + ".newSession('empty')") is False
    after = page.evaluate(ED + "._sessionStore")
    assert after["current"] == before["current"] and len(after["list"]) == 2
    page.wait_for_function(ED + ".busy === false", timeout=10000)
    # not busy: the current one goes, and another is current in its place
    assert page.evaluate("(id) => %s.deleteSession(id)" % ED, before["current"]) is True
    done = page.evaluate(ED + "._sessionStore")
    assert len(done["list"]) == 1 and done["current"] == done["list"][0]["id"] != before["current"]
    assert page.errors == []


# -- Keep: the storage seam ---------------------------------------------------

def test_a_store_that_keeps_nothing_leaves_the_browser_its_copy(browser, serving):
    """Bug: `store=False` answered as if it kept: the page wrote there (to
    nowhere) and dropped the browser's copy - every session lost."""
    doc = Document(x + y)
    srv = serving(doc, options={"sessions": True}, store=False)
    page = _open(browser, srv.url)
    page.evaluate("""() => localStorage.setItem('sympy-editor:sessions', JSON.stringify(
        {current: 'a', list: [{id: 'a', name: 'kept here', updated: 2, state: null}]}))""")
    page.reload()
    page.wait_for_selector(".se-view .katex [data-path]", timeout=30000)
    _ready(page)
    page.evaluate(ED + ".send({action: 'replace', path: '/', src: 'cos(x)'})")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'cos(x)'", timeout=10000)
    page.evaluate(ED + ".flush()")
    assert _wait(lambda: "cos" in (page.evaluate("localStorage.getItem('sympy-editor:sessions')") or ""), timeout=5)
    kept = json.loads(page.evaluate("localStorage.getItem('sympy-editor:sessions')"))
    assert "a" in [s["id"] for s in kept["list"]] and "cos" in json.dumps(_current(kept)["state"])
    assert page.errors == []


def test_one_failed_write_does_not_move_the_storage_for_good(browser, serving, tmp_path):
    """Bug: one write refused by the server (two saves racing for one
    temporary file) switched the page to the browser's storage for good.
    Now that write is retried, then left to the browser, and the next one
    goes to the server again."""
    doc = Document(x + y)
    srv = serving(doc, options={"sessions": True}, store=tmp_path)
    page = _open(browser, srv.url)
    _ready(page)
    failed = []

    def route(r):
        body = r.request.post_data or ""
        if len(failed) < 2 and '"action": "keep"' in body.replace('":"', '": "') and '"value"' in body:
            failed.append(1)
            r.fulfill(status=500, body="busy")
        else:
            r.continue_()

    page.route("**/api", route)
    page.evaluate(ED + "._saveSessions(%s._sessionStore)" % ED)          # refused, retried, refused
    assert _until(page, lambda: len(failed) == 2, timeout=5)
    page.evaluate(ED + ".send({action: 'replace', path: '/', src: 'cos(x)'})")
    page.wait_for_function("document.querySelector('.se-source').textContent === 'cos(x)'", timeout=10000)
    page.evaluate(ED + ".flush()")
    assert _until(page, lambda: "cos" in _last_step(tmp_path), timeout=5)
    assert _until(page, lambda: page.evaluate("localStorage.getItem('sympy-editor:sessions')") is None, timeout=3)
    assert page.errors == []


def test_each_editor_keeps_through_its_own_backend(browser, serving):
    """Bug: Keep had one backend for the page - the editor mounted last - so
    a notebook's second display took the first one's storage, and once that
    view was closed every read waited for an answer that never came."""
    doc = Document(x + y)
    srv = serving(doc)
    page = _open(browser, srv.url)
    got = page.evaluate("""async () => {
        const snap = document.querySelector('.sympy-editor').__sympyEditor.state;
        const make = (name, answers) => {
            const calls = [];
            const backend = {
                calls,
                send: async () => snap,
                keep: (key, value) => { calls.push([key, value === undefined ? null : value]);
                                        return answers ? Promise.resolve('from ' + name) : new Promise(() => {}); }
            };
            return backend;
        };
        const a = make('a', true), b = make('b', false);
        const hostA = document.createElement('div'), hostB = document.createElement('div');
        document.body.append(hostA, hostB);
        SympyEditor.setKeeper(a);                         // as a view of the widget used to
        const edA = new SympyEditor.Editor(hostA, a, {});
        SympyEditor.setKeeper(b);
        const edB = new SympyEditor.Editor(hostB, b, {});
        await edA.setState(snap); await edB.setState(snap);
        edB.destroy();                                   // the second view closed: its backend answers no more
        const read = await Promise.race([SympyEditor.keep.read('rules'),
                                         new Promise(r => setTimeout(() => r('no answer'), 1500))]);
        return {read, a: a.calls, b: b.calls};
    }""")
    assert got["read"] == "from a", got
    assert got["b"] == [] or all(c[0] != "addon:rules" for c in got["b"]), got
    assert page.errors == []


def test_two_views_of_one_widget_number_their_requests_apart(browser):
    """Bug: each view of a widget counted its requests from 1, and answers
    are seen by every view - so one view's answer settled the other's
    request.  Each view's ids are its own now."""
    anywidget = pytest.importorskip("anywidget")  # noqa: F841
    from sympy_editor.widget import SympyEditorWidget
    w = SympyEditorWidget(x + y, options={"sessions": True})
    page = browser.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.route("http://widget.test/esm.js", lambda r: r.fulfill(status=200, body=w._esm,
                                                                  headers={"Content-Type": "text/javascript"}))
    page.route("http://widget.test/", lambda r: r.fulfill(status=200, body="""<!doctype html><meta charset="utf-8">
<div id="a"></div><div id="b"></div>
<script type="module">
  const { default: widget } = await import("/esm.js");
  const start = %s;
  window.__sent = [];
  const model = () => ({
    get: (k) => (k === "options" ? start.options : k === "snapshot" ? start.snapshot : undefined),
    on: () => {}, off: () => {},
    send: (msg) => window.__sent.push(msg),
  });
  widget.render({ model: model(), el: document.getElementById("a") });
  widget.render({ model: model(), el: document.getElementById("b") });
  window.__rendered = true;
</script>""" % json.dumps({"options": w.options, "snapshot": w.snapshot}), headers={"Content-Type": "text/html"}))
    page.goto("http://widget.test/")
    page.wait_for_function("window.__rendered === true && window.__sent.length >= 2", timeout=30000)
    ids = [m["_req"] for m in page.evaluate("window.__sent") if "_req" in m]
    assert len(ids) >= 2 and len(set(ids)) == len(ids), ids
    assert page.errors == []


def test_the_page_s_own_expression_does_not_flash_before_the_last_session(browser, serving):
    """Bug: the apps drew the bundle's own expression at every launch and
    only then opened the last session over it - the wrong formula for a
    moment.  Now the rendering stays hidden, its place kept, until the
    sessions have answered: what is ever seen is the session's formula."""
    doc = Document(x + y)
    srv = serving(doc, store=False)
    last = _session("last", "the last one", ["Symbol('z')", "Pow(Symbol('z'), Integer(2))"], 1)
    cfg = build_config(doc, backend="native", options=dict(KATEX, sessions=True))
    page = _open(browser, srv.url)
    page.evaluate(_NATIVE_HOST, [srv.url.rstrip("/") + "/api", srv.token])
    page.evaluate("(s) => localStorage.setItem('sympy-editor:sessions', JSON.stringify(s))",
                  {"current": "last", "list": [last]})
    # every frame, what the view shows where it can be seen
    page.evaluate("""() => {
        window.__seen = [];
        const look = () => {
            const view = document.querySelector('#native-host .se-view');
            const shown = view && [...view.children].filter(c => c.querySelector('.katex')
                && getComputedStyle(c).visibility === 'visible');
            if (shown && shown.length) {
                const text = shown.map(c => c.textContent.replace(/[\\s\\u200b]/g, '')).join('|');
                if (window.__seen[window.__seen.length - 1] !== text) window.__seen.push(text);
            }
            if (!window.__stopLooking) requestAnimationFrame(look);
        };
        requestAnimationFrame(look);
    }""")
    page.evaluate("(cfg) => { window.__nativeEditor = SympyEditor.mount(document.getElementById('native-host'), cfg); }", cfg)
    _ready(page, NATIVE)
    page.wait_for_function(NATIVE + ".state.src === 'z**2'", timeout=10000)
    page.wait_for_timeout(300)
    page.evaluate("window.__stopLooking = true")
    seen = page.evaluate("window.__seen")
    assert seen and all("x+y" not in s for s in seen), seen
    assert "z2" in seen[-1], seen
    assert not page.locator("#native-host .sympy-editor.se-restoring").count()
    assert page.errors == []


def test_a_first_launch_shows_the_add_ons_the_python_has(browser, serving):
    """Bug: the page opens on the snapshot it was built with; an app's Python
    carries add-ons the page was built without (handwriting, staged with its
    model), and with no session to reopen - a first install - nothing asked
    the Python again: the Add-ons menu lacked them until the first edit."""
    from sympy_editor import Addon

    class Staged(Addon):
        name = "staged"
        label = "Staged by the app"

    built = Document(x + y, available=[])                  # what the page was built from
    live = Document(x + y, available=[Staged()])           # what the app's Python holds
    srv = serving(live, store=False)
    cfg = build_config(built, backend="native", options=dict(KATEX, sessions=True))
    assert not cfg["snapshot"]["addons_available"]
    page = _open(browser, srv.url)
    page.evaluate(_NATIVE_HOST, [srv.url.rstrip("/") + "/api", srv.token])
    page.evaluate("() => localStorage.removeItem('sympy-editor:sessions')")          # a first install
    page.evaluate("(cfg) => { window.__nativeEditor = SympyEditor.mount(document.getElementById('native-host'), cfg); }", cfg)
    _ready(page, NATIVE)
    page.wait_for_function(NATIVE + ".state.addons_available.some(a => a.name === 'staged')", timeout=10000)
    assert page.errors == []

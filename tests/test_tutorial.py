"""Tutorials (sympy_editor.tutorial, static/tutorial.js): a page that plays a
script of timed steps on the editor.  The player is checked on a real editor
against real Python - captions in order, the arrow and the ring before every
press, the presses doing what a person's would - and the rest of the package
is checked to carry none of it: nothing in the app changes."""
import json
import threading
import urllib.request
from contextlib import closing

import pytest
from sympy import sin, symbols, sympify

from sympy_editor import Document, to_html
from sympy_editor.html import default_urls, read_static
from sympy_editor.server import EditorServer
from sympy_editor.tutorial import ELEMENT_ID, load_tutorial, main, save_tutorial_html, to_tutorial_html

x, y = symbols("x y")
PLAYER = "SympyEditorTutorial"


# ---- the script ----------------------------------------------------------

def test_a_script_loads_from_a_dict_json_text_or_a_file(tmp_path):
    script = {"steps": [{"at": 0, "caption": "hello"}, {"after": 1, "click": {"path": "/1"}}]}
    path = tmp_path / "s.json"
    path.write_text(json.dumps(script), encoding="utf-8")
    for given in (script, json.dumps(script), str(path), path):
        assert load_tutorial(given) == script
    loaded = load_tutorial(script)
    loaded["steps"].append({"wait": True})
    assert len(script["steps"]) == 2                          # a copy: the caller's script is left alone


@pytest.mark.parametrize("script, says", [
    ({}, "needs 'steps'"),
    ({"steps": []}, "needs 'steps'"),
    ({"steps": [{"at": 0}]}, "step 0: says what it does"),
    ({"steps": [{"caption": "a", "click": ".b"}]}, "step 0: says what it does"),
    ({"steps": [{"wait": True}, {"at": 1, "after": 2, "wait": True}]}, "step 1: says when"),
    ({"steps": [{"at": -1, "wait": True}]}, "step 0: 'at' is a number of seconds"),
    ({"steps": [{"click": 5}]}, "step 0: click needs a target"),
    ({"steps": [{"point": {"text": "x"}}]}, "step 0: point needs a target"),
    ({"steps": [{"type": {"target": ".f"}}]}, "step 0: type needs"),
    ({"steps": [{"zoom": 0}]}, "step 0: zoom is a positive number"),
    ({"steps": [{"apply": {"path": "/"}}]}, "step 0: apply takes"),
    ({"steps": [{"addons": ["plot"]}]}, "step 0: addons takes"),
    ({"steps": [{"wait": True, "sayy": "typo"}]}, "step 0: unknown key(s) sayy"),
    ({"steps": [{"wait": True}], "stepz": []}, "unknown key(s) stepz"),
    ({"steps": [{"wait": True}], "speed": 0}, "'speed' is a positive number"),
])
def test_a_script_at_fault_is_refused_naming_the_step(script, says):
    with pytest.raises(ValueError) as err:
        load_tutorial(script)
    assert says in str(err.value)


# ---- the page, and the app left as it was --------------------------------

def test_the_page_is_the_ordinary_editor_page_with_the_player_after_it():
    script = {"title": "A tour", "expression": "x**2 + 1", "steps": [{"at": 0, "caption": "hi </script> there"}]}
    page = to_tutorial_html(script)
    assert page.count(f'SympyEditor.mount(document.getElementById("{ELEMENT_ID}")') == 1
    assert read_static("editor.js").strip() in page and read_static("tutorial.js").strip() in page
    assert read_static("tutorial.css").strip() in page and "<title>A tour</title>" in page
    assert page.index("SympyEditor.mount(") < page.index(f"{PLAYER}.run(")          # the editor first
    assert "hi </script> there" not in page                                        # the script is escaped
    assert '"x**2 + 1"' in page or "x**2 + 1" in page


def test_nothing_else_carries_the_player():
    """The app is unchanged: an ordinary page, a served page and the widget
    have no tutorial in them, nor anything to start one with."""
    for page in (to_html(x**2 / y), to_html(x, backend="readonly"), to_html(x, full_page=False)):
        assert PLAYER not in page and "se-tour" not in page
    srv = EditorServer(Document(x), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with closing(urllib.request.urlopen(srv.url, timeout=10)) as r:
            served = r.read().decode("utf-8")
    finally:
        srv.shutdown()
        srv.server_close()
    assert "SympyEditor.mount(" in served and PLAYER not in served
    assert PLAYER not in read_static("editor.js") and "se-tour" not in read_static("editor.css")
    anywidget = pytest.importorskip("anywidget")   # noqa: F841
    from sympy_editor.widget import SympyEditorWidget
    assert PLAYER not in SympyEditorWidget._esm


def test_the_command_line_builds_a_page_and_refuses_a_bad_script(tmp_path, capsys):
    good = tmp_path / "tour.json"
    good.write_text(json.dumps({"expression": "sin(x)", "steps": [{"at": 0, "caption": "hi"}]}), encoding="utf-8")
    assert main([str(good)]) == 0 and PLAYER in (tmp_path / "tour.html").read_text(encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"steps": [{"at": 0}]}), encoding="utf-8")
    assert main([str(bad), "-o", str(tmp_path / "bad.html")]) == 1
    assert "step 0" in capsys.readouterr().err and not (tmp_path / "bad.html").exists()
    out = save_tutorial_html({"steps": [{"wait": True}]}, tmp_path / "w.html", expr="y")
    assert out.is_file()


# ---- the player, on a real editor ----------------------------------------

playwright = pytest.importorskip("playwright.sync_api")


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


WATCH = """
window.__tour = {steps: [], rings: 0, end: null};
addEventListener('sympy-editor-tutorial-step', e => {
  const cap = document.querySelector('.se-tour-caption');
  const sel = document.querySelector('.se-view .se-selected[data-path]');
  window.__tour.steps.push({i: e.detail.index,
    caption: cap && !cap.hidden ? cap.textContent : null,
    source: (document.querySelector('.se-source') || {}).textContent,
    selected: sel ? sel.getAttribute('data-path') : null});
});
addEventListener('sympy-editor-tutorial-end', e => { window.__tour.end = e.detail; });
new MutationObserver(recs => recs.forEach(r => {
  if (r.target.classList && r.target.classList.contains('se-tour-ring') && !r.target.hidden) window.__tour.rings++;
})).observe(document, {subtree: true, attributes: true, attributeFilter: ['hidden']});
"""


@pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")
def test_the_player_plays_a_script_on_a_real_editor():
    doc = Document(x**2 / y - sin(x))
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    script = {"speed": 4, "steps": [
        {"at": 0, "caption": "A formula you can click"},
        {"after": 0.4, "click": {"path": "/1/d"}, "say": "Click a piece to select it"},
        {"after": 0.4, "click": '.se-toolbar [data-cmd="parent"]', "say": "Up to what holds it"},
        {"after": 0.4, "type": {"target": ".se-source", "text": "(x + 1)**2", "enter": True}, "say": "Or type it"},
        {"after": 0.4, "apply": "expand", "say": "Transform it"},
        {"after": 0.4, "undo": True},
        {"after": 0.4, "point": ".se-ops", "say": "Every transformation is in this menu"},
        {"after": 0.4, "zoom": 1.5},
        {"after": 0.4, "caption": None},
    ]}
    # the page from the server's own address, so that the player's presses
    # reach this very Document through its API
    page_html = to_tutorial_html(script, expr=doc, backend="http", api_url="/api", token=srv.token)
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"chromium not available: {exc}")
            page = browser.new_page(viewport={"width": 1000, "height": 800})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script(WATCH)
            page.route(srv.url + "tour", lambda route: route.fulfill(body=page_html, content_type="text/html"))
            page.goto(srv.url + "tour")
            page.wait_for_function("() => window.__tour && window.__tour.end", timeout=60000)
            tour = page.evaluate("window.__tour")
            zoom = page.evaluate(f"document.getElementById('{ELEMENT_ID}').querySelector('.sympy-editor').__sympyEditor.zoom")
            caption_left = page.evaluate("(() => { const c = document.querySelector('.se-tour-caption'); return !!c && !c.hidden && c.classList.contains('shown'); })()")
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()
    assert tour["end"]["errors"] == [] and errors == []
    at = {s["i"]: s for s in tour["steps"]}
    assert [s["i"] for s in tour["steps"]] == list(range(len(script["steps"])))    # every step, in order
    assert at[1]["caption"] == "A formula you can click"                           # a caption stays until the next
    assert at[2]["caption"] == "Click a piece to select it" and at[2]["selected"] == "/1/d"
    assert at[3]["selected"] == "/1"                                               # the parent button, pressed
    assert at[5]["source"] == "x**2 + 2*x + 1"                                     # typed, applied, expanded
    assert doc.expr == sympify("(x + 1)**2")                                       # and undone
    assert tour["rings"] >= 4                                                      # arrow and ring: 2 clicks, typing, a point
    assert zoom == 1.5 and not caption_left


def test_the_example_tour_is_a_script_that_builds(tmp_path):
    """examples/tutorial: tour.json is a valid script, and build.py turns it
    into a page with the two add-ons the tour switches on."""
    import importlib.util
    from pathlib import Path
    here = Path(__file__).resolve().parent.parent / "examples" / "tutorial"
    script = load_tutorial(here / "tour.json")
    assert len(script["steps"]) > 10 and set(script["addons"]) == {"plot", "tree"}
    spec = importlib.util.spec_from_file_location("tour_build", here / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    out = tmp_path / "tour.html"
    assert build.main(["--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert PLAYER in page and "sympy_editor_plot" in page and "sympy_editor_tree" in page

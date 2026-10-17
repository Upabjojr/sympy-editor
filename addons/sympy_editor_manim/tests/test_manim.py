"""The manim add-on without a browser: the kept sub-expressions marked for
Manim, the script, the span of steps, the settings, running Manim (a stand-in
command, and the real one where it is installed)."""
import ast
import base64
import sys
from pathlib import Path

import pytest
from sympy import symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor.document import render_step  # noqa: E402
from sympy_editor.printer import annotate, strip_annotations  # noqa: E402
from sympy_editor_manim import ADDON, FILE_NAME, SCENE_NAME  # noqa: E402
from sympy_editor_manim import render as rendering  # noqa: E402
from sympy_editor_manim.scene import changes, isolate, script, settings, shared_paths  # noqa: E402

x, y = symbols("x y")


def _doc(*ops):
    doc = Document((x + 1) ** 2, addons=[ADDON])
    for msg in ops:
        doc.handle(msg)
    return doc


EXPAND = {"action": "apply", "path": "/", "op": "expand"}
EDIT = {"action": "replace", "path": "/0", "src": "x**3"}
FACTOR = {"action": "apply", "path": "/", "op": "factor"}


def _call(doc, method, **payload):
    res = doc.handle(dict({"action": "addon", "addon": "manim", "method": method}, **payload))["query"]
    if "error" in res:
        raise ValueError(res["error"])
    return res["result"]


# -- the LaTeX ------------------------------------------------------------------

def test_isolate_puts_the_asked_nodes_in_manim_groups_and_strips_the_rest():
    tex, nodes = annotate(x ** 2 + 1)
    by_src = {str(n): "/" + "/".join(map(str, p)) if p else "/" for p, n in nodes.items()}
    out = isolate(tex, [by_src["x**2"]])
    assert out == " {{ x^{2} }}  + 1"
    assert r"\htmlData" not in out
    # a group inside a group is never made: the parent is the piece
    assert isolate(tex, [by_src["x**2"], by_src["x"]]) == out
    assert isolate(tex, [by_src["x"]]) == " {{ x }} ^{2} + 1"
    assert isolate(tex, []) == strip_annotations(tex) == "x^{2} + 1"


def test_shared_paths_are_the_top_most_nodes_both_steps_hold():
    a, b = render_step(x ** 2 + 2 * x + 1), render_step((x + 1) ** 2)
    kept_a, kept_b = shared_paths(a["nodes"], b["nodes"])
    assert {a["nodes"][p]["src"] for p in kept_a} == {"x", "2", "1"}
    assert len(kept_a) == 5                      # x and 2 twice (x**2 and 2*x), 1 once
    assert {b["nodes"][p]["src"] for p in kept_b} == {"x", "2", "1"}
    assert "/" not in kept_a and "/" not in kept_b
    # the whole formula kept: one group, nothing inside it
    same, same2 = shared_paths(a["nodes"], a["nodes"])
    assert same == same2 == {"/"}


def test_changes_write_both_formulas_of_each_pair():
    steps = [(s["latex"], s["nodes"], None) for s in (render_step(x + y), render_step(y + x), render_step(x * y))]
    out = changes(steps)
    assert len(out) == 2
    assert out[0] == (" {{ x + y }} ", " {{ x + y }} ")                      # SymPy sorts: the same step twice
    assert out[1] == (" {{ x }}  +  {{ y }} ", " {{ x }}   {{ y }} ")


# -- the script -------------------------------------------------------------------

def test_the_script_holds_every_step_its_captions_and_the_changes():
    doc = _doc(EXPAND, EDIT, FACTOR)
    res = _call(doc, "script")
    assert res["name"] == FILE_NAME and res["scene"] == SCENE_NAME and res["steps"] == 4
    assert (res["from"], res["to"]) == (0, 3)
    code = res["code"]
    tree = ast.parse(code)                                             # valid Python
    assert any(isinstance(n, ast.ClassDef) and n.name == SCENE_NAME for n in tree.body)
    assert "from manim import *" in code
    assert r'(r"\left(x + 1\right)^{2}", r"Start")' in code
    assert r'(r"x^{2} + 2 x + 1", r"Transform: Expand")' in code
    assert "'Edit: 1 → x**3'" in code or 'r"Edit: 1 → x**3"' in code
    assert 'r"Transform: Factor"' in code
    # the changes: one per pair, the kept parts in groups
    ns = {}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.Assign)], type_ignores=[]), "<s>", "exec"), ns)
    assert len(ns["STEPS"]) == 4 and len(ns["CHANGES"]) == 3
    src, dst = ns["CHANGES"][0]
    assert src == r"\left( {{ x }}  +  {{ 1 }} \right)^{ {{ 2 }} }"
    assert dst == r" {{ x }} ^{ {{ 2 }} } +  {{ 2 }}   {{ x }}  +  {{ 1 }} "
    assert ns["STYLE"] == "matching" and ns["CAPTIONS"] is True and ns["THEME"] == "dark" and ns["TITLE"] is None


def test_the_settings_become_the_constants():
    doc = _doc(EXPAND)
    code = _call(doc, "script", style="write", captions=False, theme="light", title="  A title ", highlight=False,
                 hold=2, run_time=0.5, scale=2)["code"]
    assert "STYLE = 'write'" in code and "CAPTIONS = False" in code and "THEME = 'light'" in code
    assert "TITLE = 'A title'" in code and "HIGHLIGHT = False" in code
    assert "HOLD = 2.0" in code and "RUN_TIME = 0.5" in code and "SCALE = 2.0" in code
    assert settings({"title": "   "})["title"] is None
    with pytest.raises(ValueError, match="style"):
        settings({"style": "fly"})
    with pytest.raises(ValueError, match="theme"):
        settings({"theme": "sepia"})
    with pytest.raises(ValueError, match="hold"):
        settings({"hold": "slow"})
    with pytest.raises(ValueError, match="between"):
        settings({"run_time": -1})


def test_a_span_of_the_history():
    doc = _doc(EXPAND, EDIT, FACTOR)
    res = _call(doc, "script", **{"from": 1, "to": 2})
    assert res["steps"] == 2 and (res["from"], res["to"]) == (1, 2)
    assert r'(r"x^{2} + 2 x + 1", r"Transform: Expand")' in res["code"]      # the first step keeps its own label
    assert "Factor" not in res["code"]
    assert "CHANGES = [\n    (" in res["code"] and res["code"].count("\n    (r", res["code"].index("CHANGES")) == 1
    with pytest.raises(ValueError, match="does not exist"):
        _call(doc, "script", **{"to": 9})
    with pytest.raises(ValueError, match="before the first"):
        _call(doc, "script", **{"from": 2, "to": 1})
    with pytest.raises(ValueError, match="step number"):
        _call(doc, "script", **{"from": "two"})


def test_one_step_has_nothing_to_change():
    code = _call(_doc(), "script")["code"]
    assert "CHANGES = [\n    # one step: nothing changes\n]" in code
    ast.parse(code)
    with pytest.raises(ValueError, match="no step"):
        script([])


def test_strings_that_a_raw_string_cannot_hold_are_written_as_repr():
    steps = [(r"\text{say ``hi\"}", {}, 'a "quoted" label'), (r"x \\", {}, None)]
    code = script(steps)
    ns = {}
    exec(compile(ast.Module(body=[n for n in ast.parse(code).body if isinstance(n, ast.Assign)], type_ignores=[]), "<s>", "exec"), ns)
    assert ns["STEPS"] == [(r"\text{say ``hi\"}", 'a "quoted" label'), (r"x \\", None)]


def test_the_snapshot_counts_the_steps():
    doc = _doc(EXPAND)
    assert doc.snapshot()["manim"] == {"n": 2, "index": 1}
    doc.undo()
    assert doc.snapshot()["manim"] == {"n": 2, "index": 0}


# -- running Manim --------------------------------------------------------------

def test_status_says_what_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv(rendering.ENV, str(tmp_path / "no-such-manim"))
    rendering._versions.clear()
    st = rendering.status()
    assert st["available"] is False and st["version"] is None
    assert "does not run" in st["hint"] and "pip install manim" in st["hint"]
    monkeypatch.delenv(rendering.ENV)
    monkeypatch.setattr(rendering, "command", lambda: None)
    st = rendering.status()
    assert st["available"] is False and "not installed" in st["hint"]
    with pytest.raises(ValueError, match="not installed"):
        rendering.render("print()")
    with pytest.raises(ValueError, match="quality"):
        rendering.render("print()", quality="z")
    with pytest.raises(ValueError, match="format"):
        rendering.render("print()", fmt="avi")


FAKE_MANIM = """
import sys, pathlib
args = sys.argv[1:]
media = pathlib.Path(args[args.index("--media_dir") + 1])
fmt = args[args.index("--format") + 1]
name = args[args.index("-o") + 1]
out = media / "videos" / "formula_steps" / "480p15" / (name + "." + fmt)
out.parent.mkdir(parents=True)
(out.parent / "partial_movie_files").mkdir()
(out.parent / "partial_movie_files" / ("part." + fmt)).write_bytes(b"part")
out.write_bytes(b"fake video " + pathlib.Path(args[-2]).read_bytes()[:12])
print("rendered", out)
"""


def test_render_runs_the_command_and_returns_the_file(monkeypatch):
    monkeypatch.setattr(rendering, "command", lambda: [sys.executable, "-c", FAKE_MANIM])
    doc = _doc(EXPAND)
    res = _call(doc, "render", quality="l", format="gif")
    assert res["name"] == "formula_steps.gif" and res["mime"] == "image/gif"
    assert base64.b64decode(res["data"]) == b'fake video """Formula s'
    assert res["size"] == len(b'fake video """Formula s') and res["steps"] == 2
    assert "rendered" in res["log"] and "-q l --format gif" in res["command"]


def test_a_failing_manim_is_reported_with_its_log(monkeypatch):
    monkeypatch.setattr(rendering, "command", lambda: [sys.executable, "-c", "print('LaTeX went wrong'); raise SystemExit(3)"])
    with pytest.raises(ValueError, match="exit code 3"):
        rendering.render("# nothing")
    try:
        rendering.render("# nothing")
    except ValueError as exc:
        assert "LaTeX went wrong" in str(exc)
    monkeypatch.setattr(rendering, "command", lambda: [sys.executable, "-c", "pass"])
    with pytest.raises(ValueError, match="wrote no .mp4"):
        rendering.render("# nothing")


def test_a_render_that_takes_too_long_is_stopped(monkeypatch):
    monkeypatch.setattr(rendering, "command", lambda: [sys.executable, "-c", "import time; time.sleep(30)"])
    with pytest.raises(ValueError, match="did not finish in 1 seconds"):
        rendering.render("# nothing", timeout=1)


@pytest.mark.skipif(not rendering.status()["available"], reason="Manim is not installed here")
def test_a_real_render_with_manim():
    """Manim itself, where it is (``pip install manim`` beside the editor, or
    SYMPY_EDITOR_MANIM naming it): the scene compiles and plays."""
    doc = _doc(EXPAND)
    res = _call(doc, "render", quality="l", title="Expanding")
    assert res["mime"] == "video/mp4" and res["size"] > 1000
    assert base64.b64decode(res["data"])[4:8] == b"ftyp"                  # an MP4 container

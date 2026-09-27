import sys
from pathlib import Path

import pytest
from sympy import Symbol, cos, exp, expand, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_console import ADDON, Console  # noqa: E402

x, y = symbols("x y")


def run(doc, code, method="run", **payload):
    snap = doc.handle(dict(action="addon", addon="console", method=method, code=code, **payload))
    query = snap.get("query") or {}
    assert "error" not in query, query.get("error")
    return query.get("result")


def text(result, kind):
    return "".join(i["text"] for i in result["items"] if i["kind"] == kind)


def test_in_and_out_as_ipython():
    doc = Document(sin(x), addons=[ADDON])
    res = run(doc, "a = 2\nprint('a is', a)\na*x")
    assert res["n"] == 1 and res["next"] == 2
    assert text(res, "stdout") == "a is 2\n"
    assert res["out"] == {"text": "2*x", "latex": "2 x"}
    assert run(doc, "_ + 1")["out"]["text"] == "2*x + 1"
    assert run(doc, "Out[1] + _2 + __")["out"]["text"] == "6*x + 1"      # 2x + (2x + 1) + 2x
    assert run(doc, "In[1]")["out"]["text"] == repr("a = 2\nprint('a is', a)\na*x")
    assert "out" not in run(doc, "b = 3")                               # a statement shows nothing
    assert not doc.can_undo                                             # and the formula is untouched


def test_the_formula_symbols_are_the_formula_s():
    t = Symbol("t", positive=True)
    beta = Symbol("beta")
    doc = Document(t + beta, addons=[ADDON])
    assert run(doc, "t.is_positive")["out"]["text"] == "True"          # the formula's t, assumptions and all
    assert run(doc, "type(beta).__name__")["out"]["text"] == "'Symbol'"   # the formula's symbol wins over sympy.beta
    run(doc, "t = 5")
    assert run(doc, "t")["out"]["text"] == "5"                          # but what the user sets is theirs


def test_errors_show_the_user_s_frames_only():
    doc = Document(x, addons=[ADDON])
    res = run(doc, "def g():\n    return 1/0\ng()")
    err = text(res, "error")
    assert err.startswith("Traceback") and '"<In [1]>", line 3' in err and "ZeroDivisionError" in err
    assert "console.py" not in err
    assert text(run(doc, "def f(:"), "error").lstrip().startswith('File "<In [2]>"')
    assert text(run(doc, "input()"), "error").startswith("UsageError: input() cannot read here")
    assert text(run(doc, "!ls"), "error").startswith("UsageError: There is no shell")
    assert "the console stays open" in text(run(doc, "exit()"), "error")


def test_an_unfinished_block_is_not_run():
    doc = Document(x, addons=[ADDON])
    assert run(doc, "for i in range(3):", interactive=True) == {"incomplete": True}
    assert run(doc, "for i in range(3):\n    print(i)", interactive=True) == {"incomplete": True}
    assert text(run(doc, "for i in range(3):\n    print(i)\n", interactive=True), "stdout") == "0\n1\n2\n"
    assert Console.needs_more("x = (1,\n2")
    assert not Console.needs_more("def f(:")                           # invalid: runs, and says so
    assert not Console.needs_more("x = 1\ny = 2")


def test_the_formula_from_python():
    doc = Document((x + 1) ** 2, addons=[ADDON])
    res = run(doc, "editor.expr = expand(editor.expr)")
    assert res["changed"] and doc.expr == expand((x + 1) ** 2)
    assert doc.history_labels()["actions"][-1] == "Console: editor.expr = expand(editor.expr)"
    assert not run(doc, "editor.expr")["changed"]
    run(doc, "editor.undo()")
    assert doc.expr == (x + 1) ** 2
    paths = run(doc, "editor.paths()")["out"]["text"]
    assert "'/': '(x + 1)**2'" in paths
    assert run(doc, "editor.find(x + 1)")["out"]["text"] == "['/0']"
    assert run(doc, "editor['/0']")["out"]["text"] == "x + 1"
    run(doc, "editor['/0'] = Symbol('y')")
    assert doc.expr == y ** 2
    run(doc, "editor.apply('expand', '/')")
    res = run(doc, "editor.select('/1')")
    assert res["select"] == "/1"


def test_the_selection():
    doc = Document(sin(x) + cos(y), addons=[ADDON])
    path = next(p for p, info in doc.snapshot()["nodes"].items() if info["src"] == "cos(y)")
    assert run(doc, "editor.selection, editor.path", path=path)["out"]["text"] == f"(cos(y), '{path}')"
    run(doc, "editor.selection = editor.selection.rewrite(exp)", path=path)
    assert doc.expr == sin(x) + cos(y).rewrite(exp)
    assert run(doc, "editor.selection")["out"]["text"] == str(doc.expr)   # nothing selected: the whole formula
    # a range: two terms of a sum
    doc = Document(x + y + 1, addons=[ADDON])
    order = doc.snapshot()["nodes"]
    terms = {order[p]["src"]: int(p.rsplit("/", 1)[1]) for p in order if p.count("/") == 1 and p != "/"}
    res = run(doc, "editor.selection", path="/", children=[terms["x"], terms["y"]])
    assert res["out"]["text"] == "x + y"
    run(doc, "editor.selection = 2*x", path="/", children=[terms["x"], terms["y"]])
    assert doc.expr == 2 * x + 1


def test_use_puts_an_output_in_the_formula():
    doc = Document(sin(x) + x, addons=[ADDON])
    n = run(doc, "y = Symbol('y')\nfactor(y**2 - 1)")["n"]
    snap = doc.handle({"action": "addon", "addon": "console", "method": "use", "n": n, "path": "/"})
    assert "query" not in snap and doc.expr == (y - 1) * (y + 1)
    assert doc.history_labels()["actions"][-1] == f"Console: Out[{n}]"
    snap = doc.handle({"action": "addon", "addon": "console", "method": "use", "n": 99})
    assert "no Out[99]" in snap["query"]["error"]


def test_help_and_magics():
    doc = Document(x, addons=[ADDON])
    assert "Signature: factor(f, *gens" in text(run(doc, "factor?"), "stdout")
    assert "Source:" in text(run(doc, "editor.find??"), "stdout")
    run(doc, "a = 1\nb = x")
    assert text(run(doc, "%who"), "stdout") == "a  b\n"
    assert "a               int" in text(run(doc, "%whos"), "stdout")
    res = run(doc, "%time factor(x**8 - 1)")
    assert text(res, "stdout").startswith("Wall time:") and res["out"]["text"].startswith("(x - 1)")
    assert text(run(doc, "c = 2\n%who"), "stdout") == "a  b  c\n"            # a magic on a line of its own, anywhere
    assert text(run(doc, "%nope"), "error").startswith("UsageError: Line magic %nope")
    res = run(doc, "%reset")
    assert res["next"] == 1 and text(run(doc, "%who"), "stdout") == "Interactive namespace is empty.\n"


def test_display_interleaves_with_print():
    doc = Document(x, addons=[ADDON])
    res = run(doc, "print(1)\ndisplay(x**2)\nprint(2)")
    assert [i["kind"] for i in res["items"]] == ["stdout", "display", "stdout"]
    assert res["items"][1]["latex"] == "x^{2}"


def test_a_script_runs_as_a_file_and_leaves_its_names():
    doc = Document(sin(x) ** 2 + cos(x) ** 2, addons=[ADDON])
    code = "def twice(e):\n    return 2*e\nprint(__name__, __file__)\neditor.expr = simplify(editor.expr)\nx + 1\n"
    res = run(doc, code, method="script", name="mine.py")
    assert text(res, "stdout") == "__main__ mine.py\n" and "out" not in res   # a file shows no values
    assert res["changed"] and doc.expr == 1
    assert doc.history_labels()["actions"][-1] == "Script: mine.py"
    assert run(doc, "twice(3)")["out"]["text"] == "6"                  # what it defined is in the console
    err = text(run(doc, "print('before')\nraise ValueError('boom')", method="script", name="bad.py"), "error")
    assert '"<bad.py>", line 2' in err and "ValueError: boom" in err


def test_completion():
    doc = Document(x, addons=[ADDON])
    res = run(doc, "fac", method="complete")
    assert res["start"] == 0 and "factor" in res["matches"]
    run(doc, "long_name_here = 1")
    code = "y = long_na"
    assert run(doc, code, method="complete", pos=len(code))["matches"] == ["long_name_here"]
    assert run(doc, "editor.fi", method="complete")["matches"] == ["editor.find"]


def test_completion_says_what_each_name_is_and_puts_the_users_first():
    doc = Document(x + y, addons=[ADDON])
    run(doc, "xs = [1]; e = x**2")
    res = run(doc, "x", method="complete")
    assert res["matches"][:2] == ["x", "xs"] and res["total"] == len(res["matches"])
    kinds = dict(zip(res["matches"], res["kinds"]))
    assert kinds["x"] == "Symbol" and kinds["xs"] == "list"
    res = run(doc, "e.", method="complete")                      # attributes, the private ones left out
    kinds = dict(zip(res["matches"], res["kinds"]))
    assert kinds["e.expand"] == "method" and kinds["e.args"] == "property" and "e.adjoint" in kinds
    assert not any(m.startswith("e._") for m in res["matches"])
    assert run(doc, "wh", method="complete")["matches"] == ["while"]
    assert run(doc, "f", method="complete")["total"] > 20        # many: the panel keeps its menu shut
    for code in ["'e.", "print('x", "# e.", "1.", "3.e"]:        # a string, a comment, a number
        assert run(doc, code, method="complete")["matches"] == [], code
    assert "e.expand" in run(doc, "s = 'a'; e.", method="complete")["matches"]


def test_output_is_bounded():
    doc = Document(x, addons=[ADDON])
    res = run(doc, "for i in range(100000):\n    print('0123456789')\n")
    assert len(text(res, "stdout")) < 250_000 and text(res, "stdout").endswith("[… output cut]\n")


def test_every_snapshot_names_the_namespace():
    doc = Document(x, addons=[ADDON])
    assert "console" not in doc.snapshot()                              # nothing until the panel speaks
    hello = run(doc, "", method="hello")
    snap = doc.snapshot()
    assert snap["console"] == {"token": hello["token"], "next": 1}
    run(doc, "1")
    assert doc.snapshot()["console"]["next"] == 2
    other = Document(x, addons=[ADDON])
    assert run(other, "", method="hello")["token"] != hello["token"]      # each document its own
    fresh = run(doc, "", method="reset")                                   # a reset is a new namespace:
    assert fresh["token"] != hello["token"] and fresh["next"] == 1          # the panel's cells from before are text


def test_unknown_method():
    doc = Document(x, addons=[ADDON])
    snap = doc.handle({"action": "addon", "addon": "console", "method": "nope"})
    assert "no method 'nope'" in snap["query"]["error"]


@pytest.mark.parametrize("code", ["editor.expr = 'x +'", "editor['/9'] = 1"])
def test_a_refused_change_is_an_error_of_the_code(code):
    doc = Document(x + y, addons=[ADDON])
    res = run(doc, code)
    assert text(res, "error") and not res["changed"] and doc.expr == x + y

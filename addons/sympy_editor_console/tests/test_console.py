import gc
import io
import json
import sys
import threading
import weakref
from pathlib import Path

import pytest
from sympy import Symbol, cos, exp, expand, sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_console import ADDON, Console, console_of  # noqa: E402
from sympy_editor_console import console as console_module  # noqa: E402

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


def use(doc, **payload):
    return doc.handle(dict(action="addon", addon="console", method="use", **payload))


@pytest.mark.parametrize("how", ["reset", "%reset"])
def test_use_refuses_an_output_of_an_earlier_namespace(how):
    """The numbers start again with every namespace, and Use sent only the
    number: beside the ``Out[1]: 42`` left on the screen from before a Reset
    it put the *new* ``Out[1]`` - ``x**2`` - in the formula.  It says which
    namespace its output is of, and one that is gone is refused."""
    doc = Document(sin(x) + x, addons=[ADDON])
    first = run(doc, "41 + 1")
    if how == "reset":
        run(doc, "", method="reset")
    else:
        run(doc, "%reset")
    second = run(doc, "x**2")
    assert (first["n"], second["n"]) == (1, 1) and first["token"] != second["token"]
    snap = use(doc, n=1, token=first["token"], path="/")
    assert "namespace that is gone" in snap["query"]["error"]
    assert doc.expr == sin(x) + x and not doc.can_undo
    assert "query" not in use(doc, n=1, token=second["token"], path="/")     # this namespace's is taken
    assert doc.expr == x ** 2


def test_a_script_leaves_the_users_own_names_alone():
    """A script is given the formula's names too, and what it left in its
    namespace was copied into the console's - the formula's ``t`` included,
    over the ``t = 5`` the user had typed.  What the script was given and
    did not touch is not something it defined."""
    t = Symbol("t", positive=True)
    doc = Document(t + x, addons=[ADDON])
    run(doc, "t = 5")
    run(doc, "print('hi')", method="script")
    assert run(doc, "t")["out"]["text"] == "5"
    assert text(run(doc, "%who"), "stdout") == "t\n"                  # and it is still the user's
    run(doc, "t = 7\nx = x + 1", method="script")                     # what a script does assign is its own
    assert run(doc, "t, x")["out"]["text"] == "(7, x + 1)"
    fresh = Document(t + x, addons=[ADDON])
    run(fresh, "print('hi')", method="script")
    assert run(fresh, "t.is_positive")["out"]["text"] == "True"        # the formula's names are there as before
    assert text(run(fresh, "%who"), "stdout") == "Interactive namespace is empty.\n"
    gone = Document(sin(x) ** 2 + cos(x) ** 2, addons=[ADDON])          # a script that takes x out of the formula:
    run(gone, "editor.expr = simplify(editor.expr)", method="script")
    assert gone.expr == 1 and run(gone, "x + 1")["out"]["text"] == "x + 1"   # the console knows it as the script did


def test_a_percent_inside_a_string_or_a_bracket_is_not_a_magic():
    """The magics were found in the text of the cell, line by line: a line
    of a string that began with % was rewritten inside the string, and the
    second line of ``a = (10`` / ``%3)`` - Python, with the value 1 - was a
    syntax error.  Only a line that begins a statement is a magic."""
    doc = Document(x, addons=[ADDON])
    res = run(doc, 's = """\n%d items\n"""\ns')
    assert res["out"]["text"] == repr("\n%d items\n") and not text(res, "error")
    res = run(doc, "a = (10\n%3)\na")
    assert res["out"]["text"] == "1" and not text(res, "error")
    assert run(doc, "b = 10 \\\n%4\nb")["out"]["text"] == "2"         # after a backslash too
    # and the magics are magics still: on a line of their own, inside a block, after a string
    res = run(doc, 'for i in range(2):\n    %who\n')
    assert text(res, "stdout") == "a  b  i  s\na  b  i  s\n"
    res = run(doc, 'u = """\n%who\n"""\n%time 1 + 1')
    assert text(res, "stdout").startswith("Wall time:") and res["out"]["text"] == "2"
    assert run(doc, "u")["out"]["text"] == repr("\n%who\n")
    # a cell that is not Python says so, whatever it has in it
    assert "SyntaxError" in text(run(doc, "c = (1,\n%who"), "error")
    assert text(run(doc, "%nope it's"), "error").startswith("UsageError: Line magic %nope")


def test_two_consoles_on_two_threads_keep_their_output_and_give_stdout_back(monkeypatch):
    """The output was captured by swapping ``sys.stdout``, which the whole
    process shares: of two documents running cells on two threads - two
    widgets in a notebook - one got the other's prints, and the one that
    finished last put a dead capture back as ``sys.stdout``: every later
    print of the process was lost."""
    real_out, real_err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", real_out)
    monkeypatch.setattr(sys, "stderr", real_err)
    one, two = Document(sin(x), addons=[ADDON]), Document(cos(x), addons=[ADDON])
    gates = {name: threading.Event() for name in ("a_in", "b_in", "main_said", "a_done")}
    for doc in (one, two):
        console_of(doc).ns.update(gates)
    results = {}

    def cell_a():        # begins first, ends first
        results["a"] = run(one, "print('A1')\na_in.set()\nassert main_said.wait(20)\nprint('A2')")
        gates["a_done"].set()

    def cell_b():        # begins while A runs, ends after it
        assert gates["a_in"].wait(20)
        results["b"] = run(two, "import sys\nprint('B1')\nb_in.set()\nassert a_done.wait(20)\n"
                                "print('B2')\nprint('oops', file=sys.stderr)")

    threads = [threading.Thread(target=cell_a), threading.Thread(target=cell_b)]
    for thread in threads:
        thread.start()
    assert gates["b_in"].wait(20)
    print("the server's own")                                          # a thread that runs no cell
    gates["main_said"].set()
    for thread in threads:
        thread.join(30)
    assert results["a"]["items"] == [{"kind": "stdout", "text": "A1\nA2\n"}]
    assert results["b"]["items"] == [{"kind": "stdout", "text": "B1\nB2\n"}, {"kind": "stderr", "text": "oops\n"}]
    assert real_out.getvalue() == "the server's own\n" and real_err.getvalue() == ""
    assert sys.stdout is real_out and sys.stderr is real_err


def test_stdout_comes_back_whatever_the_cell_did_to_it(monkeypatch):
    """Whatever happens in a cell - an error, ``sys.stdout`` replaced and
    left so, a run inside a run - the streams are afterwards what they were
    before the first capture began, and a stream a cell kept writes to the
    cell that uses it."""
    real_out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", real_out)
    doc = Document(x, addons=[ADDON])
    run(doc, "import sys, io\nkept = sys.stdout\nsys.stdout = io.StringIO()\n1/0")
    assert sys.stdout is real_out
    res = run(doc, "kept.write('late\\n')\nprint('two')")
    assert text(res, "stdout") == "late\ntwo\n"
    other = Document(x, addons=[ADDON])
    console_of(doc).ns["inner"] = lambda: run(other, "print('inside')")
    res = run(doc, "print('before')\ngot = inner()\nprint('after')\ngot['items']")
    assert text(res, "stdout") == "before\nafter\n"
    assert res["out"]["text"] == repr([{"kind": "stdout", "text": "inside\n"}])
    assert sys.stdout is real_out and real_out.getvalue() == ""


def test_display_and_latex_are_bounded_too():
    """Only what was printed counted, and only the text of a value was cut:
    ``expand((x + y + z + 1)**40)`` sent 450,000 characters of LaTeX beside
    its 20,000 of text, and a loop of 3000 ``display()`` 3000 items, 2.4 MB
    of JSON, to a panel that keeps sixty cells."""
    doc = Document(x, addons=[ADDON])
    res = run(doc, "Add(*symbols('alpha0:1500'))")                      # its text fits, its LaTeX does not
    assert len(res["out"]["text"]) < console_module.MAX_REPR and "latex" not in res["out"]
    res = run(doc, "Add(*symbols('a0:4000'))")
    assert res["out"]["text"].endswith(" …") and "latex" not in res["out"]
    assert run(doc, "x**2")["out"]["latex"] == "x^{2}"
    res = run(doc, "for i in range(3000):\n    display(x**i)\nprint('done')\n")
    assert len(res["items"]) == console_module.MAX_ITEMS + 1
    assert res["items"][-1] == {"kind": "stdout", "text": console_module.CUT_NOTE}     # said once, and nothing after
    res = run(doc, "big = Add(*symbols('b0:1000'))\nfor i in range(100):\n    display(big)\n1/0")
    shown = sum(len(i["text"]) + len(i.get("latex", "")) for i in res["items"] if i["kind"] == "display")
    assert 0 < shown <= console_module.MAX_OUTPUT
    assert [i["kind"] for i in res["items"][-2:]] == ["stdout", "error"]           # cut - and the error is still told
    assert "ZeroDivisionError" in res["items"][-1]["text"]
    assert len(json.dumps(res)) < 2 * console_module.MAX_OUTPUT
    res = run(doc, "print('a' * 150000)\nfor i in range(100):\n    display(big)\nprint('b')")
    assert len(json.dumps(res)) < 2 * console_module.MAX_OUTPUT                     # prints and displays share it
    assert text(res, "stdout").endswith(console_module.CUT_NOTE) and "b\n" not in text(res, "stdout")


CLASSES = """
calls = []
class K:
    @property
    def p(self):
        calls.append('p'); return K2()
    def m(self): pass
    def __init__(self): self.held = K2()
class K2:
    zeta = 3
    @property
    def q(self):
        calls.append('q'); return 1
class G:
    here = 1
    def __getattr__(self, name):
        calls.append('getattr ' + name); raise ValueError(name)
    def __dir__(self):
        calls.append('dir'); return ['nope']
class S:
    __slots__ = ('kept',)
    def __getattr__(self, name):
        calls.append('getattr ' + name); raise ValueError(name)
k = K(); g = G(); s = S(); s.kept = 'text'
"""


def test_completion_reads_no_property():
    """The guide says "Nothing is called to find them", but what was before
    the last dot was evaluated: completing ``k.p.`` - and the menu opens by
    itself after a dot - ran the getter of ``p``.  And an object whose
    ``__getattr__`` raised anything but AttributeError had no completions."""
    doc = Document(sin(x) + x, addons=[ADDON])
    run(doc, CLASSES)
    complete = lambda code: run(doc, code, method="complete")
    res = complete("k.")
    assert res["matches"] == ["k.held", "k.m", "k.p"] and res["kinds"] == ["K2", "method", "property"]
    assert complete("k.p.")["matches"] == [] and complete("k.p.q.")["matches"] == []     # not read: nothing to offer
    assert complete("k.held.")["matches"] == ["k.held.q", "k.held.zeta"]                 # what it holds is looked into
    assert complete("k.held.zeta.bit_l")["matches"] == ["k.held.zeta.bit_length"]
    assert complete("g.")["matches"] == ["g.here"]
    assert complete("s.ke")["matches"] == ["s.kept"] and "s.kept.upper" in complete("s.kept.")["matches"]
    assert complete("nothing_there.")["matches"] == [] and complete("k.nope.")["matches"] == []
    assert run(doc, "calls")["out"]["text"] == "[]"                                      # nothing of theirs ran
    # the formula is the console's own to read, and SymPy's slots are there
    res = complete("editor.expr.")
    assert "editor.expr.expand" in res["matches"] and dict(zip(res["matches"], res["kinds"]))["editor.expr.args"] == "property"
    assert complete("editor.doc.un")["kinds"] == ["method"] * 3
    assert "x.name.upper" in complete("x.name.")["matches"]
    # and the names of an ordinary object are the ones dir() gives
    assert {m[2:] for m in complete("x.")["matches"]} == {n for n in dir(x) if not n.startswith("_")}
    assert {m[2:] for m in complete("x._")["matches"]} == {n for n in dir(x) if n.startswith("_") and not n.startswith("__")}


def test_reset_must_be_alone_in_its_cell():
    """``%reset`` in the middle of a cell threw the namespace away and the
    rest of the cell ran on in it: after ``%reset``, ``zz = 3``, ``zz`` as In
    [7] the answer was Out[7]: 3 and the next input In [1], where ``zz``
    was not defined and ``Out`` was ``{7: 3}``."""
    doc = Document(x, addons=[ADDON])
    run(doc, "a = 1")
    for code in ["%reset\nzz = 3\nzz", "b = 2\n%reset", "for i in range(2):\n    %reset\n",
                 "%time %reset", "__magic__('reset', '')", "%reset\n%reset"]:
        res = run(doc, code)
        assert text(res, "error").startswith("UsageError: %reset must be alone in its cell"), code
        assert res["token"] == run(doc, "", method="hello")["token"] and res["next"] == res["n"] + 1
    assert text(run(doc, "%who"), "stdout") == "a\n"                  # nothing ran (no b, no zz), nothing went
    res = run(doc, "__magic__('reset', '')", method="script")           # nor from a script
    assert "must be alone" in text(res, "error") and run(doc, "a")["out"]["text"] == "1"
    before = res["token"]
    res = run(doc, "%reset -f   \n# and a comment\n")                    # alone: it resets
    assert not text(res, "error") and res["next"] == 1 and res["token"] != before
    assert "NameError" in text(run(doc, "a"), "error")


def test_a_console_goes_with_its_document():
    """The console listened to its document through a bound method, and a
    document hands its list of listeners to the one that replaces it when a
    session is opened: every earlier session's console stayed alive, its
    namespace with it, and was called at every change of the formula."""
    from sympy_editor.server import load_session

    def consoles(doc):
        return [cb for cb in doc._listeners if hasattr(cb, "console")]

    seen = []
    doc = Document(sin(x), addons=[ADDON])
    doc.on_change(seen.append)                                          # somebody else's listener stays
    run(doc, "big = list(range(1000))")
    old = weakref.ref(console_of(doc))
    for _ in range(3):                                                  # three sessions opened, one after the other
        new = load_session(doc, doc.export())
        assert new._listeners is doc._listeners
        del doc
        gc.collect()
        assert old() is None                                            # gone with its document
        assert run(new, "editor.expr = editor.expr + 1")["changed"]     # and the new one hears its own
        assert not run(new, "editor.expr")["changed"]
        assert len(consoles(new)) == 1
        old, doc = weakref.ref(console_of(new)), new
    assert len(seen) == 3 and seen.append in doc._listeners
    # a listener whose console is not running anything says nothing
    console = console_of(doc)
    doc.set(x)
    assert console.changed is False

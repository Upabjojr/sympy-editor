"""The history viewer stands on its own: a list of expressions is all it
takes, whoever produced them - an editing session, or a derivation carried
out in Python (the steps of an integration, a chain of rewrites)."""

import json
import re

import pytest
from sympy import Function, Integral, cos, sin, symbols

from sympy_editor import Document, History, save_history_html, to_history_html
from sympy_editor.html import build_history_config

x, y = symbols("x y")


def test_a_history_is_a_list_of_expressions_and_what_produced_them():
    h = History([
        Integral(x * sin(x), x),
        (-x * cos(x) + Integral(cos(x), x), "by parts"),
        (-x * cos(x) + sin(x), "the last integral"),
    ], title="By parts")
    assert len(h) == 3 and h[0] == Integral(x * sin(x), x)
    assert h.actions == [None, "by parts", "the last integral"]
    assert list(h) == h.steps
    assert "By parts" in repr(h) and "3 steps" in repr(h)
    # strings are parsed, and steps can be appended one at a time
    h2 = History()
    h2.add("x**2")
    h2.add(2 * x, "differentiate")
    assert h2.steps == [x**2, 2 * x] and h2.actions == [None, "differentiate"]
    assert "1 step" in repr(History(["x"]))


def test_the_payload_is_what_the_viewer_consumes():
    h = History([x**2 + y, (2 * x, "differentiate")], index=1)
    pay = h.payload()
    assert sorted(pay) == ["actions", "index", "labels", "steps"]
    assert pay["labels"] == ["x**2 + y", "2*x"]
    assert pay["actions"] == ["", "differentiate"]      # nothing produced the first step
    assert pay["index"] == 1
    # each step carries the annotated LaTeX and the node table the diff needs
    assert len(pay["steps"]) == 2
    for step, src in zip(pay["steps"], ["x**2 + y", "2*x"]):
        assert "\\htmlData{path=/}" in step["latex"]
        assert step["nodes"]["/"]["src"] == src
    # the same shape a Document produces, so one viewer serves both
    doc = Document(x**2 + y)
    assert sorted(doc.history_labels()) == sorted(pay)
    with pytest.raises(ValueError, match="nothing to show"):
        History().payload()


def test_a_document_hands_over_its_own_history():
    doc = Document(x**2 + y)
    doc.handle({"action": "call", "path": "/", "func": "diff(x)"})
    h = History.from_document(doc)
    assert len(h) == 2 and h[-1] == 2 * x
    assert h.index == 1                                 # where the session stands
    assert h.actions[1] and "diff" in h.actions[1]
    assert h.payload()["labels"] == [str(e) for e in doc._history]


def test_the_page_mounts_the_viewer_and_nothing_else():
    h = History([x**2, (2 * x, "differentiate")], title="A derivative")
    page = to_history_html(h)
    assert "SympyEditor.mountHistory(" in page
    assert not re.search(r"SympyEditor\.mount\(document\.getElementById", page)   # no editor
    assert "<title>A derivative</title>" in page
    cfg = json.loads(re.search(r"mountHistory\(document\.getElementById\(\"[^\"]+\"\), (\{.*?\})\);\n", page).group(1))
    assert cfg["title"] == "A derivative"
    assert cfg["history"]["labels"] == ["x**2", "2*x"]
    assert cfg["options"]["katexJs"].endswith("katex.min.js")
    assert "backend" not in cfg and "sources" not in cfg     # nothing runs Python behind it
    # a fragment for a notebook cell, and a Document taken as its own history
    frag = to_history_html(Document(x + y), full_page=False)
    assert frag.lstrip().startswith("<link") and not frag.lstrip().startswith("<!DOCTYPE")
    # a plain list of expressions works too, with the actions apart
    page2 = to_history_html([x, x**2], actions=[None, "square it"], title="T")
    assert "square it" in page2


def test_saving_writes_the_page(tmp_path):
    out = save_history_html([x, x + 1], tmp_path / "steps.html", title="Steps")
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    cfg = build_history_config(History([x, x + 1]), title="Steps")
    assert cfg["title"] == "Steps" and len(cfg["history"]["steps"]) == 2


def test_a_history_of_undefined_functions_and_odd_steps_still_renders():
    f = Function("f")
    h = History([f(x), (f(x).diff(x), "differentiate"), (Integral(f(x), x), "unrelated")])
    pay = h.payload()
    assert len(pay["steps"]) == 3
    assert all(s["latex"] for s in pay["steps"])
    with pytest.raises(TypeError):
        to_history_html(h, title="T", actions=["a", "b", "c"])     # options for a ready History

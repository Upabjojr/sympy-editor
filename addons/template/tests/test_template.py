import sys
from pathlib import Path

from sympy import sin, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_addon_template import ADDON  # noqa: E402

x = symbols("x")


def test_the_template_does_everything_once():
    doc = Document(sin(x) + x, addons=[ADDON])
    snap = doc.snapshot()
    assert snap["template"] == {"args": 2, "atoms": 1}
    assert "twice" in [op["name"] for op in snap["ops"]]
    doc.apply("/", "twice")
    assert doc.expr == 2 * (sin(x) + x)
    res = doc.handle({"action": "addon", "addon": "template", "method": "count", "path": "/"})
    assert res["query"]["result"]["args"] == 2
    doc.handle({"action": "addon", "addon": "template", "method": "double"})
    assert doc.expr == 4 * (sin(x) + x)
    assert doc.history_labels()["actions"][-1] == "Template: doubled"

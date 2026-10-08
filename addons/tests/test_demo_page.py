"""addons/demo.html embeds the add-ons' Python and JavaScript as they were
when it was built.  A stale page silently runs old code - a rewrite that
behaved differently from the tests was exactly that - so when the page
exists it must match the sources: rebuild it with ``python addons/demo.py``."""
import json
import re
import sys
from pathlib import Path

import pytest

ADDONS = Path(__file__).resolve().parents[1]
PAGE = ADDONS / "demo.html"
sys.path.insert(0, str(ADDONS.parent / "src"))
for pkg in ("sympy_editor_tree", "sympy_editor_plot", "sympy_editor_matching", "sympy_editor_latex", "sympy_editor_console", "sympy_editor_feynman"):
    sys.path.insert(0, str(ADDONS / pkg))


def test_the_demo_page_is_up_to_date():
    if not PAGE.exists():
        pytest.skip("no demo page built (python addons/demo.py)")
    from sympy_editor.html import python_sources, read_static
    text = PAGE.read_text(encoding="utf-8")
    hint = "rebuild it: python addons/demo.py"
    assert read_static("editor.js").strip() in text, f"demo.html embeds an outdated editor.js; {hint}"
    m = re.search(r"SympyEditor\.mount\(document\.getElementById\(\"[^\"]+\"\), (\{.*?\})\);\n", text)
    assert m, "no editor found in demo.html"
    cfg = json.loads(m.group(1))
    for name, src in python_sources().items():
        assert cfg["sources"].get(name) == src, f"demo.html embeds an outdated {name}; {hint}"
    import importlib
    import importlib.util
    for module, files in cfg["packages"].items():
        addon = importlib.import_module(module).ADDON
        assert files == addon.python_sources(), f"demo.html embeds an outdated {module}; {hint}"
    # every add-on demo.py builds the page with is in it, and no other
    spec = importlib.util.spec_from_file_location("addons_demo", ADDONS / "demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    built = demo.addons()
    assert set(cfg["packages"]) == {type(a).__module__.split(".")[0] for a in built}, \
        f"demo.html carries other add-ons than demo.py builds; {hint}"
    # the front end of each add-on that is on - its script, its styles and its
    # options, not the script alone
    by_name = {a["name"]: a for a in cfg["addons"]}
    assert by_name, "no add-on is on in demo.html"
    for module in cfg["packages"]:
        addon = importlib.import_module(module).ADDON
        if addon.name in by_name:
            assert by_name[addon.name] == json.loads(json.dumps(addon.client())), \
                f"demo.html embeds an outdated front end for {addon.name}; {hint}"

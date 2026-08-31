"""The generated example pages embed the package's JavaScript and, for the
Pyodide backend, its Python sources.  A stale page silently runs old code, so
whenever one exists it must match the current sources."""

import json
import re
from pathlib import Path

import pytest

from sympy_editor.html import python_sources, read_static

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
PAGES = sorted(EXAMPLES.glob("*.html"))


@pytest.mark.parametrize("page", PAGES, ids=[p.name for p in PAGES])
def test_generated_page_is_up_to_date(page):
    text = page.read_text(encoding="utf-8")
    hint = f"regenerate it: python examples/{page.stem}.py"
    assert read_static("editor.js").strip() in text, f"{page.name} embeds an outdated editor.js; {hint}"
    assert read_static("editor.css").strip() in text, f"{page.name} embeds an outdated editor.css; {hint}"
    # An editor page mounts an editor; a history page mounts the viewer alone.
    configs = re.findall(r"SympyEditor\.mount(?:History)?\(document\.getElementById\(\"[^\"]+\"\), (\{.*?\})\);\n", text)
    assert configs, f"no editor or history viewer found in {page.name}"
    current = python_sources()
    for raw in configs:
        cfg = json.loads(raw)
        if cfg.get("backend") == "pyodide":
            for name, src in current.items():
                assert cfg["sources"].get(name) == src, f"{page.name} embeds an outdated {name}; {hint}"


def test_no_pages_is_fine():
    # Pages are git-ignored build products; their absence is not an error.
    assert EXAMPLES.is_dir()


NOTEBOOKS = sorted(EXAMPLES.glob("*.ipynb"))


@pytest.mark.parametrize("nb", NOTEBOOKS, ids=[p.name for p in NOTEBOOKS])
def test_notebook_is_well_formed(nb):
    doc = json.loads(nb.read_text(encoding="utf-8"))
    assert doc["nbformat"] == 4 and doc["cells"]
    assert doc["metadata"]["kernelspec"]["language"] == "python"
    for cell in doc["cells"]:
        assert cell["cell_type"] in ("markdown", "code")
        assert isinstance(cell["source"], list)


def test_the_manualintegrate_example_still_runs(tmp_path, monkeypatch):
    """The notebook reaches into `sympy.integrals.manualintegrate` - a private
    rule tree that SymPy is free to change.  It is an example, not part of
    the library, but a broken example is worse than none: run its cells."""
    from sympy import Symbol, sin

    nb = EXAMPLES / "manualintegrate_steps.ipynb"
    if not nb.exists():
        pytest.skip("example not present")
    cells = [c for c in json.loads(nb.read_text(encoding="utf-8"))["cells"] if c["cell_type"] == "code"]
    monkeypatch.chdir(tmp_path)                      # the last cell writes a file
    ns = {}
    for i, cell in enumerate(cells):
        exec(compile("".join(cell["source"]), f"<cell {i}>", "exec"), ns)
    x = Symbol("x")
    hist = ns["integration_history"](x * sin(x), x)
    assert len(hist) > 2
    assert hist[0] == ns["Integral"](x * sin(x), x)
    assert hist[-1] == (x * sin(x)).integrate(x)     # the chain really ends at the antiderivative
    assert hist.actions[1] == "Parts"
    assert (tmp_path / "integration_steps.html").is_file()

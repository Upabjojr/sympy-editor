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

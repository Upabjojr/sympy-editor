"""Every library the project declares or links is credited: THIRD-PARTY.md
names each one - the package's own dependencies, the add-ons', what the
Android app's Gradle files and Chaquopy install, what the bundles vendor -
and the in-app guide has its credits section.  A dependency added without
its credit fails here."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CREDITS = (ROOT / "THIRD-PARTY.md").read_text(encoding="utf-8").lower()


def _name(requirement: str) -> str:
    """``sympy-matching>=0.0.4`` -> ``sympy-matching``."""
    return re.split(r"[<>=!~\[; ]", requirement.strip(), 1)[0].lower()


def _credited(name: str) -> bool:
    return name.lower() in CREDITS or name.lower().replace("-", "_") in CREDITS


def test_the_python_dependencies_are_credited():
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    runtime = re.search(r"^dependencies = \[(.*?)\]", toml, re.M | re.S).group(1)
    jupyter = re.search(r"^jupyter = \[(.*?)\]", toml, re.M | re.S).group(1)
    names = {_name(r) for r in re.findall(r'"([^"]+)"', runtime + jupyter)}
    assert names >= {"sympy", "anywidget"}
    missing = sorted(n for n in names if not _credited(n))
    assert not missing, f"not in THIRD-PARTY.md: {missing}"


def test_the_add_ons_requirements_are_credited():
    names = set()
    for manifest in (ROOT / "addons").glob("*/addon.json"):
        names |= {_name(r) for r in json.loads(manifest.read_text(encoding="utf-8")).get("requires", [])}
    for toml in (ROOT / "addons").glob("*/pyproject.toml"):
        for block in re.findall(r"^(?:dependencies|fast) = \[(.*?)\]", toml.read_text(encoding="utf-8"), re.M | re.S):
            names |= {_name(r) for r in re.findall(r'"([^"]+)"', block)}
    names -= {"sympy-editor", "sympy-editor-latex"}          # the project's own packages
    assert {"lark", "sympy-matching"} <= names
    missing = sorted(n for n in names if not _credited(n))
    assert not missing, f"not in THIRD-PARTY.md: {missing}"


def test_what_the_android_app_links_is_credited():
    app = (ROOT / "mobile" / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    libraries = re.findall(r'implementation\("([^:"]+):([^:"]+):', app)
    assert libraries
    for group, artifact in libraries:
        credited = {"androidx.appcompat": "androidx.appcompat", "androidx.webkit": "androidx.webkit",
                    "com.microsoft.onnxruntime": "onnx runtime for android"}.get(group, artifact)
        assert credited in CREDITS, f"{group}:{artifact} is not in THIRD-PARTY.md"
    installed = {_name(r) for r in re.findall(r'install\("([^"]+)"\)', app)}
    missing = sorted(n for n in installed if not _credited(n))
    assert not missing, f"installed by Chaquopy, not in THIRD-PARTY.md: {missing}"
    for plugin in ("chaquopy", "kotlin standard library"):
        assert plugin in CREDITS, plugin


def test_what_the_bundles_vendor_is_credited():
    www = (ROOT / "mobile" / "build_www.py").read_text(encoding="utf-8")
    for name in ("katex", "pyodide", "micropip", "plotly.js", "python-apple-support", "cpython"):
        assert name in CREDITS, name
    assert "Python-Apple-support" in www and "Chaquopy" in www        # the app bundle's NOTICE names both runtimes


def test_the_app_credits_what_it_uses():
    js = (ROOT / "src" / "sympy_editor" / "static" / "editor.js").read_text(encoding="utf-8")
    help_html = js[js.index("var HELP_HTML"):js.index('].join("");', js.index("var HELP_HTML"))]
    assert "Credits and licences" in help_html
    for name in ("SymPy", "mpmath", "KaTeX", "Pyodide", "anywidget", "lark", "sympy-matching", "Plotly.js",
                 "NumPy", "ONNX Runtime", "CPython", "Chaquopy", "Python-Apple-support", "NOTICE.txt"):
        assert name in help_html, name

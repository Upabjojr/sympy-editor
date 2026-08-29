import json
import re
import subprocess
import sys

from sympy import symbols

from sympy_editor import Document, to_html, save_html
from sympy_editor.html import build_config, python_sources

x, y = symbols("x y")


def _config_from(html):
    m = re.search(r"SympyEditor\.mount\(document\.getElementById\(\"[^\"]+\"\), (\{.*\})\);", html)
    assert m, "mount call not found"
    return json.loads(m.group(1))


def test_full_page_pyodide():
    html = to_html(x**2 + y, title="Demo <1>")
    assert html.startswith("<!DOCTYPE html>")
    assert "Demo &lt;1&gt;" in html
    assert "var SympyEditor" in html
    cfg = _config_from(html)
    assert cfg["backend"] == "pyodide"
    assert set(cfg["sources"]) == {"__init__.py", "printer.py", "ops.py", "document.py"}
    assert cfg["snapshot"]["nodes"]["/"]["src"] == "x**2 + y"
    assert cfg["srepr"].startswith("Add(")
    assert "</script>" not in html.split("SympyEditor.mount", 1)[1].split("</script>", 1)[0]


def test_fragment_readonly():
    frag = to_html(x, full_page=False, editable=False)
    assert not frag.startswith("<!DOCTYPE")
    cfg = _config_from(frag)
    assert cfg["backend"] == "readonly"
    assert "sources" not in cfg


def test_script_safety():
    # A symbol name containing "</script>" must not break out of the script tag.
    evil = symbols("</script><b>")
    html = to_html(evil, editable=False)
    body = html.split("SympyEditor.mount", 1)[1]
    assert "</script><b>" not in body
    assert "\\u003c/script>" in body


def test_http_config_and_options():
    doc = Document(x)
    cfg = build_config(doc, backend="http", token="abc", options={"displayMode": False})
    assert cfg["apiUrl"] == "/api" and cfg["token"] == "abc"
    assert cfg["options"]["finishButton"] is True
    assert cfg["options"]["displayMode"] is False
    assert cfg["options"]["katexJs"].startswith("https://")


def test_save_html(tmp_path):
    p = save_html(x + 1, tmp_path / "e.html", urls={"katexJs": "./katex.js"})
    text = p.read_text(encoding="utf-8")
    assert '"katexJs": "./katex.js"' in text or "./katex.js" in text


def test_embedded_sources_are_importable(tmp_path):
    # The Pyodide backend imports the embedded modules as a package.
    pkg = tmp_path / "sympy_editor"
    pkg.mkdir()
    for name, src in python_sources().items():
        (pkg / name).write_text(src, encoding="utf-8")
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from sympy_editor.document import Document; from sympy import sympify; "
        "d = Document(sympify(%r)); "
        "print(d.handle({'action': 'replace', 'path': '/', 'src': 'y'})['src'])"
    ) % (str(tmp_path), "Add(Symbol('x'), Integer(1))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "y"


def test_katex_css_url_is_escaped_in_the_link():
    page = to_html(x, urls={"katexCss": 'https://cdn.example/katex.css?a=1&b="2"'})
    assert 'href="https://cdn.example/katex.css?a=1&amp;b=&quot;2&quot;"' in page

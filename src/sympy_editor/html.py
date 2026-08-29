"""Standalone HTML output: a full page or an embeddable fragment.

Editing in the generated HTML is powered by one of three backends (see
``static/editor.js``):

``pyodide`` (default)
    Fully self-contained file.  The core Python modules of this package are
    embedded in the page and executed by Pyodide (loaded lazily from a CDN on
    the first edit), so no server is needed.
``http``
    Edits are sent to :func:`sympy_editor.serve`'s local server.
``readonly``
    Rendering and structural selection only.
"""

from __future__ import annotations

import html as _html
import json
import secrets
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Union

from sympy import Basic, srepr

from .document import Document

__all__ = [
    "KATEX_VERSION",
    "PYODIDE_VERSION",
    "default_urls",
    "to_html",
    "save_html",
    "display_html",
    "build_config",
]

STATIC_DIR = Path(__file__).parent / "static"

KATEX_VERSION = "0.16.22"
PYODIDE_VERSION = "0.27.7"

#: Python modules embedded in Pyodide-backed pages (order matters for nothing,
#: but keep this list in sync with the imports of document.py).
EMBEDDED_MODULES = ("printer.py", "ops.py", "document.py")


def default_urls() -> Dict[str, str]:
    """CDN locations of the JavaScript dependencies (all override-able)."""
    return {
        "katexJs": f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/katex.min.js",
        "katexCss": f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/katex.min.css",
        "pyodideJs": f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/pyodide.js",
        "pyodideIndex": f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/",
    }


def read_static(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def python_sources() -> Dict[str, str]:
    """Sources of the core modules, for execution inside Pyodide."""
    pkg = Path(__file__).parent
    sources = {"__init__.py": "# sympy_editor core, embedded for Pyodide\n"}
    for name in EMBEDDED_MODULES:
        sources[name] = (pkg / name).read_text(encoding="utf-8")
    return sources


def _script_json(obj: Any) -> str:
    """JSON that is safe to inline inside a <script> element."""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _as_document(expr: Union[Basic, str, Document], **document_kwargs) -> Document:
    if isinstance(expr, Document):
        if document_kwargs:
            raise TypeError("Document options cannot be combined with an existing Document")
        return expr
    return Document(expr, **document_kwargs)


def build_config(
    doc: Document,
    *,
    backend: str = "pyodide",
    options: Optional[Dict[str, Any]] = None,
    urls: Optional[Dict[str, str]] = None,
    api_url: str = "/api",
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """The JSON config consumed by ``SympyEditor.mount`` in editor.js."""
    if backend not in ("pyodide", "http", "readonly"):
        raise ValueError(f"Unknown backend {backend!r}")
    all_urls = default_urls()
    all_urls.update(urls or {})
    opts: Dict[str, Any] = {"katexJs": all_urls["katexJs"], "katexCss": all_urls["katexCss"]}
    opts.update(options or {})
    cfg: Dict[str, Any] = {"backend": backend, "snapshot": doc.snapshot(), "options": opts}
    if backend == "pyodide":
        cfg.update(
            pyodideJs=all_urls["pyodideJs"],
            pyodideIndex=all_urls["pyodideIndex"],
            sources=python_sources(),
            srepr=srepr(doc.expr),
            document={
                "printer_settings": doc.printer_settings,
                "parser": doc.parser,
                "symbols": [srepr(obj) for obj in doc.declared.values()],
            },
        )
    elif backend == "http":
        cfg.update(apiUrl=api_url, token=token or "")
        cfg["options"].setdefault("finishButton", True)
    return cfg


def render_fragment(config: Dict[str, Any], element_id: Optional[str] = None) -> str:
    """HTML fragment (styles + container + inline scripts) for ``config``."""
    element_id = element_id or "sympy-editor-" + uuid.uuid4().hex[:12]
    katex_css = _html.escape(str(config["options"].get("katexCss", "")), quote=True)
    return (
        f'<link rel="stylesheet" href="{katex_css}">\n'
        f"<style>\n{read_static('editor.css')}\n</style>\n"
        f'<div id="{element_id}" class="sympy-editor-host"></div>\n'
        # Several fragments on one page share one SympyEditor (and, through
        # it, one Pyodide runtime); the script is skipped once it is defined.
        f'<script>\nif (!window.SympyEditor) {{\n{read_static("editor.js")}\n}}\n</script>\n'
        "<script>\n"
        f'SympyEditor.mount(document.getElementById("{element_id}"), {_script_json(config)});\n'
        "</script>\n"
    )


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>
  body { margin: 2rem; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background: #ffffff; color: #1f2328; }
  @media (prefers-color-scheme: dark) { body { background: #1e1e1e; color: #e6e6e6; } }
  h1 { font-size: 1.2rem; font-weight: 600; margin: 0 0 1rem; }
  @media (max-width: 640px) { body { margin: 0.5rem; } h1 { font-size: 1rem; margin: 0.2rem 0 0.5rem; } }
</style>
</head>
<body>
<h1>%(title)s</h1>
%(fragment)s</body>
</html>
"""


def render_page(config: Dict[str, Any], title: str = "SymPy editor") -> str:
    return _PAGE % {"title": _html.escape(title), "fragment": render_fragment(config)}


def to_html(
    expr: Union[Basic, str, Document],
    *,
    full_page: bool = True,
    editable: bool = True,
    backend: Optional[str] = None,
    title: str = "SymPy editor",
    options: Optional[Dict[str, Any]] = None,
    urls: Optional[Dict[str, str]] = None,
    **document_kwargs,
) -> str:
    """Render ``expr`` as HTML.

    Parameters
    ----------
    expr
        SymPy expression, string or :class:`Document`.
    full_page
        Complete document (``True``) or embeddable fragment (``False``).
    editable
        Use the Pyodide backend (in-browser Python) so the page can be edited
        without a server; ``False`` gives a read-only (but selectable) view.
    backend
        Explicit backend name; overrides ``editable``.
    options
        Front-end options (``displayMode``, ``toolbar``, ``showSource``,
        ``readOnly``...), see ``DEFAULTS`` in editor.js.
    urls
        Override CDN URLs (``katexJs``, ``katexCss``, ``pyodideJs``, ``pyodideIndex``).
    document_kwargs
        Passed to :class:`Document` (``printer_settings``, ``parser``...).
    """
    doc = _as_document(expr, **document_kwargs)
    backend = backend or ("pyodide" if editable else "readonly")
    config = build_config(doc, backend=backend, options=options, urls=urls)
    return render_page(config, title) if full_page else render_fragment(config)


def save_html(expr, path, **kwargs) -> Path:
    """Write :func:`to_html` output to ``path`` and return it."""
    path = Path(path)
    path.write_text(to_html(expr, **kwargs), encoding="utf-8")
    return path


def display_html(expr, **kwargs):
    """An ``IPython.display.HTML`` fragment (works without anywidget; edits
    run in the browser via Pyodide and are *not* sent back to the kernel)."""
    from IPython.display import HTML

    kwargs.setdefault("full_page", False)
    return HTML(to_html(expr, **kwargs))


def new_token() -> str:
    return secrets.token_urlsafe(24)

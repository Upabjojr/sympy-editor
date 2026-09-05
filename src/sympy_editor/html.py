"""Standalone HTML output: a full page or an embeddable fragment.

Editing in the generated HTML is powered by one of four backends (see
``static/editor.js``):

``pyodide`` (default)
    Fully self-contained file.  The core Python modules of this package are
    embedded in the page and executed by Pyodide (loaded lazily from a CDN on
    the first edit), so no server is needed.
``http``
    Edits are sent to :func:`sympy_editor.serve`'s local server.
``native``
    The host application runs Python itself and answers through the object it
    injects in the page (``window.SympyEditorPy``): the Android app ships
    CPython and SymPy, so nothing is downloaded and no WebAssembly is needed
    (see ``mobile/android``).
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
from .examples import examples
from .history import History

__all__ = [
    "KATEX_VERSION",
    "PYODIDE_VERSION",
    "SYMPY_VERSION",
    "default_urls",
    "to_html",
    "save_html",
    "display_html",
    "build_config",
    "addon_clients",
    "to_history_html",
    "save_history_html",
    "display_history",
]

STATIC_DIR = Path(__file__).parent / "static"

KATEX_VERSION = "0.16.22"
PYODIDE_VERSION = "0.28.3"
#: SymPy in the browser: Pyodide's own package lags behind (1.13.3 in 0.28),
#: so the pages load this release's pure-Python wheel from PyPI (after
#: Pyodide's mpmath); the offline bundles vendor it.
SYMPY_VERSION = "1.14.0"
SYMPY_WHEEL = ("https://files.pythonhosted.org/packages/a2/09/77d55d46fd61b4a135c444fc97158ef34a095e5681d0a6c10b75bf356191/"
               f"sympy-{SYMPY_VERSION}-py3-none-any.whl")

#: Python modules embedded in Pyodide-backed pages (order matters for nothing,
#: but keep this list in sync with the imports of document.py).
EMBEDDED_MODULES = ("printer.py", "ops.py", "addons.py", "document.py")


def default_urls() -> Dict[str, str]:
    """CDN locations of the JavaScript dependencies (all override-able)."""
    return {
        "katexJs": f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/katex.min.js",
        "katexCss": f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/katex.min.css",
        "pyodideJs": f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/pyodide.js",
        "pyodideIndex": f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/",
        "sympyWheel": SYMPY_WHEEL,     # "" to use Pyodide's own sympy package instead
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


def addon_clients(doc: Document) -> list:
    """The descriptors of the document's add-ons for the front end (name,
    label, JavaScript and CSS sources, options)."""
    return [addon.client() for addon in doc.addons.values()]


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
    if backend not in ("pyodide", "http", "readonly", "native"):
        raise ValueError(f"Unknown backend {backend!r}")
    all_urls = default_urls()
    all_urls.update(urls or {})
    opts: Dict[str, Any] = {"katexJs": all_urls["katexJs"], "katexCss": all_urls["katexCss"]}
    opts.update(options or {})
    cfg: Dict[str, Any] = {"backend": backend, "snapshot": doc.snapshot(), "options": opts}
    if opts.get("sessions"):
        cfg["examples"] = examples()      # what a new session can start from
    # The add-ons' front ends: loaded by SympyEditor.mount (once per page).
    cfg["addons"] = addon_clients(doc)
    document = {
        "printer_settings": doc.printer_settings,
        "parser": doc.parser,
        "symbols": [srepr(obj) for obj in doc.declared.values()],
    }
    # The add-ons the page can switch on: the ones that are on and the rest of
    # the document's catalogue, as far as it loads - by module name, which is
    # what the Python that makes the document again (a Pyodide page, the
    # host application) can import.
    catalog = []
    for entry in doc.available_addons():
        if "error" in entry:
            continue
        addon = doc._load(entry["name"])
        if addon not in catalog:
            catalog.append(addon)
    if doc.addons:
        document["addons"] = [addon.module for addon in doc.addons.values()]
    if catalog:
        document["available"] = [addon.module for addon in catalog]
    if backend == "pyodide":
        cfg.update(
            pyodideJs=all_urls["pyodideJs"],
            pyodideIndex=all_urls["pyodideIndex"],
            sympyWheel=all_urls.get("sympyWheel", ""),
            sources=python_sources(),
            srepr=srepr(doc.expr),
            document=document,
        )
        if catalog:
            # Each add-on's package, written beside sympy_editor's in the
            # Pyodide file system, and what micropip must install first -
            # for what is on and what may be switched on later.
            cfg["packages"] = {addon.module: addon.python_sources() for addon in catalog}
            cfg["micropip"] = sorted({pkg for addon in catalog for pkg in addon.pyodide_packages()})
    elif backend == "native":
        # The host application runs Python itself (the Android app ships
        # CPython and SymPy); it only needs to know which expression to start
        # from - the sources and the runtime are its own.
        cfg.update(srepr=srepr(doc.expr), document=document)
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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>%(title)s</title>
%(head)s<style>
  body { margin: 2rem; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background: #ffffff; color: #1f2328; }
  @media (prefers-color-scheme: dark) { body { background: #1e1e1e; color: #e6e6e6; } }
  h1 { font-size: 1.2rem; font-weight: 600; margin: 0 0 1rem;
       display: flex; align-items: center; gap: 0.5rem; }
  /* the application's own icon, on the title's line and as tall as it:
     a page in a WebView has no title bar of its own to carry either */
  h1 .page-logo { flex: 0 0 auto; display: block; }
  h1 .page-logo svg { display: block; width: 1.7em; height: 1.7em; }
  /* phones: a margin that keeps the controls clear of rounded corners and notches
   * (the safe-area insets where the browser reports them; the Android app pads natively) */
  @media (max-width: 640px) {
    body { margin: 0.75rem;
           padding: env(safe-area-inset-top, 0) env(safe-area-inset-right, 0) env(safe-area-inset-bottom, 0) env(safe-area-inset-left, 0); }
    h1 { font-size: 1rem; margin: 0.2rem 0 0.5rem; gap: 0.4rem; }
  }
</style>
</head>
<body>
<h1>%(heading)s</h1>
%(fragment)s</body>
</html>
"""


def render_page(config: Dict[str, Any], title: str = "SymPy editor", head: str = "",
                element_id: Optional[str] = None, logo: str = "") -> str:
    """The full page; ``head`` is extra markup for its ``<head>`` (a web app
    manifest, meta tags, a service-worker registration...); ``element_id``
    fixes the editor's element id (random otherwise) for a reproducible page;
    ``logo`` is SVG markup shown beside the title (the applications put their
    own icon there, having no title bar to carry it)."""
    name = _html.escape(title)
    # aria-hidden: the heading beside it already says the name, and the mark's
    # own <title>/<desc> - the note that lets us use SymPy's logo - would
    # otherwise be read out as part of the heading.
    heading = f'<span class="page-logo" aria-hidden="true">{logo}</span>{name}' if logo else name
    return _PAGE % {"title": name, "heading": heading,
                    "fragment": render_fragment(config, element_id), "head": head}


def to_html(
    expr: Union[Basic, str, Document],
    *,
    full_page: bool = True,
    editable: bool = True,
    backend: Optional[str] = None,
    title: str = "SymPy editor",
    options: Optional[Dict[str, Any]] = None,
    urls: Optional[Dict[str, str]] = None,
    head: str = "",
    element_id: Optional[str] = None,
    logo: str = "",
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
        Explicit backend name (``"pyodide"``, ``"http"``, ``"native"`` - the
        host application's own Python, see mobile/android -, ``"readonly"``);
        overrides ``editable``.
    options
        Front-end options (``displayMode``, ``toolbar``, ``showSource``,
        ``readOnly``...), see ``DEFAULTS`` in editor.js.
    urls
        Override CDN URLs (``katexJs``, ``katexCss``, ``pyodideJs``, ``pyodideIndex``,
        ``sympyWheel`` - the SymPy wheel to load in the browser, ``""`` for
        Pyodide's own package).
    head
        Extra markup for the ``<head>`` of a full page (ignored for a fragment).
    element_id
        The id of the editor's element (a random one by default; fix it for a
        reproducible page, e.g. a web app bundle whose cache is keyed by content).
    logo
        SVG markup for a mark beside the page's title (the mobile apps and the
        web app show their own icon there); ignored for a fragment.
    document_kwargs
        Passed to :class:`Document` (``printer_settings``, ``parser``,
        ``addons``...).
    """
    doc = _as_document(expr, **document_kwargs)
    backend = backend or ("pyodide" if editable else "readonly")
    config = build_config(doc, backend=backend, options=options, urls=urls)
    return render_page(config, title, head, element_id, logo) if full_page else render_fragment(config, element_id)


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


# -- the history viewer, on its own ----------------------------------------
# The step-by-step view of a history needs no editor and no Python backend:
# a list of expressions is enough (see history.py).  These build a page - or
# a notebook cell - around SympyEditor.mountHistory.


def _as_history(steps, **kwargs) -> History:
    if isinstance(steps, History):
        if kwargs:
            raise TypeError("History options cannot be combined with an existing History")
        return steps
    if isinstance(steps, Document):
        return History.from_document(steps, **kwargs)
    return History(steps, **kwargs)


def build_history_config(history: History, *, title: Optional[str] = None,
                         urls: Optional[Dict[str, str]] = None,
                         options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The JSON config consumed by ``SympyEditor.mountHistory``."""
    all_urls = default_urls()
    all_urls.update(urls or {})
    opts: Dict[str, Any] = {"katexJs": all_urls["katexJs"], "katexCss": all_urls["katexCss"]}
    opts.update(options or {})
    return {"history": history.payload(), "title": title or history.title or "History", "options": opts}


def render_history_fragment(config: Dict[str, Any], element_id: Optional[str] = None) -> str:
    """HTML fragment (styles + container + inline scripts) for ``config``."""
    element_id = element_id or "sympy-history-" + uuid.uuid4().hex[:12]
    katex_css = _html.escape(str(config["options"].get("katexCss", "")), quote=True)
    return (
        f'<link rel="stylesheet" href="{katex_css}">\n'
        f"<style>\n{read_static('editor.css')}\n</style>\n"
        f'<div id="{element_id}" class="sympy-editor-host"></div>\n'
        f'<script>\nif (!window.SympyEditor) {{\n{read_static("editor.js")}\n}}\n</script>\n'
        "<script>\n"
        f'SympyEditor.mountHistory(document.getElementById("{element_id}"), {_script_json(config)});\n'
        "</script>\n"
    )


def to_history_html(
    steps,
    *,
    full_page: bool = True,
    title: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    urls: Optional[Dict[str, str]] = None,
    head: str = "",
    element_id: Optional[str] = None,
    **history_kwargs,
) -> str:
    """A page showing a sequence of expressions step by step, each change as
    a diff - the editor's History view, without the editor.

    ``steps`` is a :class:`~sympy_editor.History`, a :class:`Document` (its
    own history), or anything :class:`History` accepts: a list of
    expressions, each optionally paired with what produced it
    (``(expr, "integration by parts")``).  Nothing here needs the WYSIWYG
    editor, so a derivation computed in Python can be shown the same way.

    The other arguments are :func:`to_html`'s: ``full_page`` for a document
    rather than an embeddable fragment, ``urls`` to override the KaTeX
    locations, ``head`` for extra markup, ``element_id`` for a reproducible
    page.  ``history_kwargs`` go to :class:`History` (``actions``,
    ``index``, ``printer_settings``).
    """
    history = _as_history(steps, **history_kwargs)
    config = build_history_config(history, title=title, urls=urls, options=options)
    fragment = render_history_fragment(config, element_id)
    if not full_page:
        return fragment
    # No <h1> of its own: the report inside the viewer already opens with the
    # title and the step count.
    page = _PAGE.replace("<h1>%(heading)s</h1>\n", "")
    return page % {"title": _html.escape(config["title"]), "fragment": fragment, "head": head}


def save_history_html(steps, path, **kwargs) -> Path:
    """Write :func:`to_history_html` output to ``path`` and return it."""
    path = Path(path)
    path.write_text(to_history_html(steps, **kwargs), encoding="utf-8")
    return path


def display_history(steps, **kwargs):
    """An ``IPython.display.HTML`` fragment showing the steps in a notebook."""
    from IPython.display import HTML

    kwargs.setdefault("full_page", False)
    return HTML(to_history_html(steps, **kwargs))


def new_token() -> str:
    return secrets.token_urlsafe(24)

"""sympy-editor: click-to-edit WYSIWYG editor for SymPy expressions.

Quick start
-----------
Jupyter (requires ``pip install sympy-editor[jupyter]``)::

    from sympy_editor import edit
    w = edit(x**2 + sin(x)/y)   # display the widget
    w.expr                      # the live, edited expression

Standalone HTML (self-contained file, editing runs in the browser)::

    from sympy_editor import save_html
    save_html(expr, "expr.html")

Local server (blocks until you press *Done* in the browser)::

    from sympy_editor import serve
    new_expr = serve(expr)

A history of expressions, step by step, with no editor around it - a
derivation carried out in Python shown the way the editor shows its own
sessions::

    from sympy_editor import History, save_history_html
    steps = History([f0, (f1, "integration by parts"), (f2, "the last integral")])
    save_history_html(steps, "steps.html")
"""

from .addons import Addon, installed_addons, load_addon
from .document import Document
from .history import History
from .html import (display_history, display_html, save_history_html, save_html,
                   to_history_html, to_html)
from .ops import get_ops, make_op, register_op
from .printer import (
    AnnotatedLatexPrinter,
    AnnotatedStrPrinter,
    annotate,
    annotate_str,
    latex_spans,
    delete_at,
    format_path,
    get_at,
    parse_path,
    replace_at,
    strip_annotations,
)
from .server import EditorServer, serve

__version__ = "0.1.0"

__all__ = [
    "Addon",
    "AnnotatedLatexPrinter",
    "AnnotatedStrPrinter",
    "annotate_str",
    "latex_spans",
    "Document",
    "EditorServer",
    "History",
    "annotate",
    "delete_at",
    "display_history",
    "display_html",
    "edit",
    "format_path",
    "get_at",
    "get_ops",
    "installed_addons",
    "load_addon",
    "make_op",
    "parse_path",
    "register_op",
    "replace_at",
    "save_history_html",
    "save_html",
    "serve",
    "strip_annotations",
    "to_history_html",
    "to_html",
]


def edit(expr, backend="auto", **kwargs):
    """Edit ``expr`` in Jupyter.

    ``backend``:

    ``"kernel"``
        An `anywidget <https://anywidget.dev>`_ widget: every edit runs in
        **this** kernel's SymPy, ``w.expr`` is live (needs
        ``pip install "sympy-editor[jupyter]"``).
    ``"pyodide"``
        Plain HTML output that runs its own SymPy in the browser (Pyodide);
        it survives ``nbconvert`` / static viewers, but edits do not reach
        the kernel.  The same page as :func:`to_html`.
    ``"auto"`` (default)
        ``"kernel"`` when anywidget is installed, ``"pyodide"`` otherwise.
    """
    if backend not in ("auto", "kernel", "pyodide"):
        raise ValueError("backend must be 'auto', 'kernel' or 'pyodide'")
    if backend == "pyodide":
        return display_html(expr, **kwargs)
    try:
        from .widget import SympyEditorWidget
    except ImportError as exc:  # anywidget missing
        if backend == "kernel":
            raise ImportError("backend='kernel' needs anywidget: pip install 'sympy-editor[jupyter]'") from exc
        import warnings

        warnings.warn("anywidget is not installed: using the Pyodide (in-browser) editor; edits will not reach the kernel. "
                      "pip install 'sympy-editor[jupyter]' for the kernel-backed widget.", stacklevel=2)
        return display_html(expr, **kwargs)
    return SympyEditorWidget(expr, **kwargs)

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
"""

from .document import Document
from .html import display_html, save_html, to_html
from .ops import get_ops, register_op
from .printer import (
    AnnotatedLatexPrinter,
    annotate,
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
    "AnnotatedLatexPrinter",
    "Document",
    "EditorServer",
    "annotate",
    "delete_at",
    "display_html",
    "edit",
    "format_path",
    "get_at",
    "get_ops",
    "parse_path",
    "register_op",
    "replace_at",
    "save_html",
    "serve",
    "strip_annotations",
    "to_html",
]


def edit(expr, **kwargs):
    """Return a Jupyter widget editing ``expr`` (see :class:`~sympy_editor.widget.SympyEditorWidget`)."""
    try:
        from .widget import SympyEditorWidget
    except ImportError as exc:  # anywidget missing
        raise ImportError(
            "Jupyter integration needs anywidget: pip install 'sympy-editor[jupyter]'"
        ) from exc
    return SympyEditorWidget(expr, **kwargs)

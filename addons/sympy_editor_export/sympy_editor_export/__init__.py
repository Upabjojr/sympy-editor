"""sympy-editor add-on: the formula written out in other languages.

The selection - a node, a range of terms, or the whole formula when nothing
is selected - written as LaTeX, MathML, Python, C, Fortran, JavaScript,
Octave/MATLAB, Julia and Rust by SymPy's own printers, and as a whole
function (``sympy.utilities.codegen``) with the free symbols as its
arguments.  The panel shows each in a read-only box with Copy and Save.

One method, a query: ``export`` (``{"path", "children", "format",
"options"}``) answers :func:`~sympy_editor_export.export.export`'s dict.
Nothing changes the formula, so nothing goes in the history.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from sympy_editor.addons import Addon
from sympy_editor.printer import extract_range, get_at, parse_path

from .export import FORMATS, export, formats_for_client

__all__ = ["ExportAddon", "ADDON", "FORMATS", "export"]

STATIC = Path(__file__).parent / "static"


class ExportAddon(Addon):
    name = "export"
    label = "Export"
    js = (STATIC / "export.js").read_text(encoding="utf-8")
    css = (STATIC / "export.css").read_text(encoding="utf-8")

    def client_options(self) -> Dict[str, Any]:
        return {"formats": formats_for_client()}

    @staticmethod
    def target(doc, payload: Dict[str, Any]):
        """The node the panel asks about: the range ``children`` of the node
        at ``path``, the node at ``path``, or the whole expression."""
        path = payload.get("path") or "/"
        p = parse_path(path) if isinstance(path, str) else tuple(path)
        children = payload.get("children")
        if isinstance(children, list) and children:
            return extract_range(doc.expr, p, [int(i) for i in children], doc.printer_settings)
        return get_at(doc.expr, p, doc.printer_settings) if p else doc.expr

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "export":
            try:
                node = self.target(doc, payload)
            except Exception as exc:
                return {"format": payload.get("format"), "files": [], "notes": [],
                        "error": f"Nothing to export at {payload.get('path')}: {exc}"}
            options = payload.get("options")
            return export(node, str(payload.get("format") or "latex"),
                          options if isinstance(options, dict) else None,
                          latex_settings=doc.printer_settings)
        raise ValueError(f"Export has no method {method!r}")


ADDON = ExportAddon()

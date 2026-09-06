"""A sympy-editor add-on to copy.  Everything an add-on can do, once."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from sympy import Basic

from sympy_editor.addons import Addon
from sympy_editor.ops import make_op

__all__ = ["TemplateAddon", "ADDON"]

STATIC = Path(__file__).parent / "static"


class TemplateAddon(Addon):
    #: The name messages carry and the front end registers under; the panel
    #: gets the class ``.se-addon-<name>``.
    name = "template"
    label = "Template"
    #: pip names this add-on needs at run time (micropip installs them in a
    #: Pyodide page).  None here.
    requires = ()

    #: Transformations for the menus.  A function ``expr -> expr``; with
    #: ``kinds=("matrix",)`` it appears in the type menu for matrices only;
    #: with ``context=True`` it is called as ``func(expr, doc=document)``.
    ops = (
        make_op("twice", lambda e: 2 * e, label="Twice", doc="The expression doubled."),
    )

    #: The browser part: a plain script (see static/panel.js) and its styles.
    js = (STATIC / "panel.js").read_text(encoding="utf-8")
    css = (STATIC / "panel.css").read_text(encoding="utf-8")

    def client_options(self) -> Dict[str, Any]:
        """Anything the script needs at start (``api.options``)."""
        return {"greeting": "Hello from Python"}

    def contribute(self, doc, snap: Dict[str, Any], expr: Basic) -> None:
        """Data for the panel, in every snapshot.  Keep it small."""
        snap["template"] = {"args": len(expr.args), "atoms": len(expr.atoms())}

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        """The methods the panel calls with ``api.call(method, payload)``:
        return a dict for a query, a SymPy object to make it the new
        expression, or None after editing through ``doc`` yourself."""
        if method == "count":
            node = doc.get(payload.get("path") or "/")
            return {"src": str(node), "args": len(node.args)}
        if method == "double":
            return 2 * doc.expr
        raise ValueError(f"The template has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]):
        """The history's label for a change made by ``method``."""
        return "Template: doubled" if method == "double" else None


ADDON = TemplateAddon()

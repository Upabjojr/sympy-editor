"""sympy-editor add-on: path integrals of QED turned into Feynman diagrams.

The expression is a path integral, ``PathIntegral(psi(x_1)*psibar(x_2))``:
the correlator of a product of fields.  The **Feynman diagrams** op expands
it to a given order in the coupling the way it is done on paper - the
interaction ``-ie ψ̄ γ^μ ψ A_μ`` brought down n times, Wick's theorem
contracting the fields in every way, each full contraction a diagram, the
ones equal up to a relabelling of the vertices one diagram with its
symmetry factor and the sign of the fermion permutation - and gives the sum
of the diagrams, each a node of the expression printed by the Feynman
rules in position space (propagators, vertices, traces for closed loops).
The panel draws them: fermion lines with arrows, wavy photon lines, the
vertices integrated over; a click on a drawing selects its term.

The expansion and the nodes are in :mod:`sympy_editor_feynman.qed`; this
module is the contract with the editor.  This add-on is *not* bundled with
the apps: it is the example of one installed from the Add-ons menu - from
a .zip of this folder, or from the repository's URL.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from sympy import Basic, preorder_traversal

from sympy_editor.addons import Addon
from sympy_editor.ops import make_op

from .qed import (EXAMPLES, MAX_ORDER, Diagram, PathIntegral, Photon, Psi, PsiBar, amplitude, diagram_from_json,
                  diagram_json, diagrams)

__all__ = ["FeynmanAddon", "ADDON", "PathIntegral", "Diagram", "Psi", "PsiBar", "Photon", "diagrams", "amplitude", "diagram_from_json"]

STATIC = Path(__file__).parent / "static"
#: How many diagrams a snapshot describes for the panel (each is small, but
#: an order-4 expansion has many).
MAX_DRAWN = 40


def _op_expand(expr, order="2", which="connected"):
    order = int(str(order).strip() or 2)
    which = str(which).strip().lower().replace(" ", "_") or "connected"
    found = diagrams(expr, order, which)
    if not found:
        raise ValueError("No diagram: the fields do not contract at these orders (as many psi as psibar, an even number of photons)")
    from sympy import Add
    return Add(*found, evaluate=False) if len(found) > 1 else found[0]


class FeynmanAddon(Addon):
    name = "feynman"
    label = "Feynman diagrams"
    requires = ()

    kinds = {"pathintegral": (PathIntegral,), "diagram": (Diagram,), "field": (Psi, PsiBar, Photon)}
    kind_labels = {"pathintegral": "Path integral", "diagram": "Feynman diagram", "field": "Field"}

    ops = (
        make_op("feynman_expand", _op_expand, label="Feynman diagrams (expand)", kinds=("pathintegral",),
                params=[("order in e (0 to %d)" % MAX_ORDER, "text", True, "2"),
                        ("which: connected, no_bubbles or all", "text", True, "connected")],
                doc="Expand the path integral to this order in the coupling: the sum of the Feynman diagrams, "
                    "each with its symmetry factor and sign."),
        make_op("feynman_amplitude", amplitude, label="Feynman rules (position space)", kinds=("diagram",),
                doc="The diagram's value as an expression: propagators S_F and D_F, gamma matrices at the vertices, "
                    "a trace per closed loop, the vertices integrated over."),
    )

    js = (STATIC / "feynman.js").read_text(encoding="utf-8")
    css = (STATIC / "feynman.css").read_text(encoding="utf-8")

    def namespace(self) -> Dict[str, Any]:
        return {"psi": Psi, "Psi": Psi, "psibar": PsiBar, "PsiBar": PsiBar, "A": Photon, "Photon": Photon,
                "PathIntegral": PathIntegral, "Diagram": Diagram}

    def client_options(self) -> Dict[str, Any]:
        return {"examples": [{"label": label, "src": src} for label, src in EXAMPLES], "max_order": MAX_ORDER}

    def contribute(self, doc, snap: Dict[str, Any], expr: Basic) -> None:
        """Every diagram of the expression, for the panel to draw."""
        found: List[Dict[str, Any]] = []
        for node in preorder_traversal(expr):
            if isinstance(node, Diagram):
                found.append(diagram_json(node))
                if len(found) >= MAX_DRAWN:
                    break
        snap["feynman"] = {"diagrams": found, "integrals": sum(1 for n in preorder_traversal(expr) if isinstance(n, PathIntegral))}

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "example":
            src = str(payload.get("src") or "")
            if src not in [s for _l, s in EXAMPLES]:
                raise ValueError("Not one of the examples")
            return doc.parse(src)
        if method == "expand":
            node = doc.get(payload.get("path") or "/")
            if not isinstance(node, PathIntegral):
                raise ValueError("Select a path integral to expand")
            doc.replace(payload.get("path") or "/", _op_expand(node, payload.get("order", 2), payload.get("which", "connected")))
            return None
        if method == "edit":
            # the panel's drawing, edited: the term becomes the diagram drawn
            path = payload.get("path") or "/"
            node = doc.get(path)
            if not isinstance(node, Diagram):
                raise ValueError("Not a diagram")
            data = {"nodes": payload.get("nodes", [n for n in diagram_json(node)["nodes"]]),
                    "edges": payload.get("edges", diagram_json(node)["edges"])}
            new = diagram_from_json(data, parse=lambda s: doc.parse(s, doc.expr), factor=payload.get("factor"), template=node)
            doc.replace(path, new)
            return None
        if method == "new_diagram":
            # a bare diagram to draw by hand: the externals of the path
            # integral (which it replaces) or of the diagrams already there
            # (which it joins)
            expr = doc.expr
            if isinstance(expr, PathIntegral):
                return Diagram(expr.fields, (), (), 1, 0)
            found = [n for n in preorder_traversal(expr) if isinstance(n, Diagram)]
            externals = found[0].externals if found else ()
            from sympy import Add
            terms = list(expr.args) if isinstance(expr, Add) else [expr]
            return Add(*terms, Diagram(externals, (), (), 1, 0), evaluate=False)
        if method == "delete_term":
            doc.delete(payload.get("path") or "/")
            return None
        raise ValueError(f"Feynman diagrams has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]):
        if method == "example":
            return "Feynman: example"
        if method == "expand":
            return "Feynman: diagrams to order %s" % payload.get("order", 2)
        if method == "edit":
            return "Feynman: diagram edited" + (" (" + str(payload["what"]) + ")" if payload.get("what") else "")
        if method == "new_diagram":
            return "Feynman: new diagram"
        if method == "delete_term":
            return "Feynman: diagram removed"
        return None


ADDON = FeynmanAddon()

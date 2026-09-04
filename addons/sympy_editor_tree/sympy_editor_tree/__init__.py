"""sympy-editor add-on: the expression tree as an editable graph.

The formula shows what the printer makes of the expression; this panel shows
the expression as SymPy holds it - ``x + y*z`` is ``Add(x, Mul(y, z))`` - as
a tree of boxes under the formula, and lets it be edited there: rename a
leaf, change a head, drag a subtree under another node, add or remove an
argument, wrap a node in a function.

Paths here are *argument paths* (``[1, 0]`` = ``expr.args[1].args[0]``),
not the editor's view paths: the tree is the real one, virtual parts (the
numerator of a fraction) and all.  ``contribute`` puts the tree in every
snapshot (``snap["tree"]``); the methods edit through the same helpers the
editor uses (``get_at``/``replace_at``/``delete_at`` on integer paths), so
that every change rebuilds the ancestors with SymPy's evaluation, as any
edit does, and lands in the undo history like one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import sympy
from sympy import Basic, Function, Tuple, sympify

from sympy import Pow

from sympy_editor.addons import Addon
from sympy_editor.printer import delete_at, get_at, parse_path, rebuild, replace_at

__all__ = ["TreeAddon", "ADDON", "tree_of"]

STATIC = Path(__file__).parent / "static"

#: Heads offered in the panel's menu for a changed inner node (any name
#: typed works too: a SymPy function, or an undefined one).
COMMON_HEADS = ("Add", "Mul", "Pow", "sin", "cos", "exp", "log", "sqrt", "Integral", "Derivative", "Sum", "Eq")


def _label(node: Basic) -> str:
    """What a leaf's box says: its source; an inner node's: its head."""
    if not node.args:
        text = str(node)
        return text if len(text) <= 24 else text[:23] + "…"
    return type(node).__name__


def view_objects(expr: Basic, view_paths, settings=None) -> Dict[str, Basic]:
    """What the formula shows at each of the editor's paths: the objects
    behind ``snapshot()["nodes"]`` (a fraction's ``n``/``d`` parts included)."""
    out: Dict[str, Basic] = {}
    for path in view_paths or ():
        try:
            out[path] = get_at(expr, parse_path(path), settings)
        except Exception:
            pass
    return out


def _view_for(node: Basic, path: List[int], shown: Dict[str, Basic], parent_view: Optional[str]) -> Optional[str]:
    """The editor's path for a node of the argument tree.  Its own path when
    the formula has it; else the piece of the formula, under the nearest
    ancestor's piece, that *is* the node or equals it - the numerator ``n``
    for the ``sin(x)**2`` of ``sin(x)**2/x``; for a negative power, the
    piece it is drawn as: the denominator ``x`` for ``Pow(x, -1)``."""
    own = "/" + "/".join(str(i) for i in path) if path else "/"
    if own in shown:
        return own
    if parent_view is None:
        return None
    under = [p for p in shown if p != parent_view and p.startswith(parent_view.rstrip("/") + "/")]
    under.sort(key=lambda p: (p.count("/"), p))
    # The ancestor's own piece may be this very node: the x of Pow(x, -1)
    # is what the denominator shows.
    under.insert(0, parent_view)
    for p in under:
        if shown[p] is node:
            return p
    wanted = [node]
    if isinstance(node, Pow) and node.exp.is_negative:
        wanted.append(Pow(node.base, -node.exp))
    for target in wanted:
        for p in under:
            obj = shown[p]
            if type(obj) is type(target) and obj == target:
                return p
    return None


def tree_of(expr: Basic, max_nodes: int = 400, view_paths=None, shown: Optional[Dict[str, Basic]] = None) -> Dict[str, Any]:
    """The argument tree of ``expr`` as JSON: ``{"head", "label", "atom",
    "path": [ints], "view": the editor's path for the same node or None,
    "children": [...]}``; ``{"too_big": count}`` beyond ``max_nodes``.
    ``shown`` (from :func:`view_objects`) maps the formula's pieces to
    nodes whose argument path is not a formula path - the factors of a
    fraction."""
    count = 0
    shown = shown if shown is not None else ({p: None for p in view_paths} if view_paths else {})

    def walk(node: Basic, path: List[int], parent_view: Optional[str]) -> Optional[Dict[str, Any]]:
        nonlocal count
        count += 1
        if count > max_nodes:
            return None
        if view_paths is None and not shown:
            view: Optional[str] = "/" + "/".join(str(i) for i in path) if path else "/"
        else:
            view = _view_for(node, path, shown, parent_view)
        out: Dict[str, Any] = {
            "head": type(node).__name__, "label": _label(node), "atom": not node.args,
            "path": list(path), "src": str(node), "view": view, "children": [],
        }
        for i, arg in enumerate(node.args):
            child = walk(arg, path + [i], view if view is not None else parent_view)
            if child is None:
                return None
            out["children"].append(child)
        return out

    root = walk(expr, [], None)
    if root is None:
        return {"too_big": count, "max": max_nodes}
    return root


def _ipath(value) -> tuple:
    """A payload path (a list of ints, or "0/1") as a tuple of ints."""
    if isinstance(value, str):
        value = [p for p in value.strip("/").split("/") if p]
    try:
        return tuple(int(i) for i in (value or ()))
    except (TypeError, ValueError):
        raise ValueError(f"Not an argument path: {value!r}") from None


def _head(name: str, namespace: Dict[str, Any]):
    """The constructor a head name stands for: a name in the document's
    namespace, a public name of sympy, else an undefined function."""
    name = (name or "").strip()
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(f"Not a head name: {name!r}")
    obj = namespace.get(name, getattr(sympy, name, None))
    if obj is None:
        obj = Function(name)
    if not callable(obj):
        raise ValueError(f"{name} is not something that takes arguments")
    return obj


def _with_args(node: Basic, args: Sequence[Basic]) -> Basic:
    return rebuild(node, list(args))


class TreeAddon(Addon):
    name = "tree"
    label = "Expression tree"
    js = (STATIC / "tree.js").read_text(encoding="utf-8")
    css = (STATIC / "tree.css").read_text(encoding="utf-8")
    #: Beyond this many nodes the panel shows a note instead of the graph.
    max_nodes = 400

    def client_options(self) -> Dict[str, Any]:
        return {"heads": list(COMMON_HEADS), "maxNodes": self.max_nodes}

    def contribute(self, doc, snap: Dict[str, Any], expr: Basic) -> None:
        paths = set(snap.get("nodes") or ())
        snap["tree"] = tree_of(expr, self.max_nodes, view_paths=paths,
                               shown=view_objects(expr, paths, doc.printer_settings))

    def describe(self, method: str, payload: Dict[str, Any]) -> Optional[str]:
        def at(key="path"):
            p = payload.get(key)
            return "/" + "/".join(str(i) for i in (p or [])) if isinstance(p, list) else str(p or "/")
        if method == "set_head":
            return f"Tree: {at()} becomes {payload.get('head')}"
        if method == "replace":
            return f"Tree: {at()} → {payload.get('src')}"
        if method == "move":
            return f"Tree: move {at('from')} under {at('to')}"
        if method == "insert":
            return f"Tree: add {payload.get('src')} to {at()}"
        if method == "wrap":
            return f"Tree: wrap {at()} in {payload.get('head')}"
        if method == "delete":
            return f"Tree: delete {at()}"
        return f"Tree: {method}"

    # -- methods --------------------------------------------------------------

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        expr = doc.expr
        if method == "tree":                                  # a query: the tree alone
            return {"tree": tree_of(expr, self.max_nodes)}
        if method == "set_head":
            path = _ipath(payload.get("path"))
            node = get_at(expr, path)
            if not node.args:
                raise ValueError("A leaf has no head to change: edit its value instead")
            head = _head(str(payload.get("head", "")), doc.namespace())
            return replace_at(expr, path, sympify(head(*node.args)))
        if method == "replace":
            path = _ipath(payload.get("path"))
            new = doc.parse(str(payload.get("src", "")), context=get_at(expr, path))
            return replace_at(expr, path, new)
        if method == "delete":
            path = _ipath(payload.get("path"))
            if not path:
                raise ValueError("The root cannot be deleted: type a new expression instead")
            return delete_at(expr, path)
        if method == "insert":
            path = _ipath(payload.get("path"))
            node = get_at(expr, path)
            new = doc.parse(str(payload.get("src", "")), context=node)
            args = list(node.args)
            index = payload.get("index")
            index = len(args) if index is None else max(0, min(int(index), len(args)))
            if not args and not isinstance(node, (Tuple,)):
                # A leaf takes no argument: the new value is joined to it
                # instead, as typing beside it in the formula would.
                return replace_at(expr, path, node * new)
            args.insert(index, new)
            return replace_at(expr, path, _with_args(node, args))
        if method == "wrap":
            path = _ipath(payload.get("path"))
            head = _head(str(payload.get("head", "")), doc.namespace())
            node = get_at(expr, path)
            return replace_at(expr, path, sympify(head(node)))
        if method == "move":
            return self._move(expr, _ipath(payload.get("from")), _ipath(payload.get("to")), payload.get("index"))
        raise ValueError(f"The tree has no method {method!r}")

    @staticmethod
    def _move(expr: Basic, src: tuple, dst: tuple, index) -> Basic:
        """The subtree at ``src`` taken out and put among the arguments of
        the node at ``dst`` (at ``index``, the end by default)."""
        if not src:
            raise ValueError("The root cannot be moved")
        if dst[:len(src)] == src:
            raise ValueError("A node cannot be moved into itself")
        subtree = get_at(expr, src)
        target = get_at(expr, dst)
        if not target.args:
            raise ValueError(f"{target} is a leaf and takes no argument; drop onto an inner node")
        # Take it out first.  Removing an argument shifts the indices after
        # it in the same parent, so the destination path is corrected when
        # it passes through that parent at a later index.
        without = delete_at(expr, src)
        parent, i = src[:-1], src[-1]
        dst2 = list(dst)
        if dst[:len(parent)] == parent and len(dst) > len(parent) and dst[len(parent)] > i:
            dst2[len(parent)] -= 1
        try:
            target2 = get_at(without, tuple(dst2))
        except ValueError:
            # The parent collapsed (a Mul of two became one factor): the
            # destination is gone with it - put the subtree at its parent.
            target2 = get_at(without, tuple(dst2[:-1]))
            dst2 = dst2[:-1]
        args = list(target2.args)
        if not args:
            return replace_at(without, tuple(dst2), target2 * subtree)
        at = len(args) if index is None else max(0, min(int(index), len(args)))
        args.insert(at, subtree)
        return replace_at(without, tuple(dst2), _with_args(target2, args))


ADDON = TreeAddon()

"""sympy-editor add-on "manim": the history's steps as an animation.

The panel writes a `Manim <https://www.manim.community/>`_ scene for the
steps of the session - each formula turning into the next, the
sub-expressions a change keeps travelling to their new place, the editor's
label of each step as a caption - and, where Manim is installed beside the
editor's Python, renders it to a video or an animated GIF.  The script
needs nothing of the editor: it is a plain Manim file to keep, edit and
render anywhere Manim runs.  See :mod:`sympy_editor_manim.scene` for how the
scene is built and :mod:`sympy_editor_manim.render` for running Manim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sympy import Basic

from sympy_editor.addons import Addon
from sympy_editor.document import render_step

from . import render as _render
from .scene import DEFAULTS, FILE_NAME, SCENE_NAME, STYLES, THEMES, Step, script

__all__ = ["ManimAddon", "ADDON", "script", "FILE_NAME", "SCENE_NAME"]

STATIC = Path(__file__).parent / "static"


def _history(doc) -> Tuple[List[Basic], List[Optional[str]], int]:
    """The document's steps, their labels and the current index, read from
    its own lists (``history_labels()`` would print every step)."""
    steps = list(getattr(doc, "_history", []) or [])
    labels = list(getattr(doc, "_labels", []) or [])
    labels = [None] * (len(steps) - len(labels)) + labels[-len(steps):] if steps else []
    index = int(getattr(doc, "_index", len(steps) - 1))
    return steps, labels, index


def _span(n: int, payload: Dict[str, Any]) -> Tuple[int, int]:
    """The steps asked for, ``payload["from"]``..``payload["to"]`` (0-based,
    both included; the whole history by default), checked."""
    def index(key: str, default: int) -> int:
        value = payload.get(key)
        if value is None or value == "":
            return default
        try:
            k = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a step number, not {value!r}") from None
        if not 0 <= k < n:
            raise ValueError(f"Step {k + 1} does not exist: the history has {n} step{'s' if n != 1 else ''}")
        return k
    first, last = index("from", 0), index("to", n - 1)
    if last < first:
        raise ValueError("The last step comes before the first")
    return first, last


class ManimAddon(Addon):
    name = "manim"
    label = "Manim"
    requires = ()

    js = (STATIC / "manim.js").read_text(encoding="utf-8")
    css = (STATIC / "manim.css").read_text(encoding="utf-8")

    def client_options(self) -> Dict[str, Any]:
        return {"styles": list(STYLES), "themes": list(THEMES), "defaults": dict(DEFAULTS),
                "qualities": dict(_render.QUALITIES), "formats": list(_render.FORMATS), "file": FILE_NAME}

    def steps(self, doc, payload: Dict[str, Any]) -> List[Step]:
        """The steps the scene shows, rendered for Manim."""
        history, labels, _ = _history(doc)
        if not history:
            raise ValueError("The history is empty")
        first, last = _span(len(history), payload)
        settings = getattr(doc, "printer_settings", None)
        out: List[Step] = []
        for i in range(first, last + 1):
            try:
                step = render_step(history[i], settings)
            except Exception as exc:
                step = {"latex": r"\text{(cannot be shown: %s)}" % type(exc).__name__, "nodes": {}}
            caption = labels[i] if i > first else (labels[i] or "Start")
            out.append((step["latex"], step["nodes"], caption))
        return out

    def script(self, doc, payload: Dict[str, Any]) -> Dict[str, Any]:
        """The scene as a Python file: ``{"code", "name", "steps", "from", "to"}``."""
        history, _, _ = _history(doc)
        steps = self.steps(doc, payload)
        first, last = _span(len(history), payload)
        return {"code": script(steps, payload), "name": FILE_NAME, "scene": SCENE_NAME, "steps": len(steps),
                "from": first, "to": last}

    def contribute(self, doc, snap: Dict[str, Any], expr: Basic) -> None:
        steps, _, index = _history(doc)
        snap["manim"] = {"n": len(steps), "index": index}

    def handle(self, doc, method: str, payload: Dict[str, Any]):
        if method == "status":
            return _render.status()
        if method == "script":
            return self.script(doc, payload)
        if method == "render":
            res = self.script(doc, payload)
            out = _render.render(res["code"], quality=str(payload.get("quality") or "l"),
                                 fmt=str(payload.get("format") or "mp4"),
                                 timeout=float(payload.get("timeout") or 600.0))
            out.update({"steps": res["steps"], "from": res["from"], "to": res["to"]})
            return out
        raise ValueError(f"Manim has no method {method!r}")


ADDON = ManimAddon()

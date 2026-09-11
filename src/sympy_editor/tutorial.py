"""Tutorials: an editor page that plays a script of timed steps - captions,
an arrow and a pulsing ring on what is about to be pressed, the press itself -
to be watched, or recorded, as a video.

Nothing in the editor's interface starts one, and no ordinary page carries
any of it (``to_html``, ``save_html``, the server, the widget, the apps).  A
page plays a script only when it is built for it::

    from sympy_editor.tutorial import save_tutorial_html
    save_tutorial_html("tour.json", "tour.html")

or from the command line, ``python -m sympy_editor.tutorial tour.json -o
tour.html``; or, on a page that includes ``static/tutorial.js``, from
JavaScript: ``SympyEditorTutorial.run(editor, script)``.

The script
----------
A JSON object::

    {"title": "A tour", "expression": "x**2/y - sin(x)",
     "addons": ["plot", "tree"],        # there to be switched on by a step
     "options": {...},                  # front-end options, see editor.js DEFAULTS
     "speed": 1, "loop": false, "loopDelay": 3,
     "steps": [...]}

**When** a step happens: ``"at"`` - seconds from the start (the start is when
Python is ready) - or ``"after"`` - seconds after the previous step ended; with
neither, one second after it.  A step never starts before its time, nor while
Python is still working on the previous one (the editor would drop it), so a
slow computation delays what follows rather than losing it.

**What** it does - exactly one of:

``"caption": "text"``
    a text box describing what is going on (``"size": "large"`` for a title):
    plain text, where a line break starts a new line and what is between
    backticks is code, kept on one line (```pip install sympy-editor```);
    ``"duration"``: seconds before it goes (default: until the next
    caption); ``null`` takes it away.
``"point": target``
    the arrow and the ring on something, nothing pressed; ``"hold"`` seconds.
``"click": target``
    the arrow and the ring, then the press: a button, a checkbox, a row of a
    menu - or, with ``{"path": ...}``, a piece of the formula, selected.
    ``"lead"``: seconds of arrow and ring first (default 1.2).
``"choose": {"target": ..., "value": "x"}``
    an option of a drop-down list (a ``<select>``), by value or by text.
``"type": {"target": ..., "text": "...", "enter": true}``
    text typed into a field one character at a time (``"perChar"`` seconds),
    replacing what it held (``"replace": false`` appends); ``"enter"`` applies
    it.  ``"target": "focused"`` types where the focus is.
``"key": "ArrowUp"``
    a key pressed in the editor - or ``{"key": "z", "ctrl": true}``, or a list.
``"set": "source"``
    the expression, from SymPy source.
``"apply": "expand"`` or ``{"op": "expand", "path": "/1"}``
    one of the editor's operations, on the selection by default.
``"undo": true`` / ``"redo": true``
``"zoom": 1.5``
    the formula's zoom (1 is the normal size).
``"addons": {"enable": [...], "disable": [...]}``
``"wait": true``
    nothing: the timing is the point.

A **target** is ``{"path": "/1/d"}`` (a piece of the formula), ``{"selector":
"css", "text": "..."}`` (the first visible element matching it, holding that
text) or a CSS selector string.  Any step may also ``"say"`` something - a
caption shown as it starts (``"sayFor"`` seconds).

**Where** a caption goes, ``"position"``: ``"near"`` - beside what the step
is about, above it (and above the arrow) or below when there is no room; the
default for a step with a target - ``"above"``, ``"below"``, or ``"top"``,
``"center"`` (the default otherwise), ``"bottom"`` of the editor on the
screen.  ``"near": target`` puts a caption step beside something too.

**The end**: once the last caption has had its time, everything of the
player goes - overlay, arrow, captions - the drawer and the menus shut, and
the page is the editor as a reader finds it.

**Parts**: ``"part": "history"`` on some steps names them as a group a page
may leave out - ``to_tutorial_html(..., skip=["history"])`` - the steps after
them following on as if they had not been there.

**Stopping**: the page has a button to stop the tour (``stop_button=False``
leaves it out, for a recording); stopped, the overlay goes and the editor
is left usable, what was done kept.  With ``stop_on_leave=True`` a link
followed, or the page scrolled on past the editor, stops it too; and
``play_button`` names a button of the page's own (class ``se-tour-play``)
that plays it again, from the start, on a fresh editor.

**Embedding**: ``to_tutorial_html(..., full_page=False)`` is a fragment for
a page of one's own.  Every editor on a page runs on one Python (Pyodide)
runtime; a tutorial's add-ons are installed into it whichever editor
started it.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .html import _as_document, _script_json, build_config, read_static, render_fragment, render_page

__all__ = ["ACTIONS", "load_tutorial", "to_tutorial_html", "save_tutorial_html", "without_parts",
           "player_css", "player_html"]

#: What a step can do; each step does exactly one of these.
ACTIONS = ("caption", "point", "click", "choose", "type", "key", "set", "apply", "undo", "redo", "zoom", "addons", "wait")
_TIMING = ("at", "after")
_EXTRA = ("say", "sayFor", "position", "size", "near", "duration", "hold", "lead", "part")
POSITIONS = ("near", "above", "below", "top", "center", "bottom")
_SCRIPT_KEYS = ("title", "expression", "addons", "options", "speed", "loop", "loopDelay", "steps", "description")
#: The start of the id of a tutorial's editor element (each gets its own).
ELEMENT_PREFIX = "sympy-editor-tutorial-"


def _target_ok(target) -> bool:
    if isinstance(target, str):
        return bool(target.strip())
    return isinstance(target, dict) and (isinstance(target.get("path"), str) or isinstance(target.get("selector"), str))


def _check_step(i: int, step: Any) -> None:
    where = f"step {i}"
    if not isinstance(step, dict):
        raise ValueError(f"{where}: a step is an object, not {type(step).__name__}")
    doing = [a for a in ACTIONS if a in step]
    if len(doing) != 1:
        raise ValueError(f"{where}: says what it does with exactly one of {', '.join(ACTIONS)} "
                         f"(it has {', '.join(doing) or 'none'})")
    unknown = sorted(set(step) - set(ACTIONS) - set(_TIMING) - set(_EXTRA))
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {', '.join(unknown)}")
    if "at" in step and "after" in step:
        raise ValueError(f"{where}: says when with 'at' or 'after', not both")
    for k in _TIMING + ("sayFor", "duration", "hold", "lead"):
        if k in step and (isinstance(step[k], bool) or not isinstance(step[k], (int, float)) or step[k] < 0):
            raise ValueError(f"{where}: {k!r} is a number of seconds, not {step[k]!r}")
    if "position" in step and step["position"] not in POSITIONS:
        raise ValueError(f"{where}: position is one of {', '.join(POSITIONS)}, not {step['position']!r}")
    if "size" in step and step["size"] != "large":
        raise ValueError(f"{where}: size is \"large\" or left out")
    if "part" in step and (not isinstance(step["part"], str) or not step["part"].strip()):
        raise ValueError(f"{where}: part is the name of a group of steps")
    if "near" in step and not _target_ok(step["near"]):
        raise ValueError(f"{where}: near needs a target")
    action = doing[0]
    value = step[action]
    if action in ("point", "click") and not _target_ok(value):
        raise ValueError(f"{where}: {action} needs a target - a CSS selector, {{'selector': ...}} or {{'path': ...}}")
    if action == "choose":
        if not isinstance(value, dict) or not isinstance(value.get("value"), str) or not _target_ok(value.get("target", value.get("selector"))):
            raise ValueError(f"{where}: choose needs {{'target': ..., 'value': '...'}}")
    if action == "type":
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            raise ValueError(f"{where}: type needs {{'target': ..., 'text': '...'}}")
        target = value.get("target", value.get("selector", "focused"))
        if target != "focused" and not _target_ok(target):
            raise ValueError(f"{where}: type has no target it could find")
    if action == "caption" and value is not None and not isinstance(value, str):
        raise ValueError(f"{where}: a caption is text (or null to take it away)")
    if action == "key" and not (isinstance(value, (str, dict)) or
                                (isinstance(value, list) and value and all(isinstance(k, (str, dict)) for k in value))):
        raise ValueError(f"{where}: key is a key name, {{'key': ...}} or a list of them")
    if action == "set" and not isinstance(value, str):
        raise ValueError(f"{where}: set takes SymPy source, a string")
    if action == "apply" and not (isinstance(value, str) or (isinstance(value, dict) and isinstance(value.get("op"), str))):
        raise ValueError(f"{where}: apply takes an operation's name, or {{'op': ..., 'path': ...}}")
    if action == "zoom" and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0):
        raise ValueError(f"{where}: zoom is a positive number")
    if action == "addons" and not (isinstance(value, dict) and set(value) <= {"enable", "disable"}):
        raise ValueError(f"{where}: addons takes {{'enable': [...], 'disable': [...]}}")


def load_tutorial(script: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    """The script - a dict, JSON text, or the path of a JSON file - checked
    (a ``ValueError`` names the step at fault) and copied."""
    if isinstance(script, Path) or (isinstance(script, str) and not script.lstrip().startswith("{")):
        script = json.loads(Path(script).read_text(encoding="utf-8"))
    elif isinstance(script, str):
        script = json.loads(script)
    if not isinstance(script, dict):
        raise ValueError("a tutorial script is a JSON object")
    unknown = sorted(set(script) - set(_SCRIPT_KEYS))
    if unknown:
        raise ValueError(f"the script has unknown key(s) {', '.join(unknown)}")
    steps = script.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("the script needs 'steps': a list of at least one")
    for i, step in enumerate(steps):
        _check_step(i, step)
    for k in ("speed", "loopDelay"):
        if k in script and (isinstance(script[k], bool) or not isinstance(script[k], (int, float)) or script[k] <= 0):
            raise ValueError(f"{k!r} is a positive number")
    return copy.deepcopy(script)


def without_parts(script, skip) -> Dict[str, Any]:
    """``script`` without the steps of the parts in ``skip`` (a copy)."""
    script = copy.deepcopy(script)
    if skip:
        leave = set(skip)
        script["steps"] = [s for s in script["steps"] if s.get("part") not in leave]
        if not script["steps"]:
            raise ValueError(f"leaving out {', '.join(sorted(leave))} leaves no step")
    return script


def player_css() -> str:
    """The overlay's style, as a ``<style>`` element."""
    return f"<style>\n{read_static('tutorial.css')}\n</style>\n"


def player_html(element_id: str, script, *, full_page: bool = False, stop_button: bool = True,
                stop_on_leave: bool = False, play_button: Optional[str] = None) -> str:
    """The player, and the call that plays ``script`` on the editor mounted in
    ``element_id`` - for after that editor's mount.  The player's script is
    guarded, so several on a page share one copy.  ``stop_on_leave``: a link
    followed, or the page scrolled past the editor, stops the tour too.
    ``play_button``: the id of a button on the page, before this, that plays
    it again from the start on a fresh editor."""
    opts = {"fullPage": full_page, "stopButton": stop_button}
    if stop_on_leave:
        opts["stopOnLeave"] = True
    if play_button:
        opts["playButton"] = play_button
    return ("<script>\nif (!window.SympyEditorTutorial) {\n" + read_static("tutorial.js") + "\n}\n</script>\n"
            "<script>\n"
            f'SympyEditorTutorial.run(document.getElementById("{element_id}"), {_script_json(script)}, '
            f"{json.dumps(opts)});\n"
            "</script>\n")


def to_tutorial_html(script, *, expr=None, title: Optional[str] = None, backend: str = "pyodide",
                     options: Optional[Dict[str, Any]] = None, urls: Optional[Dict[str, str]] = None,
                     full_page: bool = True, element_id: Optional[str] = None, logo: str = "",
                     stop_button: bool = True, stop_on_leave: bool = False,
                     play_button: Optional[str] = None, skip=(), **config_kwargs) -> str:
    """A page - or, with ``full_page=False``, a fragment to embed - with the
    editor that plays ``script`` as soon as it is ready.

    ``expr`` (an expression, source, or :class:`Document`) overrides the
    script's ``"expression"``; ``options`` are merged over its ``"options"``.
    It is the ordinary editor page (fragment) with the player added after it:
    the editor is the one every other page has.  Fragments on one page share
    one copy of the scripts and one Python runtime.  ``logo``: SVG markup
    beside a full page's title.  ``stop_button``: a button to stop the tour;
    ``stop_on_leave``, ``play_button``: see :func:`player_html` (the button
    is the page's, around the fragment).
    ``skip``: the parts (``"part"`` of the steps) to leave out.  ``backend``
    is ``"pyodide"`` (a standalone file) by default; ``config_kwargs`` go to
    ``build_config`` (``api_url``/``token`` for ``"http"``)."""
    script = without_parts(load_tutorial(script), skip)
    source = expr if expr is not None else script.get("expression", "x")
    doc = _as_document(source, available=list(script.get("addons") or [])) if not hasattr(source, "handle") else source
    opts = dict(script.get("options") or {})
    opts.update(options or {})
    config = build_config(doc, backend=backend, options=opts, urls=urls, **config_kwargs)
    element_id = element_id or ELEMENT_PREFIX + uuid.uuid4().hex[:10]
    player = player_html(element_id, script, full_page=full_page, stop_button=stop_button,
                         stop_on_leave=stop_on_leave, play_button=play_button)
    if not full_page:
        return player_css() + render_fragment(config, element_id) + player
    page = render_page(config, title or script.get("title") or "SymPy Editor tutorial", player_css(), element_id, logo)
    body, end = page.rsplit("</body>", 1)
    return body + player + "</body>" + end


def save_tutorial_html(script, path, **kwargs) -> Path:
    """Write :func:`to_tutorial_html` output to ``path`` and return it."""
    path = Path(path)
    path.write_text(to_tutorial_html(script, **kwargs), encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build a page that plays a tutorial script on the editor.")
    ap.add_argument("script", help="the tutorial script (JSON)")
    ap.add_argument("-o", "--out", help="the page to write (default: the script's name, .html)")
    ap.add_argument("--addons", help="a folder of add-on folders to register first (e.g. addons/)")
    ap.add_argument("--skip", action="append", default=[], help="a part of the script to leave out (repeatable)")
    ap.add_argument("--no-stop-button", action="store_true", help="no button to stop it (for a recording)")
    args = ap.parse_args(argv)
    if args.addons:
        from .addons import register_addons_folder
        register_addons_folder(args.addons)
    out = Path(args.out) if args.out else Path(args.script).with_suffix(".html")
    try:
        save_tutorial_html(Path(args.script), out, skip=args.skip, stop_button=not args.no_stop_button)
    except ValueError as exc:
        print(f"{args.script}: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

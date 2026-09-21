"""Where a Python behind the page keeps what the page keeps: the sessions,
each with the history behind it, which add-ons are on, the zoom, what an
add-on keeps of its own.

The page asks through its ``keep`` message (``Keep`` in editor.js); the HTTP
server (:mod:`sympy_editor.server`) and the Jupyter widget
(:mod:`sympy_editor.widget`) answer it from a :class:`Store`, so the same
sessions are there whichever of the two opens the editor, and whichever
browser shows it.  Standard library only.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional, Union

__all__ = ["Store", "default_store", "unused_path"]

#: Names Windows keeps for devices, whatever the extension: ``con.json`` is
#: not a file there.  A key that is one of them is written with a mark.
_WINDOWS_DEVICES = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"{name}{digit}" for name in ("com", "lpt") for digit in "123456789"]
)


def default_store() -> Path:
    """Where what the page keeps goes, when nobody says: the place each
    platform keeps such things.

    * Windows: ``%LOCALAPPDATA%\\sympy-editor`` (``~\\AppData\\Local`` when
      the variable is not set).
    * macOS: ``~/Library/Application Support/sympy-editor``.
    * Elsewhere: ``$XDG_STATE_HOME/sympy-editor``, or ``~/.local/state`` as
      the XDG specification says when the variable is not set.

    ``XDG_STATE_HOME`` is honoured wherever it is set, for whoever has laid
    their home out that way.
    """
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "sympy-editor"
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) if local else Path.home() / "AppData" / "Local") / "sympy-editor"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "sympy-editor"
    return Path.home() / ".local" / "state" / "sympy-editor"


def _safe_name(name: str, fallback: str) -> str:
    """``name`` with nothing a file system refuses, nor a Windows device name."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip(".") or fallback
    if safe.split(".")[0].lower() in _WINDOWS_DEVICES:
        safe = "_" + safe
    return safe


class Store:
    """One file per name in ``folder``.  ``folder=False`` keeps nothing, and
    the page falls back to the browser's own storage; ``None`` is
    :func:`default_store`."""

    def __init__(self, folder: Optional[Union[str, Path, bool]] = None):
        self.folder: Optional[Path] = None if folder is False else Path(folder or default_store())

    def file(self, key: str) -> Path:
        """The file ``key`` is kept in.  A name from the page cannot reach out
        of the store: everything but letters, digits and ``._-`` is replaced,
        and a name Windows keeps for a device is marked so that it is a file
        there too."""
        assert self.folder is not None
        return self.folder / f"{_safe_name(key, 'keep')}.json"

    def kept(self, key: str) -> Optional[str]:
        """What the page kept under ``key``, or ``None``."""
        if self.folder is None:
            return None
        try:
            return self.file(key).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def keep(self, key: str, value: str) -> None:
        """Keep ``value`` under ``key``, through a temporary file and a
        rename, so that an interrupted write leaves what was there before."""
        if self.folder is None:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.file(key)
        temp = path.with_suffix(path.suffix + ".new")
        temp.write_text(value, encoding="utf-8")
        temp.replace(path)

    def answer(self, message: dict) -> dict:
        """The answer to a ``keep`` message: ``{"keep": text or None}``, or
        ``{"error": ...}`` when the folder cannot be used - which the page
        takes as "this one keeps nothing" and falls back."""
        key = str(message.get("key") or "")
        try:
            if "value" in message:
                self.keep(key, str(message.get("value") or ""))
                return {"keep": None}
            return {"keep": self.kept(key)}
        except OSError as exc:
            return {"error": f"The store could not be used: {exc}"}


def unused_path(folder: Union[str, Path], name: str) -> Path:
    """A path in ``folder`` for a file the user asked to be called ``name``,
    never one that is there already: ``formula.sympy``, then
    ``formula-2.sympy``...  Saving from a notebook must not overwrite a file
    the user did not point at."""
    folder = Path(folder)
    safe = _safe_name(Path(name).name, "file")
    stem, dot, ext = safe.partition(".")
    candidate = folder / safe
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{n}{dot}{ext}"
        n += 1
    return candidate

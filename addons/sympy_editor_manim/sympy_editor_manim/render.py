"""Running Manim: where it is, and a script rendered to a video.

Manim is not a dependency of the add-on - the script is written without it -
but where it is installed the panel renders the script to a video in the
editor's Python.  The command is found in this order: ``SYMPY_EDITOR_MANIM``
(the path of a ``manim`` executable, for a Manim in a virtualenv of its
own), the ``manim`` module of this interpreter (run as ``python -m manim``),
a ``manim`` on the PATH.  :func:`status` says which, and whether LaTeX and
ffmpeg - what Manim itself needs - are there.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .scene import FILE_NAME, SCENE_NAME

__all__ = ["command", "status", "render", "QUALITIES", "FORMATS", "ENV", "INSTALL_HINT"]

#: The executable to use, when set.
ENV = "SYMPY_EDITOR_MANIM"
#: ``manim -q``: what each quality is.
QUALITIES = {"l": "480p, 15 fps", "m": "720p, 30 fps", "h": "1080p, 60 fps", "p": "1440p, 60 fps", "k": "2160p, 60 fps"}
FORMATS = {"mp4": "video/mp4", "gif": "image/gif", "webm": "video/webm", "mov": "video/quicktime"}
INSTALL_HINT = ("pip install manim, with a LaTeX installation and ffmpeg beside it (https://docs.manim.community/); "
                f"or point {ENV} at a manim executable")

_versions: Dict[str, Optional[str]] = {}


def command() -> Optional[List[str]]:
    """The command that runs Manim, or None when there is none."""
    exe = os.environ.get(ENV, "").strip()
    if exe:
        return [exe]
    try:
        if importlib.util.find_spec("manim") is not None:
            return [sys.executable, "-m", "manim"]
    except (ImportError, ValueError):
        pass
    found = shutil.which("manim")
    return [found] if found else None


def version(cmd: List[str]) -> Optional[str]:
    """``manim --version``'s number (cached per command), or None when the
    command does not run."""
    key = " ".join(cmd)
    if key in _versions:
        return _versions[key]
    out: Optional[str] = None
    if cmd[:2] == [sys.executable, "-m"]:
        try:
            from importlib.metadata import version as dist_version
            out = dist_version("manim")
        except Exception:
            out = None
    if out is None:
        try:
            res = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=120)
            m = re.search(r"v?(\d+\.\d+(?:\.\d+)?)", res.stdout + res.stderr)
            out = m.group(1) if m and res.returncode == 0 else None
        except Exception:
            out = None
    _versions[key] = out
    return out


def status() -> Dict[str, Any]:
    """Whether a video can be rendered here: ``{"available", "command",
    "version", "latex", "ffmpeg", "hint"}``."""
    cmd = command()
    ver = version(cmd) if cmd else None
    latex = bool(shutil.which("latex") or shutil.which("pdflatex") or shutil.which("xelatex"))
    ffmpeg = bool(shutil.which("ffmpeg"))
    missing = []
    if not cmd:
        missing.append("Manim is not installed")
    elif ver is None:
        missing.append(f"{' '.join(cmd)} does not run")
    if not latex:
        missing.append("no LaTeX (latex, pdflatex or xelatex) on the PATH")
    hint = "; ".join(missing) + (". " + INSTALL_HINT if missing else "")
    return {"available": bool(cmd) and ver is not None and latex, "command": " ".join(cmd) if cmd else None,
            "version": ver, "latex": latex, "ffmpeg": ffmpeg, "hint": hint or None}


def _output(media: Path, fmt: str) -> Optional[Path]:
    """The rendered file under Manim's media directory: the newest of its
    format, partial movie files aside."""
    files = [p for p in media.rglob("*." + fmt) if "partial_movie_files" not in p.parts and p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def render(code: str, quality: str = "l", fmt: str = "mp4", timeout: float = 600.0,
           poll: float = 0.2) -> Dict[str, Any]:
    """Render ``code`` (a script holding :data:`SCENE_NAME`) with Manim and
    return the file: ``{"name", "mime", "data" (base64), "size", "seconds",
    "command", "log"}``.  Refuses, in words, when Manim is missing, when it
    fails (the end of its log) and after ``timeout`` seconds.  Manim runs as
    a child process polled every ``poll`` seconds, so an exception raised
    into this thread - the editor's Interrupt - stops it."""
    if quality not in QUALITIES:
        raise ValueError(f"Unknown quality {quality!r}: one of {', '.join(QUALITIES)}")
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format {fmt!r}: one of {', '.join(FORMATS)}")
    cmd = command()
    if cmd is None:
        raise ValueError("Manim is not installed: " + INSTALL_HINT)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="sympy-editor-manim-") as tmp:
        work = Path(tmp)
        script = work / FILE_NAME
        script.write_text(code, encoding="utf-8")
        media = work / "media"
        name = "formula_steps"
        full = cmd + ["-q", quality, "--format", fmt, "--media_dir", str(media), "-o", name, "--disable_caching",
                      "--progress_bar", "none", "-v", "WARNING", str(script), SCENE_NAME]
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            proc = subprocess.Popen(full, cwd=str(work), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, env=env)
        except OSError as exc:
            raise ValueError(f"Manim could not be started ({' '.join(cmd)}): {exc}") from None
        log = ""
        try:
            try:
                log, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                log, _ = proc.communicate()
                raise ValueError(f"Manim did not finish in {timeout:.0f} seconds; a lower quality or fewer steps may") from None
        except BaseException:
            if proc.poll() is None:          # the editor's Interrupt, or anything else: no orphan
                proc.kill()
                proc.wait()
            raise
        if proc.returncode != 0:
            tail = "\n".join((log or "").strip().splitlines()[-25:])
            raise ValueError(f"Manim failed (exit code {proc.returncode}):\n{tail}")
        out = _output(media, fmt)
        if out is None:
            tail = "\n".join((log or "").strip().splitlines()[-25:])
            raise ValueError("Manim finished but wrote no ." + fmt + " file:\n" + tail)
        data = out.read_bytes()
    return {"name": name + "." + fmt, "mime": FORMATS[fmt], "data": base64.b64encode(data).decode("ascii"),
            "size": len(data), "seconds": round(time.monotonic() - started, 1), "command": " ".join(full),
            "log": "\n".join((log or "").strip().splitlines()[-25:])}

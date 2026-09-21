"""The app's Python side: the documents the WebView edits live here.

Both apps ship CPython and SymPy instead of running them in the browser -
Android through Chaquopy (see android/app/build.gradle.kts), iOS through
Python.xcframework (see ios/project.yml) - so this module is what the page
talks to through ``window.SympyEditorPy``: the same
:class:`sympy_editor.document.Document` the server and the Jupyter widget use,
with JSON in and JSON out.

``mobile/build.py`` stages this file and the ``sympy_editor`` package side by
side where each platform's build expects them.
"""

from __future__ import annotations

import inspect
import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from sympy_editor.addons import register_addons_folder
from sympy_editor.document import Document, Interrupted, interrupt_thread

#: The add-ons the app bundles: one folder each under ``addons/`` beside this
#: module (a copy of the add-on's repository: its manifest and its package),
#: staged by ``mobile/build.py``.  Registered here, they count as installed,
#: so every document lists them in its Add-ons menu and the page switches
#: them on and off; nothing else in the app knows them.  A folder added
#: later - a repository cloned into the same directory - would be found the
#: same way.
ADDONS_DIR = Path(__file__).resolve().parent / "addons"
BUNDLED_ADDONS = register_addons_folder(ADDONS_DIR) if ADDONS_DIR.is_dir() else {}

#: The keyword arguments this version's Document takes.  A session saved by a
#: newer app can carry settings it does not know (the app's storage outlives
#: an install of an older build): those are left out, not a document lost.
_DOCUMENT_SETTINGS = {name for name, prm in inspect.signature(Document.__init__).parameters.items()
                      if prm.kind is inspect.Parameter.KEYWORD_ONLY}

#: One Document per editor/session, by the id the page chose.
_documents: Dict[str, Document] = {}

#: The message being processed, while one is: ``(thread, document id)``,
#: what :func:`interrupt` stops.  Read and written under ``_lock`` only, and
#: an interrupt is delivered under it too - so it can reach only the message
#: it was meant for, never the next one.
_running: Optional[Tuple[int, str]] = None
#: The thread an interrupt was delivered to during the current message: the
#: exception may still be pending when the message ends, and is taken back.
_delivered: Optional[int] = None
_lock = threading.Lock()


def _begin(doc_id: str) -> None:
    global _running, _delivered
    with _lock:
        _running = (threading.get_ident(), doc_id)
        _delivered = None


def _end() -> None:
    """The message is over: nothing may stop it any more, and an interrupt
    delivered as it ended - still pending in this thread - is cancelled.
    Retried if that very exception fires in here: it fires at most once per
    delivery, and none comes once ``_running`` is cleared."""
    global _running, _delivered
    while True:
        try:
            with _lock:
                _running = None
                if _delivered is not None:
                    cancel_interrupt(_delivered)
                    _delivered = None
            return
        except Interrupted:
            continue


def cancel_interrupt(ident: int) -> None:
    """Take back an exception :func:`interrupt_thread` set in ``ident`` and
    which has not fired yet (harmless if it has)."""
    import ctypes
    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), None)


def new_doc(doc_id: str, srepr: str, settings_json: str) -> str:
    """Create (or replace) the document ``doc_id`` from ``srepr``, and return
    its first snapshot as JSON.  ``settings_json`` holds the Document keyword
    arguments the page carries (printer settings, parser, declared symbols,
    and a session's history and index)."""
    settings: Dict[str, Any] = json.loads(settings_json or "{}")
    if BUNDLED_ADDONS:
        # The page names the add-ons it was built with; the folders the app
        # carries are the truth at run time - a document can switch on any
        # of them (and only the on/off state is the page's to say).
        named = list(settings.get("available") or [])
        settings["available"] = named + [m["module"] for m in BUNDLED_ADDONS.values() if m["module"] not in named]
    settings = {k: v for k, v in settings.items() if k in _DOCUMENT_SETTINGS}
    _documents[doc_id] = Document(srepr, **settings)
    return handle(doc_id, '{"action": "snapshot"}')


def handle(doc_id: str, message_json: str) -> str:
    """Process one front-end message for ``doc_id``; the answer is a snapshot
    (errors of the edit itself travel inside it, in ``error``).  An interrupt
    always ends in an answer - the document as it stands, with the reason -
    never in an exception the host would take for a failed call."""
    doc = _documents.get(doc_id)
    if doc is None:
        raise KeyError(f"Unknown document {doc_id!r}: the page must call new_doc first")
    answer: Optional[str] = None
    try:
        try:
            _begin(doc_id)
            answer = json.dumps(doc.handle(json.loads(message_json)))
        finally:
            _end()
    except Interrupted:
        # stopped where Document.handle does not report it itself (or just
        # as it finished: then its answer, computed, still stands)
        pass
    if answer is None:
        answer = json.dumps(doc.snapshot(error="Interrupted"))
    return answer


def interrupt(doc_id: Optional[str] = None) -> str:
    """Stop the message being processed, if any; JSON ``true`` when there was
    one.  With ``doc_id``, only a message for that document - or for one
    under it, ``doc_id/...``: the Mac app's windows share this interpreter,
    each bridge naming its documents ``<window>/<page's id>`` and asking to
    stop its window's work alone.

    The bridges call this from a thread of their own, not the Python
    thread: that one is busy with the computation, and lets this in between
    two of its steps (:func:`sympy_editor.document.interrupt_thread`)."""
    global _delivered
    with _lock:
        running = _running
        if running is None:
            return json.dumps(False)
        ident, running_id = running
        if doc_id and running_id != doc_id and not running_id.startswith(doc_id + "/"):
            return json.dumps(False)
        stopped = interrupt_thread(ident)
        if stopped:
            _delivered = ident
    return json.dumps(bool(stopped))


def close(doc_id: str) -> None:
    """Forget a document (the page left the session)."""
    _documents.pop(doc_id, None)


def addons() -> str:
    """The add-ons the app bundles, as JSON: name, label, module, version,
    description - for an about box, and to check a build."""
    return json.dumps([{k: m.get(k) for k in ("name", "label", "module", "version", "description", "requires")}
                       for m in BUNDLED_ADDONS.values()])


def version() -> str:
    """What the app is running, for the about box and bug reports."""
    import platform

    import sympy

    return json.dumps({"python": platform.python_version(), "sympy": sympy.__version__,
                       "addons": sorted(BUNDLED_ADDONS)})

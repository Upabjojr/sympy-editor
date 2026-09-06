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

import json
from pathlib import Path
from typing import Any, Dict

from sympy_editor.addons import register_addons_folder
from sympy_editor.document import Document

#: The add-ons the app bundles: one folder each under ``addons/`` beside this
#: module (a copy of the add-on's repository: its manifest and its package),
#: staged by ``mobile/build.py``.  Registered here, they count as installed,
#: so every document lists them in its Add-ons menu and the page switches
#: them on and off; nothing else in the app knows them.  A folder added
#: later - a repository cloned into the same directory - would be found the
#: same way.
ADDONS_DIR = Path(__file__).resolve().parent / "addons"
BUNDLED_ADDONS = register_addons_folder(ADDONS_DIR) if ADDONS_DIR.is_dir() else {}

#: One Document per editor/session, by the id the page chose.
_documents: Dict[str, Document] = {}


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
    _documents[doc_id] = Document(srepr, **settings)
    return handle(doc_id, '{"action": "snapshot"}')


def handle(doc_id: str, message_json: str) -> str:
    """Process one front-end message for ``doc_id``; the answer is a snapshot
    (errors of the edit itself travel inside it, in ``error``)."""
    doc = _documents.get(doc_id)
    if doc is None:
        raise KeyError(f"Unknown document {doc_id!r}: the page must call new_doc first")
    return json.dumps(doc.handle(json.loads(message_json)))


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

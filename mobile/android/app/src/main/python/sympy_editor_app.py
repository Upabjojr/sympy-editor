"""The app's Python side: the documents the WebView edits live here.

The Android app ships CPython and SymPy (Chaquopy, see app/build.gradle.kts)
instead of running them in the browser, so this module is what the page talks
to through ``window.SympyEditorPy`` (MainActivity.PythonBridge) - the same
:class:`sympy_editor.document.Document` the server and the Jupyter widget use,
with JSON in and JSON out.

``sympy_editor`` itself is copied next to this file by ``mobile/build.py``.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from sympy_editor.document import Document

#: One Document per editor/session, by the id the page chose.
_documents: Dict[str, Document] = {}


def new_doc(doc_id: str, srepr: str, settings_json: str) -> str:
    """Create (or replace) the document ``doc_id`` from ``srepr``, and return
    its first snapshot as JSON.  ``settings_json`` holds the Document keyword
    arguments the page carries (printer settings, parser, declared symbols,
    and a session's history and index)."""
    settings: Dict[str, Any] = json.loads(settings_json or "{}")
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


def version() -> str:
    """What the app is running, for the about box and bug reports."""
    import platform

    import sympy

    return json.dumps({"python": platform.python_version(), "sympy": sympy.__version__})

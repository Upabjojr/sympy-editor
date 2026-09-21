# What a saved formula holds

A formula saved from the editor (**File → Save formula…**, or
`Document.save_text()`) is a `.sympy` file: JSON, holding the expression and
the whole session behind it. `Document.open_text()` takes it back, and the
same text travels through the `savefile` and `openfile` messages.

```json
{
  "sympy-editor": 1,
  "saved": "2026-09-19T08:30:00+00:00",
  "name": "the working one",
  "expr": "x**2 + 3",
  "session": {
    "history": ["Add(Integer(1), Pow(Symbol('x'), Integer(2)))",
                "Add(Integer(3), Pow(Symbol('x'), Integer(2)))"],
    "index": 1,
    "labels": [null, "Edit: 1 → 3"],
    "symbols": ["MatrixSymbol(Str('M'), Integer(2), Integer(2))"],
    "allow_invalid": false,
    "addon_state": {"matching": {"rules": []}}
  }
}
```

| Field | What it is |
| --- | --- |
| `sympy-editor` | the format's version. A file written by a newer one is refused rather than read as something it is not |
| `saved` | when it was written, UTC |
| `name` | what the session was called, when it had a name of its own |
| `expr` | the current expression as SymPy source — so the file says what it holds to whoever opens it in an editor |
| `session` | exactly what `Document.export()` gives: the fields below |

`session` is the session itself:

| Field | What it is |
| --- | --- |
| `history` | every step, as `srepr` — the derivation, not just the answer |
| `index` | which step is the current one (undo/redo stand either side of it) |
| `labels` | what produced each step (`Transform: Factor`), `null` for the first |
| `symbols` | the declared names, as `srepr`, with their assumptions and shapes |
| `allow_invalid` | whether the document keeps what SymPy refuses to build |
| `addon_state` | what each add-on kept about this document, by add-on name |

## Opening something simpler

Opening is forgiving, because a file is often written by hand or by another
program:

* the whole thing above — the session comes back as it was;
* `{"expr": "sin(x)"}` — a document of one step;
* `x**2 + 1` — a line of SymPy source, likewise.

Anything else is refused with a message saying why (empty, not JSON, no
expression in it, a format from the future).

## Where the editor keeps things by itself

The file above is what the user asks for. Everything the editor keeps without
being asked goes through one seam (`Keep` in `editor.js`), under these names:

| Name | What it holds |
| --- | --- |
| `sessions` | the list of sessions, each with the `session` payload above |
| `addons` | which add-ons are switched on (`rememberAddons`) |
| `zoom` | the size the formula is shown at (`rememberZoom`) |
| `addon:<name>` | what an add-on keeps of its own — the rewrite rules' sets, say (`SympyEditor.keep`) |

Where they are kept depends on what is running the page, and the rule is that
the browser's own storage is the last resort:

| Running as | Kept in |
| --- | --- |
| the Android app | `filesDir/keep/<name>.json`, written through a temporary file and a rename (Kotlin, `MainActivity`) |
| the iOS or macOS app | `Application Support/SymPyEditor/keep/<name>.json`, written atomically (Swift, `FilesBridge`) |
| a page served by Python | the server's store — `EditorServer(store=…)`, by default the user's state directory: `%LOCALAPPDATA%` on Windows, `~/Library/Application Support` on macOS, `$XDG_STATE_HOME` or `~/.local/state` elsewhere |
| the Jupyter widget | the kernel's store — `SympyEditorWidget(store=…)`, the same folder as the server's by default, so a notebook and `serve()` share their sessions |
| a standalone HTML page, or the Pyodide web app | `localStorage`, which is all such a page has |

Both Python stores are `sympy_editor.store.Store`: one file per name, written
through a temporary file and a rename.

A page that has a keeper but has kept nothing yet reads the browser's storage
once, so what a page kept before it had one moves across on the first save —
and the browser's copy is then dropped, so that it cannot come back stale the
day the keeper's is lost.

## Where a saved file goes, and where one comes from

| Running as | Save writes | Open takes |
| --- | --- | --- |
| the Android app | where the user says (the system's create-document dialog); the history exports go to Downloads and the share sheet | the system's picker — or a `.sympy` file opened with the app from a file manager or a mail, or a formula shared to it as text |
| the iOS app | the share sheet, which offers *Save to Files* | the document picker — or a `.sympy` file opened with the app (the app declares the type `org.sympy.editor.formula`) |
| the macOS app | the save panel | the open panel — or a `.sympy` file opened from the Finder |
| the Jupyter widget | next to the notebook (the kernel's working directory, or `save_dir`), under a name not taken yet; `w.save_formula()` / `w.open_formula(path)` from Python | the browser's file picker |
| a page in a browser | a download (or the Web Share API where there is one) | the browser's file picker |

A file handed over by the app opens in a session of its own, as File → Open
does, so nothing already open is lost.

"""Add-ons: what a package outside this one can plug into the editor.

An add-on is an :class:`Addon` object.  A :class:`~sympy_editor.document.Document`
made with ``addons=[...]`` asks its add-ons, in turn, for

* **nodes**: kinds (:attr:`Addon.kinds`) and names for typed input
  (:meth:`Addon.namespace`, :meth:`Addon.make_symbol`) - so that a class from
  another library can appear in the expression, be printed, be typed, be
  restored from an ``srepr`` and get tools of its own;
* **transformations**: :attr:`Addon.ops`, appended to the document's table
  and offered in the menus like the built-in ones;
* **data** beside every snapshot (:meth:`Addon.contribute`), and **methods**
  of its own (:meth:`Addon.handle`), reached from the front end through one
  message - ``{"action": "addon", "addon": name, "method": ..., ...}``;
* **a front end** (:attr:`Addon.js`, :attr:`Addon.css`): a plain script that
  registers itself with ``SympyEditor.registerAddon(name, {...})`` and gets a
  panel under the editor, toolbar buttons, and the editor's state and
  selection as they change.

All of it is optional: an add-on with nothing but ``ops`` is a way to ship a
menu of transformations; one with nothing but ``js`` is a way to put a widget
under the formula.  The three drafts in ``addons/`` at the root of the
repository show the shape of each kind.

The Python side of an add-on is a package of its own (installed with pip, or
merely importable), not part of ``sympy_editor``: this module holds the
contract and the loader only, and imports nothing but the standard library
and SymPy, since it is embedded in the Pyodide pages with ``document.py``.
"""

from __future__ import annotations

import base64
import importlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from sympy import Basic

from .ops import Op
from .printer import AnnotatedLatexPrinter, register_rebuild

if TYPE_CHECKING:  # pragma: no cover
    from .document import Document

__all__ = ["Addon", "load_addon", "load_addons", "installed", "scan_addons", "register_addons_folder",
           "read_manifest", "ENTRY_POINT_GROUP", "API_VERSION", "MANIFEST", "ADDONS_ENV",
           "user_dir", "set_user_dir", "user_installed", "inspect_addons", "install_addons", "uninstall_addon",
           "unpack_addons", "find_addons", "USER_ADDONS_ENV"]

#: Installed add-ons announce themselves under this entry-point group:
#: ``[project.entry-points."sympy_editor.addons"] tree = "sympy_editor_tree:ADDON"``.
ENTRY_POINT_GROUP = "sympy_editor.addons"

#: An add-on *folder* - what a checkout of an add-on's repository is, and
#: what the apps bundle one as - carries this manifest beside the Python
#: package: ``{"name", "label", "module", "version", "requires": [...],
#: "description"}``.  :func:`scan_addons` reads a directory of such folders.
MANIFEST = "addon.json"
#: Directories of add-on folders, ``os.pathsep``-separated, that count as
#: installed (the apps point it at the folders they bundle).
ADDONS_ENV = "SYMPY_EDITOR_ADDONS"
#: Directories registered from Python (:func:`register_addons_folder`).
ADDON_FOLDERS: List[str] = []
#: Where the add-ons a user installs while editing go (:func:`install_addons`):
#: this variable, else ``SYMPY_EDITOR_USER_ADDONS``, else ``~/.sympy-editor/addons``
#: (``/sympy_editor_user_addons`` in a Pyodide page, whose file system is its
#: own).  The apps point it into their data directory (:func:`set_user_dir`).
USER_ADDONS_DIR: Optional[str] = None
USER_ADDONS_ENV = "SYMPY_EDITOR_USER_ADDONS"
#: The index the user directory keeps beside its folders: name -> {folder,
#: version, source, ...}, so that the menu can say where an add-on came from.
USER_INDEX = "installed.json"
#: What an install refuses: more than this many bytes or files in one go.
INSTALL_MAX_BYTES = 40 * 1024 * 1024
INSTALL_MAX_FILES = 4000
#: What of an add-on folder is not installed (tests, caches, a checkout's git).
INSTALL_SKIP_DIRS = ("tests", "test", "__pycache__", ".git", ".github", "build", "dist", "node_modules")
INSTALL_SKIP_SUFFIXES = (".pyc", ".pyo", ".egg-info")

#: The version of this contract.  An add-on may set :attr:`Addon.api_version`
#: to the one it was written for; a later, incompatible contract refuses it
#: with a clear message rather than failing somewhere inside.
API_VERSION = 1

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
#: What of an add-on's ``static/`` goes into a Pyodide page with its Python.
TEXT_SUFFIXES = (".js", ".css", ".json", ".txt", ".md", ".svg", ".html")


class Addon:
    """The base class of an add-on: override what the add-on provides.

    Class attributes describe the add-on; methods are asked by the document
    (``doc``) as it works.  An instance may serve several documents; keep
    per-document state on the document (``doc.addon_state[self.name]``, a
    dict the document keeps for each add-on).
    """

    #: Identifier: ``[a-z][a-z0-9_]*``.  The front end part registers under
    #: the same name, and messages carry it.
    name: str = ""
    #: The contract version the add-on was written for (:data:`API_VERSION`).
    api_version: int = API_VERSION
    #: What the panel and the history call it.
    label: str = ""
    #: pip distributions the add-on needs at run time besides sympy-editor
    #: (installed with micropip in a Pyodide page; a hint in an ImportError
    #: otherwise).
    requires: Tuple[str, ...] = ()

    #: Kinds this add-on adds - name -> SymPy types - and their menu labels.
    #: A document that has the add-on on puts them in its own kind table
    #: ahead of "scalar" (first match wins), and takes them out again when
    #: the add-on is switched off.
    kinds: Dict[str, Tuple[type, ...]] = {}
    kind_labels: Dict[str, str] = {}
    #: Transformations (:class:`~sympy_editor.ops.Op`, see
    #: :func:`~sympy_editor.ops.make_op`), appended to the document's table.
    ops: Sequence[Op] = ()
    #: Class -> ``(node, args) -> node`` for a node type whose constructor
    #: does not take its own ``args`` (:func:`~sympy_editor.printer.register_rebuild`).
    rebuilders: Dict[type, Callable[[Basic, List[Basic]], Basic]] = {}
    #: Class name -> ``(printer, node) -> latex`` for a node type whose own
    #: printing (``_latex``, or SymPy's default) is not what the editor
    #: should show: installed on the annotated LaTeX printer as
    #: ``_print_<ClassName>``.  Print the children through ``printer._print``
    #: so that they stay selectable.
    latex_printers: Dict[str, Callable[[Any, Basic], str]] = {}

    #: The front end: JavaScript source (a plain script - no imports - run
    #: once per page with ``SympyEditor`` in scope; it calls
    #: ``SympyEditor.registerAddon(name, {mount: function (api) {...}})``) and
    #: CSS (put in the page once; scope the rules under ``.se-addon-<name>``).
    js: Optional[str] = None
    css: Optional[str] = None

    # -- the tree -----------------------------------------------------------

    def namespace(self) -> Dict[str, Any]:
        """Names for typed input and for reading ``srepr`` strings back:
        the constructors of the add-on's node types, under the names a user
        types *and* the class names ``srepr`` writes (``{"Rule":
        RewriteRule, "RewriteRule": RewriteRule}``).  The expression's own
        names and the declared ones win over these."""
        return {}

    def make_symbol(self, name: str) -> Optional[Basic]:
        """What a *new* name typed by the user stands for, or None for a
        plain ``Symbol``: an add-on can read ``a_`` as a wildcard, say."""
        return None


    # -- the document ---------------------------------------------------------

    def activate(self) -> None:
        """Called each time a document switches the add-on on: install what
        is process-wide (rebuilders, printer methods - they only touch the
        add-on's own classes).  Override to check the add-on's requirements
        (raise ImportError with a pip hint) - and call ``super().activate()``."""
        for cls, func in self.rebuilders.items():
            register_rebuild(cls, func)
        for cls_name, func in self.latex_printers.items():
            setattr(AnnotatedLatexPrinter, "_print_" + cls_name, func)

    def contribute(self, doc: "Document", snap: Dict[str, Any], expr: Basic) -> None:
        """Add the add-on's data to a snapshot of ``expr`` (the current
        expression, or a preview's): put it under ``snap[self.name]`` or a
        key of the add-on's own; keep it small, it travels with every
        message's answer."""

    def contribute_step(self, doc: "Document", step: Dict[str, Any], expr: Basic) -> None:
        """Add the add-on's data to one step of the history (``{"latex",
        "nodes"}`` for ``expr``, see ``render_step``) when the front end asks
        for the history: what its ``historyStep`` hook draws under that step
        in the drawer's list and in the report."""

    def handle(self, doc: "Document", method: str, payload: Dict[str, Any]) -> Union[None, Dict[str, Any], Basic]:
        """Answer ``{"action": "addon", "addon": self.name, "method": method,
        ...payload}``.  Return

        * a ``dict``: a *query* - nothing changes, the front end's
          ``api.call(method, payload)`` resolves with it;
        * a SymPy object: committed as the new whole expression;
        * None: the document as it is now - use it after editing through the
          document's own methods (``doc.replace(path, ...)``), which commit.

        Raise ``ValueError`` (any exception) to refuse: nothing changes, the
        answer carries the error under ``snap["query"]["error"]`` and the
        front end's ``api.call`` rejects with it - the panel that asked shows
        it (the editor's own error line is for the editor's edits)."""
        raise ValueError(f"{self.label or self.name} has no method {method!r}")

    def describe(self, method: str, payload: Dict[str, Any]) -> Optional[str]:
        """The history's label for a change made by ``method``."""
        return f"{self.label or self.name}: {method}"

    def export_state(self, doc: "Document") -> Any:
        """What the add-on keeps about ``doc`` (``doc.addon_state[name]``) as
        JSON, for :meth:`Document.export` - a session saved by the front end,
        a page made again elsewhere; None for nothing.  The default exports
        nothing: state that is not plain JSON needs the add-on's own words."""
        return None

    def restore_state(self, doc: "Document", data: Any) -> None:
        """The inverse of :meth:`export_state`, when a document is made from
        a saved session (``Document(addon_state={name: data})``)."""

    # -- packaging --------------------------------------------------------------

    @property
    def module(self) -> str:
        """The top-level package the add-on lives in: how a Pyodide page
        finds it again (``importlib.import_module(module).ADDON``)."""
        return type(self).__module__.split(".")[0]

    def python_sources(self) -> Dict[str, str]:
        """The add-on's package, for a Pyodide page: ``{path relative to the
        package: text}``.  The default is every ``.py`` under the add-on's
        package directory and every text file under its ``static/`` (the
        scripts and styles the add-on reads at import); an add-on that needs
        more (data files) adds them."""
        mod = importlib.import_module(self.module)
        root = Path(getattr(mod, "__file__", "") or "").parent
        if not root.is_dir() or not (root / "__init__.py").is_file():
            return {}
        out: Dict[str, str] = {}
        files = list(root.rglob("*.py")) + [p for p in root.rglob("static/*") if p.is_file() and p.suffix in TEXT_SUFFIXES]
        for p in sorted(set(files)):
            if "__pycache__" in p.parts:
                continue
            out[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")
        return out

    def pyodide_packages(self) -> List[str]:
        """What a Pyodide page must ``micropip.install`` for the add-on:
        :attr:`requires` by default."""
        return list(self.requires)

    def client_options(self) -> Dict[str, Any]:
        """JSON handed to the front end part as ``api.options``."""
        return {}

    def client(self) -> Dict[str, Any]:
        """The descriptor the front end loads the add-on from."""
        return {"name": self.name, "label": self.label or self.name, "js": self.js, "css": self.css,
                "options": self.client_options()}

    def __repr__(self) -> str:
        return f"<Addon {self.name}>"


def _entry_points(group: str):
    """``importlib.metadata.entry_points`` for one group, on every Python
    this package supports (3.9 returns a dict, 3.10+ has ``select``)."""
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        return []
    eps = entry_points()
    if hasattr(eps, "select"):
        return list(eps.select(group=group))
    return list(eps.get(group, []))  # type: ignore[union-attr]


def read_manifest(folder: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """The manifest of an add-on folder (``addon.json`` beside the package),
    or None when the folder is not one."""
    path = Path(folder) / MANIFEST
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("name") or not data.get("module"):
        return None
    data.setdefault("label", data["name"])
    data.setdefault("requires", [])
    data["folder"] = str(Path(folder).resolve())
    return data


def scan_addons(directory: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
    """The add-on folders under ``directory`` (each a checkout of an add-on's
    repository, or a copy of one: a manifest beside the package), by name.
    Each folder that holds its package is put on ``sys.path``, so that the
    add-on's module imports; the manifest says which module.  This is how
    the apps find the add-ons they bundle, and how a folder of cloned
    repositories will be found later."""
    out: Dict[str, Dict[str, Any]] = {}
    root = Path(directory)
    if not root.is_dir():
        return out
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = read_manifest(folder)
        if manifest is None:
            continue
        if (folder / manifest["module"]).is_dir() and str(folder.resolve()) not in sys.path:
            sys.path.append(str(folder.resolve()))
        out[manifest["name"]] = manifest
    return out


def register_addons_folder(directory: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
    """Make the add-on folders under ``directory`` count as installed (see
    :func:`installed`), and return them."""
    path = str(Path(directory).resolve())
    if path not in ADDON_FOLDERS:
        ADDON_FOLDERS.append(path)
    return scan_addons(path)


def installed() -> Dict[str, str]:
    """The add-ons installed in this environment, by name -> spec (an
    entry point's ``"module:object"``, or an add-on folder's module): what
    ``Document(addons=[name])`` can take.  Entry points of installed
    distributions, then the add-on folders of the directories in
    ``SYMPY_EDITOR_ADDONS`` and of :func:`register_addons_folder`."""
    out = {ep.name: ep.value for ep in sorted(_entry_points(ENTRY_POINT_GROUP), key=lambda e: e.name)}
    dirs = [d for d in os.environ.get(ADDONS_ENV, "").split(os.pathsep) if d] + list(ADDON_FOLDERS)
    user = user_dir()
    if user.is_dir() and str(user) not in dirs:
        dirs.append(str(user))                            # what the user installed while editing
    for directory in dirs:
        for name, manifest in scan_addons(directory).items():
            out.setdefault(name, manifest["module"])
    return out


installed_addons = installed


# -- installing while editing ---------------------------------------------------
#
# An add-on folder (the manifest beside the package, the layout of a checkout
# of an add-on's repository) can be installed at run time - from a .zip, or
# from the files of a repository the front end fetched - into the user
# directory, which :func:`installed` counts among the installed add-ons and
# every new Document lists in its menu.  The apps and a Pyodide page keep
# nothing else: the folders are the installation, and a folder removed is an
# add-on gone (at the next start, where its module was already imported).
# An add-on is code: it runs with the editor's rights, in the app's Python
# and in the page.  The front end says so before installing anything.


def user_dir() -> Path:
    """The directory the add-ons installed while editing live in (see
    :data:`USER_ADDONS_DIR`); it may not exist yet."""
    if USER_ADDONS_DIR:
        return Path(USER_ADDONS_DIR)
    env = os.environ.get(USER_ADDONS_ENV)
    if env:
        return Path(env)
    if sys.platform == "emscripten":                      # a Pyodide page: a file system of its own
        return Path("/sympy_editor_user_addons")
    return Path.home() / ".sympy-editor" / "addons"


def set_user_dir(path: Union[str, Path]) -> Path:
    """Put the user directory at ``path`` (an app's data directory) and make
    what is there count as installed."""
    global USER_ADDONS_DIR
    USER_ADDONS_DIR = str(Path(path))
    if Path(path).is_dir():
        register_addons_folder(path)
    return Path(path)


def _read_index(root: Path) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads((root / USER_INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_index(root: Path, index: Dict[str, Dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / USER_INDEX).write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")


def user_installed(into: Union[str, Path, None] = None) -> Dict[str, Dict[str, Any]]:
    """The add-ons installed while editing, by name: their manifests (with
    ``folder``) plus what the index knows - ``source`` (where they came
    from) and ``installed`` (when).  Empty when the directory does not exist."""
    root = Path(into) if into is not None else user_dir()
    if not root.is_dir():
        return {}
    index = _read_index(root)
    out = {}
    for name, manifest in scan_addons(root).items():
        entry = dict(manifest)
        entry.update({k: v for k, v in index.get(name, {}).items() if k not in entry})
        entry["user"] = True
        out[name] = entry
    return out


def _safe_relpath(name: str) -> Optional[str]:
    """A zip member / files-map path as a clean relative posix path, or None
    for one that must not be written (absolute, ``..``, a drive, empty)."""
    text = str(name).replace("\\", "/")
    if not text or text.startswith("/") or ":" in text.split("/", 1)[0] and len(text.split("/", 1)[0]) == 2:
        return None
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def unpack_addons(payload: Dict[str, Any]) -> Dict[str, bytes]:
    """The files of an install payload, by clean relative path.  ``payload``
    is ``{"zip": <base64 of a .zip>}`` or ``{"files": {path: text or
    {"b64": base64}}}`` (what the front end makes of a repository); paths
    that escape (``..``, absolute) are refused, sizes are capped."""
    files: Dict[str, bytes] = {}
    total = 0

    def put(name: str, data: bytes) -> None:
        nonlocal total
        rel = _safe_relpath(name)
        if rel is None:
            raise ValueError(f"Refusing the path {name!r} in the add-on archive")
        total += len(data)
        if total > INSTALL_MAX_BYTES or len(files) >= INSTALL_MAX_FILES:
            raise ValueError("The add-on archive is too large to install")
        files[rel] = data

    if payload.get("zip"):
        raw = payload["zip"]
        try:
            blob = base64.b64decode(raw, validate=False) if isinstance(raw, str) else bytes(raw)
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if info.file_size > INSTALL_MAX_BYTES:
                        raise ValueError("The add-on archive is too large to install")
                    put(info.filename, zf.read(info))
        except zipfile.BadZipFile:
            raise ValueError("Not a .zip file") from None
    elif isinstance(payload.get("files"), dict):
        for name, value in payload["files"].items():
            if isinstance(value, dict) and "b64" in value:
                data = base64.b64decode(value["b64"])
            elif isinstance(value, str):
                data = value.encode("utf-8")
            else:
                raise ValueError(f"The file {name!r} is neither text nor base64")
            put(name, data)
    else:
        raise ValueError("Nothing to install: give a zip or a files map")
    if not files:
        raise ValueError("The archive is empty")
    return files


def find_addons(files: Dict[str, bytes]) -> List[Dict[str, Any]]:
    """The add-on folders among ``files``: every ``addon.json`` whose package
    (``<folder>/<module>/__init__.py``) is there too, as manifests with
    ``prefix`` (the folder's path in the archive, "" for the top) and
    ``files`` (how many go with it); in the order of their paths."""
    out = []
    for path in sorted(files):
        if path.rsplit("/", 1)[-1] != MANIFEST:
            continue
        prefix = path[: -len(MANIFEST)].rstrip("/")
        if any(part in INSTALL_SKIP_DIRS for part in prefix.split("/") if part):
            continue
        try:
            data = json.loads(files[path].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict) or not data.get("name") or not data.get("module"):
            continue
        if not NAME_RE.match(str(data["name"])) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(data["module"])):
            continue
        init = (prefix + "/" if prefix else "") + str(data["module"]) + "/__init__.py"
        if init not in files:
            continue
        manifest = {"name": str(data["name"]), "label": str(data.get("label") or data["name"]), "module": str(data["module"]),
                    "version": str(data.get("version") or ""), "description": str(data.get("description") or ""),
                    "requires": [str(r) for r in (data.get("requires") or []) if isinstance(r, str)],
                    "prefix": prefix, "files": sum(1 for f in files if _under(f, prefix) and not _skipped(f, prefix))}
        out.append(manifest)
    return out


def _under(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix + "/")


def _skipped(path: str, prefix: str) -> bool:
    """Whether a file of an add-on folder stays out of the installation."""
    rel = path[len(prefix) + 1:] if prefix else path
    parts = rel.split("/")
    return any(p in INSTALL_SKIP_DIRS for p in parts[:-1]) or parts[-1].endswith(INSTALL_SKIP_SUFFIXES)


def inspect_addons(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What an install payload holds (see :func:`find_addons`), each entry
    also saying whether an add-on of that name is installed already
    (``"installed": version or None``)."""
    found = find_addons(unpack_addons(payload))
    have = user_installed()
    for m in found:
        m["installed"] = have[m["name"]].get("version") if m["name"] in have else None
    if not found:
        raise ValueError("No add-on found: an add-on is a folder with addon.json beside its package")
    return found


def install_addons(payload: Dict[str, Any], select: Optional[Iterable[str]] = None,
                   into: Union[str, Path, None] = None, source: str = "") -> List[Dict[str, Any]]:
    """Install the add-on folders of ``payload`` (see :func:`unpack_addons`)
    - the ones ``select`` names, or all - into ``into`` (the user directory
    by default), one folder each, replacing a folder of the same name.
    Returns their manifests (with ``folder``).  A module already imported in
    this process is forgotten (``sys.modules``), so the new files load."""
    root = Path(into) if into is not None else user_dir()
    files = unpack_addons(payload)
    found = find_addons(files)
    wanted = set(select) if select is not None else None
    chosen = [m for m in found if wanted is None or m["name"] in wanted]
    if not chosen:
        raise ValueError("No add-on found: an add-on is a folder with addon.json beside its package"
                         if not found else "None of the add-ons named is in the archive")
    if len({m["module"] for m in chosen}) < len(chosen):
        raise ValueError("Two add-ons in the archive share a module name")
    root.mkdir(parents=True, exist_ok=True)
    index = _read_index(root)
    out = []
    for m in chosen:
        folder = root / (m["prefix"].rsplit("/", 1)[-1] if m["prefix"] else m["module"])
        if folder.exists():
            shutil.rmtree(folder)
        for path, data in files.items():
            if not _under(path, m["prefix"]) or _skipped(path, m["prefix"]):
                continue
            rel = path[len(m["prefix"]) + 1:] if m["prefix"] else path
            target = folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for old, entry in list(index.items()):        # another folder of the same add-on: gone
            if old == m["name"] and entry.get("folder") not in (None, str(folder)) and Path(entry["folder"]).is_dir():
                shutil.rmtree(entry["folder"], ignore_errors=True)
        index[m["name"]] = {"folder": str(folder), "module": m["module"], "version": m["version"], "source": source,
                            "installed": _now()}
        _forget_module(m["module"])
        entry = dict(m)
        entry["folder"] = str(folder)
        out.append(entry)
    _write_index(root, index)
    importlib.invalidate_caches()
    register_addons_folder(root)
    return out


def uninstall_addon(name: str, into: Union[str, Path, None] = None) -> bool:
    """Remove an add-on installed while editing: its folder and its index
    entry.  Returns whether there was one.  Its module, if imported, stays
    in this process until the next start."""
    root = Path(into) if into is not None else user_dir()
    have = user_installed(root)
    entry = have.get(name)
    index = _read_index(root)
    if entry is None and name not in index:
        return False
    folder = Path(entry["folder"]) if entry else Path(index[name].get("folder", ""))
    if folder.is_dir() and folder.resolve().parent == root.resolve():
        shutil.rmtree(folder)
        if str(folder.resolve()) in sys.path:
            sys.path.remove(str(folder.resolve()))
    index.pop(name, None)
    _write_index(root, index)
    return True


def _forget_module(module: str) -> None:
    for key in [k for k in sys.modules if k == module or k.startswith(module + ".")]:
        del sys.modules[key]


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_addon(spec: Union[str, Addon]) -> Addon:
    """An :class:`Addon` from an instance, an entry-point name (``"tree"``,
    see :func:`installed`), a module name (``"sympy_editor_tree"``: its
    ``ADDON``) or ``"module:object"`` (``"my_pkg.addons:PLOT"``).  A class is
    instantiated with no arguments."""
    if isinstance(spec, Addon):
        addon = spec
    elif isinstance(spec, str):
        addon = None
        for ep in _entry_points(ENTRY_POINT_GROUP):
            if ep.name == spec:
                addon = ep.load()
                break
        if addon is None:
            # the name of an add-on folder (bundled, or installed while
            # editing): its manifest says the module
            folders = installed() if ":" not in spec else {}
            target = folders.get(spec, spec) if not _importable(spec) else spec
            mod_name, _, attr = target.partition(":")
            try:
                mod = importlib.import_module(mod_name)
            except ImportError as exc:
                names = ", ".join(installed()) or "none"
                raise ValueError(f"No add-on {spec!r}: not an installed add-on's name (installed: {names}), "
                                 f"and not an importable module ({exc})") from None
            addon = getattr(mod, attr or "ADDON", None)
            if addon is None:
                raise ValueError(f"Module {mod_name!r} defines no {attr or 'ADDON'}")
        if isinstance(addon, type):
            addon = addon()
    else:
        raise TypeError(f"An add-on is an Addon, an entry-point name or a module name, not {type(spec).__name__}")
    if not isinstance(addon, Addon):
        raise TypeError(f"{spec!r} is not an Addon")
    if not NAME_RE.match(addon.name or ""):
        raise ValueError(f"Add-on name {addon.name!r} is not [a-z][a-z0-9_]*")
    if int(getattr(addon, "api_version", API_VERSION)) > API_VERSION:
        raise ValueError(f"Add-on {addon.name!r} needs add-on API version {addon.api_version}; this sympy-editor has {API_VERSION}")
    return addon


def _importable(name: str) -> bool:
    """Whether ``name`` names a module this Python can import (without importing it)."""
    try:
        return importlib.util.find_spec(name.partition(":")[0]) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def load_addons(specs: Iterable[Union[str, Addon]]) -> Dict[str, Addon]:
    """The add-ons of ``specs`` by name, loaded (not activated), in order."""
    out: Dict[str, Addon] = {}
    for spec in specs or ():
        addon = load_addon(spec)
        if addon.name in out:
            raise ValueError(f"Two add-ons named {addon.name!r}")
        out[addon.name] = addon
    return out

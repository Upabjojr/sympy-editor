"""The editor as a program of its own: ``sympy-editor`` (or ``python -m
sympy_editor``) starts the local server and opens the editor in the browser.

It is the editor the apps are: sessions kept on disk with their history,
the zoom and the add-on switches remembered, every installed add-on in the
Add-ons window (those marked experimental off until switched on), and no
*Done* button - it runs until Ctrl+C.  The mathematics is this Python's.

    sympy-editor                         # an empty session, the last one reopened
    sympy-editor "sin(x)**2 + cos(x)**2" # start from a formula
    sympy-editor --no-browser --port 8000
    sympy-editor --addons-dir ~/my-addons   # add-on folders (each with its addon.json)

By default it listens on this computer only (127.0.0.1).  ``--host 0.0.0.0``
makes it reachable from the network: anyone who can reach the port can then
run Python through it (typed input is evaluated), so do that only on a
network you trust.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from typing import List, Optional

from . import __version__
from .addons import installed, register_addons_folder
from .document import Document
from .html import app_logo
from .server import EditorServer

#: The front end as the apps have it: a program of its own keeps its sessions,
#: its zoom and its add-on switches, and has nobody to hand a result back to.
APP_OPTIONS = {"sessions": True, "rememberZoom": True, "rememberAddons": True, "finishButton": False}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sympy-editor",
        description="SymPy Editor: edit SymPy expressions in the browser, backed by this Python.")
    parser.add_argument("expr", nargs="?", default=None,
                        help="a formula to start from, as SymPy source (default: the last session, or an empty one)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="the address to listen on (default 127.0.0.1: this computer only; "
                             "0.0.0.0 exposes the editor - and this Python - to the network)")
    parser.add_argument("--port", type=int, default=0, help="the port (default: a free one)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--store", default=None,
                        help="the folder sessions and settings are kept in (default: the user's state directory)")
    parser.add_argument("--no-store", action="store_true",
                        help="keep nothing on disk (the browser keeps the sessions instead)")
    parser.add_argument("--addons-dir", action="append", default=[], metavar="DIR",
                        help="a folder of add-on folders to offer as well (may be given more than once)")
    parser.add_argument("--title", default="SymPy Editor", help="the page's title")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    parser.add_argument("--version", action="version", version=f"sympy-editor {__version__}")
    return parser


def make_server(args: argparse.Namespace) -> EditorServer:
    """The server the arguments describe (not yet serving)."""
    for folder in args.addons_dir:
        register_addons_folder(folder)
    # A placeholder (the empty view) unless a formula was given: the last
    # session opens over it as the page starts, as in the apps.
    expr = args.expr if args.expr else "0"
    document = Document(expr, available=sorted(installed()))
    store = False if args.no_store else args.store
    options = dict(APP_OPTIONS, reopenLastSession=not args.expr)
    return EditorServer(document, host=args.host, port=args.port, title=args.title,
                        options=options, verbose=args.verbose, store=store, logo=app_logo())


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    server = make_server(args)
    print(f"SymPy Editor {__version__} running at {server.url} (Ctrl+C to stop)", flush=True)
    if server.store is not None:
        print(f"Sessions are kept in {server.store}", flush=True)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("Warning: listening beyond this computer - whoever reaches this port can run Python here.",
              file=sys.stderr, flush=True)
    if not args.no_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

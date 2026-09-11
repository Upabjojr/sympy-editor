"""Build tour.html: the editor playing tour.json - a tour of what it does.

    python examples/tutorial/build.py [--open] [--out PATH]

The page is a standalone file: Python runs in the browser (Pyodide, from its
CDN), so it needs a network the first time it is opened, and then plays on its
own - to be watched, or recorded as a video.  Edit tour.json to change what it
shows; sympy_editor/tutorial.py documents every kind of step.
"""
import argparse
import sys
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sympy_editor import register_addons_folder, save_tutorial_html  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", default=str(HERE / "tour.json"), help="the tutorial script (default: tour.json)")
    ap.add_argument("--out", default=str(HERE / "tour.html"), help="the page to write (default: tour.html)")
    ap.add_argument("--open", action="store_true", help="open it in the browser")
    args = ap.parse_args(argv)
    register_addons_folder(ROOT / "addons")        # the tour switches on the plot and the tree
    out = save_tutorial_html(Path(args.script), Path(args.out))
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB)")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())

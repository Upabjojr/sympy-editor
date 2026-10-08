#!/usr/bin/env python3
"""Zip an add-on folder the way the Add-ons menu installs it.

    python addons/pack.py sympy_editor_feynman            # -> addons/dist/sympy_editor_feynman-0.0.1.zip
    python addons/pack.py addons/sympy_editor_tree -o /tmp # any add-on folder, anywhere

The archive holds the folder itself - addon.json beside the package, the
README and pyproject - without its tests, caches or a checkout's git.  It
is what "From a file…" in the editor's Add-ons menu takes (and what a
repository downloaded from GitHub as a .zip is, one level deeper).
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from sympy_editor.addons import INSTALL_SKIP_DIRS, INSTALL_SKIP_SUFFIXES, read_manifest  # noqa: E402


def pack(folder: Path, out_dir: Path) -> Path:
    manifest = read_manifest(folder)
    if manifest is None:
        sys.exit(f"{folder} is not an add-on folder (no addon.json naming a module)")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{folder.name}-{manifest.get('version') or '0'}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(folder)
            if any(part in INSTALL_SKIP_DIRS for part in rel.parts[:-1]) or rel.name.endswith(INSTALL_SKIP_SUFFIXES):
                continue
            zf.write(path, (folder.name / rel).as_posix())
    return target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder", help="the add-on folder (a name under addons/, or a path)")
    ap.add_argument("-o", "--out", type=Path, default=HERE / "dist", help="where to write the .zip (default: addons/dist)")
    args = ap.parse_args(argv)
    folder = Path(args.folder)
    if not folder.is_dir() and (HERE / args.folder).is_dir():
        folder = HERE / args.folder
    made = pack(folder.resolve(), args.out)
    print("wrote", made, f"({made.stat().st_size / 1e3:.0f} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build the Mac app: a .app carrying its own CPython, SymPy and the editor page.

    python desktop/build.py           # desktop/macos/build/SymPyEditor.app (ad-hoc signed: it runs here)
    python desktop/build.py --run     # ... and open it
    python desktop/build.py --zip     # ... and a .zip of it, to hand to someone
    python desktop/build.py --app-store   # a .pkg for App Store Connect (TestFlight for Mac, the Mac App Store)

It is the phone apps' shell in a window: the Swift and the Objective-C are the
files mobile/ios builds (with their few ``#if os(macOS)`` branches), and the
page, the app's Python and SymPy are the folders mobile/build.py stages, used
from where they are.  The interpreter is the macOS build of the release
mobile/build.py pins, which carries the standard library inside the framework -
so, unlike iOS, nothing is unpacked into the app.

For the Mac App Store (and so for TestFlight on a Mac) the app is sandboxed -
``SymPyEditorMAS.entitlements``, which the App Store build uses in place of the
ad-hoc one.  WebKit wants ``com.apple.security.network.client`` even for the
page in the bundle: without it the window stays blank.  That build needs a
Mac App Store provisioning profile, an Apple Distribution certificate to sign
the app and a Mac Installer Distribution one to sign the .pkg:

    MACOS_TEAM_ID=... MACOS_SIGN_IDENTITY="Apple Distribution: ..." \
    MACOS_INSTALLER_IDENTITY="3rd Party Mac Developer Installer: ..." \
    MACOS_PROVISIONING_PROFILE="SymPy editor Mac App Store" python desktop/build.py --app-store
    xcrun altool --upload-app -f desktop/macos/build/pkg/SymPyEditor.pkg -t macos --apiKey ... --apiIssuer ...

Signing: none is needed to run it on this Mac.  For another Mac, set
MACOS_SIGN_IDENTITY to a Developer ID Application certificate (and
MACOS_TEAM_ID), then notarize the zip:

    xcrun notarytool submit SymPyEditor.zip --apple-id ... --team-id ... --wait
    xcrun stapler staple desktop/macos/build/SymPyEditor.app
"""

from __future__ import annotations

import argparse
import os
import plistlib
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MACOS = HERE / "macos"

sys.path.insert(0, str(HERE.parent / "mobile"))
import build as mobile  # noqa: E402 - the phone apps' build: the page, the app's Python, SymPy


def python_framework() -> Path:
    """CPython for macOS, as python.org's own support project packages it: the
    release the iOS app pins, in its macOS flavour, linked into the project."""
    version, build = mobile.PYTHON_APPLE_SUPPORT.split("-")
    root = mobile.CACHE / "python-apple-support" / f"{mobile.PYTHON_APPLE_SUPPORT}-macOS"
    framework = root / "Python.xcframework"
    if not framework.is_dir():
        archive = mobile.download(
            f"https://github.com/beeware/Python-Apple-support/releases/download/"
            f"{mobile.PYTHON_APPLE_SUPPORT}/Python-{version}-macOS-support.{build}.tar.gz",
            mobile.CACHE / "python-apple-support" / f"Python-{version}-macOS-support.{build}.tar.gz")
        print(f"+ unpacking {archive.name}", flush=True)
        root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tar:
            try:
                tar.extractall(root, filter="tar")
            except TypeError:                     # no extraction filter before 3.12
                tar.extractall(root)
    link = MACOS / "Python.xcframework"
    if link.is_symlink() or link.exists():
        link.unlink() if link.is_symlink() else shutil.rmtree(link)
    link.symlink_to(framework, target_is_directory=True)
    print(f"+ linked {link} -> {framework}")
    return link


def export_options(team: str, profile: str, installer: str) -> bytes:
    """What `xcodebuild -exportArchive` needs for a Mac App Store package: the
    profile by name, the certificate that signs the app, and the one that
    signs the installer (a .pkg is what App Store Connect takes for a Mac)."""
    return plistlib.dumps({
        "method": "app-store-connect",
        "teamID": team,
        "signingStyle": "manual",
        "signingCertificate": "Apple Distribution",
        "installerSigningCertificate": installer,
        "provisioningProfiles": {"org.sympy.editor": profile},
    })


def app_store_build(env: dict) -> list[Path]:
    """Archive the app sandboxed and export the .pkg App Store Connect takes."""
    for name in ("MACOS_TEAM_ID", "MACOS_PROVISIONING_PROFILE", "MACOS_INSTALLER_IDENTITY"):
        if not env.get(name):
            sys.exit(f"--app-store needs {name} (see the docstring)")
    out = MACOS / "build"
    archive = out / "SymPyEditor.xcarchive"
    mobile.run(["xcodebuild", "-project", "SymPyEditor.xcodeproj", "-scheme", "SymPyEditor",
                "-configuration", "Release", "-destination", "platform=macOS",
                "-archivePath", str(archive),
                "CODE_SIGN_ENTITLEMENTS=SymPyEditor/SymPyEditorMAS.entitlements",
                f"PROVISIONING_PROFILE_SPECIFIER={env['MACOS_PROVISIONING_PROFILE']}",
                f"DEVELOPMENT_TEAM={env['MACOS_TEAM_ID']}", "archive"], cwd=MACOS, env=env)
    plist = out / "ExportOptions.plist"
    plist.write_bytes(export_options(env["MACOS_TEAM_ID"], env["MACOS_PROVISIONING_PROFILE"],
                                     env["MACOS_INSTALLER_IDENTITY"]))
    mobile.run(["xcodebuild", "-exportArchive", "-archivePath", str(archive),
                "-exportOptionsPlist", str(plist), "-exportPath", str(out / "pkg")], cwd=MACOS, env=env)
    return sorted((out / "pkg").glob("*.pkg"))


def macos_build(cdn: bool = False, run_it: bool = False, zip_it: bool = False, app_store: bool = False) -> list[Path]:
    if platform.system() != "Darwin":
        sys.exit("The Mac app is built on macOS, with Xcode.")
    mobile.build_www(cdn, native=True)              # the page, editing through the app's own Python
    mobile.copy_python_sources(mobile.IOS / "app")  # sympy_editor and sympy_editor_app, fresh
    mobile.ios_packages()                           # SymPy and the add-ons' packages (pure Python: one staging serves both)
    python_framework()
    mobile.make_icons(MACOS / "SymPyEditor/Assets.xcassets/AppIcon.appiconset/icon-1024.png")
    if not shutil.which("xcodegen"):
        sys.exit("xcodegen not found: brew install xcodegen (or open the project in Xcode, see desktop/README.md)")
    env = dict(os.environ,
               MACOS_BUILD_NUMBER=os.environ.get("MACOS_BUILD_NUMBER") or mobile.build_number(),
               MACOS_SIGN_IDENTITY=os.environ.get("MACOS_SIGN_IDENTITY", "Apple Distribution" if app_store else "-"),
               MACOS_TEAM_ID=os.environ.get("MACOS_TEAM_ID", ""))
    mobile.run(["xcodegen", "generate"], cwd=MACOS, env=env)
    if app_store:
        return app_store_build(env)
    out = MACOS / "build"
    mobile.run(["xcodebuild", "-project", "SymPyEditor.xcodeproj", "-scheme", "SymPyEditor",
                "-configuration", "Release", "-destination", "platform=macOS",
                "-derivedDataPath", str(out / "derived"), "build"], cwd=MACOS, env=env)
    built = out / "derived" / "Build" / "Products" / "Release" / "SymPyEditor.app"
    if not built.is_dir():
        return []
    app = out / "SymPyEditor.app"
    if app.exists():
        shutil.rmtree(app)
    shutil.copytree(built, app, symlinks=True)
    made = [app]
    if zip_it:
        archive = out / "SymPyEditor.zip"
        archive.unlink(missing_ok=True)
        # ditto, not zip: it keeps the signature and the framework's symlinks
        mobile.run(["ditto", "-c", "-k", "--keepParent", str(app), str(archive)])
        made.append(archive)
    if run_it:
        mobile.run(["open", str(app)])
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true", help="open the app once it is built")
    ap.add_argument("--zip", action="store_true", dest="zip_it", help="also write SymPyEditor.zip beside it")
    ap.add_argument("--cdn", action="store_true", help="bundle without vendored assets (needs network at run time)")
    ap.add_argument("--app-store", action="store_true", dest="app_store",
                    help="a sandboxed .pkg for App Store Connect (TestFlight for Mac); see the docstring for what it needs")
    args = ap.parse_args(argv)
    made = macos_build(args.cdn, args.run, args.zip_it, args.app_store)
    print("\nBuilt:" if made else "\nNo artifacts found.")
    for p in made:
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
        print("  ", p, f"({size / 1e6:.0f} MB)")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())

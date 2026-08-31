#!/usr/bin/env python3
"""Build the mobile apps: .apk / .aab (Android) and .ipa (iOS).

    python mobile/build.py android            # debug APK (installable on a device)
    python mobile/build.py android --release  # release APK + AAB (signed if ANDROID_KEYSTORE... are set)
    python mobile/build.py ios                # .ipa (macOS + Xcode; IOS_TEAM_ID for signing)
    python mobile/build.py ios --simulator    # .app for the iOS simulator, no signing needed

Both start by (re)building the shared bundle mobile/www with build_www.py.

Environment for signing:
  Android release:  ANDROID_KEYSTORE (path), ANDROID_KEYSTORE_PASSWORD, ANDROID_KEY_ALIAS, ANDROID_KEY_PASSWORD
  iOS:              IOS_TEAM_ID (Apple developer team), optional IOS_EXPORT_METHOD (development, ad-hoc, app-store-connect)
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANDROID = HERE / "android"
IOS = HERE / "ios"


def run(cmd, cwd=None, env=None):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def build_www(cdn: bool, android: bool) -> None:
    cmd = [sys.executable, str(HERE / "build_www.py")]
    if cdn:
        cmd.append("--cdn")
    if android:
        cmd.append("--android")
    run(cmd)


def copy_python_sources() -> Path:
    """Put the current ``sympy_editor`` package where Chaquopy picks it up.

    The Android app runs the editor's own Python (see
    ``android/app/src/main/python/sympy_editor_app.py``); SymPy comes from
    PyPI at build time, this package comes from the checkout, so the app is
    never built against a stale copy."""
    src = HERE.parent / "src" / "sympy_editor"
    dest = ANDROID / "app" / "src" / "main" / "python" / "sympy_editor"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"+ copied {src} -> {dest}")
    return dest


#: JDK majors the pinned Gradle/AGP/Kotlin accept (a newer default `java`,
#: e.g. 25, fails with a bare "IllegalArgumentException: 25.0.4").
JDK_MAJORS = (17, 21)


def java_major(java: str) -> int:
    """The major version of ``java`` (0 if it cannot be run)."""
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True).stderr
        version = out.split('"')[1]                      # openjdk version "17.0.2" ...
        return int(version.split(".")[0])
    except (OSError, IndexError, ValueError):
        return 0


def android_env() -> dict:
    """The build environment: ANDROID_HOME set to the SDK at its usual place
    when neither it nor local.properties says where it is, and JAVA_HOME
    pointing at a supported JDK when the default one is not (looked up under
    the usual install roots)."""
    env = dict(os.environ)
    if not (env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT") or (ANDROID / "local.properties").is_file()):
        home = Path.home()
        for sdk in (home / "Android" / "Sdk", home / "Library" / "Android" / "sdk",
                    Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "Android" / "Sdk"):
            if (sdk / "platforms").is_dir():
                print(f"note: using ANDROID_HOME={sdk}", flush=True)
                env["ANDROID_HOME"] = str(sdk)
                break
    if env.get("JAVA_HOME") or java_major("java") in JDK_MAJORS:
        return env
    roots = [Path("/usr/lib/jvm"), Path("/usr/local/opt"), Path("/opt/homebrew/opt"), Path("/Library/Java/JavaVirtualMachines"),
             Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Eclipse Adoptium",
             Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Java"]
    for root in roots:
        for home in sorted(root.glob("*")) if root.is_dir() else []:
            for candidate in (home, home / "Contents" / "Home"):
                java = candidate / "bin" / ("java.exe" if platform.system() == "Windows" else "java")
                if java.is_file() and java_major(str(java)) in JDK_MAJORS:
                    print(f"note: the default java is {java_major('java') or 'missing'}; using JAVA_HOME={candidate}", flush=True)
                    env["JAVA_HOME"] = str(candidate)
                    return env
    print(f"warning: no JDK {'/'.join(map(str, JDK_MAJORS))} found; set JAVA_HOME if the build fails", flush=True)
    return env


def android_build(release: bool, cdn: bool) -> list[Path]:
    build_www(cdn, android=True)
    copy_python_sources()
    gradlew = ANDROID / ("gradlew.bat" if platform.system() == "Windows" else "gradlew")
    tasks = ["assembleRelease", "bundleRelease"] if release else ["assembleDebug"]
    run([str(gradlew), "--no-daemon", *tasks], cwd=ANDROID, env=android_env())
    outputs = ANDROID / "app" / "build" / "outputs"
    made = sorted(p for p in outputs.rglob("*") if p.suffix in (".apk", ".aab"))
    if release and not os.environ.get("ANDROID_KEYSTORE"):
        print("note: no ANDROID_KEYSTORE in the environment - release artifacts are unsigned "
              "(sign with apksigner / jarsigner, or set the ANDROID_* variables).")
    return made


def ios_build(simulator: bool, cdn: bool, method: str) -> list[Path]:
    if platform.system() != "Darwin":
        sys.exit("iOS builds need macOS with Xcode (or the mobile.yml GitHub workflow).")
    build_www(cdn, android=False)
    if not shutil.which("xcodegen"):
        sys.exit("xcodegen not found: brew install xcodegen (or create the project by hand, see mobile/README.md)")
    env = dict(os.environ, IOS_TEAM_ID=os.environ.get("IOS_TEAM_ID", ""))
    run(["xcodegen", "generate"], cwd=IOS, env=env)
    out = IOS / "build"
    if simulator:
        run(["xcodebuild", "-project", "SymPyEditor.xcodeproj", "-scheme", "SymPyEditor", "-configuration", "Debug",
             "-sdk", "iphonesimulator", "-destination", "generic/platform=iOS Simulator",
             "-derivedDataPath", str(out / "derived"), "CODE_SIGNING_ALLOWED=NO", "build"], cwd=IOS)
        return sorted((out / "derived").rglob("SymPyEditor.app"))
    if not env["IOS_TEAM_ID"]:
        sys.exit("set IOS_TEAM_ID (Apple developer team) to build a signed .ipa, or use --simulator")
    archive = out / "SymPyEditor.xcarchive"
    run(["xcodebuild", "-project", "SymPyEditor.xcodeproj", "-scheme", "SymPyEditor", "-configuration", "Release",
         "-destination", "generic/platform=iOS", "-archivePath", str(archive),
         "-allowProvisioningUpdates", f"DEVELOPMENT_TEAM={env['IOS_TEAM_ID']}", "archive"], cwd=IOS, env=env)
    options = (IOS / "ExportOptions.plist").read_text(encoding="utf-8").replace(
        "<string>development</string>", f"<string>{method}</string>", 1)
    plist = out / "ExportOptions.plist"
    plist.write_text(options, encoding="utf-8")
    run(["xcodebuild", "-exportArchive", "-archivePath", str(archive), "-exportOptionsPlist", str(plist),
         "-exportPath", str(out / "ipa"), "-allowProvisioningUpdates"], cwd=IOS, env=env)
    return sorted((out / "ipa").glob("*.ipa"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("platform", choices=["android", "ios"])
    ap.add_argument("--release", action="store_true", help="Android: release APK + AAB instead of a debug APK")
    ap.add_argument("--simulator", action="store_true", help="iOS: build a simulator .app instead of an .ipa")
    ap.add_argument("--cdn", action="store_true", help="bundle without vendored assets (needs network at run time)")
    ap.add_argument("--method", default=os.environ.get("IOS_EXPORT_METHOD", "development"),
                    help="iOS export method: development, ad-hoc, app-store-connect")
    args = ap.parse_args(argv)
    made = android_build(args.release, args.cdn) if args.platform == "android" else ios_build(args.simulator, args.cdn, args.method)
    print("\nBuilt:" if made else "\nNo artifacts found.")
    for p in made:
        print("  ", p, f"({p.stat().st_size / 1e6:.1f} MB)" if p.is_file() else "")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())

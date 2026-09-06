#!/usr/bin/env python3
"""Build the mobile apps: .apk / .aab (Android) and .ipa (iOS).

    python mobile/build.py android            # debug APK (installable on a device)
    python mobile/build.py android --release  # release APK + AAB (signed if ANDROID_KEYSTORE... are set)
    python mobile/build.py ios                # .ipa (macOS + Xcode; IOS_TEAM_ID for signing)
    python mobile/build.py ios --simulator    # .app for the iOS simulator, no signing needed
    python mobile/build.py ios --simulator --run   # ... and install and launch it there

Both start by (re)building the shared bundle mobile/www with build_www.py.

Environment for signing:
  Android release:  ANDROID_KEYSTORE (path), ANDROID_KEYSTORE_PASSWORD, ANDROID_KEY_ALIAS, ANDROID_KEY_PASSWORD
  iOS:              IOS_TEAM_ID (Apple developer team), optional IOS_EXPORT_METHOD (development, ad-hoc, app-store-connect);
                    without an Apple ID in Xcode, IOS_API_KEY_ID + IOS_API_ISSUER_ID (App Store Connect API key) and,
                    to sign with a certificate of the keychain, IOS_PROVISIONING_PROFILE (the name of an installed profile);
                    IOS_BUILD_NUMBER (CFBundleVersion, default: the number of commits)
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANDROID = HERE / "android"
IOS = HERE / "ios"
APP = HERE / "app"                      # the Python side both apps run

#: The iOS interpreter: a release of github.com/beeware/Python-Apple-support,
#: which is how CPython's official iOS support is packaged as an XCFramework
#: (the standard library and the tools to install it travel with it).
PYTHON_APPLE_SUPPORT = "3.13-b14"

#: Where the downloads live, as in build_www.py.
CACHE = Path.home() / ".cache" / "sympy-editor"


def run(cmd, cwd=None, env=None):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def build_www(cdn: bool, *, android: bool = False, native: bool = False) -> None:
    cmd = [sys.executable, str(HERE / "build_www.py")]
    if cdn:
        cmd.append("--cdn")
    if android:
        cmd.append("--android")
    if native:
        cmd.append("--native")
    run(cmd)


def download(url: str, dest: Path) -> Path:
    """Fetch ``url`` once into ``dest`` (a file in the cache)."""
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print("  downloading", url, flush=True)
    with urllib.request.urlopen(url, timeout=300) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)
    return dest


def sympy_version() -> str:
    """The SymPy the apps ship: the release the browser build uses too."""
    sys.path.insert(0, str(HERE.parent / "src"))
    from sympy_editor.html import SYMPY_VERSION

    return SYMPY_VERSION


def copy_python_sources(dest: Path) -> Path:
    """Put the app's Python - ``mobile/app/sympy_editor_app.py`` and the
    current ``sympy_editor`` package - where the platform's build looks for
    it: ``src/main/python`` for Chaquopy, ``ios/app`` for the app bundle.

    SymPy comes from PyPI at build time; this code comes from the checkout,
    so neither app is ever built against a stale copy."""
    dest.mkdir(parents=True, exist_ok=True)
    package = dest / "sympy_editor"
    if package.exists():
        shutil.rmtree(package)
    shutil.copytree(HERE.parent / "src" / "sympy_editor", package,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(APP / "sympy_editor_app.py", dest / "sympy_editor_app.py")
    stage_addons(dest / "addons")
    print(f"+ staged the app's Python in {dest}")
    return dest


#: Where the add-ons live in the checkout: one folder per add-on, each with
#: its manifest (addon.json) beside its package - the layout a checkout of
#: an add-on's own repository has.
ADDONS = HERE.parent / "addons"
#: What of an add-on folder does not travel into an app.
ADDON_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "*.egg-info", "build", "dist", ".git")


def addon_manifests() -> list[dict]:
    """The manifests of the add-on folders under ``addons/``, in name order."""
    if str(HERE.parent / "src") not in sys.path:
        sys.path.insert(0, str(HERE.parent / "src"))
    from sympy_editor.addons import scan_addons
    return [m for _name, m in sorted(scan_addons(ADDONS).items())]


def stage_addons(dest: Path) -> Path:
    """Copy every add-on folder - manifest and package, not its tests - into
    ``dest``, one folder each, as ``sympy_editor_app`` expects them
    (``addons/<folder>/addon.json``).  Each stays a folder of its own, so
    that one cloned from a repository later can sit beside them unchanged."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for manifest in addon_manifests():
        if manifest.get("bundle") is False:          # the template: an example to copy, not to ship
            continue
        src = Path(manifest["folder"])
        shutil.copytree(src, dest / src.name, ignore=ADDON_SKIP)
    print(f"+ staged {len(list(dest.iterdir()))} add-ons in {dest}")
    return dest


def addon_requirements() -> list[str]:
    """The pip requirements of the bundled add-ons (from their manifests):
    what each app must install beside SymPy."""
    out: list[str] = []
    for manifest in addon_manifests():
        if manifest.get("bundle") is False:
            continue
        for req in manifest.get("requires", []):
            if req not in out:
                out.append(req)
    return out


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


def make_icons(needed: Path) -> None:
    """Draw the app's icons, unless ``needed`` - the one this platform would
    miss first - is already there.

    They are build products - no PNG is committed - so a fresh checkout has
    the SVGs and this makes the rest.  Without them the manifest points at a
    `@mipmap/ic_launcher` that does not exist, or the asset catalogue has no
    AppIcon, and the build stops.
    """
    if needed.is_file():
        return
    if not shutil.which("rsvg-convert"):
        sys.exit("the icons are missing and rsvg-convert is not installed "
                 "(apt install librsvg2-bin), so mobile/make_icons.py cannot draw them")
    run([sys.executable, str(HERE / "make_icons.py")])


def android_build(release: bool, cdn: bool) -> list[Path]:
    build_www(cdn, android=True)
    copy_python_sources(ANDROID / "app" / "src" / "main" / "python")
    make_icons(ANDROID / "app/src/main/res/mipmap-mdpi/ic_launcher.png")
    gradlew = ANDROID / ("gradlew.bat" if platform.system() == "Windows" else "gradlew")
    tasks = ["assembleRelease", "bundleRelease"] if release else ["assembleDebug"]
    run([str(gradlew), "--no-daemon", *tasks], cwd=ANDROID, env=android_env())
    outputs = ANDROID / "app" / "build" / "outputs"
    made = sorted(p for p in outputs.rglob("*") if p.suffix in (".apk", ".aab"))
    if release and not os.environ.get("ANDROID_KEYSTORE"):
        print("note: no ANDROID_KEYSTORE in the environment - release artifacts are unsigned "
              "(sign with apksigner / jarsigner, or set the ANDROID_* variables).")
    return made


def ios_runtime() -> Path:
    """Stage the interpreter the iOS app ships.

    ``Python.xcframework`` is CPython built for iOS - the device and the
    simulator, and for the simulator both architectures - as python.org's own
    support project packages it.  It is 100+ MB of build product, so it is
    downloaded once into the cache and linked into ``mobile/ios`` rather than
    committed or copied; ``project.yml`` embeds it, and the build phase it
    carries (``build/utils.sh``) installs the standard library into the app
    and turns each extension module into the framework iOS insists on.
    """
    version, build = PYTHON_APPLE_SUPPORT.split("-")
    root = CACHE / "python-apple-support" / PYTHON_APPLE_SUPPORT
    framework = root / "Python.xcframework"
    if not framework.is_dir():
        archive = download(
            f"https://github.com/beeware/Python-Apple-support/releases/download/"
            f"{PYTHON_APPLE_SUPPORT}/Python-{version}-iOS-support.{build}.tar.gz",
            CACHE / "python-apple-support" / f"Python-{version}-iOS-support.{build}.tar.gz")
        print(f"+ unpacking {archive.name}", flush=True)
        root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tar:
            try:
                tar.extractall(root, filter="tar")
            except TypeError:                     # no extraction filter before 3.12
                tar.extractall(root)
    link = IOS / "Python.xcframework"
    if link.is_symlink() or link.exists():
        link.unlink() if link.is_symlink() else shutil.rmtree(link)
    link.symlink_to(framework, target_is_directory=True)
    print(f"+ linked {link} -> {framework}")
    return link


def ios_packages() -> Path:
    """Install SymPy - pure Python, so the wheel from PyPI runs on iOS as it
    stands - into the folder the app bundles as ``app_packages``.

    Its own test suite is half of what SymPy weighs and no app runs it, so it
    does not travel: the app is ~25 MB lighter for the loss of `sympy.test()`.
    """
    packages = IOS / "app_packages"
    stamp = packages / ".sympy-version"
    wanted = sympy_version() + " " + " ".join(addon_requirements())   # the add-ons' packages travel with SymPy
    if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == wanted:
        return packages
    if packages.exists():
        shutil.rmtree(packages)
    run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(packages),
         "--no-compile", "--only-binary=:all:", f"sympy=={sympy_version()}"] + addon_requirements())
    for tests in sorted(packages.rglob("tests")):
        if tests.is_dir():
            shutil.rmtree(tests)
    for cache in sorted(packages.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    stamp.write_text(wanted + "\n", encoding="utf-8")
    size = sum(f.stat().st_size for f in packages.rglob("*") if f.is_file())
    print(f"+ staged SymPy {wanted} in {packages} ({size / 1e6:.0f} MB)")
    return packages


def simulator_arch() -> str:
    """The one architecture a simulator build needs: this Mac's own.

    It is also the one the interpreter can be installed for - the standard
    library that travels with Python.xcframework is per architecture, and its
    install script takes a single ``ARCHS`` - so the build asks for exactly
    the slice the simulator on this machine will run.
    """
    return "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"


def simulator_run(app: Path) -> None:
    """Install ``app`` on a booted simulator - booting the newest iPhone if
    none is running - and launch it."""
    listing = subprocess.run(["xcrun", "simctl", "list", "devices", "booted"],
                             capture_output=True, text=True).stdout
    if "(Booted)" not in listing:
        run(["xcrun", "simctl", "boot", "iPhone 17"])
    run(["open", "-a", "Simulator"])
    run(["xcrun", "simctl", "install", "booted", str(app)])
    run(["xcrun", "simctl", "launch", "booted", "org.sympy.editor"])


def ios_build(simulator: bool, cdn: bool, method: str, launch: bool = False) -> list[Path]:
    if platform.system() != "Darwin":
        sys.exit("iOS builds need macOS with Xcode (or the mobile.yml GitHub workflow).")
    # The app runs its own Python, as the Android one does, so the page uses
    # the native backend and no Pyodide is vendored into the bundle.
    build_www(cdn, native=True)
    copy_python_sources(IOS / "app")
    ios_runtime()
    ios_packages()
    make_icons(IOS / "SymPyEditor/Assets.xcassets/AppIcon.appiconset/icon-1024.png")
    if not shutil.which("xcodegen"):
        sys.exit("xcodegen not found: brew install xcodegen (or create the project by hand, see mobile/README.md)")
    env = dict(os.environ, IOS_TEAM_ID=os.environ.get("IOS_TEAM_ID", ""),
               IOS_BUILD_NUMBER=os.environ.get("IOS_BUILD_NUMBER") or build_number())
    run(["xcodegen", "generate"], cwd=IOS, env=env)
    out = IOS / "build"
    if simulator:
        run(["xcodebuild", "-project", "SymPyEditor.xcodeproj", "-scheme", "SymPyEditor", "-configuration", "Debug",
             "-sdk", "iphonesimulator", "-destination", "generic/platform=iOS Simulator",
             "-derivedDataPath", str(out / "derived"), "CODE_SIGNING_ALLOWED=NO",
             f"ARCHS={simulator_arch()}", "ONLY_ACTIVE_ARCH=NO", "build"], cwd=IOS)
        made = sorted((out / "derived").rglob("SymPyEditor.app"))
        if launch and made:
            simulator_run(made[0])
        return made
    if launch:
        sys.exit("--run installs on the simulator: use it with --simulator.")
    if not env["IOS_TEAM_ID"]:
        sys.exit("set IOS_TEAM_ID (Apple developer team) to build a signed .ipa, or use --simulator")
    signing = ["-allowProvisioningUpdates", *api_key_arguments()]
    profile = os.environ.get("IOS_PROVISIONING_PROFILE")
    archive = out / "SymPyEditor.xcarchive"
    # With a profile named, the archive is built unsigned and signed on the
    # way out: the export is where Xcode takes a named profile and the
    # certificate of the keychain, and the archive would only have insisted
    # on a profile of its own making.
    run(["xcodebuild", "-project", "SymPyEditor.xcodeproj", "-scheme", "SymPyEditor", "-configuration", "Release",
         "-destination", "generic/platform=iOS", "-archivePath", str(archive),
         *(["CODE_SIGNING_ALLOWED=NO"] if profile else signing),
         f"DEVELOPMENT_TEAM={env['IOS_TEAM_ID']}", "archive"], cwd=IOS, env=env)
    plist = out / "ExportOptions.plist"
    plist.write_bytes(export_options(method, env["IOS_TEAM_ID"], profile))
    run(["xcodebuild", "-exportArchive", "-archivePath", str(archive), "-exportOptionsPlist", str(plist),
         "-exportPath", str(out / "ipa"), *signing], cwd=IOS, env=env)
    made = sorted((out / "ipa").glob("*.ipa"))
    for ipa in made:
        app_plist_first(ipa)
    return made


def app_plist_first(ipa: Path) -> None:
    """Put the app's Info.plist ahead of the frameworks' in the archive.

    altool takes the bundle identifier of an .ipa from the first Info.plist
    it meets, and Xcode zips ``Frameworks/`` before the app's own: with
    sixty-odd extension modules, each a framework, the upload was refused
    as ``org.sympy.editor.-socket``.  The order of the entries is nothing
    to the signature.
    """
    first = next(name for name in zipfile.ZipFile(ipa).namelist() if name.count("/") == 2 and name.endswith("/Info.plist"))
    ordered = ipa.with_suffix(".ordered")
    with zipfile.ZipFile(ipa) as src, zipfile.ZipFile(ordered, "w") as dst:
        for info in sorted(src.infolist(), key=lambda i: i.filename != first):
            dst.writestr(info, src.read(info))
    ordered.replace(ipa)


def export_options(method: str, team: str, profile: str | None = None) -> bytes:
    """The ``-exportOptionsPlist`` of ``xcodebuild -exportArchive``.

    Automatic signing lets Xcode find or make the certificate and profile
    (through the account signed in, or the API key).  With ``profile`` - the
    name of a provisioning profile installed on this machine, matching
    ``method`` - signing is manual: that profile, and the certificate of its
    kind in the keychain (Apple Development for a development build, Apple
    Distribution otherwise).  It is the way when the key may not create
    Apple's cloud-managed distribution certificate: export the certificate
    from the machine that has it, import the .p12, install the profile.
    """
    if method not in ("development", "ad-hoc", "app-store-connect"):
        sys.exit(f"unknown iOS export method {method!r}: development, ad-hoc or app-store-connect")
    options: dict = {"method": method, "teamID": team, "compileBitcode": False}
    if profile:
        options.update(signingStyle="manual",
                       signingCertificate="Apple Development" if method == "development" else "Apple Distribution",
                       provisioningProfiles={"org.sympy.editor": profile})
    else:
        options["signingStyle"] = "automatic"
    return plistlib.dumps(options)


def build_number() -> str:
    """CFBundleVersion: the store wants every upload's to be new, and the
    count of commits only grows (1 outside a checkout: set IOS_BUILD_NUMBER)."""
    try:
        out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=HERE, capture_output=True, text=True, check=True).stdout
        return str(int(out.strip()))
    except (OSError, subprocess.CalledProcessError, ValueError):
        return "1"


def api_key_arguments() -> list[str]:
    """Sign without an Apple ID in Xcode: an App Store Connect API key.

    With ``IOS_API_KEY_ID`` and ``IOS_API_ISSUER_ID`` set (App Store Connect
    > Users and Access > Integrations > API, a key with the Developer role),
    xcodebuild fetches and creates the certificate and profile through the
    API - what a machine with no Xcode account, such as CI, needs.  The key
    itself is ``IOS_API_KEY_PATH`` or, as for Apple's own tools,
    ``AuthKey_<ID>.p8`` in ``~/.appstoreconnect/private_keys`` (or
    ``~/.private_keys``, ``~/private_keys``, ``./private_keys``).  Without
    the variables, signing goes through the account signed into Xcode.
    """
    key_id, issuer = os.environ.get("IOS_API_KEY_ID"), os.environ.get("IOS_API_ISSUER_ID")
    if not key_id and not issuer:
        return []
    if not (key_id and issuer):
        sys.exit("IOS_API_KEY_ID and IOS_API_ISSUER_ID go together (App Store Connect API key)")
    home = Path.home()
    candidates = [Path(os.environ["IOS_API_KEY_PATH"])] if os.environ.get("IOS_API_KEY_PATH") else [
        folder / f"AuthKey_{key_id}.p8"
        for folder in (home / ".appstoreconnect" / "private_keys", home / ".private_keys",
                       home / "private_keys", Path.cwd() / "private_keys")]
    key = next((p for p in candidates if p.is_file()), None)
    if key is None:
        sys.exit(f"the API key AuthKey_{key_id}.p8 was not found: set IOS_API_KEY_PATH")
    return ["-authenticationKeyPath", str(key.resolve()),
            "-authenticationKeyID", key_id, "-authenticationKeyIssuerID", issuer]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("platform", choices=["android", "ios"])
    ap.add_argument("--release", action="store_true", help="Android: release APK + AAB instead of a debug APK")
    ap.add_argument("--simulator", action="store_true", help="iOS: build a simulator .app instead of an .ipa")
    ap.add_argument("--run", action="store_true", help="iOS: install the simulator .app and launch it")
    ap.add_argument("--cdn", action="store_true", help="bundle without vendored assets (needs network at run time)")
    ap.add_argument("--method", default=os.environ.get("IOS_EXPORT_METHOD", "development"),
                    help="iOS export method: development, ad-hoc, app-store-connect")
    args = ap.parse_args(argv)
    made = (android_build(args.release, args.cdn) if args.platform == "android"
            else ios_build(args.simulator, args.cdn, args.method, args.run))
    print("\nBuilt:" if made else "\nNo artifacts found.")
    for p in made:
        print("  ", p, f"({p.stat().st_size / 1e6:.1f} MB)" if p.is_file() else "")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())

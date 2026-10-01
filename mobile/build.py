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
                    IOS_BUILD_NUMBER (CFBundleVersion, default: BUILD_NUMBER in this file)
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANDROID = HERE / "android"
IOS = HERE / "ios"
APP = HERE / "app"                      # the Python side both apps run

#: The iOS interpreter: a release of github.com/beeware/Python-Apple-support,
#: which is how CPython's official iOS support is packaged as an XCFramework
#: (the standard library and the tools to install it travel with it).
PYTHON_APPLE_SUPPORT = "3.13-b14"

#: ONNX Runtime for iOS, which the handwriting add-on's model runs on: the
#: archive its CocoaPod is made from (onnxruntime.xcframework, a static
#: library, and its LICENSE), pinned by version and checksum.
#:
#: Not the Android app's 1.29: from that release the iOS library carries
#: Microsoft's telemetry client (1DS: an uploader to
#: mobile.events.data.microsoft.com over NSURLSession, with a reachability
#: monitor and an offline store), and iOS has no permission to take away as
#: Android's manifest does.  1.28 has none of it - no network call of any
#: kind - and :func:`ios_onnxruntime` refuses a library that has
#: (:data:`ONNXRUNTIME_FORBIDDEN`), so bumping this cannot bring it in.
ONNXRUNTIME_IOS = "1.28.0"
ONNXRUNTIME_IOS_SHA256 = "b503cf5949ab718a1dff17d1643237ecc0fecf50ad86264572dcafddb140a327"
#: What the library must not reach for (its undefined symbols, by nm) or
#: carry (its strings): anything that opens a connection, and the collector.
ONNXRUNTIME_FORBIDDEN = ("NSURLSession", "NSURLConnection", "CFNetwork", "CFHTTP", "CFSocket", "CFStream",
                         "_nw_", "SCNetwork", "_getaddrinfo", "_gethostby", "_socket", "_connect", "_sendto",
                         "_curl_", "_SSL_")
ONNXRUNTIME_FORBIDDEN_TEXT = ("events.data.microsoft.com", "OneCollector", "Applications6Events")

#: NumPy built for iOS: PyPI has no such wheel, BeeWare's index does (the
#: project that packages the interpreter above).
IOS_NUMPY = "2.5.2.post1"
IOS_WHEELS = "https://pypi.anaconda.org/beeware/simple"

#: Where the downloads live, as in build_www.py.
CACHE = Path.home() / ".cache" / "sympy-editor"


def run(cmd, cwd=None, env=None):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


#: What a build for an app says to ``--cdn``, here and in desktop/build.py
#: (and build_www.py, to ``--cdn --android``).  The apps have no network:
#: Android's manifest takes the permission out, and WebKit blocks every
#: http(s) load on iOS and the Mac.  A bundle that loads KaTeX from a CDN
#: would be a blank page there.
NO_CDN = ("--cdn: the apps never use the network, so their bundle carries everything; "
          "use mobile/build_www.py --cdn for a page to open in a browser")


def build_www(cdn: bool, *, android: bool = False, native: bool = False, debug: bool = False) -> None:
    """The bundle of an app (mobile/www, which the three apps share).  Never
    one that loads from the CDNs, whoever asks: see :data:`NO_CDN`."""
    if cdn:
        sys.exit(NO_CDN)
    cmd = [sys.executable, str(HERE / "build_www.py")]
    if android:
        cmd.append("--android")
    if native:
        cmd.append("--native")
    if debug:
        cmd.append("--debug")
    run(cmd)


#: What a debug build calls itself.  It is a second application -
#: org.sympy.editor.debug - so everything that names it says which one it is:
#: the launcher's label (build.gradle.kts), the title over the formula and
#: the icon beside it (build_www.py), and the launcher icon (make_icons.py).
DEBUG_TITLE = "SymPy Editor (debug)"


def download(url: str, dest: Path) -> Path:
    """Fetch ``url`` once into ``dest`` (a file in the cache): all of it, or
    nothing - ``build_www.download`` writes it under another name until it is
    as long as the server said, so a connection that dropped leaves no piece
    here for the next build to take for the file."""
    if dest.is_file():
        return dest
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from build_www import download as whole

    return whole(url, dest, timeout=300)


def unpack(archive: Path, root: Path) -> Path:
    """Unpack the downloaded ``archive`` into ``root``: all of it, or nothing.

    Into a folder beside ``root`` that takes its name once everything is out:
    unpacked in place, an archive that was cut short left the start of a
    framework there, and every later build found the folder and used it.  An
    archive that cannot be read is deleted, so that the next build downloads
    it again instead of failing on the same file."""
    part = root.with_name(root.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True)
    print(f"+ unpacking {archive.name}", flush=True)
    try:
        if archive.suffix == ".zip":              # ONNX Runtime's archive
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(part)
        else:
            with tarfile.open(archive) as tar:
                try:
                    tar.extractall(part, filter="tar")
                except TypeError:                     # no extraction filter before 3.12
                    tar.extractall(part)
        shutil.rmtree(root, ignore_errors=True)
        part.replace(root)
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, gzip.BadGzipFile, zlib.error) as exc:
        archive.unlink(missing_ok=True)
        sys.exit(f"{archive} could not be unpacked ({exc}): the cached copy was deleted - "
                 "build again to download it afresh")
    finally:
        shutil.rmtree(part, ignore_errors=True)
    return root


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


#: The handwriting add-on: its manifest says "bundle": false - a Pyodide page
#: cannot run it, nor can the Mac app, which shares the iOS app's staged
#: add-ons - and the Android and iOS builds stage it themselves, with what it
#: reads with (stage_ink, ios_ink).
INK_ADDON = "sympy_editor_handwriting"
INK_MODEL_FILES = ("encoder.onnx", "decoder_step.onnx", "vocab.json", "meta.json")
#: The model's attribution and licence terms, beside its files in the export:
#: they travel with the model, and the add-on's guide shows them.
INK_MODEL_NOTICE = "NOTICE"


def stage_ink(dest: Path, wanted: bool) -> bool:
    """The handwriting add-on in an app's build (debug or release), in
    ``dest`` - beside the app's Python on Android, the ``ink`` folder on iOS:
    the add-on's folder, math-ocr's ``mathocr.tokenizer`` and
    ``mathocr.data.inkml`` (the features the model was trained on), and the
    model as the package ``mathocr_model``, which ONNX Runtime runs
    (onnxruntime-android in build.gradle.kts, onnxruntime.xcframework on iOS).

    The model is math-ocr's and must never reach git: every folder staged
    here is git-ignored.  Its NOTICE - the terms it is distributed under -
    goes into the app with it, and a build whose export has none says so."""
    for name in ("mathocr", "mathocr_model"):
        shutil.rmtree(dest / name, ignore_errors=True)
    shutil.rmtree(dest / "addons" / INK_ADDON, ignore_errors=True)
    if not wanted:
        return False
    sys.path.insert(0, str(ADDONS / INK_ADDON))
    from sympy_editor_handwriting.recognizer import StrokeRecognizer
    rec = StrokeRecognizer()
    root, model = rec.root, rec.model_dir()
    if root is None or model is None or not all((model / f).is_file() for f in INK_MODEL_FILES):
        print("+ no handwriting model (a math-ocr checkout beside this one, or SYMPY_EDITOR_MATHOCR): not staged")
        return False
    shutil.copytree(ADDONS / INK_ADDON, dest / "addons" / INK_ADDON, ignore=ADDON_SKIP)
    package = dest / "mathocr"
    (package / "data").mkdir(parents=True)
    for init in (package / "__init__.py", package / "data" / "__init__.py"):
        init.write_text("", encoding="utf-8")
    shutil.copyfile(root / "mathocr" / "tokenizer.py", package / "tokenizer.py")
    shutil.copyfile(root / "mathocr" / "data" / "inkml.py", package / "data" / "inkml.py")
    models = dest / "mathocr_model"
    models.mkdir()
    (models / "__init__.py").write_text('"""math-ocr\'s stroke model, staged into an app\'s build: never commit it."""\n',
                                        encoding="utf-8")
    for f in INK_MODEL_FILES:
        shutil.copyfile(model / f, models / f)
    if (model / INK_MODEL_NOTICE).is_file():
        shutil.copyfile(model / INK_MODEL_NOTICE, models / INK_MODEL_NOTICE)
    else:
        print(f"warning: {model} has no {INK_MODEL_NOTICE}: the app carries the model without the terms it is distributed under")
    print(f"+ staged the handwriting add-on and {model}")
    return True


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


def make_icons(*needed: Path) -> None:
    """Draw the app's icons, unless every ``needed`` one - those this
    platform would miss first - is already there.

    They are build products - no PNG is committed - so a fresh checkout has
    the SVGs and this makes the rest.  Without them the manifest points at a
    `@mipmap/ic_launcher` that does not exist, or the asset catalogue has no
    AppIcon, and the build stops.
    """
    if all(p.is_file() for p in needed):
        return
    if not shutil.which("rsvg-convert"):
        sys.exit("the icons are missing and rsvg-convert is not installed "
                 "(apt install librsvg2-bin), so mobile/make_icons.py cannot draw them")
    run([sys.executable, str(HERE / "make_icons.py")])


def android_build(release: bool, cdn: bool) -> list[Path]:
    # A debug build is its own application (see build.gradle.kts): its bundle
    # says so over the formula, and its icons - drawn into src/debug/res -
    # wear a bug badge, so neither the page nor the launcher leaves any doubt
    # about which of the two is open.
    build_www(cdn, android=True, debug=not release)
    copy_python_sources(ANDROID / "app" / "src" / "main" / "python")
    stage_ink(ANDROID / "app" / "src" / "main" / "python", wanted=True)
    make_icons(ANDROID / "app/src/main/res/mipmap-mdpi/ic_launcher.png",
               ANDROID / "app/src/debug/res/mipmap-mdpi/ic_launcher.png")
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
        unpack(download(
            f"https://github.com/beeware/Python-Apple-support/releases/download/"
            f"{PYTHON_APPLE_SUPPORT}/Python-{version}-iOS-support.{build}.tar.gz",
            CACHE / "python-apple-support" / f"Python-{version}-iOS-support.{build}.tar.gz"), root)
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


def ios_onnxruntime() -> Path:
    """Stage ONNX Runtime for iOS: ``onnxruntime.xcframework``, downloaded
    once into the cache, checked against its pinned checksum and linked into
    ``mobile/ios``, where ``project.yml`` links it into the app (a static
    library: nothing of it is embedded).  The app's Python reaches it through
    the module ``OrtModule.m`` builds into the interpreter."""
    root = CACHE / "onnxruntime" / ONNXRUNTIME_IOS
    framework = root / "onnxruntime.xcframework"
    if not framework.is_dir():
        name = f"pod-archive-onnxruntime-c-{ONNXRUNTIME_IOS}.zip"
        archive = download(f"https://download.onnxruntime.ai/{name}", CACHE / "onnxruntime" / name)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != ONNXRUNTIME_IOS_SHA256:
            archive.unlink()
            sys.exit(f"{name}: sha256 {digest}, not the pinned {ONNXRUNTIME_IOS_SHA256}")
        unpack(archive, root)
    check_no_network(framework)
    link = IOS / "onnxruntime.xcframework"
    if link.is_symlink() or link.exists():
        link.unlink() if link.is_symlink() else shutil.rmtree(link)
    link.symlink_to(framework, target_is_directory=True)
    print(f"+ linked {link} -> {framework}")
    return link


def check_no_network(framework: Path) -> None:
    """Refuse an ONNX Runtime that could send anything anywhere.

    The app's privacy statement says nothing leaves the device.  The library
    is linked into the app itself, so what it imports the app can call: every
    iOS slice is read with ``nm`` and ``strings``, and one that reaches for a
    networking API, or names Microsoft's telemetry collector, stops the build
    - an environment variable asking it not to upload is not a guarantee."""
    for binary in sorted(framework.glob("ios-*/onnxruntime.framework/onnxruntime")):
        wanted = subprocess.run(["nm", "-u", str(binary)], capture_output=True, text=True).stdout
        if not wanted.strip():
            sys.exit(f"{binary}: nm listed no symbols, so the library cannot be checked for network code")
        found = sorted({line.strip() for line in wanted.splitlines()
                        if any(bad in line for bad in ONNXRUNTIME_FORBIDDEN)})
        text = subprocess.run(["strings", "-a", str(binary)], capture_output=True, text=True, errors="replace").stdout
        found += [bad for bad in ONNXRUNTIME_FORBIDDEN_TEXT if bad in text]
        if found:
            sys.exit(f"{binary} can reach the network ({', '.join(found[:8])}): the app must not carry it.\n"
                     f"Pin an ONNX Runtime without the telemetry client (ONNXRUNTIME_IOS).")
    print(f"+ checked {framework.name}: no networking, no telemetry collector")


def ios_ink(simulator: bool) -> Path:
    """Stage ``mobile/ios/ink``: what the handwriting add-on needs in the iOS
    app - the add-on, math-ocr's modules and model (:func:`stage_ink`), and
    NumPy built for the platform being built for, the device or the simulator.

    A folder of its own, made afresh by every build, because none of it is
    the Mac app's, which shares ``app`` and ``app_packages``: NumPy's
    extension modules are per platform.  Without a model the folder holds a
    note and nothing else, and the app says it has no handwriting model."""
    dest = IOS / "ink"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "README.txt").write_text(
        "What the handwriting add-on reads with, staged by mobile/build.py (ios_ink): never commit it.\n",
        encoding="utf-8")
    if not stage_ink(dest, wanted=True):
        return dest
    arch, sdk = (simulator_arch(), "iphonesimulator") if simulator else ("arm64", "iphoneos")
    run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(dest), "--no-compile", "--no-deps",
         "--only-binary=:all:", "--implementation", "cp", "--python-version", PYTHON_APPLE_SUPPORT.split("-")[0],
         "--platform", f"ios_13_0_{arch}_{sdk}", "--index-url", IOS_WHEELS, f"numpy=={IOS_NUMPY}"])
    for junk in [d for d in sorted(dest.rglob("*")) if d.is_dir() and d.name in ("tests", "__pycache__")] + [dest / "bin"]:
        shutil.rmtree(junk, ignore_errors=True)
    licence = CACHE / "onnxruntime" / ONNXRUNTIME_IOS / "LICENSE"
    if licence.is_file():                       # MIT: the notice travels with the library
        shutil.copyfile(licence, dest / "onnxruntime-LICENSE.txt")
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print(f"+ staged NumPy {IOS_NUMPY} ({arch}, {sdk}) and the handwriting model in {dest} ({size / 1e6:.0f} MB)")
    return dest


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
    ios_onnxruntime()
    ios_ink(simulator)
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


#: Apple's build number (CFBundleVersion) of this version: it counts the
#: uploads of one CFBundleShortVersionString, so it starts again at 1 with each
#: new version and goes up by one for each further upload of the same version,
#: iOS and macOS alike.  (Android's versionCode, in build.gradle.kts, never
#: starts again: Google Play wants it higher than every earlier upload.)
BUILD_NUMBER = 1


def build_number() -> str:
    """CFBundleVersion for iOS and macOS: :data:`BUILD_NUMBER`, unless
    ``IOS_BUILD_NUMBER`` / ``MACOS_BUILD_NUMBER`` say otherwise."""
    return str(BUILD_NUMBER)


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
    ap.add_argument("--cdn", action="store_true",
                    help="refused: the apps never use the network (their manifests forbid it); "
                         "build_www.py --cdn makes a CDN page for a browser")
    ap.add_argument("--method", default=os.environ.get("IOS_EXPORT_METHOD", "development"),
                    help="iOS export method: development, ad-hoc, app-store-connect")
    args = ap.parse_args(argv)
    if args.cdn:
        sys.exit(NO_CDN)
    made = (android_build(args.release, args.cdn) if args.platform == "android"
            else ios_build(args.simulator, args.cdn, args.method, args.run))
    print("\nBuilt:" if made else "\nNo artifacts found.")
    for p in made:
        print("  ", p, f"({p.stat().st_size / 1e6:.1f} MB)" if p.is_file() else "")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())

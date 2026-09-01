# sympy-editor as a mobile app

Minimal native shells around **one shared web bundle**.  Nothing in the
editor is platform-specific: the Android and iOS apps display the exact page
`sympy_editor.to_html()` produces for the desktop, with KaTeX vendored so the
app works offline.  No node.js toolchain.

**Both apps ship CPython and SymPy** and edit in them - Android through
Chaquopy, iOS through `Python.xcframework` - so the page uses the `native`
backend of editor.js and no Pyodide is bundled: nothing is downloaded at run
time, the first edit does not wait for a WebAssembly runtime, and it is the
same `sympy_editor.document.Document` the server and the Jupyter widget use.

```
mobile/
  build_www.py   -> www/   the shared bundle (index.html + vendor/katex, ~1 MB native)
  build.py                 one command to produce .apk / .aab / .ipa
  app/                     the Python both apps run (sympy_editor_app.py)
  android/                 Gradle + Kotlin: a WebView serving www/ (MainActivity.kt, ~60 lines)
  ios/                     SwiftUI + WKWebView serving www/ (EditorView.swift + PythonRuntime.m)
```

## 1. The shared bundle

```bash
pip install -e .                    # from the repository root
python mobile/build_www.py          # -> mobile/www  (downloads ~30 MB once, cached in ~/.cache/sympy-editor)
python -m http.server -d mobile/www # try it in a desktop browser at http://localhost:8000
```

`--cdn` skips vendoring (the page then needs a network connection).  The
bundle must be *served* (as the apps do): WebAssembly and `fetch` do not work
from `file://` URLs.  `tests/test_mobile.py` builds the bundle and, with
`SYMPY_EDITOR_SLOW_TESTS=1`, edits in it with every external request blocked.

## 2. Android: .apk and .aab

Requirements: JDK 17 or 21 and the Android SDK (Android Studio installs both;
on a bare machine `sdkmanager "platforms;android-36" "build-tools;36.0.0"`).
`build.py` finds them on its own when the environment does not say: a JDK 17/21
under the usual install roots when the default `java` is newer (Gradle 8.9 /
Kotlin cannot run on JDK 25), and the SDK at `~/Android/Sdk` (or the macOS /
Windows equivalents) when neither `ANDROID_HOME` nor `local.properties` is set.
`gradlew` honours `JAVA_HOME`.

```bash
python mobile/build.py android            # debug APK, signed with the debug key: install with adb
python mobile/build.py android --release  # release APK + AAB (for Google Play)
```

Artifacts land in `mobile/android/app/build/outputs/{apk,bundle}/`.  Release
builds are signed when these variables are set (otherwise they are built
unsigned and can be signed later with `apksigner` / `jarsigner`):

```
ANDROID_KEYSTORE=/path/to/release.keystore  ANDROID_KEYSTORE_PASSWORD=...  ANDROID_KEY_ALIAS=...  ANDROID_KEY_PASSWORD=...
```

(`keytool -genkeypair -v -keystore release.keystore -alias sympy -keyalg RSA -keysize 2048 -validity 10000` creates one.)
Opening `mobile/android` in Android Studio works too: run `python
mobile/build.py android` once first, so that the bundle
(`app/src/main/assets/www`) and the app's Python (`app/src/main/python`) are
staged.  `gradlew` is a
two-line wrapper that fetches the official wrapper jar on first use.

## 3. iOS: .ipa

Requirements: macOS with Xcode, `brew install xcodegen`, and an Apple
developer team for signing (the simulator needs none).

```bash
python mobile/build.py ios --simulator        # .app for the simulator, no signing needed
python mobile/build.py ios --simulator --run  # ... and install it on a simulator and launch it
IOS_TEAM_ID=ABCDE12345 python mobile/build.py ios              # development .ipa
IOS_TEAM_ID=ABCDE12345 python mobile/build.py ios --method app-store-connect
```

Signing goes through the Apple ID signed into Xcode (Settings > Accounts) -
or, on a machine that has none, through an App Store Connect API key (Users
and Access > Integrations > API, Developer role), which is what CI wants:

```bash
IOS_TEAM_ID=ABCDE12345 IOS_API_KEY_ID=U1234ABCDE IOS_API_ISSUER_ID=<issuer uuid> python mobile/build.py ios
```

The key is `~/.appstoreconnect/private_keys/AuthKey_<ID>.p8`, where Apple's
tools look too (or `IOS_API_KEY_PATH`).

A development build needs a device registered with the team (Xcode makes
the profile for it), and an App Store build with a Developer-role key wants
Apple's *cloud-managed* distribution certificate, which the key may not be
allowed to create ("Cloud signing permission error").  The way round both
is a certificate of your own in the keychain and a profile you name:

```bash
security import distribution.p12 -k ~/Library/Keychains/login.keychain-db -T /usr/bin/codesign   # once
IOS_TEAM_ID=... IOS_PROVISIONING_PROFILE="SymPy editor App Store" python mobile/build.py ios --method app-store-connect
```

The profile (App Store type, for `org.sympy.editor` and that certificate,
made in the developer portal or through the API) goes in
`~/Library/MobileDevice/Provisioning Profiles/<uuid>.mobileprovision`; the
archive is then built unsigned and signed on export, with the certificate
the method calls for - Apple Development, or Apple Distribution.  The first
signing asks for the keychain in a dialog; *Always Allow* answers it for
good.  (`security import` refuses a `.p12` written by a recent OpenSSL:
`openssl pkcs12 -in it.p12 -nodes | openssl pkcs12 -export -legacy -out legacy.p12` re-encodes it.)

The first build downloads the interpreter (~40 MB, cached in
`~/.cache/sympy-editor`) and installs SymPy for the app; the app comes to
about 95 MB, most of it the standard library and SymPy.

Artifacts: `mobile/ios/build/ipa/SymPyEditor.ipa` (or the simulator `.app`
under `mobile/ios/build/derived/`).  The Xcode project is generated from
`project.yml`; to work in Xcode instead: `python mobile/build.py ios
--simulator` once (it stages `Python.xcframework`, `app/` and
`app_packages/`, which are not in the repository), then `cd mobile/ios &&
xcodegen generate && open SymPyEditor.xcodeproj`.

A simulator build is made for one architecture, this Mac's own: the standard
library that travels with the interpreter is per architecture, and the script
that installs it takes a single `ARCHS`.

## 4. Without a Mac or an Android SDK: GitHub Actions

`.github/workflows/mobile.yml` (manual trigger, or on `v*` tags) builds the
debug APK, the release APK + AAB and an iOS simulator app, and uploads them
as artifacts; with the secrets `ANDROID_KEYSTORE_BASE64` + passwords and
`IOS_TEAM_ID` + `IOS_API_KEY_ID` + `IOS_API_ISSUER_ID` + `IOS_API_KEY_BASE64` (the
`.p8`, base64) it produces signed release artifacts and a `.ipa`.

## Notes

- **Both apps run Python natively**, through one protocol: the page uses the
  `native` backend of editor.js and hands JSON messages to
  `window.SympyEditorPy`, each with a request id, and gets the answer back
  through `window.__sympyEditorNative(id, ok, payload)`.  Every call returns
  at once and is answered from a thread of the app's own, so a long
  computation never blocks the interface.  Both ends call the same
  `mobile/app/sympy_editor_app.py`, which `mobile/build.py` stages beside a
  fresh copy of `src/sympy_editor` - neither app is ever built against stale
  code.
  - **Android:** CPython 3.12 and SymPy in the APK (Chaquopy, configured in
    `android/app/build.gradle.kts`), bridged by `MainActivity.PythonBridge`.
    Needs Android 7.0 (API 24, what Chaquopy requires).  A debug build can be
    inspected from the desktop:
    `adb forward tcp:9222 localabstract:$(adb shell cat /proc/net/unix | grep -o webview_devtools_remote_[0-9]* | head -1)`,
    then open `http://localhost:9222` (or drive it with Playwright's
    `connect_over_cdp`).
  - **iOS:** CPython 3.13 as `Python.xcframework` - python.org's own iOS
    support, packaged by [Python-Apple-support][pas] and pinned in
    `build.py` - with the standard library installed into the app and each
    extension module turned into the framework iOS insists on, by the script
    that travels with it.  `PythonRuntime.m` starts an isolated interpreter
    (no environment, no bytecode written beside a signed bundle) and
    `EditorView.swift` bridges it; a debug build sets `isInspectable`, so
    Safari's *Develop > Simulator* menu opens the Web Inspector on the page.
- **Pyodide is not what the apps use, and iOS could not use it anyway.**  The
  bundle can still be built with it (`build_www.py` without `--native`, which
  is what the web app and a desktop preview want), but WebKit on the iOS
  *simulator* segfaults inside its WebAssembly signal handler while Pyodide
  starts - every version tried, in Safari as much as in a WKWebView - so an
  app that edited in the page would be untestable there.  Shipping the
  interpreter settles it, and is better on a device besides: no 20 MB of
  WebAssembly, and no wait before the first edit.
- Licences of the vendored parts are listed in `www/vendor/NOTICE.txt`
  (KaTeX MIT, CPython PSF, SymPy/mpmath BSD; Pyodide MPL-2.0 when it is
  vendored at all).  Ship that file with the app (it is inside the bundle
  already).

[pas]: https://github.com/beeware/Python-Apple-support
- Interaction on touch screens: tap to select, tap the selected node again
  to edit it (tap a gap for a caret, again to insert), drag to select a
  range, ↑ / *Delete* / the menus in the toolbar; the keyboard button opens the on-screen
  keyboard for the selection, the caret or the whole expression.

## The icon

`python mobile/make_icons.py` builds every size the app and the store need
from two things: `mobile/icon/sympy-mark.svg` - SymPy's own logo with the
wordmark taken off, since an icon has no room for text - and a pencil the
script draws over it, which is what says the app *edits* the mathematics.
It writes the master SVGs beside the mark, the `mipmap-*` PNGs (legacy,
round and the 108dp adaptive foreground), the adaptive-icon XML with its
background colour, the iOS asset catalogue, and the two the stores ask for:

| where | size | why |
| --- | --- | --- |
| `res/mipmap-*/ic_launcher*.png` | 48-192 px | what Android draws in the launcher |
| `res/mipmap-*/ic_launcher_foreground.png` | 108-432 px | the adaptive icon's foreground, art inside the central 72dp |
| `ios/.../AppIcon.appiconset/icon-1024.png` | 1024 | every iOS size, made by Xcode from this one - and it must have no alpha |
| `icon-512.png` | 512 | Google Play's listing |
| `icon-1024.png` | 1024 | the App Store's |

Needs `rsvg-convert` (`apt install librsvg2-bin`), and Pillow to flatten the
iOS icon.

**No image is committed.**  The SVGs are the source and every PNG is drawn
from them, so `python mobile/build.py android` (and the iOS build) calls
`make_icons.py` first when the icons are not there - a fresh checkout builds
without a thought.  `*.png` is in `.gitignore` and a test refuses any image
that finds its way into the index.

The mark's author, Fredrik Johansson, permits its free use on SymPy's own
terms (the note travels in the file).  That is a copyright licence, not a
trademark one: an app that is not part of the SymPy project should not
present itself as if it were - see the application id above.

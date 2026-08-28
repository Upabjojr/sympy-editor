# sympy-editor as a mobile app

Minimal native shells around **one shared web bundle**.  Nothing in the
editor is platform-specific: the Android and iOS apps display the exact page
`sympy_editor.to_html()` produces for the desktop, with KaTeX and the part of
Pyodide SymPy needs vendored so the app works offline.  No node.js toolchain.

```
mobile/
  build_www.py   -> www/   the shared bundle (index.html + vendor/, ~30 MB)
  build.py                 one command to produce .apk / .aab / .ipa
  android/                 Gradle + Kotlin: a WebView serving www/ (MainActivity.kt, ~60 lines)
  ios/                     SwiftUI + WKWebView serving www/ (EditorView.swift, ~60 lines)
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

Requirements: JDK 17 and the Android SDK (Android Studio installs both; on a
bare machine `sdkmanager "platforms;android-35" "build-tools;35.0.0"`).

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
Opening `mobile/android` in Android Studio works too: run `build_www.py
--android` first so `app/src/main/assets/www` exists.  `gradlew` is a
two-line wrapper that fetches the official wrapper jar on first use.

## 3. iOS: .ipa

Requirements: macOS with Xcode, `brew install xcodegen`, and an Apple
developer team for signing.

```bash
python mobile/build.py ios --simulator    # .app for the simulator, no signing needed
IOS_TEAM_ID=ABCDE12345 python mobile/build.py ios              # development .ipa
IOS_TEAM_ID=ABCDE12345 python mobile/build.py ios --method app-store-connect
```

Artifacts: `mobile/ios/build/ipa/SymPyEditor.ipa` (or the simulator `.app`
under `mobile/ios/build/derived/`).  The Xcode project is generated from
`project.yml`; to work in Xcode instead: `cd mobile/ios && xcodegen generate
&& open SymPyEditor.xcodeproj`.  Without XcodeGen, create an iOS App
(SwiftUI) project named SymPyEditor, add the two files in `SymPyEditor/`, and
add `mobile/www` as a *folder reference* named `www`.

## 4. Without a Mac or an Android SDK: GitHub Actions

`.github/workflows/mobile.yml` (manual trigger, or on `v*` tags) builds the
debug APK, the release APK + AAB and an iOS simulator app, and uploads them
as artifacts; with the secrets `ANDROID_KEYSTORE_BASE64` + passwords and
`IOS_TEAM_ID` it produces signed release artifacts and a `.ipa`.

## Notes

- The page renders instantly and starts Pyodide (Python in WebAssembly) in
  the background; the status line says "Loading Python runtime" for a few
  seconds after launch, then edits are immediate.
- Licences of the vendored parts are listed in `www/vendor/NOTICE.txt`
  (KaTeX MIT, Pyodide MPL-2.0, CPython PSF, SymPy/mpmath BSD).  Ship that file
  with the app (it is inside the bundle already).
- Interaction on touch screens: tap to select, tap the selected node again
  to edit it (tap a gap for a caret, again to insert), drag to select a
  range, ↑ / *Delete* / *Apply* in the toolbar; ⌨ opens the on-screen
  keyboard for the selection, the caret or the whole expression.

# sympy-editor on the desktop: the Mac app

The editor as a Mac application: the same page the phones show, editing in the
app's own CPython, in a window.  Nothing is installed and nothing is
downloaded at run time - the interpreter, SymPy and the add-ons are inside the
`.app`.

```bash
brew install xcodegen
python desktop/build.py --run        # -> desktop/macos/build/SymPyEditor.app, and it opens
```

It is the iOS app's shell, in a window: `desktop/macos/project.yml` builds the
very Swift and Objective-C files of `mobile/ios/SymPyEditor` (which carry a few
`#if os(macOS)` branches - an `NSViewRepresentable` instead of the iOS one,
`NSWorkspace` for a link, and Python's home inside the embedded framework), and
the page (`mobile/www`), the app's Python (`mobile/ios/app`) and SymPy
(`mobile/ios/app_packages`) are the folders `mobile/build.py` stages, used where
they are.  What differs from iOS is the interpreter: the macOS build of
[Python-Apple-support][pas] ships the standard library *inside*
`Python.framework`, so the app embeds the framework and there is no install
step, no `.fwork` placeholders and no per-architecture slicing - the app is
universal (arm64 and x86_64).

The build signs ad-hoc, which is all a Mac needs to run an app it built itself.
To hand it to someone else, sign it with a Developer ID certificate and
notarize it:

```bash
MACOS_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" MACOS_TEAM_ID=TEAMID \
    python desktop/build.py --zip
xcrun notarytool submit desktop/macos/build/SymPyEditor.zip --apple-id you@example.com --team-id TEAMID --wait
xcrun stapler staple desktop/macos/build/SymPyEditor.app
```

The app is sandbox-free and asks for no permissions; it keeps the hardened
runtime, with library validation off because the interpreter loads the
standard library's extension modules out of the framework it brings.

[pas]: https://github.com/beeware/Python-Apple-support

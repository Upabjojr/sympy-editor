import Foundation
import SwiftUI
import WebKit
#if os(macOS)
import AppKit
#else
import UIKit
#endif

/// What the app as a whole holds for the page, beyond one message: whether
/// the page asked for full screen (the scene hides the status bar and the
/// home indicator for it), and the files opened with the app from elsewhere
/// - a .sympy file tapped in Files, in a mail, or dropped on the Dock icon -
/// which go to the page as `SympyEditor.openText(name, text)`.
///
/// The Android app does the same in MainActivity (`applyFullscreen`,
/// `receive`): one page, whichever host it is in.
final class HostChrome: ObservableObject {
    static let shared = HostChrome()

    /// The page's own full-screen button, through `SympyEditorApp.setFullscreen`.
    @Published var fullscreen = false

    /// Files that arrived when there was no page to take them - the app was
    /// opened by the file, and its window was still to come: the first page
    /// that loads takes them (FilesBridge.pageLoaded).  On the main thread.
    private var waiting: [(String, String)] = []

    /// The largest file taken as a formula: a saved one is a few kB.
    static let maxBytes = 20 * 1024 * 1024

    /// The text of a file handed to the app - opened with it, or chosen in
    /// the panel - which is no larger than a formula can be.  Read up to the
    /// limit and one byte more, so that the limit holds for a file that does
    /// not say how long it is (one a file provider has yet to bring).  Not
    /// for the main thread: the file may be a slow one.
    static func text(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let data = try handle.read(upToCount: maxBytes + 1) ?? Data()
        if data.count > maxBytes { throw CocoaError(.fileReadTooLarge) }
        guard let text = String(data: data, encoding: .utf8) else {
            throw CocoaError(.fileReadInapplicableStringEncoding)
        }
        return text
    }

    /// A file the system asked the app to open (`onOpenURL`).
    func open(_ url: URL) {
        guard url.isFileURL else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            // A file from another app's container needs its leave to be read.
            let reachable = url.startAccessingSecurityScopedResource()
            defer { if reachable { url.stopAccessingSecurityScopedResource() } }
            do {
                let text = try HostChrome.text(of: url)
                DispatchQueue.main.async { self?.deliver(name: url.lastPathComponent, text: text) }
            } catch {
                DispatchQueue.main.async {
                    self?.front()?.reportError("The file could not be opened: \(error.localizedDescription)")
                }
            }
        }
    }

    /// The bridge of the page a file is handed to, looked for when the file
    /// is there to hand over: the window in front.  It used to be the bridge
    /// made last, held weakly - so on the Mac a file went to the newest
    /// window whichever was in front, and to nobody, without a word, once
    /// that window had been closed.  On the main thread.
    private func front() -> FilesBridge? {
        let bridges = FilesBridge.live.allObjects.filter { $0.webView != nil && !$0.isClosing }
        if let key = bridges.first(where: { $0.webView?.window?.isKeyWindow == true }) { return key }
        #if os(macOS)
        // A panel is the key window, or the app is not in front: the main
        // window then, or the one on top of the others.
        if let main = bridges.first(where: { $0.webView?.window?.isMainWindow == true }) { return main }
        for window in NSApp.orderedWindows {
            if let top = bridges.first(where: { $0.webView?.window === window }) { return top }
        }
        #endif
        return bridges.first
    }

    /// Hand a file to the page in front, or keep it for the first page there
    /// will be.  (The bridge keeps it in turn until its page has loaded.)
    private func deliver(name: String, text: String) {
        if let bridge = front() {
            bridge.deliver(name: name, text: text)
        } else {
            waiting.append((name, text))
        }
    }

    /// What arrived before there was a page, for the page that has loaded.
    func takeWaiting() -> [(String, String)] {
        let files = waiting
        waiting = []
        return files
    }
}

/// The scene's side of full screen: the status bar and (iOS 16) the home
/// indicator go while the page is in full screen.  The Mac has a window,
/// which FilesBridge takes into full screen itself.
struct HostChromeModifier: ViewModifier {
    @ObservedObject var chrome = HostChrome.shared

    func body(content: Content) -> some View {
        #if os(macOS)
        content.onOpenURL { HostChrome.shared.open($0) }
        #else
        if #available(iOS 16.0, *) {
            content
                .statusBarHidden(chrome.fullscreen)
                .persistentSystemOverlays(chrome.fullscreen ? .hidden : .automatic)
                .onOpenURL { HostChrome.shared.open($0) }
        } else {
            content
                .statusBarHidden(chrome.fullscreen)
                .onOpenURL { HostChrome.shared.open($0) }
        }
        #endif
    }
}

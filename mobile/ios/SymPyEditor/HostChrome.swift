import Foundation
import SwiftUI
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

    /// The bridge of the page that is showing, which files are handed to.
    weak var files: FilesBridge?

    /// The largest file taken as a formula: a saved one is a few kB.
    private let maxBytes = 20 * 1024 * 1024

    /// A file the system asked the app to open (`onOpenURL`).
    func open(_ url: URL) {
        guard url.isFileURL else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            // A file from another app's container needs its leave to be read.
            let reachable = url.startAccessingSecurityScopedResource()
            defer { if reachable { url.stopAccessingSecurityScopedResource() } }
            do {
                let size = (try url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
                if size > (self?.maxBytes ?? 0) { throw CocoaError(.fileReadTooLarge) }
                let text = try String(contentsOf: url, encoding: .utf8)
                DispatchQueue.main.async { self?.files?.deliver(name: url.lastPathComponent, text: text) }
            } catch {
                DispatchQueue.main.async {
                    self?.files?.reportError("The file could not be opened: \(error.localizedDescription)")
                }
            }
        }
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

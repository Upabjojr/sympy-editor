import SwiftUI
#if os(macOS)
import AppKit
#endif

@main
struct SymPyEditorApp: App {
    #if os(macOS)
    /// Quitting waits for every window's page to keep its work (AppDelegate).
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    #endif

    var body: some Scene {
        WindowGroup {
            // No native chrome, and the same edges as Android, which pads its
            // WebView with the window insets: one page, one view, whichever
            // phone it is.  What is left of the safe area is the page's own
            // business (the CSS uses env(safe-area-inset-*)).
            #if os(macOS)
            // A window, with a size to open at and one it will not go under;
            // the page inside is the same one the phones show.
            EditorView().frame(minWidth: 520, idealWidth: 1000, minHeight: 420, idealHeight: 760)
                .modifier(HostChromeModifier())
            #else
            // The status bar and the home indicator follow the page's full
            // screen; a .sympy file opened with the app goes to the page.
            EditorView().modifier(HostChromeModifier())
            #endif
        }
    }
}

#if os(macOS)
/// The Mac app's delegate, for one thing: quitting.  A session is kept a
/// moment after the edit that changed it, and keeping it is a round trip -
/// the page asks Python for the export, then hands it to FilesBridge.keepWrite.
/// Flushing from willTerminate could not finish: the app was gone before the
/// answer came back.  So the answer to "may I quit?" is "later": every
/// window's page is asked to keep what is waiting, and the app quits when
/// each of them has written (or after 1.5 s, since a page with nothing
/// waiting writes nothing).
final class AppDelegate: NSObject, NSApplicationDelegate {
    /// Whether the pending termination has been answered.
    private var answered = true

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        let bridges = FilesBridge.live.allObjects.filter { $0.webView != nil }
        if bridges.isEmpty { return .terminateNow }
        answered = false
        var waiting = bridges.count
        let finish: () -> Void = { [weak self] in
            guard let self = self, !self.answered else { return }
            self.answered = true
            sender.reply(toApplicationShouldTerminate: true)
        }
        for bridge in bridges {
            bridge.flushForKeeping {
                waiting -= 1
                if waiting == 0 { finish() }
            }
        }
        // While the answer is pending AppKit runs the loop in its modal panel
        // mode: a timer scheduled for the common modes fires there too.
        let timeout = Timer(timeInterval: 1.5, repeats: false) { _ in finish() }
        RunLoop.main.add(timeout, forMode: .common)
        return .terminateLater
    }
}
#endif

import SwiftUI

@main
struct SymPyEditorApp: App {
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
            #else
            EditorView()
            #endif
        }
    }
}

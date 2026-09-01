import SwiftUI

@main
struct SymPyEditorApp: App {
    var body: some Scene {
        WindowGroup {
            // No native chrome, and the same edges as Android, which pads its
            // WebView with the window insets: one page, one view, whichever
            // phone it is.  What is left of the safe area is the page's own
            // business (the CSS uses env(safe-area-inset-*)).
            EditorView()
        }
    }
}

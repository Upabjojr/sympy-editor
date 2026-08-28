import SwiftUI

@main
struct SymPyEditorApp: App {
    var body: some Scene {
        WindowGroup {
            EditorView()
                .ignoresSafeArea(edges: .bottom)
        }
    }
}

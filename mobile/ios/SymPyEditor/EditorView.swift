import SwiftUI
import WebKit

/// The whole app: a WKWebView showing the shared bundle (the `www` folder of
/// the app bundle, built by mobile/build_www.py).  Files are served through a
/// custom URL scheme because fetch() and WebAssembly - which Pyodide needs -
/// are not available to file:// pages.
struct EditorView: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.setURLSchemeHandler(BundleSchemeHandler(), forURLScheme: "app")
        let web = WKWebView(frame: .zero, configuration: config)
        web.allowsBackForwardNavigationGestures = false
        web.load(URLRequest(url: URL(string: "app://www/index.html")!))
        return web
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}
}

/// Serves app://www/<path> from the bundled `www` folder with proper MIME types.
final class BundleSchemeHandler: NSObject, WKURLSchemeHandler {
    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url, let base = Bundle.main.resourceURL else {
            task.didFailWithError(URLError(.badURL)); return
        }
        let relative = url.path.hasPrefix("/") ? String(url.path.dropFirst()) : url.path
        let file = base.appendingPathComponent("www").appendingPathComponent(relative)
        guard let data = try? Data(contentsOf: file) else {
            task.didFailWithError(URLError(.fileDoesNotExist)); return
        }
        let headers = ["Content-Type": mimeType(for: file.pathExtension), "Content-Length": String(data.count)]
        let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: headers)!
        task.didReceive(response)
        task.didReceive(data)
        task.didFinish()
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {}

    private func mimeType(for ext: String) -> String {
        switch ext.lowercased() {
        case "html": return "text/html; charset=utf-8"
        case "js", "mjs": return "text/javascript"
        case "css": return "text/css"
        case "wasm": return "application/wasm"
        case "json": return "application/json"
        case "woff2": return "font/woff2"
        case "woff": return "font/woff"
        case "ttf": return "font/ttf"
        case "zip", "whl": return "application/zip"
        case "svg": return "image/svg+xml"
        case "png": return "image/png"
        default: return "application/octet-stream"
        }
    }
}

import SwiftUI
import WebKit

/// The whole app: a WKWebView showing the shared bundle (the `www` folder of
/// the app bundle, built by mobile/build_www.py), and the Python the page
/// edits with.
///
/// The editing itself happens in the app's own CPython (Python.xcframework
/// and the `app`/`app_packages` folders, staged by mobile/build.py), not in
/// the browser: the page uses the "native" backend of editor.js and talks to
/// ``PythonBridge`` below, exactly as the Android app talks to its
/// MainActivity.PythonBridge.  Files are served through a custom URL scheme
/// because fetch() is not available to file:// pages.
struct EditorView: UIViewRepresentable {
    func makeCoordinator() -> PythonBridge { PythonBridge() }

    func makeUIView(context: Context) -> WKWebView {
        let bridge = context.coordinator
        let config = WKWebViewConfiguration()
        config.setURLSchemeHandler(BundleSchemeHandler(), forURLScheme: "app")
        config.userContentController.addUserScript(
            WKUserScript(source: PythonBridge.injectedScript, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController.add(bridge, name: PythonBridge.handlerName)

        let web = WKWebView(frame: .zero, configuration: config)
        web.allowsBackForwardNavigationGestures = false
        #if DEBUG
        // Safari's Web Inspector can attach to a debug build (Develop >
        // Simulator): without it a page that fails is a white rectangle.
        if #available(iOS 16.4, *) { web.isInspectable = true }
        #endif
        bridge.webView = web
        // Start the interpreter while the page loads, so the first edit does
        // not wait for it (importing SymPy takes a moment).
        bridge.warmUp()
        web.load(URLRequest(url: URL(string: "app://www/index.html")!))
        return web
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}
}

/// `window.SympyEditorPy` in the page: the native backend of editor.js hands
/// it JSON messages, each with a request id, and gets the answer back through
/// `window.__sympyEditorNative(id, ok, payload)`.  Every call returns at once
/// and is answered from the Python thread.
final class PythonBridge: NSObject, WKScriptMessageHandler {
    static let handlerName = "sympyEditorPy"

    /// The object the page finds: each method forwards its arguments to the
    /// message handler, which is all a WKWebView offers - a script message
    /// cannot return a value, and the page does not expect one.
    static let injectedScript = """
        (function () {
          function forward(method) {
            return function () {
              window.webkit.messageHandlers.\(PythonBridge.handlerName).postMessage({
                method: method, args: Array.prototype.map.call(arguments, String)
              });
            };
          }
          window.SympyEditorPy = {
            newDoc: forward("newDoc"), handle: forward("handle"), version: forward("version")
          };
        })();
        """

    /// What each method of the page's object is called in sympy_editor_app.py.
    private static let functions = ["newDoc": "new_doc", "handle": "handle", "version": "version"]

    weak var webView: WKWebView?

    private let runtime = PythonRuntime()
    /// Python runs on one thread of its own: a long computation must not
    /// block the interface, and the interpreter is entered from here only.
    private let queue = DispatchQueue(label: "org.sympy.editor.python", qos: .userInitiated)
    private var started: Result<Void, Error>?

    /// Start the interpreter, once, on the Python thread.
    func warmUp() {
        queue.async { [self] in _ = start() }
    }

    private func start() -> Result<Void, Error> {
        if let started { return started }
        let result = Result { try runtime.start() }
        started = result
        return result
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any],
              let method = body["method"] as? String,
              let function = Self.functions[method],
              let arguments = body["args"] as? [String],
              let request = arguments.first
        else { return }
        let rest = Array(arguments.dropFirst())
        queue.async { [self] in
            switch start().flatMap({ _ in Result { try runtime.call(function, arguments: rest) } }) {
            case .success(let payload): answer(request, ok: true, payload: payload)
            case .failure(let error): answer(request, ok: false, payload: error.localizedDescription)
            }
        }
    }

    /// Hand one answer back to the page.  The three values travel as JSON, so
    /// that no amount of quoting in a snapshot can break the call.
    private func answer(_ request: String, ok: Bool, payload: String) {
        guard let json = try? JSONSerialization.data(withJSONObject: [request, ok, payload]),
              let arguments = String(data: json, encoding: .utf8)
        else { return }
        DispatchQueue.main.async { [weak self] in
            self?.webView?.evaluateJavaScript("window.__sympyEditorNative.apply(null, \(arguments));")
        }
    }
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

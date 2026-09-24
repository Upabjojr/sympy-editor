import SwiftUI
import WebKit
#if os(macOS)
import AppKit
#else
import UIKit
#endif

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
/// The same view serves the Mac app (desktop/macos), which is this shell in a
/// window: a WKWebView is a WKWebView, and only the wrapper differs.
#if os(macOS)
struct EditorView: NSViewRepresentable {
    func makeCoordinator() -> PythonBridge { PythonBridge() }
    func makeNSView(context: Context) -> WKWebView { Self.webView(for: context.coordinator) }
    func updateNSView(_ view: WKWebView, context: Context) {}
}
#else
struct EditorView: UIViewRepresentable {
    func makeCoordinator() -> PythonBridge { PythonBridge() }
    func makeUIView(context: Context) -> WKWebView { Self.webView(for: context.coordinator) }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}
#endif

extension EditorView {
    /// The bundle's own origin - the only one this WebView ever navigates to
    /// - and the page it opens, which is the same bundle Android loads.
    static let scheme = "app"
    static let host = "www"
    static let start = URL(string: "app://www/index.html")!

    /// No network, ever: every http(s) and ws(s) load the page might ask for -
    /// a script, a stylesheet, a fetch, a socket - is blocked by WebKit
    /// itself, beside the navigation policy that keeps the page on app://.
    /// The bundle carries everything; the privacy statement says nothing is
    /// sent.  (WebKit's filter has no alternation: one rule per scheme.)
    static let offlineRules = """
    [{"trigger": {"url-filter": "^https?://"}, "action": {"type": "block"}},
     {"trigger": {"url-filter": "^wss?://"}, "action": {"type": "block"}}]
    """

    static func webView(for bridge: PythonBridge) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.setURLSchemeHandler(BundleSchemeHandler(), forURLScheme: Self.scheme)
        config.userContentController.addUserScript(
            WKUserScript(source: PythonBridge.injectedScript, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController.add(bridge, name: PythonBridge.handlerName)
        // The host's other half: files (see FilesBridge) - keeping a formula,
        // opening one, and sharing what the editor writes out.
        config.userContentController.addUserScript(
            WKUserScript(source: FilesBridge.injectedScript, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        config.userContentController.add(bridge.files, name: FilesBridge.handlerName)

        let web = WKWebView(frame: .zero, configuration: config)
        web.allowsBackForwardNavigationGestures = false
        web.navigationDelegate = bridge.navigation
        bridge.navigation.files = bridge.files
        #if DEBUG
        // Safari's Web Inspector can attach to a debug build (Develop >
        // Simulator, or the Mac itself): without it a page that fails is a
        // white rectangle.
        if #available(iOS 16.4, macOS 13.3, *) { web.isInspectable = true }
        #endif
        bridge.webView = web
        // Start the interpreter while the page loads, so the first edit does
        // not wait for it (importing SymPy takes a moment).
        bridge.warmUp()
        // The page loads once the rules are in: nothing it asks for may reach
        // the network, not even before they compile.
        WKContentRuleListStore.default().compileContentRuleList(
            forIdentifier: "sympy-editor-offline", encodedContentRuleList: Self.offlineRules) { list, error in
            DispatchQueue.main.async {
                if let list = list {
                    web.configuration.userContentController.add(list)
                } else {
                    NSLog("sympy-editor: the offline rules did not compile: \(String(describing: error))")
                }
                web.load(URLRequest(url: Self.start))
            }
        }
        return web
    }
}

/// The app's one Python, shared by every window: CPython is initialized once
/// per process (a second window that started one of its own never had a
/// working Python on the Mac), and it is entered from one serial queue.  A
/// long computation in one window therefore waits for the other's; the
/// Interrupt button stops only its own window's (`interrupt(<window>)`).
final class PythonHost {
    static let shared = PythonHost()

    let runtime = PythonRuntime.shared
    /// Python runs on one thread of its own: a long computation must not
    /// block the interface, and the interpreter is entered from here only.
    let queue = DispatchQueue(label: "org.sympy.editor.python", qos: .userInitiated)

    /// Set once, on the queue; read from the interrupt's thread too.
    private let lock = NSLock()
    private var started: Result<Void, Error>?

    private init() {}

    /// Start the interpreter, once, on the Python thread.
    func warmUp() {
        queue.async { [self] in _ = start() }
    }

    /// Only on `queue`.
    func start() -> Result<Void, Error> {
        lock.lock()
        let known = started
        lock.unlock()
        if let known = known { return known }
        let result = Result { try runtime.start() }
        lock.lock()
        started = result
        lock.unlock()
        return result
    }

    /// Whether Python is up (from any thread): before it is there is nothing
    /// to interrupt.
    var isRunning: Bool {
        lock.lock()
        defer { lock.unlock() }
        if case .success? = started { return true }
        return false
    }
}

/// `window.SympyEditorPy` in the page: the native backend of editor.js hands
/// it JSON messages, each with a request id, and gets the answer back through
/// `window.__sympyEditorNative(id, ok, payload)`.  Every call returns at once
/// and is answered from the Python thread.
///
/// One per window.  The page in every window starts its ids at `doc1`, and
/// all windows share one interpreter (PythonHost), so the bridge puts its
/// window's name in front of every document id it forwards (`w2/doc1`) and
/// asks `interrupt` for its own window's documents only.
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
            newDoc: forward("newDoc"), handle: forward("handle"), version: forward("version"),
            interrupt: forward("interrupt"), close: forward("close")
          };
        })();
        """

    /// What each method of the page's object is called in sympy_editor_app.py.
    private static let functions = ["newDoc": "new_doc", "handle": "handle", "version": "version",
                                    "interrupt": "interrupt", "close": "close"]
    /// The functions whose first argument (after the request id) is a document id.
    private static let takesDocument: Set<String> = ["new_doc", "handle", "close"]

    /// Windows made so far (on the main thread), which names the next one.
    private static var windows = 0

    /// This window's name, in front of its documents' ids.
    let window: String

    /// The documents this window made and has not closed, closed with it.
    private var documents = Set<String>()

    weak var webView: WKWebView? {
        didSet { files.webView = webView }
    }

    /// The other half of the host: what the page asks of the app that is not
    /// Python (files and sharing).  Kept here because this object is the one
    /// SwiftUI keeps alive, as the navigation delegate is.
    let files = FilesBridge()

    /// Kept here because a WKWebView holds its navigation delegate weakly,
    /// and this object is the one SwiftUI keeps alive.
    let navigation = BundleNavigation()

    private let host = PythonHost.shared

    override init() {
        Self.windows += 1
        window = "w\(Self.windows)"
        super.init()
    }

    deinit {
        // The window is gone: so are its documents, in the interpreter the
        // other windows go on using.
        let ids = documents
        let host = self.host
        guard !ids.isEmpty else { return }
        host.queue.async {
            guard host.isRunning else { return }
            for id in ids { _ = try? host.runtime.call("close", arguments: [id]) }
        }
    }

    /// Start the interpreter while the page loads (once in the process).
    func warmUp() { host.warmUp() }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any],
              let method = body["method"] as? String,
              let function = Self.functions[method],
              let arguments = body["args"] as? [String],
              let request = arguments.first
        else { return }
        var rest = Array(arguments.dropFirst())
        if Self.takesDocument.contains(function) {
            guard !rest.isEmpty else { answer(request, ok: false, payload: "\(method) needs a document id"); return }
            rest[0] = window + "/" + rest[0]
            if function == "new_doc" { documents.insert(rest[0]) }
            if function == "close" { documents.remove(rest[0]) }
        }
        let host = self.host
        if function == "interrupt" {
            // This window's work, not another's: all of them share the one
            // interpreter.  Not queued behind the computation it is to stop,
            // on the Python thread: from a thread of its own.
            // -call:arguments:error: takes the GIL, which that computation
            // lets go of every few milliseconds; before Python has started
            // there is nothing to stop.
            let scope = [window]
            DispatchQueue.global(qos: .userInitiated).async { [self] in
                guard host.isRunning else { answer(request, ok: true, payload: "false"); return }
                switch Result(catching: { try host.runtime.call(function, arguments: scope) }) {
                case .success(let payload): answer(request, ok: true, payload: payload)
                case .failure(let error): answer(request, ok: false, payload: error.localizedDescription)
                }
            }
            return
        }
        let forwarded = rest
        host.queue.async { [weak self] in
            let result = host.start().flatMap({ _ in Result { try host.runtime.call(function, arguments: forwarded) } })
            switch result {
            case .success(let payload): self?.answer(request, ok: true, payload: payload)
            case .failure(let error): self?.answer(request, ok: false, payload: error.localizedDescription)
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

/// Only the bundle is shown in this WebView.  The bridge above is injected
/// into whatever page it loads and evaluates what it is given, so a page from
/// anywhere else must never get it; any other link opens outside the app.
final class BundleNavigation: NSObject, WKNavigationDelegate {
    /// Told when the page has loaded: files opened with the app wait for it.
    weak var files: FilesBridge?

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        files?.pageLoaded()
    }

    /// The page's process ended (the system reclaimed its memory, or it
    /// crashed): the view is left blank and dead.  Load the page again - it
    /// opens the sessions it keeps, as at a launch.
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        files?.pageUnloaded()               // a file opened meanwhile waits for the new page
        if webView.url != nil {
            webView.reload()
        } else {
            webView.load(URLRequest(url: EditorView.start))
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == EditorView.scheme && url.host == EditorView.host {
            decisionHandler(.allow)
            return
        }
        decisionHandler(.cancel)
        #if os(macOS)
        NSWorkspace.shared.open(url)
        #else
        if UIApplication.shared.canOpenURL(url) { UIApplication.shared.open(url) }
        #endif
    }
}

/// Serves app://www/<path> from the bundled `www` folder with proper MIME types.
final class BundleSchemeHandler: NSObject, WKURLSchemeHandler {
    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url, let base = Bundle.main.resourceURL else {
            task.didFailWithError(URLError(.badURL)); return
        }
        // The bundle and nothing above it: a path of ../.. in a request must
        // not reach the rest of the app.
        let root = base.appendingPathComponent(EditorView.host).standardizedFileURL
        let relative = url.path.hasPrefix("/") ? String(url.path.dropFirst()) : url.path
        let file = root.appendingPathComponent(relative).standardizedFileURL
        guard url.host == EditorView.host,
              file.path == root.path || file.path.hasPrefix(root.path + "/"),
              let data = try? Data(contentsOf: file) else {
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

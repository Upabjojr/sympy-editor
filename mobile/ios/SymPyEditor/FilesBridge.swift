import Foundation
import WebKit
import UniformTypeIdentifiers
#if os(macOS)
import AppKit
#else
import UIKit
#endif

/// `window.SympyEditorApp` in the page: what the editor asks of the host that
/// is not Python - keeping a formula in a file, opening one, and sharing what
/// it writes out (the history as a Python script, or as a web page).
///
/// The page calls these and does not wait (a script message cannot answer);
/// opening a file is answered later, by name, through
/// `window.SympyEditor.openedFile(token, name, text)` - the token the page
/// gave when it asked.  Anything that fails is said in the page's own error
/// line (`window.SympyEditor.hostError`).
///
/// Android does the same things through MainActivity.ReportBridge, with the
/// same method names: the page knows one host, whichever it is.
final class FilesBridge: NSObject, WKScriptMessageHandler {
    static let handlerName = "sympyEditorApp"

    /// The object the page finds.  `openFile` carries the token first, as the
    /// Python bridge carries its request id.
    static let injectedScript = """
        (function () {
          function forward(method) {
            return function () {
              window.webkit.messageHandlers.\(FilesBridge.handlerName).postMessage({
                method: method, args: Array.prototype.map.call(arguments, String)
              });
            };
          }
          window.SympyEditorApp = {
            saveFile: forward("saveFile"), shareFile: forward("shareFile"),
            shareHtml: forward("shareHtml"), openFile: forward("openFile"),
            keepRead: forward("keepRead"), keepWrite: forward("keepWrite"),
            recognizeInk: forward("recognizeInk"), showKeyboard: forward("showKeyboard"),
            copyText: forward("copyText"), pasteText: forward("pasteText"), haptic: forward("haptic"),
            printHtml: forward("printHtml"), setFullscreen: forward("setFullscreen")
          };
        })();
        """

    weak var webView: WKWebView?

    /// Whether the page has loaded (BundleNavigation says so): a file opened
    /// with the app waits in `arrived` until there is a page to take it.
    private var pageReady = false
    private var arrived: [(String, String)] = []

    /// The web view a report is printed from, held until it has loaded.
    private var printer: ReportPrinter?

    override init() {
        super.init()
        HostChrome.shared.files = self
        // Leaving the foreground, where the system may end the app without
        // another word: the page keeps now what it was about to keep.
        #if os(macOS)
        let names = [NSApplication.willResignActiveNotification, NSApplication.willTerminateNotification]
        #else
        let names = [UIApplication.willResignActiveNotification, UIApplication.didEnterBackgroundNotification]
        #endif
        for name in names {
            NotificationCenter.default.addObserver(self, selector: #selector(flush), name: name, object: nil)
        }
    }

    deinit { NotificationCenter.default.removeObserver(self) }

    @objc private func flush() {
        #if os(macOS)
        webView?.evaluateJavaScript("window.SympyEditor && window.SympyEditor.flush && window.SympyEditor.flush();")
        #else
        // A moment of background time for the write to reach keepWrite.
        var task = UIBackgroundTaskIdentifier.invalid
        task = UIApplication.shared.beginBackgroundTask { UIApplication.shared.endBackgroundTask(task) }
        webView?.evaluateJavaScript("window.SympyEditor && window.SympyEditor.flush && window.SympyEditor.flush();") { _, _ in
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { UIApplication.shared.endBackgroundTask(task) }
        }
        #endif
    }

    /// The page has loaded: hand it what arrived meanwhile.
    func pageLoaded() {
        pageReady = true
        let waiting = arrived
        arrived = []
        for (name, text) in waiting { deliver(name: name, text: text) }
    }

    /// A file opened with the app from elsewhere (HostChrome.open), for the
    /// page to open in a session of its own.
    func deliver(name: String, text: String) {
        guard pageReady else { arrived.append((name, text)); return }
        webView?.evaluateJavaScript("window.SympyEditor && window.SympyEditor.openText(\(quote(name)), \(quote(text)));")
    }

    func reportError(_ message: String) { report(message) }

    /// The file the page asked to keep, until the panel or the picker is done
    /// with it (macOS answers on the spot; iOS keeps it on disk meanwhile).
    private var keeper: Keeper?

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any],
              let method = body["method"] as? String,
              let arguments = body["args"] as? [String] else { return }
        switch method {
        case "saveFile" where arguments.count >= 3:
            save(name: arguments[0], mime: arguments[1], text: arguments[2], share: false)
        case "shareFile" where arguments.count >= 3:
            save(name: arguments[0], mime: arguments[1], text: arguments[2], share: true)
        case "shareHtml" where arguments.count >= 2:
            save(name: arguments[0], mime: "text/html", text: arguments[1], share: true)
        case "openFile" where arguments.count >= 1:
            open(token: arguments[0], accept: arguments.count > 1 ? arguments[1] : "")
        case "keepRead" where arguments.count >= 2:
            keepRead(token: arguments[0], key: arguments[1])
        case "keepWrite" where arguments.count >= 2:
            keepWrite(key: arguments[0], text: arguments[1])
        case "recognizeInk" where arguments.count >= 2:
            recognizeInk(token: arguments[0], strokes: arguments[1])
        case "showKeyboard":
            showKeyboard()
        case "copyText" where arguments.count >= 1:
            copyText(arguments[0])
        case "pasteText" where arguments.count >= 1:
            pasteText(token: arguments[0])
        case "haptic":
            haptic(arguments.first ?? "select")
        case "printHtml" where arguments.count >= 2:
            printHtml(name: arguments[0], html: arguments[1])
        case "setFullscreen" where arguments.count >= 1:
            setFullscreen(arguments[0] == "true")
        default:
            break
        }
    }

    // MARK: - keeping a file

    /// A file written where the user says (`share: false`) or handed to the
    /// share sheet (`share: true`, what the history exports do).
    private func save(name: String, mime: String, text: String, share: Bool) {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(safe(name))
        do {
            try text.write(to: file, atomically: true, encoding: .utf8)
        } catch {
            report("The file could not be written: \(error.localizedDescription)")
            return
        }
        #if os(macOS)
        DispatchQueue.main.async { [weak self] in
            if share {
                guard let view = self?.webView else { return }
                NSSharingServicePicker(items: [file]).show(relativeTo: .zero, of: view, preferredEdge: .minY)
                return
            }
            let panel = NSSavePanel()
            panel.nameFieldStringValue = self?.safe(name) ?? name
            panel.canCreateDirectories = true
            panel.begin { answer in
                guard answer == .OK, let url = panel.url else { return }        // nothing chosen
                do { try text.write(to: url, atomically: true, encoding: .utf8) }
                catch { self?.report("The file could not be written: \(error.localizedDescription)") }
            }
        }
        #else
        DispatchQueue.main.async { [weak self] in
            guard let self = self, let view = self.webView else { return }
            // Saving and sharing are one panel on iOS: "Save to Files" is one
            // of the things the share sheet offers, beside sending it on.
            let sheet = UIActivityViewController(activityItems: [file], applicationActivities: nil)
            if let pop = sheet.popoverPresentationController {       // an iPad wants somewhere to point at
                pop.sourceView = view
                pop.sourceRect = CGRect(x: view.bounds.midX, y: view.bounds.maxY - 8, width: 1, height: 1)
                pop.permittedArrowDirections = [.down]
            }
            self.present(sheet, from: view)
        }
        #endif
    }

    // MARK: - opening one

    private func open(token: String, accept: String) {
        #if os(macOS)
        DispatchQueue.main.async { [weak self] in
            let panel = NSOpenPanel()
            panel.allowsMultipleSelection = false
            panel.canChooseDirectories = false
            panel.allowedContentTypes = Self.types(for: accept)
            panel.begin { answer in
                guard answer == .OK, let url = panel.url else { self?.answer(token, nil, nil); return }
                self?.read(url, token: token)
            }
        }
        #else
        DispatchQueue.main.async { [weak self] in
            guard let self = self, let view = self.webView else { return }
            let picker = UIDocumentPickerViewController(forOpeningContentTypes: Self.types(for: accept))
            picker.allowsMultipleSelection = false
            let keeper = Keeper(token: token, bridge: self)
            self.keeper = keeper
            picker.delegate = keeper
            self.present(picker, from: view)
        }
        #endif
    }

    /// Read a file the user chose and hand it to the page.
    fileprivate func read(_ url: URL, token: String) {
        // A file outside the app needs its owner's leave to be read.
        let reachable = url.startAccessingSecurityScopedResource()
        defer { if reachable { url.stopAccessingSecurityScopedResource() } }
        do {
            let text = try String(contentsOf: url, encoding: .utf8)
            answer(token, url.lastPathComponent, text)
        } catch {
            answer(token, nil, nil)
            report("The file could not be read: \(error.localizedDescription)")
        }
    }

    /// What the page takes: a saved formula is JSON, and a formula typed into
    /// a plain file opens too.
    private static func types(for accept: String) -> [UTType] {
        var types: [UTType] = [.json, .plainText, .text, .data]
        if let own = UTType("org.sympy.editor.formula") { types.insert(own, at: 0) }
        _ = accept
        return types
    }

    // MARK: - the keyboard

    /// Bring the keyboard up for a field the page has just opened.  A
    /// WKWebView shows it when a field is focused in answer to a tap; this
    /// makes sure the web view is the responder, for a field the page put
    /// there by script (the LaTeX add-on opens its field in the formula).
    private func showKeyboard() {
        #if !os(macOS)
        DispatchQueue.main.async { [weak self] in
            guard let view = self?.webView, !view.isFirstResponder else { return }
            view.becomeFirstResponder()
        }
        #endif
    }

    // MARK: - the clipboard, the hand, the printer, the screen

    /// The system pasteboard, which every other app reads.
    private func copyText(_ text: String) {
        DispatchQueue.main.async {
            #if os(macOS)
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
            #else
            UIPasteboard.general.string = text
            #endif
        }
    }

    /// What the pasteboard holds, as text, answered through
    /// `SympyEditor.hostAnswer`.  iOS asks the user the first time the app
    /// reads what another app copied (Settings can make it "Allow"), which
    /// is still one question where the page's own reading asked every time.
    private func pasteText(token: String) {
        DispatchQueue.main.async { [weak self] in
            #if os(macOS)
            let text = NSPasteboard.general.string(forType: .string)
            #else
            let text = UIPasteboard.general.hasStrings ? UIPasteboard.general.string : nil
            #endif
            self?.answerHost(token, text)
        }
    }

    /// A touch the hand feels: a long press that selected.
    private func haptic(_ kind: String) {
        DispatchQueue.main.async {
            #if os(macOS)
            NSHapticFeedbackManager.defaultPerformer.perform(.generic, performanceTime: .now)
            #else
            let generator = UIImpactFeedbackGenerator(style: kind == "select" ? .medium : .light)
            generator.impactOccurred()
            #endif
        }
    }

    /// Print the history report, or keep it as a PDF (both platforms' print
    /// panels offer that).
    private func printHtml(name: String, html: String) {
        DispatchQueue.main.async { [weak self] in
            guard let self = self, let view = self.webView else { return }
            let printer = ReportPrinter(name: name.isEmpty ? "SymPy history" : name, html: html, over: view) { [weak self] error in
                if let error = error { self?.report(error) }
                self?.printer = nil
            }
            self.printer = printer
            printer.start()
        }
    }

    /// Full screen for real: the scene hides the status bar and the home
    /// indicator (HostChrome); on the Mac the window goes full screen.
    private func setFullscreen(_ on: Bool) {
        DispatchQueue.main.async { [weak self] in
            HostChrome.shared.fullscreen = on
            #if os(macOS)
            guard let window = self?.webView?.window else { return }
            if window.styleMask.contains(.fullScreen) != on { window.toggleFullScreen(nil) }
            #else
            _ = self
            #endif
        }
    }

    // MARK: - what the page keeps

    /// Where the page's own things live: Application Support, which is the
    /// app's to keep and a backup carries - not the WebView's localStorage,
    /// which the system may clear at any time.  A session is the user's work.
    private func keepURL(for key: String) throws -> URL {
        let safe = key.components(separatedBy: CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "._-")).inverted)
            .joined(separator: "_")
        let manager = FileManager.default
        let support = try manager.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                      appropriateFor: nil, create: true)
        let directory = support.appendingPathComponent("SymPyEditor/keep", isDirectory: true)
        try manager.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appendingPathComponent((safe.isEmpty ? "keep" : safe) + ".json")
    }

    /// What was kept under `key`, back to the page that asked for it.
    private func keepRead(token: String, key: String) {
        var text: String?
        do {
            let url = try keepURL(for: key)
            text = FileManager.default.fileExists(atPath: url.path) ? try String(contentsOf: url, encoding: .utf8) : nil
        } catch {
            report("What was kept could not be read: \(error.localizedDescription)")
            text = nil
        }
        let call: String
        if let text = text {
            call = "window.SympyEditor.keptValue(\(quote(token)), \(quote(text)));"
        } else {
            call = "window.SympyEditor.keptValue(\(quote(token)));"
        }
        DispatchQueue.main.async { [weak self] in self?.webView?.evaluateJavaScript(call, completionHandler: nil) }
    }

    /// Keep `text` under `key`, written atomically so that an interruption
    /// leaves what was there before.
    private func keepWrite(key: String, text: String) {
        do {
            try text.write(to: try keepURL(for: key), atomically: true, encoding: .utf8)
        } catch {
            report("What the editor keeps could not be written: \(error.localizedDescription)")
        }
    }

    // MARK: - reading handwriting with this device's own reader

    /// What the handwriting add-on's "host" engine asks for: the strokes read
    /// by Apple's Vision (see InkReader), answered through
    /// `SympyEditor.inkRead`.  It reads text, not mathematical layout - the
    /// add-on says as much beside the engine's name.
    private func recognizeInk(token: String, strokes: String) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var answer: [String: Any]
            do {
                answer = ["candidates": try InkReader.candidates(from: strokes)]
            } catch {
                answer = ["error": error.localizedDescription]
            }
            let json = (try? JSONSerialization.data(withJSONObject: answer))
                .flatMap { String(data: $0, encoding: .utf8) } ?? "{\"error\": \"the reading could not be sent\"}"
            guard let self = self else { return }
            let call = "window.SympyEditor.inkRead(\(self.quote(token)), \(self.quote(json)));"
            DispatchQueue.main.async { self.webView?.evaluateJavaScript(call, completionHandler: nil) }
        }
    }

    // MARK: - talking back to the page

    /// Answer a question of the page's (`Host.ask` in editor.js).
    private func answerHost(_ token: String, _ value: String?) {
        let call = value.map { "window.SympyEditor && window.SympyEditor.hostAnswer(\(quote(token)), \(quote($0)));" }
            ?? "window.SympyEditor && window.SympyEditor.hostAnswer(\(quote(token)));"
        DispatchQueue.main.async { [weak self] in self?.webView?.evaluateJavaScript(call, completionHandler: nil) }
    }

    fileprivate func answer(_ token: String, _ name: String?, _ text: String?) {
        let call: String
        if let text = text {
            call = "window.SympyEditor.openedFile(\(quote(token)), \(quote(name ?? "")), \(quote(text)));"
        } else {
            call = "window.SympyEditor.openedFile(\(quote(token)));"
        }
        DispatchQueue.main.async { [weak self] in self?.webView?.evaluateJavaScript(call, completionHandler: nil) }
    }

    fileprivate func report(_ message: String) {
        let call = "window.SympyEditor && window.SympyEditor.hostError && "
            + "window.SympyEditor.hostError(\(quote(message)));"
        DispatchQueue.main.async { [weak self] in self?.webView?.evaluateJavaScript(call, completionHandler: nil) }
    }

    /// A JavaScript string literal, whatever the text holds.
    private func quote(_ text: String) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: [text], options: []),
              let array = String(data: data, encoding: .utf8) else { return "\"\"" }
        return String(array.dropFirst().dropLast())        // ["…"] -> "…"
    }

    /// A file name with nothing in it that a file system would refuse.
    fileprivate func safe(_ name: String) -> String {
        let cleaned = name.components(separatedBy: CharacterSet(charactersIn: "/\\:*?\"<>|\n\t")).joined(separator: "_")
        return cleaned.isEmpty ? "formula.sympy" : cleaned
    }

    #if !os(macOS)
    /// The view controller the page's WebView is in, which is what presents.
    private func present(_ controller: UIViewController, from view: UIView) {
        var responder: UIResponder? = view
        while let next = responder?.next {
            if let host = next as? UIViewController {
                host.present(controller, animated: true)
                return
            }
            responder = next
        }
        report("The panel could not be opened")
    }
    #endif

    /// Holds on to the picker's delegate while a picker is up (UIKit keeps it
    /// weakly), and turns what it says into an answer for the page.
    fileprivate final class Keeper: NSObject {
        let token: String
        weak var bridge: FilesBridge?

        init(token: String, bridge: FilesBridge) {
            self.token = token
            self.bridge = bridge
        }
    }
}

#if !os(macOS)
extension FilesBridge.Keeper: UIDocumentPickerDelegate {
    func documentPicker(_ picker: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        guard let url = urls.first else { bridge?.answer(token, nil, nil); return }
        bridge?.read(url, token: token)
    }

    func documentPickerWasCancelled(_ picker: UIDocumentPickerViewController) {
        bridge?.answer(token, nil, nil)            // nothing chosen: the page stops waiting
    }
}
#endif

/// Prints the history report from a web view of its own, made for it: no
/// scripts, no bridge, going nowhere - the report is static and carries its
/// fonts.  It lives until the page has loaded and gone to the print panel.
final class ReportPrinter: NSObject, WKNavigationDelegate {
    private let name: String
    private let html: String
    private weak var over: WKWebView?
    private let done: (String?) -> Void
    private var web: WKWebView?

    init(name: String, html: String, over: WKWebView, done: @escaping (String?) -> Void) {
        self.name = name
        self.html = html
        self.over = over
        self.done = done
        super.init()
    }

    func start() {
        let config = WKWebViewConfiguration()
        if #available(iOS 14.0, macOS 11.0, *) {
            config.defaultWebpagePreferences.allowsContentJavaScript = false
        }
        let web = WKWebView(frame: CGRect(x: 0, y: 0, width: 800, height: 1100), configuration: config)
        web.navigationDelegate = self
        self.web = web
        web.loadHTMLString(html, baseURL: nil)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // The document itself, and nothing it links to.
        decisionHandler(navigationAction.navigationType == .other ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        #if os(macOS)
        let operation = webView.printOperation(with: NSPrintInfo.shared)
        operation.jobTitle = name
        // A web view's print operation needs a view with a size to lay out in.
        operation.view?.frame = webView.bounds
        if let window = over?.window {
            operation.runModal(for: window, delegate: nil, didRun: nil, contextInfo: nil)
        } else {
            operation.run()
        }
        done(nil)
        #else
        let info = UIPrintInfo(dictionary: nil)
        info.jobName = name
        info.outputType = .general
        let controller = UIPrintInteractionController.shared
        controller.printInfo = info
        controller.printFormatter = webView.viewPrintFormatter()
        let finished: UIPrintInteractionController.CompletionHandler = { [weak self] _, _, error in
            self?.done(error.map { "The report could not be printed: \($0.localizedDescription)" })
        }
        if UIDevice.current.userInterfaceIdiom == .pad, let view = over {
            // An iPad shows the panel as a popover, which wants somewhere to point at.
            controller.present(from: CGRect(x: view.bounds.midX, y: view.bounds.maxY - 8, width: 1, height: 1),
                               in: view, animated: true, completionHandler: finished)
        } else {
            controller.present(animated: true, completionHandler: finished)
        }
        #endif
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        done("The report could not be printed: \(error.localizedDescription)")
    }
}

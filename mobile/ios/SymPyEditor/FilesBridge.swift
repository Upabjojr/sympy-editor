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
            keepRead: forward("keepRead"), keepWrite: forward("keepWrite")
          };
        })();
        """

    weak var webView: WKWebView?

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

    // MARK: - talking back to the page

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

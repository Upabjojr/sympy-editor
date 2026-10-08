import Foundation
import Vision
#if os(macOS)
import AppKit
#else
import UIKit
#endif

/// This device's own reader of handwriting, offered to the page as one of the
/// handwriting add-on's engines (`window.SympyEditorApp.recognizeInk`).
///
/// It is Apple's Vision, which reads **text**: the strokes are drawn into an
/// image and `VNRecognizeTextRequest` says what is written there, a line at a
/// time.  It knows nothing of mathematical layout - a fraction comes back as
/// two lines, an exponent as another character beside the base - so it is not
/// a replacement for math-ocr's stroke model, which reads the layout.  It is
/// here for what it is good at: a device that has no model beside it can
/// still take `x + 1` written by hand, and the LaTeX reader turns the text
/// into SymPy as if it had been typed.
///
/// The answer goes back the way a file does: `SympyEditor.inkRead(token,
/// json)` with `{"candidates": [{"latex": "…"}]}`, or `{"error": "…"}`.
enum InkReader {
    /// How large the ink is drawn before it is read: Vision wants something
    /// of a photograph's size, and a line of writing scaled to about this
    /// height is read far better than the same ink at its own size.
    private static let targetHeight: CGFloat = 220
    private static let margin: CGFloat = 40

    /// Strokes as the page sends them: `[[[x, y, t], …], …]`.
    static func candidates(from json: String, limit: Int = 4) throws -> [[String: Any]] {
        guard let data = json.data(using: .utf8),
              let strokes = try JSONSerialization.jsonObject(with: data) as? [[[Double]]],
              !strokes.isEmpty else {
            throw Failure.nothingWritten
        }
        guard let image = draw(strokes) else { throw Failure.nothingWritten }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        // What is written is a formula, not prose: a language model would
        // "correct" x + 1 into something it likes better.
        request.usesLanguageCorrection = false
        if #available(iOS 16.0, macOS 13.0, *) {
            request.automaticallyDetectsLanguage = false
        }
        request.recognitionLanguages = ["en-US"]

        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try handler.perform([request])

        // One line of writing is what the page wants; several lines are joined
        // in the order they were written, which is the order Vision returns.
        let observations = request.results ?? []
        var readings: [String] = []
        for count in [1, 2, 3] where readings.count < limit {
            let line = observations.compactMap { $0.topCandidates(count).last?.string }.joined(separator: " ")
            let text = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty && !readings.contains(text) {
                readings.append(text)
            }
        }
        if readings.isEmpty { throw Failure.nothingRead }
        return readings.map { ["latex": $0, "raw": $0] }
    }

    /// The strokes drawn in black on white, scaled and padded: what Vision
    /// reads is an image, and this is the image of what was written.
    private static func draw(_ strokes: [[[Double]]]) -> CGImage? {
        var minX = Double.greatestFiniteMagnitude, minY = Double.greatestFiniteMagnitude
        var maxX = -Double.greatestFiniteMagnitude, maxY = -Double.greatestFiniteMagnitude
        for stroke in strokes {
            for point in stroke where point.count >= 2 {
                minX = min(minX, point[0]); maxX = max(maxX, point[0])
                minY = min(minY, point[1]); maxY = max(maxY, point[1])
            }
        }
        guard maxX > minX || maxY > minY else { return nil }
        let scale = max(1.0, Double(targetHeight) / max(1.0, maxY - minY))
        let width = Int((maxX - minX) * scale + Double(margin) * 2)
        let height = Int((maxY - minY) * scale + Double(margin) * 2)
        guard width > 0, height > 0, width < 8000, height < 8000 else { return nil }

        let space = CGColorSpaceCreateDeviceGray()
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                      bytesPerRow: 0, space: space,
                                      bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return nil }
        context.setFillColor(gray: 1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        context.setStrokeColor(gray: 0, alpha: 1)
        context.setLineWidth(max(3, CGFloat(scale) * 2.2))
        context.setLineCap(.round)
        context.setLineJoin(.round)
        for stroke in strokes where stroke.count > 0 {
            var started = false
            for point in stroke where point.count >= 2 {
                // The page's y grows downwards, a bitmap's upwards.
                let x = CGFloat((point[0] - minX) * scale) + margin
                let y = CGFloat(height) - (CGFloat((point[1] - minY) * scale) + margin)
                if started { context.addLine(to: CGPoint(x: x, y: y)) } else { context.move(to: CGPoint(x: x, y: y)); started = true }
            }
            if stroke.count == 1 {          // a dot: a line to itself draws nothing
                context.addLine(to: context.currentPointOfPath.applying(CGAffineTransform(translationX: 0.1, y: 0)))
            }
            context.strokePath()
        }
        return context.makeImage()
    }

    enum Failure: LocalizedError {
        case nothingWritten
        case nothingRead

        var errorDescription: String? {
            switch self {
            case .nothingWritten: return "Nothing is written yet"
            case .nothingRead: return "This device's reader made nothing of it"
            }
        }
    }
}

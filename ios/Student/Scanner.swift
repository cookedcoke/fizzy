import SwiftUI
import VisionKit

/// Reads a QR code with the camera. `read` turns what the camera saw into what's wanted, or nil to
/// keep looking (a sign-in code wants its token; a challenge takes any code, and the server decides).
struct CodeScanner: View {
    var title = "Scan a code"
    var read: (String) -> String? = { $0 }
    let found: (String) -> Void

    /// A sign-in code from the counter is a link ending in /q/<token>.
    static func signInToken(_ text: String) -> String? {
        guard let url = URL(string: text), url.pathComponents.count >= 2,
              url.pathComponents[url.pathComponents.count - 2] == "q" else { return nil }
        return url.lastPathComponent
    }
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
                    ScannerView(read: read, found: found).ignoresSafeArea()
                } else {
                    ContentUnavailableView("Can't use the camera", systemImage: "camera.fill",
                                           description: Text("Allow camera access for My CCC in Settings."))
                }
            }
            .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
    }
}

private struct ScannerView: UIViewControllerRepresentable {
    let read: (String) -> String?
    let found: (String) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let vc = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])], qualityLevel: .fast,
                                           isHighlightingEnabled: true)
        vc.delegate = context.coordinator
        try? vc.startScanning()
        return vc
    }

    func updateUIViewController(_ vc: DataScannerViewController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(read: read, found: found) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let read: (String) -> String?
        let found: (String) -> Void
        private var done = false
        init(read: @escaping (String) -> String?, found: @escaping (String) -> Void) { self.read = read; self.found = found }

        func dataScanner(_ scanner: DataScannerViewController, didAdd items: [RecognizedItem], allItems: [RecognizedItem]) {
            for case .barcode(let code) in items {
                guard !done, let text = code.payloadStringValue, let value = read(text) else { continue }
                done = true
                scanner.stopScanning()
                found(value)
            }
        }
    }
}

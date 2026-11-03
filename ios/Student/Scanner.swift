import AVFoundation
import SwiftUI
import UIKit

/// Reads a QR code with the camera. `read` turns what the camera saw into what's wanted, or nil to
/// keep looking (a sign-in code wants its token; a challenge takes any code, and the server decides).
///
/// Our own camera rather than VisionKit's scanner: it runs at 4K on the main lens zoomed to 2×, so a
/// poster reads from across a room, with a zoom picker, pinch, the torch, and macro when very close.
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
    @State private var camera = Camera()
    @State private var pinchFrom: CGFloat?

    var body: some View {
        NavigationStack {
            Group {
                if camera.denied {
                    ContentUnavailableView("Can't use the camera", systemImage: "camera.fill",
                                           description: Text("Allow camera access in Settings, then try again."))
                } else {
                    CameraPreview(session: camera.session)
                        .ignoresSafeArea()
                        .gesture(MagnifyGesture()
                            .onChanged { g in
                                if pinchFrom == nil { pinchFrom = camera.zoom }
                                camera.setZoom((pinchFrom ?? 1) * g.magnification)
                            }
                            .onEnded { _ in pinchFrom = nil })
                        .overlay(alignment: .bottom) { controls }
                }
            }
            .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                if camera.hasTorch {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Torch", systemImage: camera.torch ? "flashlight.on.fill" : "flashlight.off.fill") { camera.toggleTorch() }
                    }
                }
            }
        }
        .sensoryFeedback(.success, trigger: camera.reads)
        .task {
            camera.read = read
            camera.found = found
            await camera.start()
        }
        .onDisappear { camera.stop() }
    }

    private var controls: some View {
        VStack(spacing: 12) {
            Text("Point at the code. Far away? Zoom in.")
                .font(.subheadline).padding(.horizontal, 12).padding(.vertical, 6)
                .background(.thinMaterial, in: Capsule())
            Picker("Zoom", selection: Binding(get: { Camera.presets.min { abs($0 - camera.zoom) < abs($1 - camera.zoom) } ?? 1 },
                                              set: { camera.setZoom($0) })) {
                ForEach(Camera.presets, id: \.self) { Text("\(Int($0))×").tag($0) }
            }
            .pickerStyle(.segmented).frame(width: 180)
        }
        .padding(.bottom, 24)
    }
}

/// The capture session. `zoom` is as the Camera app counts it: 1× is the main lens, whatever the phone.
@Observable
final class Camera: NSObject, AVCaptureMetadataOutputObjectsDelegate, @unchecked Sendable {
    static let presets: [CGFloat] = [1, 2, 3]

    @ObservationIgnored let session = AVCaptureSession()
    @ObservationIgnored var read: (String) -> String? = { $0 }
    @ObservationIgnored var found: (String) -> Void = { _ in }
    var denied = false
    var zoom: CGFloat = 2
    var torch = false
    var hasTorch = false
    var reads = 0

    @ObservationIgnored private var device: AVCaptureDevice?
    /// The zoom factor where a multi-lens camera hands over from ultra wide to the main lens.
    @ObservationIgnored private var mainLens: CGFloat = 1
    @ObservationIgnored private var done = false
    @ObservationIgnored private let queue = DispatchQueue(label: "co.cookedcoke.scanner")

    @MainActor
    func start() async {
        if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
            _ = await AVCaptureDevice.requestAccess(for: .video)
        }
        guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else { denied = true; return }
        // A virtual multi-lens camera, so it moves to the ultra wide (macro) by itself when held close.
        let types: [AVCaptureDevice.DeviceType] = [.builtInTripleCamera, .builtInDualWideCamera, .builtInDualCamera, .builtInWideAngleCamera]
        guard let device = AVCaptureDevice.DiscoverySession(deviceTypes: types, mediaType: .video, position: .back).devices.first,
              let input = try? AVCaptureDeviceInput(device: device) else { denied = true; return }
        self.device = device
        mainLens = device.deviceType == .builtInDualCamera ? 1 : (device.virtualDeviceSwitchOverVideoZoomFactors.first.map { CGFloat(truncating: $0) } ?? 1)
        hasTorch = device.hasTorch

        let output = AVCaptureMetadataOutput()
        session.beginConfiguration()
        session.sessionPreset = session.canSetSessionPreset(.hd4K3840x2160) ? .hd4K3840x2160 : .hd1920x1080
        if session.canAddInput(input) { session.addInput(input) }
        if session.canAddOutput(output) { session.addOutput(output) }
        output.setMetadataObjectsDelegate(self, queue: .main)
        output.metadataObjectTypes = output.availableMetadataObjectTypes.contains(.qr) ? [.qr] : []
        session.commitConfiguration()

        try? device.lockForConfiguration()
        if device.isFocusModeSupported(.continuousAutoFocus) { device.focusMode = .continuousAutoFocus }
        if device.isSmoothAutoFocusSupported { device.isSmoothAutoFocusEnabled = false }   // snap to focus, don't glide
        device.unlockForConfiguration()
        setZoom(zoom)

        queue.async { [session] in session.startRunning() }
    }

    func stop() {
        queue.async { [session] in session.stopRunning() }
    }

    func setZoom(_ z: CGFloat) {
        guard let device else { return }
        let factor = min(max(z * mainLens, device.minAvailableVideoZoomFactor), min(device.maxAvailableVideoZoomFactor, mainLens * 8))
        zoom = factor / mainLens
        try? device.lockForConfiguration()
        device.videoZoomFactor = factor
        device.unlockForConfiguration()
    }

    func toggleTorch() {
        guard let device, device.hasTorch else { return }
        try? device.lockForConfiguration()
        device.torchMode = torch ? .off : .on
        device.unlockForConfiguration()
        torch.toggle()
    }

    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) {
        for case let code as AVMetadataMachineReadableCodeObject in objects {
            guard !done, let text = code.stringValue, let value = read(text) else { continue }
            done = true
            reads += 1
            stop()
            found(value)
        }
    }
}

private struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    final class Preview: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var preview: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }

    func makeUIView(context: Context) -> Preview {
        let view = Preview()
        view.backgroundColor = .black
        view.preview.session = session
        view.preview.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ view: Preview, context: Context) {}
}

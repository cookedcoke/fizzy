import AVFoundation
import SwiftUI
import UIKit

/// Reads a QR code with the camera. `read` turns what the camera saw into what's wanted, or nil to
/// keep looking (a sign-in code wants its token; a challenge takes any code, and the server decides).
///
/// Our own camera rather than VisionKit's scanner: it runs at 4K on the main lens zoomed to 2×, so a
/// poster reads from across a room, with a zoom picker, pinch, the torch, and macro when very close.
/// TileReader also reads codes packed edge to edge with no border, which no ordinary reader can.
struct CodeScanner: View {
    var title = "Scan a code"
    /// What to fit inside the corner guides: a code on its own, or a whole hunt poster (A4, upright).
    var guide = Guide.code
    var read: (String) -> String? = { $0 }
    let found: (String) -> Void

    enum Guide { case code, poster }

    /// A sign-in code from the counter is a link ending in /q/<token>.
    static func signInToken(_ text: String) -> String? {
        guard let url = URL(string: text), url.pathComponents.count >= 2,
              url.pathComponents[url.pathComponents.count - 2] == "q" else { return nil }
        return url.lastPathComponent
    }
    @Environment(\.dismiss) private var dismiss
    /// Made once the sheet is up (in .task), not each time a parent redraws: a capture session isn't cheap.
    @State private var camera: Camera?
    @State private var pinchFrom: CGFloat?

    var body: some View {
        NavigationStack {
            Group {
                if let camera {
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
                            .overlay { guides }
                            .overlay(alignment: .bottom) { controls(camera) }
                    }
                } else {
                    Color.black.ignoresSafeArea()
                }
            }
            .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                if let camera, camera.hasTorch {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Torch", systemImage: camera.torch ? "flashlight.on.fill" : "flashlight.off.fill") { camera.toggleTorch() }
                    }
                }
            }
        }
        .sensoryFeedback(.success, trigger: camera?.reads ?? 0)
        .task {
            let camera = Camera(posters: guide == .poster)   // a fresh one each time it's shown: a stopped session stays stopped
            camera.read = read
            camera.found = found
            self.camera = camera
            await camera.start()
        }
        .onDisappear { camera?.stop() }
    }

    /// Corners to line the code or poster up in. Only a guide: anything in the frame still reads.
    private var guides: some View {
        GeometryReader { geo in
            // a poster: as big as fits above the controls, at A4's shape
            let posterWidth = min(geo.size.width * 0.8, geo.size.height * 0.6 / 1.414)
            let size = guide == .poster ? CGSize(width: posterWidth, height: posterWidth * 1.414)
                                        : CGSize(width: geo.size.width * 0.65, height: geo.size.width * 0.65)
            Corners()
                .stroke(.white, style: StrokeStyle(lineWidth: 5, lineCap: .round, lineJoin: .round))
                .shadow(color: .black.opacity(0.4), radius: 3)
                .frame(width: size.width, height: size.height)
                .position(x: geo.size.width / 2, y: geo.size.height * 0.45)
        }
        .allowsHitTesting(false)
    }

    private func controls(_ camera: Camera) -> some View {
        VStack(spacing: 12) {
            Text(guide == .poster ? "Fit the whole poster inside the corners." : "Fit the code inside the corners.")
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
/// The session is set up, started and stopped on its own queue, never the main one.
@Observable
final class Camera: NSObject, AVCaptureMetadataOutputObjectsDelegate, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    static let presets: [CGFloat] = [1, 2, 3]

    @ObservationIgnored let session = AVCaptureSession()
    @ObservationIgnored var read: (String) -> String? = { $0 }
    @ObservationIgnored var found: (String) -> Void = { _ in }
    var denied = false
    var zoom: CGFloat = 1
    var torch = false
    var hasTorch = false
    var reads = 0

    /// Whole hunt posters: TileReader runs on frames too, for their packed codes. Off for a single code,
    /// which the camera's own reader manages.
    @ObservationIgnored private let posters: Bool
    @ObservationIgnored private var device: AVCaptureDevice?
    /// The zoom factor where a multi-lens camera hands over from ultra wide to the main lens.
    @ObservationIgnored private var mainLens: CGFloat = 1
    @ObservationIgnored private var done = false
    @ObservationIgnored private let queue = DispatchQueue(label: "co.cookedcoke.scanner")
    /// On `queue`: stop() has been asked for, so a start still waiting there mustn't run the camera.
    @ObservationIgnored private var stopped = false
    /// Frames for TileReader, for codes packed with no white border round them (the hunt posters).
    @ObservationIgnored private let frames = DispatchQueue(label: "co.cookedcoke.scanner.frames", qos: .userInitiated)
    /// On `frames`: when TileReader last started on a frame.
    @ObservationIgnored private var lastTile: CFTimeInterval = 0
    /// On `frames`: TileReader's last answer. It can make a code out of noise (a dark desk, say), so an
    /// answer only counts once two frames in a row agree.
    @ObservationIgnored private var lastTileText: String?

    init(posters: Bool) {
        self.posters = posters
        super.init()
    }

    @MainActor
    func start() async {
        if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
            _ = await AVCaptureDevice.requestAccess(for: .video)
        }
        guard !Task.isCancelled, !done else { return }   // closed while the permission question was up
        guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else { denied = true; return }
        // A virtual multi-lens camera, so it moves to the ultra wide (macro) by itself when held close.
        let types: [AVCaptureDevice.DeviceType] = [.builtInTripleCamera, .builtInDualWideCamera, .builtInDualCamera, .builtInWideAngleCamera]
        guard let device = AVCaptureDevice.DiscoverySession(deviceTypes: types, mediaType: .video, position: .back).devices.first,
              let input = try? AVCaptureDeviceInput(device: device) else { denied = true; return }
        self.device = device
        let mainLens = device.deviceType == .builtInDualCamera ? 1 : (device.virtualDeviceSwitchOverVideoZoomFactors.first.map { CGFloat(truncating: $0) } ?? 1)
        self.mainLens = mainLens
        hasTorch = device.hasTorch
        let wanted = zoom

        queue.async { [self] in
            guard !stopped else { return }
            let output = AVCaptureMetadataOutput()
            session.beginConfiguration()
            if session.canAddInput(input) { session.addInput(input) }
            // After the camera is attached: before that, nothing can do 4K and it quietly stays at 1080p.
            session.sessionPreset = session.canSetSessionPreset(.hd4K3840x2160) ? .hd4K3840x2160 : .hd1920x1080
            if session.canAddOutput(output) { session.addOutput(output) }
            output.setMetadataObjectsDelegate(self, queue: .main)
            output.metadataObjectTypes = output.availableMetadataObjectTypes.contains(.qr) ? [.qr] : []
            if posters {
                let video = AVCaptureVideoDataOutput()
                video.alwaysDiscardsLateVideoFrames = true   // only ever the latest frame, never a queue of old ones
                video.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarFullRange]
                video.setSampleBufferDelegate(self, queue: frames)
                if session.canAddOutput(video) { session.addOutput(video) }
            }
            session.commitConfiguration()

            // The zoom after the preset: choosing a format puts it back to 1×.
            let factor = Self.factor(wanted, device, mainLens)
            do {
                try device.lockForConfiguration()
                if device.isFocusModeSupported(.continuousAutoFocus) { device.focusMode = .continuousAutoFocus }
                if device.isSmoothAutoFocusSupported { device.isSmoothAutoFocusEnabled = false }   // snap to focus, don't glide
                device.videoZoomFactor = factor
                device.unlockForConfiguration()
                DispatchQueue.main.async { self.zoom = factor / mainLens }
            } catch {
                // Someone else has the camera: it still reads, just without our focus and zoom.
            }
            session.startRunning()
        }
    }

    func stop() {
        queue.async { [self] in
            stopped = true
            session.stopRunning()
        }
    }

    /// The device's zoom factor for a Camera-app zoom, within what this camera can do (and 8× the main lens at most).
    private static func factor(_ z: CGFloat, _ device: AVCaptureDevice, _ mainLens: CGFloat) -> CGFloat {
        min(max(z * mainLens, device.minAvailableVideoZoomFactor), min(device.maxAvailableVideoZoomFactor, mainLens * 8))
    }

    func setZoom(_ z: CGFloat) {
        guard let device else { return }
        let factor = Self.factor(z, device, mainLens)
        do {
            try device.lockForConfiguration()
        } catch {
            return
        }
        device.videoZoomFactor = factor
        device.unlockForConfiguration()
        zoom = factor / mainLens
    }

    func toggleTorch() {
        guard let device, device.hasTorch else { return }
        let mode: AVCaptureDevice.TorchMode = torch ? .off : .on
        guard device.isTorchModeSupported(mode) else { return }
        do {
            try device.lockForConfiguration()
        } catch {
            return
        }
        device.torchMode = mode
        device.unlockForConfiguration()
        torch = mode == .on
    }

    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) {
        for case let code as AVMetadataMachineReadableCodeObject in objects {
            if let text = code.stringValue { use(text) }
        }
    }

    /// The camera's own reader needs a white border round a code; this reads the hunt posters' packed
    /// codes, a few frames a second (late frames are dropped, and so are frames between reads).
    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer, from connection: AVCaptureConnection) {
        let now = CACurrentMediaTime()
        // About ten a second, but straight on to the next frame once one has read something, to confirm it fast.
        guard lastTileText != nil || now - lastTile >= 0.1, let buffer = CMSampleBufferGetImageBuffer(sample) else { return }
        lastTile = now
        let text = TileReader.read(buffer)
        defer { lastTileText = text }
        guard let text, text == lastTileText else { return }
        DispatchQueue.main.async { self.use(text) }
    }

    /// On the main queue: both readers hand their codes over there.
    private func use(_ text: String) {
        guard !done, let value = read(text) else { return }
        done = true
        reads += 1
        stop()
        found(value)
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

/// Four L-shaped corners round a rectangle, like the Camera app's code guide.
private struct Corners: Shape {
    func path(in r: CGRect) -> Path {
        let l = min(36, r.width / 3, r.height / 3)
        var p = Path()
        for (x, y, dx, dy) in [(r.minX, r.minY, 1.0, 1.0), (r.maxX, r.minY, -1, 1), (r.minX, r.maxY, 1, -1), (r.maxX, r.maxY, -1, -1)] {
            p.move(to: CGPoint(x: x, y: y + dy * l))
            p.addLine(to: CGPoint(x: x, y: y))
            p.addLine(to: CGPoint(x: x + dx * l, y: y))
        }
        return p
    }
}

import SwiftUI
import VisionKit

struct SignInView: View {
    @Environment(Student.self) private var model
    @State private var who = UserDefaults.standard.string(forKey: "lastWho") ?? ""
    @State private var passcode = ""
    @State private var remember = true
    @State private var busy = false
    @State private var problem: String?
    @State private var scanning = false
    @State private var settings = false
    @FocusState private var field: Field?

    enum Field { case who, passcode }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(spacing: 10) {
                        Image("Logo").resizable().scaledToFit().frame(width: 72, height: 72)
                        Text("My CCC").font(.largeTitle.bold())
                        Text("Your Cooked Coke Co. points, tab and badges.").foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }
                    .frame(maxWidth: .infinity)
                    .listRowBackground(Color.clear)
                }
                Section {
                    TextField("Name or student number", text: $who)
                        .textContentType(.username).autocorrectionDisabled().textInputAutocapitalization(.words)
                        .focused($field, equals: .who).submitLabel(.next).onSubmit { field = .passcode }
                    SecureField("Passcode", text: $passcode)
                        .textContentType(.password).focused($field, equals: .passcode).submitLabel(.go)
                        .onSubmit { Task { await signIn() } }
                    Toggle("Stay signed in", isOn: $remember)
                } footer: {
                    Text("No passcode yet? Ask at the Cooked Coke Co. stall.")
                }
                if let problem {
                    Section { Label(problem, systemImage: "exclamationmark.circle.fill").foregroundStyle(.red) }
                }
                Section {
                    Button {
                        Task { await signIn() }
                    } label: {
                        HStack { Spacer(); if busy { ProgressView() } else { Text("Let me in").bold() }; Spacer() }
                    }
                    .disabled(busy || who.trimmingCharacters(in: .whitespaces).isEmpty || passcode.isEmpty)
                }
                Section {
                    Button {
                        scanning = true
                    } label: {
                        Label("Scan a sign-in code", systemImage: "qrcode.viewfinder")
                    }
                } footer: {
                    Text("Staff can show a sign-in code at the counter. It works once, for a minute.")
                }
                WhatsOnSection(page: model.whatsOn, showRewards: true)
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Server", systemImage: "gearshape") { settings = true }
                }
            }
            .sheet(isPresented: $scanning) {
                CodeScanner(title: "Scan the code", read: CodeScanner.signInToken) { token in scanning = false; Task { await useCode(token) } }
            }
            .sheet(isPresented: $settings) { ServerSheet() }
        }
    }

    private func signIn() async {
        let name = who.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty, !passcode.isEmpty, !busy else { return }
        busy = true
        problem = nil
        defer { busy = false }
        do {
            try await model.signIn(who: name, passcode: passcode, remember: remember)
            UserDefaults.standard.set(name, forKey: "lastWho")
            passcode = ""
        } catch {
            problem = isOffline(error) ? "No signal. Try again when there is." : error.localizedDescription
        }
    }

    private func useCode(_ token: String) async {
        busy = true
        problem = nil
        defer { busy = false }
        do { try await model.useCode(token) } catch {
            problem = isOffline(error) ? "No signal. Try again when there is." : error.localizedDescription
        }
    }
}

/// Double points days and rewards that are on: shown before signing in, and on the Me tab.
struct WhatsOnSection: View {
    let page: J
    var showRewards = false

    var body: some View {
        let on = page.events_on.array, soon = page.events_soon.array, custom = page.custom_events.array
        let rewards = showRewards ? page.rewards.array : []
        if !on.isEmpty || !soon.isEmpty || !custom.isEmpty || !rewards.isEmpty {
            Section("What's on") {
                if !on.isEmpty {
                    ForEach(on, id: \.self) { e in
                        Label {
                            Text("**\(e.name.string)** is on \(e.start_date.string == e.end_date.string ? "today" : "until \(Fmt.when(e.end_date.string, "EEE d MMM"))") - \(Fmt.g(e.multiplier.double))× points on everything.")
                        } icon: { Image(systemName: "bolt.fill").foregroundStyle(.orange) }
                    }
                } else if let e = soon.first {
                    Label {
                        Text("Coming up: **\(e.name.string)** on \(Fmt.when(e.start_date.string, "EEEE d MMM")) - \(Fmt.g(e.multiplier.double))× points.")
                    } icon: { Image(systemName: "calendar") }
                }
                ForEach(custom, id: \.self) { e in
                    Label {
                        Text((e.soon.bool ? "From \(Fmt.when(e.start.string, "EEE d MMM")): " : "") + "**\(e.name.string)** - \(e.summary.string)")
                    } icon: { Image(systemName: e.soon.bool ? "calendar" : "bolt.fill").foregroundStyle(e.soon.bool ? Color.secondary : .orange) }
                }
                ForEach(rewards, id: \.self) { r in
                    Label {
                        Text("**\(r.name.string)** for \(Fmt.number(r.points_cost.int)) points")
                    } icon: { Image(systemName: "gift.fill").foregroundStyle(.pink) }
                }
            }
        }
    }
}

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

/// Which server the app talks to. Only for testing; students never need it.
struct ServerSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(Student.self) private var model
    @State private var text = UserDefaults.standard.string(forKey: "server") ?? ""
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField(StudentAPI.defaultServer, text: $text)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                } header: {
                    Text("Server")
                } footer: {
                    Text(problem ?? "Leave blank for \(StudentAPI.defaultServer). A test server's student site is its address with /me on the end.")
                        .foregroundStyle(problem == nil ? Color.secondary : .red)
                }
            }
            .navigationTitle("Server").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        if text.trimmingCharacters(in: .whitespaces).isEmpty {
                            UserDefaults.standard.removeObject(forKey: "server")
                        } else if let s = StudentAPI.normalise(text) {
                            UserDefaults.standard.set(s, forKey: "server")
                        } else {
                            problem = "That isn't a web address."
                            return
                        }
                        SavedSignIn.clear()
                        dismiss()
                        Task { await model.load() }
                    }
                }
            }
        }
    }
}

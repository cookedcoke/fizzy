import SwiftUI

/// The Fizzy address (to try a test server) and registering this device.
struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage("baseURL") private var baseURL = ""
    @State private var address = ""
    @State private var link = ""
    @State private var message: (text: String, ok: Bool)?
    @State private var busy = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField(API.defaultBaseURL, text: $address)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    if !baseURL.isEmpty {
                        Button("Use Default") { address = ""; save() }
                    }
                } header: {
                    Text("Fizzy address")
                } footer: {
                    Text("Change it to try the app with a test server, e.g. http://localhost:8000.")
                }

                Section {
                    TextField("Paste the registration link", text: $link, axis: .vertical)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Register this device", action: register).disabled(link.isEmpty || busy)
                } header: {
                    Text("Counter device")
                } footer: {
                    Text("If Fizzy only lets registered devices in, make a link in Fizzy (Settings > Devices) and paste it here. Only do this on a device that stays at the counter.")
                }

                if let message {
                    Section { Label(message.text, systemImage: message.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(message.ok ? .green : .red) }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { save(); dismiss() } }
            }
            .onAppear { address = baseURL }
        }
    }

    private func save() {
        if address.trimmingCharacters(in: .whitespaces).isEmpty { baseURL = ""; return }
        if let url = API.normalise(address) {
            baseURL = url == API.defaultBaseURL ? "" : url
            address = baseURL
        } else {
            message = ("That doesn't look like a web address.", false)
        }
    }

    private func register() {
        guard let url = URL(string: link.trimmingCharacters(in: .whitespacesAndNewlines)), url.path.hasPrefix("/enrol/") else {
            message = ("That isn't a registration link. They look like https://fizzy.cookedcoke.co/enrol/...", false)
            return
        }
        busy = true
        Task {
            do {
                try await API.shared.register(link: url)
                message = ("This device is registered.", true)
                link = ""
            } catch {
                message = (error.localizedDescription, false)
            }
            busy = false
        }
    }
}

// Shared pieces: loading a page, posting a change, and the small controls every screen uses.
import SwiftUI
import UIKit

/// What happened to a change. `message` is what the server said (or why it failed).
struct Outcome {
    var ok: Bool
    var message: String?
    var body: J?   // set when the server answered with a page rather than a redirect (a report)
}

struct Flash: Equatable {
    var text: String
    var ok: Bool
}

/// One page of the website as data: loads it, keeps the message the server leaves, posts changes.
@Observable
@MainActor
final class Page {
    let path: String
    var query: [String: String]
    var data: J = .null
    var loaded = false
    var busy = false
    var error: String?
    var flash: Flash?

    init(_ path: String, query: [String: String] = [:]) {
        self.path = path
        self.query = query
    }

    func load() async {
        do {
            let d = try await AppModel.shared.call { try await API.shared.get(path, query) }
            data = d
            loaded = true
            error = nil
            if let text = d.flash.optString { flash = Flash(text: text, ok: d.flash_kind.string != "bad") }
        } catch is CancellationError {
        } catch let e as URLError where e.code == .cancelled {
        } catch {
            if !loaded || self.error != nil { self.error = error.localizedDescription } else { flash = Flash(text: error.localizedDescription, ok: false) }
        }
    }

    /// Posts a change, then reloads to pick up the server's message and the new numbers.
    @discardableResult
    func submit(_ path: String, _ form: Params = [], files: [FilePart] = [], query: [String: String] = [:], reload: Bool = true) async -> Outcome {
        flash = nil
        busy = true
        defer { busy = false }
        do {
            let (body, http) = try await AppModel.shared.call { try await API.shared.post(path, form, files: files, query: query) }
            if http.statusCode == 200, let page = try? JSONDecoder().decode(J.self, from: body), !page.headers.isNull {
                return Outcome(ok: true, message: nil, body: page)   // a report: nothing to reload
            }
            if reload { await load() }
            let ok = flash?.ok ?? true
            return Outcome(ok: ok, message: flash?.text, body: nil)
        } catch {
            return Outcome(ok: false, message: error.localizedDescription, body: nil)
        }
    }

    /// Like submit, for the buttons that don't have a form to stay on: failures show as the banner.
    func act(_ path: String, _ form: Params = [], query: [String: String] = [:]) {
        Task {
            let o = await submit(path, form, query: query)
            if !o.ok, flash == nil { flash = Flash(text: o.message ?? "That didn't work.", ok: false) }
        }
    }
}

/// A list screen for a page: loads it, pulls to refresh, shows the server's message on top.
struct PageList<Content: View>: View {
    let page: Page
    var title: String
    var staffMenu = false
    @ViewBuilder var content: (J) -> Content

    var body: some View {
        List {
            if let flash = page.flash { FlashRow(flash: flash) }
            if let error = page.error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
            if page.loaded { content(page.data) }
        }
        .overlay { if !page.loaded && page.error == nil { ProgressView() } }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .scrollDismissesKeyboard(.interactively)
        .refreshable { await page.load() }
        .task { if !page.loaded { await page.load() } }
        .toolbar {
            if staffMenu { ToolbarItem(placement: .topBarLeading) { StaffMenu() } }
            KeyboardDoneButton()
        }
    }
}

/// Number pads have no Return key, so give every keyboard a Done button.
struct KeyboardDoneButton: ToolbarContent {
    var body: some ToolbarContent {
        ToolbarItemGroup(placement: .keyboard) {
            Spacer()
            Button("Done") { UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil) }
        }
    }
}

struct FlashRow: View {
    let flash: Flash
    var body: some View {
        Label(flash.text, systemImage: flash.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
            .foregroundStyle(flash.ok ? .green : .red)
    }
}

// MARK: small controls

struct Tag: View {
    let text: String
    var color: Color = .secondary
    var body: some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
    }
}

/// The colour for a tab status like "approved" or "blocked_overdue".
func statusColor(_ key: String) -> Color {
    switch key {
    case "approved": .green
    case "eligible": .blue
    case "blocked_overdue", "rejected", "revoked": .red
    case "paused": .orange
    default: .secondary
    }
}

/// A figure with a caption, for the stat rows at the top of pages.
struct Stat: View {
    let title: String
    let value: String
    var sub: String?
    var alert = false
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title3.bold()).monospacedDigit().foregroundStyle(alert ? .red : .primary)
            if let sub { Text(sub).font(.caption2).foregroundStyle(.secondary) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Stats laid out two to a row.
struct StatGrid: View {
    let stats: [Stat]
    var body: some View {
        let rows = stride(from: 0, to: stats.count, by: 2).map { Array(stats[$0..<min($0 + 2, stats.count)]) }
        VStack(spacing: 12) {
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                HStack(alignment: .top, spacing: 16) {
                    ForEach(Array(row.enumerated()), id: \.offset) { _, s in s }
                    if row.count == 1 { Spacer().frame(maxWidth: .infinity) }
                }
            }
        }
        .padding(.vertical, 4)
    }
}

/// A row of two aligned bits of text, for tables on a narrow screen.
struct Line: View {
    let left: String
    var sub: String?
    let right: String
    var rightSub: String?
    var rightColor: Color = .primary

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(left)
                if let sub, !sub.isEmpty { Text(sub).font(.caption).foregroundStyle(.secondary) }
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 2) {
                Text(right).monospacedDigit().foregroundStyle(rightColor)
                if let rightSub, !rightSub.isEmpty { Text(rightSub).font(.caption).foregroundStyle(.secondary) }
            }
        }
    }
}

/// A day, as the "yyyy-MM-dd" text the server wants.
struct DayField: View {
    let title: String
    @Binding var text: String
    var body: some View {
        DatePicker(title, selection: Binding(get: { Fmt.parseDay(text) }, set: { text = Fmt.day($0) }), displayedComponents: .date)
    }
}

/// Type a name, tap the match. Fills in "Name <email>", which the server accepts exactly.
struct PersonField: View {
    let title: String
    @Binding var text: String
    let people: [J]
    @State private var picked = false

    private var matches: [J] {
        let q = text.trimmingCharacters(in: .whitespaces).lowercased()
        guard q.count >= 2, !picked else { return [] }
        return Array(people.filter { $0.name.string.lowercased().contains(q) || $0.email.string.lowercased().contains(q) }.prefix(5))
    }

    var body: some View {
        TextField(title, text: $text)
            .textInputAutocapitalization(.words)
            .autocorrectionDisabled()
            .onChange(of: text) { if !text.hasSuffix(">") { picked = false } }
        ForEach(matches, id: \.self) { p in
            Button {
                text = "\(p.name.string) <\(p.email.string)>"
                picked = true
            } label: {
                VStack(alignment: .leading) {
                    Text(p.name.string)
                    Text(p.email.string).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

// MARK: forms in a sheet

/// A form with Cancel and a save button. A refusal stays on the form so it can be fixed.
struct FormSheet<Content: View>: View {
    let title: String
    var action = "Save"
    var ready = true
    var destructive = false
    let page: Page
    let send: () async -> Outcome
    var done: (Outcome) -> Void = { _ in }
    @ViewBuilder var content: Content
    @Environment(\.dismiss) private var dismiss
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                content
            }
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .scrollDismissesKeyboard(.interactively)
            .disabled(busy)
            .toolbar {
                KeyboardDoneButton()
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    if busy { ProgressView() } else {
                        Button(action, role: destructive ? .destructive : nil) { submit() }.disabled(!ready)
                    }
                }
            }
        }
    }

    private func submit() {
        busy = true
        error = nil
        Task {
            let o = await send()
            busy = false
            if o.ok {
                done(o)
                dismiss()
            } else {
                error = o.message ?? "That didn't work."
                page.flash = nil
            }
        }
    }
}

// MARK: passcode

struct PasscodeSheet: View {
    let ask: PasscodeAsk
    @State private var passcode = ""
    @State private var error: String?
    @State private var busy = false
    @Environment(AppModel.self) private var model

    var body: some View {
        NavigationStack {
            Form {
                if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                Section {
                    SecureField(ask.need == "owner" ? "Owner password" : "Passcode", text: $passcode)
                        .keyboardType(ask.need == "owner" ? .default : .numberPad)
                        .submitLabel(.go)
                        .onSubmit(unlock)
                } footer: {
                    Text(ask.need == "owner" ? "This needs the owner's password." : "This change needs the passcode. It stays unlocked for a few minutes.")
                }
            }
            .navigationTitle("Passcode")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { finish(false) } }
                ToolbarItem(placement: .confirmationAction) {
                    if busy { ProgressView() } else { Button("Unlock", action: unlock).disabled(passcode.isEmpty) }
                }
            }
        }
        .presentationDetents([.medium])
        .interactiveDismissDisabled()
    }

    private func unlock() {
        guard !passcode.isEmpty, !busy else { return }
        busy = true
        Task {
            do {
                try await API.shared.unlock(passcode: passcode, need: ask.need)
                finish(true)
            } catch {
                self.error = error.localizedDescription
                passcode = ""
            }
            busy = false
        }
    }

    private func finish(_ ok: Bool) {
        ask.finish(ok)
        model.passcodeAsk = nil
    }
}

// MARK: files

/// A file the server made (an export, the backup, a receipt), ready to save or share.
struct SharedFile: Identifiable {
    let id = UUID()
    let url: URL
}

struct ShareSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController { UIActivityViewController(activityItems: [url], applicationActivities: nil) }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}

/// A button that fetches a file from the server and offers it to save or share.
struct DownloadButton: View {
    let title: String
    let path: String
    var query: [String: String] = [:]
    var systemImage = "square.and.arrow.down"
    var confirm: String?
    @State private var busy = false
    @State private var file: SharedFile?
    @State private var error: String?
    @State private var asking = false

    private func fetch() {
        busy = true
        error = nil
        Task {
            do {
                let url = try await AppModel.shared.call { try await API.shared.download(path, query) }
                file = SharedFile(url: url)
            } catch {
                self.error = error.localizedDescription
            }
            busy = false
        }
    }

    var body: some View {
        Button {
            if confirm != nil { asking = true } else { fetch() }
        } label: {
            HStack {
                Label(title, systemImage: systemImage)
                if busy { Spacer(); ProgressView() }
            }
        }
        .disabled(busy)
        .confirmationDialog(confirm ?? "", isPresented: $asking, titleVisibility: .visible) {
            Button("Download anyway", action: fetch)
            Button("Cancel", role: .cancel) {}
        }
        .sheet(item: $file) { ShareSheet(url: $0.url) }
        .alert("Couldn't download", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("OK", role: .cancel) {}
        } message: { Text(error ?? "") }
    }
}

/// Asks before a destructive change, like the website's confirm boxes.
struct ConfirmButton: View {
    let title: String
    var systemImage: String?
    var message: String
    var confirmLabel = "Do it"
    var role: ButtonRole? = .destructive
    let action: () -> Void
    @State private var asking = false

    var body: some View {
        Button(role: role) { asking = true } label: {
            if let systemImage { Label(title, systemImage: systemImage) } else { Text(title) }
        }
        .confirmationDialog(message, isPresented: $asking, titleVisibility: .visible) {
            Button(confirmLabel, role: role, action: action)
            Button("Cancel", role: .cancel) {}
        }
    }
}

/// Dollars typed by hand, as the server reads them.
func dollars(_ cents: Int) -> String { String(format: "%.2f", Double(cents) / 100) }

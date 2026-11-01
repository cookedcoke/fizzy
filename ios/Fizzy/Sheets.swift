// The counter actions. Each posts the same form as the staff page, then the customer page
// reloads and shows the server's message.
import SwiftUI

/// Pick the sale just rung up in Square (with no customer) and give it to this customer.
struct ClaimSheet: View {
    let id: Int
    let done: (Customer?) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var result: Claimable?
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        List {
            if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
            if let note = result?.note { Label(note, systemImage: "info.circle").foregroundStyle(.orange) }
            ForEach(result?.sales ?? []) { sale in
                Button { claim(sale) } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(sale.items).foregroundStyle(.primary)
                            Spacer()
                            Text(sale.at).foregroundStyle(.secondary).monospacedDigit()
                        }
                        Text(sale.amounts).font(.subheadline).foregroundStyle(.secondary)
                        if sale.tab_warning {
                            Label("More than they can put on their tab", systemImage: "exclamationmark.triangle.fill")
                                .font(.caption).foregroundStyle(.red)
                        }
                    }
                }
                .disabled(busy)
            }
        }
        .overlay {
            if result == nil && error == nil {
                ProgressView("Asking Square for the latest sales…")
            } else if result?.sales.isEmpty == true {
                ContentUnavailableView("No sales to claim", systemImage: "tray",
                                       description: Text(result?.square_on == false ? "Square isn't connected." : "Ring it up in Square without a customer, then pull to refresh."))
            }
        }
        .navigationTitle("Claim sale")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        .refreshable { await load() }
        .task { await load() }
    }

    private func load() async {
        do {
            result = try await API.shared.claimable(id)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func claim(_ sale: Sale) {
        busy = true
        Task {
            do {
                done(try await API.shared.act(id, "claim", ["key": sale.key]))
            } catch {
                self.error = error.localizedDescription
            }
            busy = false
        }
    }
}

struct PointsSheet: View {
    let id: Int
    let done: (Customer?) -> Void
    @State private var delta = ""
    @State private var note = ""

    var body: some View {
        ActionForm(title: "Adjust points", action: "Save", ready: !delta.isEmpty && !note.isEmpty, done: done,
                   send: { try await API.shared.act(id, "points", ["delta": delta, "note": note]) }) {
            Section {
                TextField("Points, e.g. 50 or -20", text: $delta).keyboardType(.numbersAndPunctuation)
                TextField("Reason", text: $note)
            } footer: {
                Text("A reason is required. It's kept in the audit log.")
            }
        }
    }
}

struct RepaySheet: View {
    let id: Int
    let done: (Customer?) -> Void
    @State private var amount = ""
    @State private var method = "cash"
    @State private var note = ""
    @State private var separate = false

    var body: some View {
        ActionForm(title: "Record repayment", action: "Record", ready: !amount.isEmpty, done: done, send: {
            var form = ["amount": amount, "method": method, "note": note]
            if separate { form["confirm_dup"] = "1" }
            return try await API.shared.act(id, "repay", form)
        }) {
            Section {
                TextField("Amount, e.g. 5.50", text: $amount).keyboardType(.decimalPad)
                Picker("Paid by", selection: $method) {
                    Text("Cash").tag("cash"); Text("Card").tag("card"); Text("Other").tag("other")
                }
                .pickerStyle(.segmented)
                TextField("Note (optional)", text: $note)
                Toggle("This is a separate payment", isOn: $separate)
            } footer: {
                Text("Repayments rung up in Square are recorded by themselves. Only record one here if it wasn't.")
            }
        }
    }
}

struct TabSheet: View {
    let id: Int
    let done: (Customer?) -> Void
    @State private var decision = "approve"
    @State private var note = ""

    var body: some View {
        ActionForm(title: "Tab decision", action: "Save", ready: true, done: done,
                   send: { try await API.shared.act(id, "tab", ["decision": decision, "note": note]) }) {
            Section {
                Picker("Decision", selection: $decision) {
                    Text("Approve").tag("approve")
                    Text("Reject").tag("reject")
                    Text("Revoke").tag("revoke")
                    Text("Reset to automatic").tag("reset")
                }
                .pickerStyle(.inline)
                .labelsHidden()
                TextField("Note (optional)", text: $note)
            }
        }
    }
}

/// Some actions need the passcode first, as on the staff pages.
struct PasscodeSheet: View {
    @Environment(\.dismiss) private var dismiss
    @State private var passcode = ""

    var body: some View {
        ActionForm(title: "Passcode", action: "Unlock", ready: !passcode.isEmpty, done: { _ in dismiss() },
                   send: { try await API.shared.unlock(passcode: passcode); return nil }) {
            Section {
                SecureField("Passcode", text: $passcode).keyboardType(.numberPad)
            } footer: {
                Text("Then try again.")
            }
        }
    }
}

/// A small form with Cancel and a save button. A refusal stays on the form so it can be fixed.
private struct ActionForm<Content: View>: View {
    let title: String
    let action: String
    let ready: Bool
    let done: (Customer?) -> Void
    let send: () async throws -> Customer?
    @ViewBuilder let content: Content
    @Environment(\.dismiss) private var dismiss
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        Form {
            if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
            content
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .disabled(busy)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            ToolbarItem(placement: .confirmationAction) {
                if busy { ProgressView() } else { Button(action) { submit() }.disabled(!ready) }
            }
        }
    }

    private func submit() {
        busy = true
        error = nil
        Task {
            do {
                done(try await send())
            } catch {
                self.error = error.localizedDescription
            }
            busy = false
        }
    }
}

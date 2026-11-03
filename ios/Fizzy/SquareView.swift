import SwiftUI

/// Square: sync, what Fizzy makes of each payment, and payments with nobody on them.
struct SquareView: View {
    @State private var page = Page("square")
    @State private var onlyUnlinked = false
    @State private var assigning: Pick?
    @State private var raw: RawPayment?

    struct RawPayment: Identifiable { let id = UUID(); let text: String }

    var body: some View {
        PageList(page: page, title: "Square", staffMenu: true) { d in
            Section {
                Button { page.act("square/sync") } label: { Label("Sync now", systemImage: "arrow.triangle.2.circlepath") }
                    .disabled(!d.square_on.bool || page.busy)
                if d.unclaimed.int > 0 && !onlyUnlinked {
                    Label("\(d.unclaimed.int) sale\(d.unclaimed.int == 1 ? "" : "s") this week with nobody on \(d.unclaimed.int == 1 ? "it" : "them"). No points or tab until someone's assigned.", systemImage: "person.fill.questionmark")
                        .foregroundStyle(.orange)
                    Button("See them") { onlyUnlinked = true; Task { await reload() } }
                }
                if let e = d.sync_error.optString { Label("Last sync error: \(e)", systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
            } header: {
                Text("Last sync \(d.last_sync.optString.map { Fmt.when($0) } ?? "never")")
            }
            Section {
                StatGrid(stats: [
                    Stat(title: "Connection", value: d.square_on.bool ? "Connected" : "Not set up", sub: d.square_on.bool ? (d.webhook_on.bool ? "Webhooks + polling" : "Polling every 5 min") : nil),
                    Stat(title: "Synced up to", value: d.cursor.optString.map { Fmt.when($0) } ?? "Never"),
                    Stat(title: "Not in Square", value: "\(d.unlinked_customers.int)", alert: d.unlinked_customers.int > 0),
                ])
                DownloadButton(title: "Download import CSV", path: "export/square-customers.csv")
            }
            if !d.conflicts.array.isEmpty {
                Section("Square profiles not linked · \(d.conflicts.count)") {
                    ForEach(d.conflicts.array, id: \.self) { Line(left: $0.name.string, sub: $0.email.string, right: $0.existing.string) }
                }
            }
            if !d.item_errors.array.isEmpty {
                Section("Last sync skipped \(d.item_errors.count) item\(d.item_errors.count == 1 ? "" : "s")") {
                    ForEach(d.item_errors.strings, id: \.self) { Text($0).font(.caption.monospaced()) }
                }
            }
            Section("How Fizzy reads Square") {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Tab sale")
                    Text([d.tab_discount.optString.map { "Sale with the “\($0)” discount" }, d.tab_tenders.array.isEmpty ? nil : "paid with " + d.tab_tenders.strings.joined(separator: " / ")]
                        .compactMap { $0 }.joined(separator: ", or ").ifEmpty("Not set up")).font(.subheadline).foregroundStyle(.secondary)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text("Tab repayment")
                    Text(d.repay_item.optString.map { "A sale with an item called “\($0)”" } ?? "Recorded in Fizzy only").font(.subheadline).foregroundStyle(.secondary)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text("Everything else")
                    Text("A normal sale").font(.subheadline).foregroundStyle(.secondary)
                }
            }
            Section {
                ForEach(d.rows.array, id: \.self) { p in payment(p, d) }
                if d.rows.array.isEmpty { Text(onlyUnlinked ? "No payments waiting for a customer." : "No payments yet.").foregroundStyle(.secondary) }
            } header: {
                HStack {
                    Text(onlyUnlinked ? "Payments with no customer" : "Recent payments")
                    Spacer()
                    Button(onlyUnlinked ? "Show all" : "Only unlinked") { onlyUnlinked.toggle(); Task { await reload() } }
                        .textCase(nil).font(.footnote)
                }
            }
            Section("More") {
                ToolLink(id: "resync", from: page)
                ToolLink(id: "history", from: page)
                ToolLink(id: "find_payment", from: page)
                DownloadButton(title: "Export Square payments", path: "tools/export/square_payments.csv")
            }
        }
        .sheet(item: $assigning) { AssignSheet(page: page, paymentID: $0.value.string, people: page.data.people.array) }
        .confirmationDialog("Change how this payment is counted?", isPresented: $confirmChange, titleVisibility: .visible) {
            Button("Change to \(pendingChange?.2 ?? "")") {
                if let c = pendingChange { page.act("square/payments/\(c.0)/kind", [("kind", c.1)]) }
            }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(item: $raw) { r in
            NavigationStack {
                ScrollView { Text(r.text).font(.caption.monospaced()).textSelection(.enabled).padding() }
                    .navigationTitle("What Square sent").navigationBarTitleDisplayMode(.inline)
                    .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { raw = nil } } }
            }
        }
    }

    private func reload() async {
        page.query = onlyUnlinked ? ["show": "unlinked"] : [:]
        await page.load()
    }

    @ViewBuilder private func payment(_ p: J, _ d: J) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    if !p.customer_id.isNull { NavigationLink(p.name.string, value: CustomerID(id: p.customer_id.int)) }
                    else { Button("Assign to…") { assigning = Pick(value: p.square_payment_id) }.buttonStyle(.bordered).controlSize(.small) }
                    Text(Fmt.when(p.created_at.string, "EEE d MMM HH:mm")).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Text(Fmt.money(p.amount_cents.int)).monospacedDigit()
            }
            if let items = p["items"].optString { Text(items).font(.caption).foregroundStyle(.secondary) }
            HStack {
                if let t = p.tender.optString { Text(t).font(.caption2).foregroundStyle(.secondary) }
                Spacer()
                Menu {
                    ForEach([("sale", "Sale"), ("tab_charge", "Tab charge"), ("tab_repayment", "Tab repayment"), ("void", "Not a real sale")], id: \.0) { k, label in
                        Button { change(p, k, label) } label: { if p.kind.string == k { Label(label, systemImage: "checkmark") } else { Text(label) } }
                    }
                } label: {
                    Text("Counted as: \(label(p.kind.string))").font(.caption)
                }
                Button("Raw") { Task { raw = RawPayment(text: (try? await API.shared.text("square/payments/\(p.square_payment_id.string)")) ?? "Couldn't load it.") } }
                    .font(.caption).buttonStyle(.borderless)
            }
        }
    }

    private func label(_ kind: String) -> String {
        ["sale": "Sale", "tab_charge": "Tab charge", "tab_repayment": "Tab repayment", "void": "Not a real sale"][kind] ?? kind
    }

    private func change(_ p: J, _ kind: String, _ label: String) {
        guard kind != p.kind.string else { return }
        pendingChange = (p.square_payment_id.string, kind, label)
        confirmChange = true
    }

    @State private var confirmChange = false
    @State private var pendingChange: (String, String, String)?
}

extension String {
    func ifEmpty(_ other: String) -> String { isEmpty ? other : self }
}

import SwiftUI

/// One customer: can they put it on their tab, points, what they owe, and the counter actions.
struct CustomerView: View {
    let id: Int
    @Environment(AppModel.self) private var model
    @State private var c: Customer?
    @State private var error: String?
    @State private var flash: (text: String, ok: Bool)?
    @State private var sheet: Sheet?
    @State private var newSale: Sale?
    @State private var dismissedSales: Set<String> = []

    enum Sheet: String, Identifiable {
        case claim, points, repay, tab, passcode
        var id: String { rawValue }
    }

    var body: some View {
        List {
            if let flash {
                Label(flash.text, systemImage: flash.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(flash.ok ? .green : .red)
                if !flash.ok && flash.text.localizedCaseInsensitiveContains("passcode") {
                    Button("Enter the passcode", systemImage: "lock.open") { sheet = .passcode }
                }
            }
            if let error {
                Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red)
            }
            if let c {
                Section { TabAnswer(c: c) }
                Section {
                    LabeledContent("Points") { Text("\(c.points)").font(.title3.bold()).monospacedDigit().foregroundStyle(.primary) }
                    LabeledContent("Owing") {
                        Text(c.owing).monospacedDigit().foregroundStyle(c.owing_cents > 0 ? .orange : .secondary)
                    }
                    if let overdue = c.overdue {
                        LabeledContent("Overdue") { Text(overdue).monospacedDigit().foregroundStyle(.red) }
                    }
                }
                Section {
                    Button("Claim sale", systemImage: "square.and.arrow.down") { sheet = .claim }
                    Button("Record repayment", systemImage: "dollarsign.circle") { sheet = .repay }
                    Button("Adjust points", systemImage: "plusminus.circle") { sheet = .points }
                    Button("Tab decision", systemImage: "checkmark.seal") { sheet = .tab }
                }
                if !c.charges.isEmpty {
                    Section("Unpaid tab") {
                        ForEach(c.charges, id: \.self) { ch in
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(ch.at)
                                    Text("due \(ch.due)").font(.caption).foregroundStyle(ch.overdue ? .red : .secondary)
                                }
                                Spacer()
                                Text(ch.unpaid).monospacedDigit()
                            }
                        }
                    }
                }
                if !c.rewards.isEmpty {
                    Section("Rewards") {
                        ForEach(c.rewards, id: \.self) { r in
                            HStack {
                                Text(r.name)
                                Spacer()
                                if r.limit_hit {
                                    Text("used up").foregroundStyle(.secondary)
                                } else if r.can_redeem {
                                    Text("Ready").bold().foregroundStyle(.green)
                                } else {
                                    Text("\(r.short) to go").foregroundStyle(.secondary).monospacedDigit()
                                }
                            }
                        }
                    }
                }
                if !c.history.isEmpty {
                    Section("Points history") {
                        ForEach(Array(c.history.enumerated()), id: \.offset) { _, e in
                            HStack(alignment: .firstTextBaseline) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(reasonLabel(e.reason))
                                    Text([e.at, e.note, e.staff].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Text(e.delta > 0 ? "+\(e.delta)" : "\(e.delta)").monospacedDigit()
                                    .foregroundStyle(e.delta > 0 ? .green : .primary)
                            }
                        }
                    }
                }
            }
        }
        .overlay { if c == nil && error == nil { ProgressView() } }
        .navigationTitle(c?.name ?? "")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task { await load() }
        .task(id: c?.opened) { await watchForSales() }
        .sheet(item: $sheet) { s in
            NavigationStack {
                switch s {
                case .claim: ClaimSheet(id: id, done: changed)
                case .points: PointsSheet(id: id, done: changed)
                case .repay: RepaySheet(id: id, done: changed)
                case .tab: TabSheet(id: id, done: changed)
                case .passcode: PasscodeSheet()
                }
            }
        }
        .alert("New sale", isPresented: Binding(get: { newSale != nil }, set: { if !$0 { newSale = nil } }), presenting: newSale) { sale in
            Button("Claim for \(c?.name.split(separator: " ").first.map(String.init) ?? "them")") { claim(sale) }
            Button("Not theirs", role: .cancel) { dismissedSales.insert(sale.key) }
        } message: { sale in
            Text("\(sale.at) · \(sale.items)\n\(sale.amounts)" + (sale.tab_warning ? "\n\nThat's more than they can put on their tab." : ""))
        }
    }

    private func load() async {
        do {
            let fresh = try await API.shared.customer(id)
            c = fresh
            error = nil
            if let text = fresh.flash { flash = (text, fresh.flash_kind != "bad") }
        } catch {
            model.handle(error)
            self.error = error.localizedDescription
        }
    }

    /// After a sheet saved something: show the result and the server's message.
    private func changed(_ fresh: Customer?) {
        sheet = nil
        guard let fresh else { return }
        c = fresh
        flash = fresh.flash.map { ($0, true) }
    }

    /// The "new sale, claim it?" prompt: only sales from about when the page was opened, and
    /// never claimed without a tap.
    private func watchForSales() async {
        guard let opened = c?.opened, let start = ISO8601DateFormatter().date(from: opened) else { return }
        let since = ISO8601DateFormatter().string(from: start.addingTimeInterval(-90))
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled, sheet == nil, newSale == nil else { continue }
            if let sales = try? await API.shared.newSales(id, since: since),
               let sale = sales.first(where: { !dismissedSales.contains($0.key) }) {
                newSale = sale
            }
        }
    }

    private func claim(_ sale: Sale) {
        Task {
            do {
                changed(try await API.shared.act(id, "claim", ["key": sale.key]))
            } catch {
                flash = (error.localizedDescription, false)
            }
        }
    }
}

/// The answer to "can they put it on their tab?" at the top of the page.
private struct TabAnswer: View {
    let c: Customer

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: c.tab.can_charge ? "checkmark.circle.fill" : "xmark.circle.fill")
                .font(.largeTitle)
                .foregroundStyle(c.tab.can_charge ? .green : .red)
            VStack(alignment: .leading, spacing: 2) {
                Text(c.tab.can_charge ? "Can use tab, up to \(c.tab.available)" : "Can't use tab")
                    .font(.headline)
                Text(detail).font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }

    private var detail: String {
        switch c.tab.status {
        case "approved": c.tab.can_charge ? "Limit \(c.tab.limit)" : "At their limit of \(c.tab.limit)"
        case "locked": "Spent \(c.tab.spent) of the \(c.tab.unlock_spend) needed"
        case "eligible": "Eligible: a staff member can approve a tab"
        case "blocked_overdue": "Overdue: nothing more until that's paid"
        case "paused": "All tabs are paused"
        default: [c.tab.label, c.tab.decision_note].compactMap { $0 }.joined(separator: ": ")
        }
    }
}

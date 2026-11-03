import SwiftUI

/// Tabs as a whole, as on the website's Tabs page: who owes, what's overdue or due soon, the tab
/// rules, and the tools that act on every tab at once. One person's tab is on their own page.
struct TabsView: View {
    @State private var page = Page("tabs")

    var body: some View {
        PageList(page: page, title: "Tabs") { d in
            if d.tabs_paused.bool {
                Section {
                    Label("All tabs are paused.", systemImage: "pause.circle.fill").foregroundStyle(.red)
                    ToolLink(id: "pause_tabs", label: "Turn tabs back on", from: page)
                }
            }
            Section {
                StatGrid(stats: [
                    Stat(title: "Total owed", value: Fmt.money(d.total_owed.int), sub: people(d.owing.count)),
                    Stat(title: "Overdue", value: Fmt.money(d.total_overdue.int), sub: charges(d.overdue.count),
                         alert: d.total_overdue.int > 0),
                    Stat(title: "Due in 2 days", value: "\(d.due_soon.count)", sub: d.due_soon.count == 1 ? "charge" : "charges"),
                    Stat(title: "Tabs turned off", value: "\(d.tabs_off.int)", sub: "by staff"),
                ])
            }
            if !d.overdue.array.isEmpty {
                Section("Overdue · \(d.overdue.count)") {
                    ForEach(Array(d.overdue.array.enumerated()), id: \.offset) { _, r in
                        row(r.customer, Fmt.money(r.charge.unpaid_cents.int), "Was due " + Fmt.when(r.charge.due_at.string, "EEE d MMM"), alert: true)
                    }
                }
            }
            if !d.due_soon.array.isEmpty {
                Section("Due in the next 2 days · \(d.due_soon.count)") {
                    ForEach(Array(d.due_soon.array.enumerated()), id: \.offset) { _, r in
                        row(r.customer, Fmt.money(r.charge.unpaid_cents.int), "Due " + Fmt.when(r.charge.due_at.string))
                    }
                }
            }
            Section("Who owes · \(d.owing.count)") {
                if d.owing.array.isEmpty { Text("Nobody owes anything.").foregroundStyle(.secondary) }
                ForEach(Array(d.owing.array.enumerated()), id: \.offset) { _, r in
                    let overdue = r.overdue_cents.int
                    row(r.customer, Fmt.money(r.owing_cents.int),
                        overdue > 0 ? "\(Fmt.money(overdue)) overdue" : r.due_at.optString.map { "Due " + Fmt.when($0, "EEE d MMM") },
                        alert: overdue > 0)
                }
            }
            Section {
                ToolLink(id: "chase", systemImage: "envelope", from: page)
                ToolLink(id: "balances", systemImage: "list.number", from: page)
                NavigationLink { TabRulesForm(page: page) } label: { Label("Tab rules", systemImage: "slider.horizontal.3") }
            } footer: {
                Text("One person's tab (a charge by hand, more time to pay, a write-off) is on their own page.")
            }
            Section("Export") {
                DownloadButton(title: "Tab charges", path: "tools/export/tab_charges.csv")
                DownloadButton(title: "Tab repayments", path: "tools/export/repayments.csv")
                DownloadButton(title: "Tab decisions", path: "tools/export/tab_decisions.csv")
            }
            Section {
                if !d.tabs_paused.bool { ToolLink(id: "pause_tabs", from: page) }
                ToolLink(id: "revoke_all", from: page)
            }
        }
    }

    private func row(_ c: J, _ amount: String, _ sub: String?, alert: Bool = false) -> some View {
        NavigationLink(value: CustomerID(id: c.id.int)) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(c.name.string)
                    if let sub { Text(sub).font(.caption).foregroundStyle(alert ? .red : .secondary) }
                }
                Spacer()
                Text(amount).monospacedDigit().foregroundStyle(alert ? .red : .primary)
            }
        }
    }

    private func people(_ n: Int) -> String { n == 1 ? "1 person" : "\(n) people" }
    private func charges(_ n: Int) -> String { n == 1 ? "1 charge" : "\(n) charges" }
}

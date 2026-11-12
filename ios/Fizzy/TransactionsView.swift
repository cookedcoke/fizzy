import SwiftUI

/// Every Square sale, with who it was.
struct TransactionsView: View {
    @State private var page = Page("transactions")
    @State private var q = ""
    @State private var start = ""
    @State private var end = ""
    @State private var show = ""
    @State private var assigning: Pick?
    @State private var showDates = false

    private let quickOrder = ["Today", "Yesterday", "Last 7 days", "This month"]

    var body: some View {
        PageList(page: page, title: "Transactions", staffMenu: true) { d in
            Section {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        ForEach(quickOrder.filter { d.quick[$0].count == 2 }, id: \.self) { label in
                            let r = d.quick[label]
                            Button(label) { start = r[0].string; end = r[1].string; Task { await reload() } }
                                .buttonStyle(.bordered)
                                .tint(r[0].string == d.start.string && r[1].string == d.end.string ? Color("Brand") : .secondary)
                        }
                        Button("Dates…") { showDates.toggle() }.buttonStyle(.bordered).tint(.secondary)
                    }
                }
                if showDates {
                    DayField(title: "From", text: $start)
                    DayField(title: "To", text: $end)
                    Button("Show these dates") { Task { await reload() } }
                }
                Picker("Show", selection: $show) {
                    ForEach(["", "sales", "tab", "repaid", "nobody"], id: \.self) { Text(d.shows[$0].optString ?? $0).tag($0) }
                }
                .pickerStyle(.menu)
                .onChange(of: show) { Task { await reload() } }
            } header: {
                Text(range(d))
            }
            Section {
                StatGrid(stats: [
                    Stat(title: "Checkouts", value: "\(d.totals["count"].int)"),
                    Stat(title: "Sold", value: Fmt.money(d.totals.sold.int), sub: "incl. \(Fmt.money(d.totals.tab.int)) on tabs"),
                    Stat(title: "Tabs repaid", value: Fmt.money(d.totals.repaid.int)),
                    Stat(title: "Refunded", value: Fmt.money(d.totals.refunded.int)),
                    Stat(title: "No customer", value: "\(d.totals.nobody.int)", alert: d.totals.nobody.int > 0),
                ])
            }
            Section {
                ForEach(d.rows.array, id: \.self) { row($0) }
                if d.rows.array.isEmpty {
                    Text(q.isEmpty && show.isEmpty ? "No sales in these dates." : "Nothing matches.").foregroundStyle(.secondary)
                }
            } footer: {
                if d.more.int > 0 { Text("Latest \(d.rows.count) shown, \(d.more.int) more.") }
            }
            Section {
                ToolLink(id: "takings", label: "Takings report", systemImage: "doc.text", from: page)
            } footer: {
                Text("Syncing, re-syncing and finding a payment are on Square.")
            }
        }
        .searchable(text: $q, prompt: "Customer or item")
        .onSubmit(of: .search) { Task { await reload() } }
        .onChange(of: q) { if q.isEmpty { Task { await reload() } } }
        .task { await page.load(); sync() }
        .sheet(item: $assigning) { AssignSheet(page: page, paymentID: $0.value.string, people: page.data.people.array) }
    }

    private func sync() {
        start = page.data.start.string
        end = page.data.end.string
        show = page.data.show.string
    }

    private func reload() async {
        page.query = ["start": start, "end": end, "show": show, "q": q].filter { !$0.value.isEmpty }
        await page.load()
    }

    private func range(_ d: J) -> String {
        d.start.string == d.end.string ? Fmt.when(d.start.string, "EEEE d MMMM")
            : "\(Fmt.when(d.start.string, "d MMM")) to \(Fmt.when(d.end.string, "d MMM yyyy"))"
    }

    @ViewBuilder private func row(_ r: J) -> some View {
        let amount = r.sold_cents.int != 0 ? r.sold_cents.int : r.repay_cents.int
        let items = r["items"].array.map { "\($0.qty.int)× \($0.name.string)" }.joined(separator: ", ")
        let source = r.source.string
        // Who and how much on top, what they bought under it, then when and how it was paid. A sale with a
        // customer opens them when tapped (an invisible link over the row, so no arrow sits mid-row).
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline) {
                if !r.customer_id.isNull {
                    Text(r.customer.string).font(.body.weight(.medium)).lineLimit(1)
                } else if let pid = r.payment_ids[0].optString {
                    Button("Assign to…") { assigning = Pick(value: .str(pid)) }.buttonStyle(.bordered).controlSize(.small)
                } else {
                    Text("No customer").foregroundStyle(.secondary)
                }
                Spacer()
                Text(Fmt.money(amount)).font(.body.weight(.semibold)).monospacedDigit()
            }
            HStack(alignment: .firstTextBaseline) {
                if source == "square" {
                    Text(items.isEmpty ? "Sale" : items).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                } else {
                    Text((source == "writeoff" ? "Debt written off" : "Recorded in Fizzy") + (r.note.optString.map { ": \($0)" } ?? "") + " · \(r.staff.string)")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer()
                if r.points.int != 0 { Text("\(Fmt.points(r.points.int)) pts").font(.caption).foregroundStyle(.secondary).monospacedDigit() }
            }
            HStack(spacing: 6) {
                Text(Fmt.when(r.at.string) + (r.tenders.array.isEmpty ? "" : " · " + r.tenders.strings.joined(separator: ", ").lowercased()))
                    .font(.caption).foregroundStyle(.tertiary).lineLimit(1)
                if r.tab_cents.int != 0 { Tag(text: r.tab_cents.int == r.sold_cents.int ? "On tab" : "\(Fmt.money(r.tab_cents.int)) on tab", color: .blue) }
                if r.repay_cents.int != 0 && source != "writeoff" { Tag(text: "Tab repaid", color: .green) }
                if source == "writeoff" { Tag(text: "Written off") }
                if r.refunded_cents.int != 0 { Tag(text: "\(Fmt.money(r.refunded_cents.int)) refunded", color: .red) }
            }
        }
        .padding(.vertical, 2)
        .background {
            if !r.customer_id.isNull {
                NavigationLink(value: CustomerID(id: r.customer_id.int)) { EmptyView() }.opacity(0)
            }
        }
    }
}

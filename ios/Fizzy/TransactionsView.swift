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
                    Button("Show") { Task { await reload() } }
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
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    if !r.customer_id.isNull {
                        NavigationLink(r.customer.string, value: CustomerID(id: r.customer_id.int))
                    } else if let pid = r.payment_ids[0].optString {
                        Button("Assign to…") { assigning = Pick(value: .str(pid)) }.buttonStyle(.bordered).controlSize(.small)
                    } else {
                        Text("No customer").foregroundStyle(.secondary)
                    }
                    Text(Fmt.when(r.at.string)).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text(Fmt.money(amount)).monospacedDigit()
                    if r.points.int != 0 { Text("\(Fmt.points(r.points.int)) pts").font(.caption).foregroundStyle(.secondary) }
                }
            }
            if source == "square" {
                if !items.isEmpty { Text(items).font(.caption).foregroundStyle(.secondary) }
            } else {
                Text((source == "writeoff" ? "Debt written off" : "Recorded in Fizzy") + (r.note.optString.map { ": \($0)" } ?? "") + " · \(r.staff.string)")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack(spacing: 4) {
                if r.tab_cents.int != 0 { Tag(text: r.tab_cents.int == r.sold_cents.int ? "On tab" : "\(Fmt.money(r.tab_cents.int)) on tab", color: .blue) }
                if r.repay_cents.int != 0 && source != "writeoff" { Tag(text: "Tab repaid", color: .green) }
                if source == "writeoff" { Tag(text: "Written off") }
                if r.refunded_cents.int != 0 { Tag(text: "\(Fmt.money(r.refunded_cents.int)) refunded", color: .red) }
                if !r.tenders.array.isEmpty { Text(r.tenders.strings.joined(separator: ", ").lowercased()).font(.caption2).foregroundStyle(.secondary) }
            }
        }
    }
}

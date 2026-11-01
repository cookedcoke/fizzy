import SwiftUI

/// Type in what one of each thing costs us and Fizzy works out the rest.
struct ProfitView: View {
    @State private var page = Page("profit")
    @State private var range = "7"
    @State private var costs: [String: String] = [:]
    @State private var original: [String: String] = [:]

    var body: some View {
        PageList(page: page, title: "Profit") { d in
            Section {
                Picker("Range", selection: $range) {
                    ForEach(["today", "7", "30"], id: \.self) { Text(d.ranges[$0][0].string).tag($0) }
                }
                .pickerStyle(.segmented)
                .onChange(of: range) { Task { page.query = ["range": range]; await page.load(); fill() } }
                StatGrid(stats: [
                    Stat(title: "Revenue", value: Fmt.money(d.pr.revenue.int), sub: "\(d.label.string.lowercased()), items with a cost"),
                    Stat(title: "Cost of goods", value: Fmt.money(d.pr.cost.int)),
                    Stat(title: "Profit", value: Fmt.money(d.pr.profit.int)),
                    Stat(title: "Margin", value: d.pr.margin.isNull ? "-" : "\(d.pr.margin.int)%"),
                    Stat(title: "No cost set", value: "\(d.pr.uncosted.int)", sub: "\(Fmt.money(d.pr.uncosted_revenue.int)) of sales not counted", alert: d.pr.uncosted.int > 0),
                ])
            }
            if let e = d.catalog_error.optString {
                Label("Square's item list: \(e) Until then, items show up here once they've sold.", systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red)
            }
            Section("Items · \(d.pr["items"].count + d.idle.count)") {
                ForEach(d.pr["items"].array, id: \.self) { r in
                    if r.custom.bool {
                        Line(left: r.name.string, sub: "no item, not in profit", right: Fmt.money(r.revenue.int), rightSub: "\(r.qty.int) sold")
                    } else {
                        costRow(r, sold: true)
                    }
                }
                if !d.idle.array.isEmpty {
                    Text("Not sold \(d.label.string.lowercased())" + (d.catalog_count.int > 0 ? " · from Square's item list" : "")).font(.footnote).foregroundStyle(.secondary)
                    ForEach(d.idle.array, id: \.self) { costRow($0, sold: false) }
                }
                if d.pr["items"].array.isEmpty && d.idle.array.isEmpty {
                    Text("No items sold \(d.label.string.lowercased()) yet" + (d.square_on.bool ? " - press Get items from Square below to list everything you sell." : "."))
                        .foregroundStyle(.secondary)
                }
                if hasChanges {
                    Button("Save costs") { Task { await save() } }.buttonStyle(.borderedProminent).disabled(page.busy)
                }
            }
            if d.square_on.bool {
                Section {
                    Button { page.act("profit/refresh") } label: { Label("Get items from Square", systemImage: "arrow.triangle.2.circlepath") }
                } footer: {
                    Text(d.catalog_sync.optString.map { "\(d.catalog_count.int) items · \(Fmt.when($0))" } ?? "Not read from Square yet")
                }
            }
        }
        .task { await page.load(); fill() }
    }

    private var hasChanges: Bool { costs != original }

    private func fill() {
        var m: [String: String] = [:]
        for r in page.data.pr["items"].array + page.data.idle.array where !r.custom.bool {
            m[r.key.string] = r.cost_each.isNull ? "" : dollars(r.cost_each.int)
        }
        costs = m
        original = m
        range = page.data.range.string
    }

    private func costRow(_ r: J, sold: Bool) -> some View {
        let key = r.key.string
        let missing = (costs[key] ?? "").isEmpty
        return VStack(alignment: .leading, spacing: 4) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(r.name.string)
                    Text(sold ? "\(r.qty.int) sold · \(Fmt.money(r.revenue.int))" + (r.profit.isNull ? "" : " · profit \(Fmt.money(r.profit.int))")
                              : (r.price.isNull ? "" : "sells for \(Fmt.money(r.price.int))")).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                HStack(spacing: 2) {
                    Text("$").foregroundStyle(.secondary)
                    TextField("e.g. 1.20", text: Binding(get: { costs[key] ?? "" }, set: { costs[key] = $0 }))
                        .keyboardType(.decimalPad).multilineTextAlignment(.trailing).frame(width: 70)
                        .padding(4).background(missing ? Color.orange.opacity(0.2) : Color.clear, in: RoundedRectangle(cornerRadius: 6))
                }
            }
            if !r.margin.isNull { Text("\(r.margin.int)% margin").font(.caption).foregroundStyle(r.margin.int < 20 ? .red : .secondary) }
        }
    }

    private func save() async {
        var form: Params = []
        for r in page.data.pr["items"].array + page.data.idle.array where !r.custom.bool {
            form.append(("name", r.name.string)); form.append(("key", r.key.string)); form.append(("base", r.base.string))
            form.append(("cost", costs[r.key.string] ?? ""))
        }
        let o = await page.submit("profit", form)
        if o.ok { fill() }
    }
}

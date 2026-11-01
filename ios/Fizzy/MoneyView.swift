import PhotosUI
import SwiftUI

/// Stock spending and what's been paid out to the partners.
struct MoneyView: View {
    @State private var page = Page("money")
    @State private var start = ""
    @State private var end = ""
    @State private var sheet: Sheet?
    @State private var showDates = false

    enum Sheet: String, Identifiable {
        case restock, payout
        var id: String { rawValue }
    }

    var body: some View {
        PageList(page: page, title: "Money") { d in
            Section {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        ForEach(["This month", "Last 30 days", "This year"].filter { d.quick[$0].count == 2 }, id: \.self) { label in
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
            } header: {
                Text("\(Fmt.when(d.start.string, "d MMM yyyy")) to \(Fmt.when(d.end.string, "d MMM yyyy"))")
            }
            Section {
                StatGrid(stats: [
                    Stat(title: "Spent on stock", value: Fmt.money(d.spent.int), sub: "\(d.restocks.count) restock\(d.restocks.count == 1 ? "" : "s")"),
                    Stat(title: "Paid out", value: Fmt.money(d.paid_total.int), sub: "\(d.payouts.count) payout\(d.payouts.count == 1 ? "" : "s")"),
                ] + d.partners.strings.map { Stat(title: $0, value: Fmt.money(d.paid[$0].int), sub: "paid out") })
                Button { sheet = .restock } label: { Label("Add a restock", systemImage: "cart.badge.plus") }
                Button { sheet = .payout } label: { Label("Pay the partners", systemImage: "banknote") }
            }
            if d.restocks.array.isEmpty && d.payouts.array.isEmpty {
                Text("No restocks or payouts in these dates.").foregroundStyle(.secondary)
            }
            if !d.restocks.array.isEmpty {
                Section("Restocks · \(d.restocks.count)") {
                    ForEach(d.restocks.array, id: \.self) { r in
                        VStack(alignment: .leading, spacing: 4) {
                            Line(left: Fmt.when(r.day.string, "EEE d MMM"), sub: [r.shop.optString, r.note.optString].compactMap { $0 }.joined(separator: " · "),
                                 right: Fmt.money(r.total_cents.int))
                            ForEach(d.items[String(r.id.int)].array, id: \.self) { it in
                                Text("\(it.qty.int)× \(it.name.string) (\(Fmt.money(it.cost_cents.int)))").font(.caption).foregroundStyle(.secondary)
                            }
                            HStack {
                                if let receipt = r.receipt.optString { DownloadButton(title: "Receipt", path: "money/receipts/\(receipt)", systemImage: "doc.text.image") }
                                Spacer()
                                ConfirmButton(title: "Delete", message: "Delete this restock (\(Fmt.money(r.total_cents.int)))?", confirmLabel: "Delete") {
                                    page.act("money/restock/\(r.id.int)/delete")
                                }
                                .buttonStyle(.bordered).controlSize(.small)
                            }
                            .font(.footnote)
                        }
                    }
                }
            }
            if !d.payouts.array.isEmpty {
                Section("Payouts · \(d.payouts.count)") {
                    ForEach(d.payouts.array, id: \.self) { p in
                        HStack {
                            Line(left: p.partner.string, sub: [Fmt.when(p.day.string, "EEE d MMM"), p.note.optString].compactMap { $0 }.joined(separator: " · "),
                                 right: Fmt.money(p.amount_cents.int))
                            ConfirmButton(title: "Delete", message: "Delete \(p.partner.string)'s \(Fmt.money(p.amount_cents.int)) payout?", confirmLabel: "Delete") {
                                page.act("money/payout/\(p.id.int)/delete")
                            }
                            .labelStyle(.iconOnly).buttonStyle(.borderless)
                        }
                    }
                }
            }
        }
        .task { await page.load(); start = page.data.start.string; end = page.data.end.string }
        .sheet(item: $sheet) { which in
            switch which {
            case .restock: RestockSheet(page: page)
            case .payout: PayoutSheet(page: page)
            }
        }
    }

    private func reload() async {
        page.query = ["start": start, "end": end]
        await page.load()
    }
}

struct RestockSheet: View {
    let page: Page
    struct Line: Identifiable { let id = UUID(); var item = ""; var qty = "1"; var cost = "" }
    @State private var day = Fmt.day(Date())
    @State private var shop = ""
    @State private var lines = [Line()]
    @State private var note = ""
    @State private var photo: PhotosPickerItem?
    @State private var photoData: Data?

    private var total: Int { lines.reduce(0) { $0 + Int((parse($1.cost) * 100).rounded()) } }

    var body: some View {
        FormSheet(title: "Add a restock", ready: lines.contains { !$0.item.isEmpty && !$0.cost.isEmpty }, page: page, send: {
            var form: Params = [("day", day), ("shop", shop), ("note", note)]
            for l in lines where !l.item.trimmingCharacters(in: .whitespaces).isEmpty || !l.cost.isEmpty {
                form += [("item", l.item), ("qty", l.qty), ("cost", l.cost)]
            }
            var files: [FilePart] = []
            if let photoData { files.append(FilePart(field: "receipt", filename: "receipt.jpg", mime: "image/jpeg", data: photoData)) }
            return await page.submit("money/restock", form, files: files)
        }) {
            Section {
                DayField(title: "Day", text: $day)
                TextField("Shop, e.g. Costco, Woolies", text: $shop)
            }
            Section("Items") {
                ForEach($lines) { $l in
                    VStack(alignment: .leading) {
                        TextField("Item, as named in Square", text: $l.item).textInputAutocapitalization(.words)
                        HStack {
                            Text("Qty"); TextField("1", text: $l.qty).keyboardType(.numberPad).frame(width: 50).textFieldStyle(.roundedBorder)
                            Text("Cost for the line $"); TextField("total", text: $l.cost).keyboardType(.decimalPad).textFieldStyle(.roundedBorder)
                        }
                        .font(.footnote)
                    }
                }
                .onDelete { if lines.count > 1 { lines.remove(atOffsets: $0) } }
                Button("+ Another item") { lines.append(Line()) }
                LabeledContent("Total", value: Fmt.money(total))
            }
            Section {
                PhotosPicker(selection: $photo, matching: .images) {
                    Label(photoData == nil ? "Add a receipt photo (optional)" : "Receipt photo added", systemImage: "camera")
                }
                .onChange(of: photo) {
                    Task {
                        if let data = try? await photo?.loadTransferable(type: Data.self), let img = UIImage(data: data) {
                            photoData = img.jpegData(compressionQuality: 0.7)
                        }
                    }
                }
                TextField("Note (optional)", text: $note)
            }
        }
    }

    /// "12,50" is $12.50 (a comma before two digits at the end is a decimal point).
    private func parse(_ s: String) -> Double {
        var v = s.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "$", with: "")
        if let r = v.range(of: #",\d{1,2}$"#, options: .regularExpression) { v.replaceSubrange(r, with: "." + v[r].dropFirst()) }
        return Double(v.replacingOccurrences(of: ",", with: "")) ?? 0
    }
}

struct PayoutSheet: View {
    let page: Page
    @State private var pool = ""
    @State private var keep = "0"
    @State private var included: Set<String> = []
    @State private var amounts: [String: String] = [:]
    @State private var day = Fmt.day(Date())
    @State private var note = ""

    private var partners: [String] { page.data.partners.strings }

    /// An even split of (pool - keep) between whoever's ticked, to the cent.
    private var split: [String: Int] {
        let names = partners.filter { included.contains($0) }
        let cents = max(0, Int(((Double(pool) ?? 0) - (Double(keep) ?? 0)) * 100 + 0.5))
        var res: [String: Int] = [:]
        for (i, n) in names.enumerated() { res[n] = cents / names.count + (i < cents % names.count ? 1 : 0) }
        return res
    }

    var body: some View {
        FormSheet(title: "Pay the partners", action: "Record payouts", ready: amounts.values.contains { !$0.isEmpty }, page: page, send: {
            var form: Params = [("day", day), ("note", note)]
            for n in partners { form += [("partner", n), ("amount", amounts[n] ?? "")] }
            return await page.submit("money/payout", form)
        }) {
            Section("Split calculator") {
                TextField("Money to split ($)", text: $pool).keyboardType(.decimalPad)
                TextField("Keep for stock ($)", text: $keep).keyboardType(.decimalPad)
                ForEach(partners, id: \.self) { n in
                    Toggle(isOn: Binding(get: { included.contains(n) }, set: { if $0 { included.insert(n) } else { included.remove(n) } })) {
                        HStack { Text(n); Spacer(); if let c = split[n] { Text(Fmt.money(c)).foregroundStyle(.secondary).monospacedDigit() } }
                    }
                }
                Button("Use these amounts") {
                    for n in partners { amounts[n] = split[n].map { $0 > 0 ? dollars($0) : "" } ?? "" }
                }
            }
            Section("Paid") {
                ForEach(partners, id: \.self) { n in
                    HStack {
                        Text(n)
                        TextField("$0.00", text: Binding(get: { amounts[n] ?? "" }, set: { amounts[n] = $0 })).keyboardType(.decimalPad).multilineTextAlignment(.trailing)
                    }
                }
                DayField(title: "Day", text: $day)
                TextField("Note (optional)", text: $note)
            }
        }
        .onAppear { included = Set(partners) }
    }
}

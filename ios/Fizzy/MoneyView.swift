import PhotosUI
import SwiftUI

/// Stock spending and what's been paid out to the partners.
struct MoneyView: View {
    @State private var page = Page("money")
    @State private var start = ""
    @State private var end = ""
    @State private var sheet: Sheet?
    @State private var showDates = false
    @State private var confirmDelete: DeleteAsk?

    struct DeleteAsk: Identifiable { let id = UUID(); let message: String; let path: String }

    enum Sheet: Identifiable {
        case restock, edit(J), payout
        var id: String {
            switch self { case .restock: "restock"; case .edit(let r): "edit-\(r.id.int)"; case .payout: "payout" }
        }
    }

    var body: some View {
        PageList(page: page, title: "Money", above: { WaitingSection(page: page) }) { d in
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
                Section {
                    ForEach(d.restocks.array, id: \.self) { r in
                        VStack(alignment: .leading, spacing: 4) {
                            Line(left: Fmt.when(r.day.string, "EEE d MMM"), sub: [r.shop.optString, r.note.optString].compactMap { $0 }.joined(separator: " · "),
                                 right: Fmt.money(r.total_cents.int))
                            ForEach(d.items[String(r.id.int)].array, id: \.self) { it in
                                Text("\(it.qty.int)× \(it.name.string) (\(Fmt.money(it.cost_cents.int)))").font(.caption).foregroundStyle(.secondary)
                            }
                            if let receipt = r.receipt.optString {
                                DownloadButton(title: "Receipt", path: "money/receipts/\(receipt)", systemImage: "doc.text.image").font(.footnote)
                                    .buttonStyle(.borderless)   // just the label, not the whole row
                            }
                        }
                        .swipeActions(edge: .trailing) {
                            Button("Delete", role: .destructive) { confirmDelete = DeleteAsk(message: "Delete this restock (\(Fmt.money(r.total_cents.int)))?", path: "money/restock/\(r.id.int)/delete") }
                            Button("Edit") { sheet = .edit(r) }.tint(.blue)
                        }
                    }
                } header: {
                    Text("Restocks · \(d.restocks.count)")
                } footer: {
                    Text("Swipe left to edit or delete.")
                }
            }
            if !d.payouts.array.isEmpty {
                Section {
                    ForEach(d.payouts.array, id: \.self) { p in
                        Line(left: p.partner.string, sub: [Fmt.when(p.day.string, "EEE d MMM"), p.note.optString].compactMap { $0 }.joined(separator: " · "),
                             right: Fmt.money(p.amount_cents.int))
                            .swipeActions(edge: .trailing) {
                                Button("Delete", role: .destructive) { confirmDelete = DeleteAsk(message: "Delete \(p.partner.string)'s \(Fmt.money(p.amount_cents.int)) payout?", path: "money/payout/\(p.id.int)/delete") }
                            }
                    }
                } header: {
                    Text("Payouts · \(d.payouts.count)")
                } footer: {
                    Text("Swipe left to delete.")
                }
            }
        }
        .task { if !page.loaded { await page.load() }; start = page.data.start.string; end = page.data.end.string }
        .confirmationDialog(confirmDelete?.message ?? "", isPresented: Binding(get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }), titleVisibility: .visible) {
            Button("Delete", role: .destructive) { if let c = confirmDelete { page.act(c.path) } }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(item: $sheet) { which in
            switch which {
            case .restock: RestockSheet(page: page)
            case .edit(let r): RestockSheet(page: page, editing: r, items: page.data.items[String(r.id.int)].array)
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
    var editing: J? = nil       // a restock to change (substitutions, things that didn't turn up)
    var items: [J] = []
    @State private var clientID = UUID().uuidString
    struct Line: Identifiable { let id = UUID(); var item = ""; var qty = "1"; var cost = "" }
    @State private var day = Fmt.day(Date())
    @State private var shop = ""
    @State private var lines = [Line()]
    @State private var note = ""
    @State private var photo: PhotosPickerItem?
    @State private var photoData: Data?
    @State private var pickingLine: UUID?

    private var total: Int { lines.reduce(0) { $0 + Fmt.cents($1.cost) } }

    private func fill() {
        guard let r = editing, lines.count == 1, lines[0].item.isEmpty else { return }
        day = r.day.string
        shop = r.shop.string
        note = r.note.string
        lines = items.map { Line(item: $0.name.string, qty: "\($0.qty.int)", cost: dollars($0.cost_cents.int)) }
        if lines.isEmpty { lines = [Line()] }
    }

    var body: some View {
        FormSheet(title: editing == nil ? "Add a restock" : "Edit restock", ready: lines.contains { !$0.item.isEmpty && !$0.cost.isEmpty }, page: page, send: {
            var form: Params = [("day", day), ("shop", shop), ("note", note)]
            for l in lines where !l.item.trimmingCharacters(in: .whitespaces).isEmpty || !l.cost.isEmpty {
                form += [("item", l.item), ("qty", l.qty), ("cost", l.cost)]
            }
            var files: [FilePart] = []
            if let photoData { files.append(FilePart(field: "receipt", filename: "receipt.jpg", mime: "image/jpeg", data: photoData)) }
            if editing == nil { form.append(("client_id", clientID)) }   // so a resend isn't saved twice
            let title = (editing == nil ? "Restock" : "Restock edit") + (shop.isEmpty ? "" : " at \(shop)") + " (\(Fmt.money(total)))"
            return await page.submit(editing.map { "money/restock/\($0.id.int)" } ?? "money/restock", form, files: files, queue: title)
        }) {
            Section {
                DayField(title: "Day", text: $day)
                TextField("Shop, e.g. Costco, Woolies", text: $shop)
            }
            Section("Items") {
                ForEach($lines) { $l in
                    VStack(alignment: .leading) {
                        Button { pickingLine = l.id } label: {
                            HStack {
                                Text(l.item.isEmpty ? "Choose an item" : l.item).foregroundStyle(l.item.isEmpty ? Color.secondary : Color.primary)
                                Spacer()
                                Image(systemName: "chevron.up.chevron.down").font(.footnote).foregroundStyle(.secondary)
                            }
                        }
                        .buttonStyle(.plain)
                        HStack {
                            Text("Qty"); TextField("1", text: $l.qty).keyboardType(.numberPad).frame(width: 50).textFieldStyle(.roundedBorder)
                            Text("Cost for the line $"); TextField("total", text: $l.cost).keyboardType(.decimalPad).textFieldStyle(.roundedBorder)
                        }
                        .font(.footnote)
                    }
                }
                .onDelete { if lines.count > 1 { lines.remove(atOffsets: $0) } }
                Button { lines.append(Line()) } label: { Label("Another item", systemImage: "plus.circle") }
                LabeledContent("Total", value: Fmt.money(total))
            }
            Section {
                PhotosPicker(selection: $photo, matching: .images) {
                    Label(photoData != nil ? "Receipt photo added"
                          : (editing?.receipt.optString != nil ? "Replace the receipt photo (optional)" : "Add a receipt photo (optional)"), systemImage: "camera")
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
        .onAppear(perform: fill)
        .sheet(item: Binding(get: { pickingLine.map { Pick(value: .str($0.uuidString)) } }, set: { if $0 == nil { pickingLine = nil } })) { _ in
            ItemPicker(known: page.data.known.strings, current: lines.first { $0.id == pickingLine }?.item ?? "") { name in
                if let i = lines.firstIndex(where: { $0.id == pickingLine }) { lines[i].item = name }
            }
        }
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
        let cents = max(0, Fmt.cents(pool) - Fmt.cents(keep))
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
                LabeledContent("Money to split") {
                    TextField("$0.00", text: $pool).keyboardType(.decimalPad).multilineTextAlignment(.trailing).monospacedDigit()
                }
                LabeledContent("Keep for stock") {
                    TextField("$0.00", text: $keep).keyboardType(.decimalPad).multilineTextAlignment(.trailing).monospacedDigit()
                }
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


/// Restocks saved on this phone with no signal, until they're sent.
struct WaitingSection: View {
    let page: Page
    @State private var queue = OfflineQueue.shared
    @State private var deleting: Pending?

    var body: some View {
        if let sent = queue.lastSent {
            Label(sent, systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                .task(id: sent) { await page.load() }
        }
        if !queue.items.isEmpty {
            Section {
                ForEach(queue.items) { p in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(p.title)
                        Text("Saved \(p.created.formatted(date: .abbreviated, time: .shortened))" + (p.staff.map { " by \($0)" } ?? ""))
                            .font(.caption).foregroundStyle(.secondary)
                        if let problem = p.problem {
                            Text("Fizzy said no: \(problem) Delete it and add it again.").font(.caption).foregroundStyle(.red)
                        } else if let h = p.host, h != API.shared.baseURL.host {
                            Text("Saved for \(h). It sends when the Fizzy address is that again.").font(.caption).foregroundStyle(.orange)
                        } else if p.needsPasscode {
                            Text("Needs the passcode. Tap Send now.").font(.caption).foregroundStyle(.orange)
                        } else if let s = p.staff, s != AppModel.shared.staff {
                            Text("Saved by \(s): it sends when they're on the counter, or tap Send now to send it as you.").font(.caption).foregroundStyle(.orange)
                        } else if let e = p.lastError {
                            Text("Couldn't send: \(e) It'll try again.").font(.caption).foregroundStyle(.orange)
                        } else if queue.sendingID == p.id {
                            Text("Sending…").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .swipeActions {
                        Button("Delete", role: .destructive) { deleting = p }.disabled(queue.sendingID == p.id)
                    }
                }
                Button {
                    Task { await queue.send(asking: true) }
                } label: {
                    HStack { Label("Send now", systemImage: "arrow.up.circle"); if queue.sending { Spacer(); ProgressView() } }
                }
                .disabled(queue.sending)
                .confirmationDialog("Delete \(deleting?.title ?? "this")? It hasn't been sent, so it's gone for good" + ((deleting?.files.isEmpty ?? true) ? "." : ", receipt photo included."),
                                    isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }), titleVisibility: .visible) {
                    Button("Delete", role: .destructive) { if let d = deleting { queue.remove(d) } }
                    Button("Cancel", role: .cancel) {}
                }
            } header: {
                Text("Waiting to send · \(queue.items.count)")
            } footer: {
                Text("Saved on this phone because there was no signal. They send by themselves when there is. Swipe to delete one.")
            }
        }
    }
}


/// Pick an item from Square's list (saved with the page, so it works with no signal), or type one that
/// isn't there. Searchable.
struct ItemPicker: View {
    let known: [String]
    let current: String
    let choose: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var searching = true

    private var matches: [String] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return q.isEmpty ? known : known.filter { $0.lowercased().contains(q) }
    }

    private var typed: String { query.trimmingCharacters(in: .whitespaces) }

    var body: some View {
        NavigationStack {
            List {
                if !typed.isEmpty && !known.contains(where: { $0.lowercased() == typed.lowercased() }) {
                    Button { pick(typed) } label: { Label("Use “\(typed)”", systemImage: "plus.circle") }
                }
                ForEach(matches, id: \.self) { name in
                    Button { pick(name) } label: {
                        HStack {
                            Text(name).foregroundStyle(.primary)
                            Spacer()
                            if name == current { Image(systemName: "checkmark").foregroundStyle(Color("Brand")) }
                        }
                    }
                    .buttonStyle(.plain)
                }
                if matches.isEmpty && typed.isEmpty { Text("No items yet.").foregroundStyle(.secondary) }
            }
            .navigationTitle("Item")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $query, isPresented: $searching, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search items")
            .autocorrectionDisabled()
            .textInputAutocapitalization(.words)
            .onSubmit(of: .search) { if let first = matches.first, typed.isEmpty == false, matches.count == 1 { pick(first) } else if !typed.isEmpty { pick(matches.first(where: { $0.lowercased() == typed.lowercased() }) ?? typed) } }
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
    }

    private func pick(_ name: String) {
        choose(name)
        dismiss()
    }
}

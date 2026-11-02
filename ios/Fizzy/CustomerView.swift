import SwiftUI
import WebKit

/// One customer: can they put it on their tab, points, what they owe, and every counter action
/// the website's customer page has.
struct CustomerView: View {
    let id: Int
    @State private var page: Page
    @State private var sheet: Sheet?
    @State private var newSale: J?
    @State private var dismissedSales: Set<String> = []
    @State private var qr: QRCode?

    init(id: Int) {
        self.id = id
        _page = State(initialValue: Page("customers/\(id)"))
    }

    enum Sheet: String, Identifiable {
        case claim, repay, points, tab, passcode, edit, kind
        var id: String { rawValue }
    }

    struct QRCode: Identifiable {
        let id = UUID()
        let svg: String
        let seconds: Int
    }

    private var customer: J { page.data.c }
    private var first: String { customer.name.string.split(separator: " ").first.map(String.init) ?? "them" }

    var body: some View {
        PageList(page: page, title: customer.name.string) { d in
            header(d)
            verdict(d)
            todos(d)
            rewards(d)
            figures(d)
            actions(d)
            charges(d)
            repayments(d)
            history(d)
            payments(d)
            score(d)
            more(d)
        }
        .onAppear { if page.loaded { Task { await page.load() } } }
        .task(id: page.data.opened.string) { await watchForSales() }
        .sheet(item: $sheet) { which in sheetContent(which) }
        .sheet(item: $qr) { QRSheet(code: $0) }
        .alert("New sale", isPresented: Binding(get: { newSale != nil }, set: { if !$0 { newSale = nil } }), presenting: newSale) { sale in
            Button("Claim for \(first)") { dismissedSales.insert(sale.key.string); claim(sale) }   // not offered again while it goes through
            Button("Not theirs", role: .cancel) { dismissedSales.insert(sale.key.string) }
        } message: { sale in
            Text("\(sale.at.string) · \(sale["items"].string)\n\(amounts(sale))" + (sale.tab_warning.bool ? "\n\n\(first) can't put this on a tab - it'll be flagged." : ""))
        }
    }

    // MARK: sections

    @ViewBuilder private func header(_ d: J) -> some View {
        let c = d.c, st = d.s.status
        Section {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Tag(text: st.label.string, color: statusColor(st.status.string))
                    if c.archived_at.optString != nil { Tag(text: "Archived") }
                    if c.kind.string == "admin" { Tag(text: "Admin", color: .purple) }
                    if c.kind.string == "test" { Tag(text: "Test", color: .orange) }
                }
                let number = c.email.string.split(separator: "@").first.map(String.init) ?? ""
                let link = !c.square_customer_id.isNull ? "Linked to Square" : (c.kind.string == "test" ? "Test account" : "Not in Square yet")
                Text("\(number) · \(link)").font(.footnote).foregroundStyle(.secondary)
            }
            if c.archived_at.optString == nil {
                Button { sheet = .claim } label: { Label("Claim sale", systemImage: "square.and.arrow.down") }
                Button { Task { await makeQR() } } label: { Label("Show sign-in code", systemImage: "qrcode") }
            }
        }
    }

    @ViewBuilder private func verdict(_ d: J) -> some View {
        let st = d.s.status, led = d.s.ledger, c = d.c
        let canCharge = st.can_charge.bool
        let waiting = ["low_score", "paused"].contains(st.status.string)
        Section {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: canCharge ? "checkmark.circle.fill" : (waiting ? "minus.circle.fill" : "xmark.circle.fill"))
                    .font(.largeTitle).foregroundStyle(canCharge ? .green : (waiting ? .orange : .red))
                VStack(alignment: .leading, spacing: 4) {
                    if canCharge {
                        Text("Can use tab – up to \(Fmt.money(st.available_cents.int))").font(.headline)
                        Text("In Square: attach \(first)'s profile, apply the Fizzy Tab discount to what goes on the tab, then charge the rest - if it's all on the tab, charge $0 as cash.")
                            .font(.footnote).foregroundStyle(.secondary)
                    } else {
                        Text(waiting ? "No tab yet" : "No tab").font(.headline)
                        Text(why(st, led, c)).font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func why(_ st: J, _ led: J, _ c: J) -> String {
        switch st.status.string {
        case "approved": return "Owes \(Fmt.money(led.owing_cents.int)), at or over the \(Fmt.money(st.limit_cents.int)) limit - needs to pay some off first."
        case "paused": return "All tabs are paused right now."
        case "blocked_overdue": return "Has an overdue charge. Clears as soon as it's paid."
        case "low_score": return "Tab score is \(st.score.score.int); it needs \(st.min_score.int) for a tab. Give them a limit to let them have one."
        default: return st.label.string + (c.tab_decision_note.optString.map { ": “\($0)”" } ?? "") + "."
        }
    }

    @ViewBuilder private func todos(_ d: J) -> some View {
        let c = d.c, led = d.s.ledger
        if c.archived_at.optString == nil {
            let overdue = led.has_overdue.bool, owes = led.owing_cents.int > 0
            let needsPasscode = c.passcode_set_at.optString == nil
            let notInSquare = c.square_customer_id.isNull && c.kind.string != "test"
            if overdue || owes || needsPasscode || notInSquare {
                Section("To do") {
                    if overdue {
                        Label("\(Fmt.money(led.overdue_cents.int)) overdue. Ask \(first) to pay it off - ring it up as Tab repayment in Square.", systemImage: "exclamationmark.circle.fill").foregroundStyle(.red)
                    } else if owes {
                        let due = led.charges.array.filter { $0.unpaid_cents.int > 0 }.map { $0.due_at.string }.min() ?? ""
                        Label("Owes \(Fmt.money(led.owing_cents.int)). Due \(Fmt.when(due, "EEE d MMM")).", systemImage: "dollarsign.circle").foregroundStyle(.orange)
                    }
                    if needsPasscode {
                        Button { sheet = .passcode } label: { Label("No student site passcode yet. Set one", systemImage: "key") }
                    }
                    if notInSquare {
                        Label("Not in Square yet. Their sales won't link by themselves - export new people from Customers, or claim their sales.", systemImage: "person.crop.circle.badge.questionmark")
                            .font(.footnote)
                    }
                }
            }
        }
    }

    @ViewBuilder private func rewards(_ d: J) -> some View {
        if !d.offers.array.isEmpty || !d.recent_redemptions.array.isEmpty {
            Section("Rewards") {
                ForEach(d.offers.array, id: \.self) { o in
                    let r = o.reward
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(r.name.string).bold() + Text(" · \(Fmt.number(r.points_cost.int)) pts").foregroundStyle(.secondary)
                            Text(offerNote(o, r)).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if o.can_redeem.bool {
                            ConfirmButton(title: "Redeem", message: "Redeem \(r.name.string) for \(d.c.name.string)? Takes \(Fmt.number(r.points_cost.int)) points.",
                                          confirmLabel: "Redeem", role: nil) {
                                page.act("customers/\(id)/redeem/\(r.id.int)")
                            }
                            .buttonStyle(.borderedProminent)
                        }
                    }
                }
                ForEach(d.recent_redemptions.array, id: \.self) { r in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Redeemed \(r.name.string)").bold()
                            Text("\(Fmt.when(r.created_at.string, "HH:mm")) by \(r.staff.string)" + (r.square_note.optString.map { " · In Square: \($0)" } ?? ""))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        ConfirmButton(title: "Undo", message: "Undo this redemption and give \(r.points.int) points back?", confirmLabel: "Undo") {
                            page.act("redemptions/\(r.id.int)/undo")
                        }
                        .buttonStyle(.bordered).controlSize(.small)
                    }
                }
            }
        }
    }

    private func offerNote(_ o: J, _ r: J) -> String {
        if o.can_redeem.bool { return "\(first) can have this now." + (r.square_note.optString.map { " In Square: \($0)." } ?? "") }
        if o.limit_hit.bool { return "Already used" + (r.per_customer_limit.int > 1 ? " \(o.used.int) times" : "") + " - that's the limit." }
        return "Needs \(Fmt.number(o.short.int)) more points."
    }

    @ViewBuilder private func figures(_ d: J) -> some View {
        let st = d.s.status, led = d.s.ledger
        Section {
            StatGrid(stats: [
                Stat(title: "Points", value: Fmt.number(d.s.points.int)),
                Stat(title: "Tab owing", value: Fmt.money(led.owing_cents.int), sub: led.balance_cents.int < 0 ? "\(Fmt.money(-led.balance_cents.int)) in credit" : nil, alert: led.has_overdue.bool),
                Stat(title: "Overdue", value: Fmt.money(led.overdue_cents.int),
                     sub: led.late_count.int > 0 ? "\(led.late_count.int) late payment\(led.late_count.int == 1 ? "" : "s") on record" : nil, alert: led.has_overdue.bool),
                Stat(title: "Spent", value: Fmt.money(st.spend_cents.int)),
            ])
        }
    }

    @ViewBuilder private func actions(_ d: J) -> some View {
        if d.c.archived_at.optString == nil {
            Section("Actions") {
                Button { sheet = .repay } label: { Label("Record repayment without Square", systemImage: "dollarsign.circle") }
                Button { sheet = .tab } label: { Label("Tab limit", systemImage: "slider.horizontal.3") }
                Button { sheet = .points } label: { Label("Adjust points", systemImage: "plusminus.circle") }
            }
        }
    }

    @ViewBuilder private func charges(_ d: J) -> some View {
        let list = d.s.ledger.charges.array.reversed()
        if !list.isEmpty {
            Section("Tab charges · \(list.count)") {
                ForEach(Array(list), id: \.self) { ch in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(Fmt.when(ch.charged_at.string))
                            Spacer()
                            if ch.overdue.bool { Tag(text: "Overdue", color: .red) }
                            else if ch.unpaid_cents.int > 0 { Tag(text: "Open") }
                            else if ch.paid_late.bool { Tag(text: "Paid late", color: .blue) }
                            else { Tag(text: "Paid", color: .green) }
                        }
                        if let items = ch["items"].optString { Text(items).font(.caption).foregroundStyle(.secondary) }
                        if let breach = ch.breach.optString { Text(breach).font(.caption).foregroundStyle(.red) }
                        Text("\(Fmt.money(ch.net_cents.int)) · paid \(Fmt.money(ch.paid_cents.int)) · unpaid \(Fmt.money(ch.unpaid_cents.int)) · due \(Fmt.when(ch.due_at.string, "EEE d MMM"))"
                             + (ch.refunded_cents.int > 0 ? " · \(Fmt.money(ch.refunded_cents.int)) refunded" : ""))
                            .font(.caption).monospacedDigit()
                    }
                }
            }
        }
    }

    @ViewBuilder private func repayments(_ d: J) -> some View {
        let list = d.s.ledger.repayments.array.reversed()
        if !list.isEmpty {
            Section("Repayments · \(list.count)") {
                ForEach(Array(list), id: \.self) { r in
                    Line(left: Fmt.when(r.created_at.string),
                         sub: [r.note.optString, (r.method.string == "writeoff" ? "write-off" : r.method.string) + (r.square_payment_id.isNull ? "" : " · Square"),
                               r.staff.optString ?? "Square"].compactMap { $0 }.joined(separator: " · "),
                         right: Fmt.money(r.amount_cents.int))
                }
            }
        }
    }

    @ViewBuilder private func history(_ d: J) -> some View {
        Section("Points history") {
            if d.points.array.isEmpty { Text("No points yet.").foregroundStyle(.secondary) }
            ForEach(Array(d.points.array.prefix(historyLimit)), id: \.self) { p in
                let who = p.staff.optString.flatMap { $0 == "square" ? nil : $0 }
                Line(left: p.note.optString ?? p.reason.string, sub: [Fmt.when(p.created_at.string), who].compactMap { $0 }.joined(separator: " · "),
                     right: Fmt.points(p.delta.int), rightColor: p.delta.int > 0 ? .green : .primary)
            }
            if d.points.count > historyLimit {
                Button("Show all \(d.points.count)") { historyLimit = .max }
            }
        }
    }

    @State private var historyLimit = 8

    @ViewBuilder private func payments(_ d: J) -> some View {
        if !d.payments.array.isEmpty {
            Section("Square payments") {
                ForEach(Array(d.payments.array.prefix(paymentLimit)), id: \.self) { p in
                    Line(left: p.kind.string.replacingOccurrences(of: "_", with: " "),
                         sub: [Fmt.when(p.created_at.string), p.tender.optString, p["items"].optString].compactMap { $0 }.joined(separator: " · "),
                         right: Fmt.money(p.amount_cents.int))
                }
                if d.payments.count > paymentLimit { Button("Show all \(d.payments.count)") { paymentLimit = .max } }
            }
        }
    }

    @State private var paymentLimit = 8

    @ViewBuilder private func score(_ d: J) -> some View {
        let st = d.s.status, sc = st.score
        Section("Tab score") {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text("\(sc.score.int)").font(.system(size: 40, weight: .bold)).monospacedDigit()
                    .foregroundStyle(sc.score.int >= 80 ? .green : (sc.score.int >= st.min_score.int ? .orange : .red))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Limit \(Fmt.money(sc.limit_cents.int))").bold()
                    if !sc.next.isNull { Text("\(sc.next.score.int)+ gets \(Fmt.money(sc.next.limit_cents.int))").font(.caption).foregroundStyle(.secondary) }
                }
            }
            ForEach(Array(sc.factors.array.enumerated()), id: \.offset) { i, f in
                let pts = f[1].int
                Line(left: f[0].string, right: i == 0 ? "\(pts)" : (pts > 0 ? "+\(pts)" : "\(pts)"), rightColor: pts < 0 ? .red : .primary)
            }
        }
    }

    @ViewBuilder private func more(_ d: J) -> some View {
        let c = d.c
        Section("More") {
            DisclosureGroup("Tab history (\(d.decisions.count))") {
                if d.decisions.array.isEmpty { Text("None.").foregroundStyle(.secondary) }
                ForEach(d.decisions.array, id: \.self) { x in
                    Line(left: x.decision.string.capitalized, sub: [Fmt.when(x.created_at.string), x.note.optString].compactMap { $0 }.joined(separator: " · "), right: x.staff.string)
                }
            }
            DisclosureGroup("Activity log") {
                ForEach(d.audit_rows.array, id: \.self) { a in
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(a.action.string) · \(a.staff.string)").font(.subheadline)
                        Text([Fmt.when(a.created_at.string), a.details.optString].compactMap { $0 }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if c.passcode_set_at.optString != nil {
                Button { sheet = .passcode } label: {
                    Label("Student site passcode (set \(Fmt.when(c.passcode_set_at.string, "d MMM")))", systemImage: "key")
                }
            }
            Button { sheet = .edit } label: { Label("Edit customer", systemImage: "pencil") }
            Button { sheet = .kind } label: { Label("Account type", systemImage: "person.badge.shield.checkmark") }
        }
    }

    // MARK: sheets

    @ViewBuilder private func sheetContent(_ which: Sheet) -> some View {
        let d = page.data
        switch which {
        case .claim: ClaimSheet(id: id, name: customer.name.string, page: page)
        case .repay: RepaySheet(id: id, page: page, owing: d.s.ledger.owing_cents.int)
        case .points: PointsSheet(id: id, page: page)
        case .tab: TabSheet(id: id, page: page, customer: d.c, status: d.s.status, bands: d.bands.array)
        case .passcode: StudentPasscodeSheet(id: id, page: page, customer: d.c, readable: d.passcode_readable.bool)
        case .edit: EditCustomerSheet(id: id, page: page, customer: d.c)
        case .kind: KindSheet(id: id, page: page, kind: d.c.kind.string)
        }
    }

    private func makeQR() async {
        do {
            let (data, _) = try await AppModel.shared.call { try await API.shared.post("customers/\(id)/qr-login") }
            let r = try JSONDecoder().decode(J.self, from: data)
            qr = QRCode(svg: r.svg.string, seconds: r.seconds.int)
        } catch {
            page.flash = Flash(text: error.localizedDescription, ok: false)
        }
    }

    // MARK: new sales

    private func amounts(_ s: J) -> String {
        [s.paid.optString.map { "\($0) paid" }, s.tab.optString.map { "\($0) on tab" }, s.repaid.optString.map { "\($0) repaid" }]
            .compactMap { $0 }.joined(separator: " · ")
    }

    /// The "new sale, claim it?" prompt: only sales from about when the page was opened, and
    /// never claimed without a tap.
    private func watchForSales() async {
        guard customer.archived_at.optString == nil, let start = Fmt.date(page.data.opened.string) else { return }
        let since = ISO8601DateFormatter().string(from: start.addingTimeInterval(-90))
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled, sheet == nil, newSale == nil, page.loaded else { continue }
            if let sales = try? await API.shared.get("api/customers/\(id)/new-sales", ["since": since]),
               let sale = sales.array.first(where: { !dismissedSales.contains($0.key.string) }) {
                newSale = sale
            }
        }
    }

    private func claim(_ sale: J) {
        Task {
            let o = await page.submit("customers/\(id)/claim", [("key", sale.key.string)])
            if !o.ok {   // refused, or the passcode put away: say so, and offer the sale again
                dismissedSales.remove(sale.key.string)
                if page.flash == nil { page.flash = Flash(text: o.message ?? "That didn't work.", ok: false) }
            }
        }
    }
}

// MARK: forms

/// Pick the sale just rung up in Square (with no customer) and give it to this customer.
struct ClaimSheet: View {
    let id: Int
    let name: String
    let page: Page
    @Environment(\.dismiss) private var dismiss
    @State private var claimable: Page
    @State private var error: String?
    @State private var busy = false

    init(id: Int, name: String, page: Page) {
        self.id = id
        self.name = name
        self.page = page
        _claimable = State(initialValue: Page("customers/\(id)/claim"))
    }

    var body: some View {
        NavigationStack {
            List {
                if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                if let note = claimable.data.note.optString { Label("\(note) Showing what Fizzy already has.", systemImage: "info.circle").foregroundStyle(.orange) }
                ForEach(claimable.data.checkouts.array, id: \.self) { k in
                    Button { claim(k) } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Text(k["items"].optString ?? (k.repay_cents.int > 0 ? "Tab repayment" : "Sale")).foregroundStyle(.primary)
                                Spacer()
                                Text(Fmt.when(k.at.string, "HH:mm")).foregroundStyle(.secondary).monospacedDigit()
                            }
                            Text(parts(k)).font(.subheadline).foregroundStyle(.secondary)
                            Text(Fmt.when(k.at.string, "EEE d MMM") + (k.tenders.array.isEmpty ? "" : " · " + k.tenders.strings.joined(separator: " · ")))
                                .font(.caption).foregroundStyle(.secondary)
                            if k.tab_cents.int > 0, !canTab(k) {
                                Text("\(name.split(separator: " ").first.map(String.init) ?? "They") can't put this on a tab - it'll be flagged").font(.caption).foregroundStyle(.red)
                            }
                        }
                    }
                    .disabled(busy)
                }
            }
            .overlay {
                if !claimable.loaded && claimable.error == nil { ProgressView("Asking Square for the latest sales…") }
                else if claimable.loaded && claimable.data.checkouts.array.isEmpty {
                    ContentUnavailableView("No unclaimed sales", systemImage: "tray",
                                           description: Text(claimable.data.square_on.bool
                                                             ? "None from the last \(claimable.data.hours.int) hours. Ring it up in Square without a customer, then pull to refresh."
                                                             : "Square isn't connected yet, so there's nothing to claim. Ask the owner."))
                }
            }
            .navigationTitle("Claim for \(name)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(busy) } }
            .refreshable { await claimable.load() }
            .task { await claimable.load() }
        }
        .interactiveDismissDisabled(busy)
        .passcodeHost()
    }

    private func canTab(_ k: J) -> Bool {
        let st = claimable.data.st
        return st.can_charge.bool && k.tab_cents.int <= st.available_cents.int
    }

    private func parts(_ k: J) -> String {
        [k.sale_cents.int > 0 ? "\(Fmt.money(k.sale_cents.int)) paid" : nil, k.tab_cents.int > 0 ? "\(Fmt.money(k.tab_cents.int)) on tab" : nil,
         k.repay_cents.int > 0 ? "\(Fmt.money(k.repay_cents.int)) repaid" : nil].compactMap { $0 }.joined(separator: " · ")
    }

    private func claim(_ k: J) {
        busy = true
        error = nil
        Task {
            let o = await page.submit("customers/\(id)/claim", [("key", k.key.string)])
            busy = false
            if o.ok { dismiss() } else { error = o.message; page.flash = nil }
        }
    }
}

struct PointsSheet: View {
    let id: Int
    let page: Page
    @State private var delta = ""
    @State private var note = ""

    var body: some View {
        FormSheet(title: "Adjust points", action: "Apply", ready: !delta.isEmpty && !note.trimmingCharacters(in: .whitespaces).isEmpty, page: page,
                  send: { await page.submit("customers/\(id)/points", [("delta", delta), ("note", note)]) }) {
            Section {
                TextField("Points, e.g. +100 or -50", text: $delta).keyboardType(.numbersAndPunctuation)
                TextField("Reason (required)", text: $note)
            } footer: {
                Text("A reason is required. It's kept in the activity log.")
            }
        }
    }
}

struct RepaySheet: View {
    let id: Int
    let page: Page
    let owing: Int
    @State private var amount = ""
    @State private var method = "cash"
    @State private var note = ""
    @State private var separate = false

    var body: some View {
        FormSheet(title: "Repayment", action: "Record", ready: !amount.isEmpty, page: page, send: {
            var form: Params = [("amount", Fmt.normalised(amount)), ("method", method), ("note", note)]
            if separate { form.append(("confirm_dup", "1")) }
            return await page.submit("customers/\(id)/repay", form)
        }) {
            Section {
                TextField("Amount ($)", text: $amount).keyboardType(.decimalPad)
                Picker("Paid by", selection: $method) {
                    Text("Cash").tag("cash"); Text("Card").tag("card"); Text("Other").tag("other")
                }
                .pickerStyle(.segmented)
                TextField("Note (optional)", text: $note)
                Toggle("This is a separate payment (not one already rung up in Square)", isOn: $separate)
            } footer: {
                Text(owing > 0 ? "Owes \(Fmt.money(owing)). Repayments rung up in Square as Tab repayment are recorded by themselves; use this for cash outside Square."
                               : "Nothing owing. Repayments rung up in Square are recorded by themselves.")
            }
        }
        .onAppear { if owing > 0 && amount.isEmpty { amount = dollars(owing) } }
    }
}

/// Their tab limit: put them in a limit band or set the staff adjustment by hand (both a line in
/// their tab score), or turn the tab off and back on.
struct TabSheet: View {
    let id: Int
    let page: Page
    let customer: J
    let status: J
    let bands: [J]   // [score floor, limit cents], smallest first
    @State private var adjust = ""
    @State private var note = ""
    @State private var busy = false
    @State private var error: String?
    @Environment(\.dismiss) private var dismiss

    private var off: Bool { ["rejected", "revoked"].contains(customer.tab_decision.string) }
    private var score: Int { status.score.score.int }
    /// The band they're in now: the last one whose floor is at or under their score (none under the first).
    private var current: Int? { bands.last(where: { $0[0].int <= score })?[0].int }

    private func signed(_ n: Int) -> String { n > 0 ? "+\(n)" : "\(n)" }

    private var summary: String {
        let low = status.status.string == "low_score" ? ", but it's under the \(status.min_score.int) needed for a tab" : ""
        return "Score \(score) gives a \(Fmt.money(status.score.limit_cents.int)) limit\(low)."
    }

    var body: some View {
        NavigationStack {
            Form {
                if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                Section {
                    if off {
                        Text("Turned off by \(customer.tab_decision_by.string) · \(Fmt.when(customer.tab_decision_at.string))").font(.subheadline)
                        if let n = customer.tab_decision_note.optString { Text("“\(n)”").font(.footnote).foregroundStyle(.secondary) }
                    } else {
                        Text(summary).font(.subheadline)
                    }
                    if status.score.adjust.int != 0 {
                        Text("Includes a staff adjustment of \(signed(status.score.adjust.int)).").font(.footnote).foregroundStyle(.secondary)
                    }
                    TextField("Why? (optional)", text: $note)
                }
                Section {
                    ForEach(bands, id: \.self) { b in
                        let here = b[0].int == current
                        Button { send([("band", "\(b[0].int)")]) } label: {
                            HStack {
                                Text(Fmt.money(b[1].int))
                                Spacer()
                                if here { Image(systemName: "checkmark") } else { Text("Score \(b[0].int)").foregroundStyle(.secondary) }
                            }
                        }
                        .disabled(here)
                    }
                } header: {
                    Text("Set their limit")
                } footer: {
                    Text("Sets their score to the start of that limit with a staff adjustment. Later payments still move it up or down.")
                }
                Section {
                    HStack {
                        TextField("Adjustment, e.g. +20 or -10", text: $adjust).keyboardType(.numbersAndPunctuation)
                        Button("Set") { send([("decision", "adjust"), ("adjust", adjust)]) }
                            .buttonStyle(.bordered)
                    }
                } footer: {
                    Text("A line in their tab score, from -100 to +100. Leave it empty and tap Set to clear it.")
                }
                Section {
                    if off {
                        Button("Turn tab back on") { send([("decision", "on")]) }
                    } else {
                        ConfirmButton(title: "Turn tab off", message: "Turn \(customer.name.string)'s tab off? Their score stays; you can turn it back on.",
                                      confirmLabel: "Turn off") { send([("decision", "off")]) }
                    }
                }
            }
            .disabled(busy)
            .navigationTitle("Tab limit")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(busy) } }
        }
        .interactiveDismissDisabled(busy)
        .passcodeHost()
        .onAppear {
            let a = status.score.adjust.int
            adjust = a == 0 ? "" : signed(a)
        }
    }

    private func send(_ form: Params) {
        busy = true
        error = nil
        Task {
            let o = await page.submit("customers/\(id)/tab", form + [("note", note)])
            busy = false
            if o.ok { dismiss() } else { error = o.message; page.flash = nil }
        }
    }
}

/// The passcode the student signs in to the student site with.
struct StudentPasscodeSheet: View {
    let id: Int
    let page: Page
    let customer: J
    let readable: Bool
    @State private var passcode = ""
    @State private var shown: String?
    @State private var error: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        FormSheet(title: "Student passcode", action: "Set", ready: passcode.count >= 4, page: page,
                  send: { await page.submit("customers/\(id)/passcode", [("passcode", passcode)]) }) {
            if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
            if customer.passcode_set_at.optString != nil {
                Section("Current passcode") {
                    if let shown { Text(shown).font(.title3.monospaced()).textSelection(.enabled) }
                    else if readable { Button("Show passcode") { Task { await reveal() } } }
                    else { Text("Can't be read (the secret key changed) - set a new one.").foregroundStyle(.red) }
                }
            }
            Section {
                TextField("New passcode (4 or more characters)", text: $passcode)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            }
            if customer.passcode_set_at.optString != nil {
                Section {
                    ConfirmButton(title: "Remove passcode", message: "Remove \(customer.name.string)'s passcode?", confirmLabel: "Remove") {
                        Task { if (await page.submit("customers/\(id)/passcode", [("remove", "1")])).ok { dismiss() } }
                    }
                }
            }
        }
    }

    private func reveal() async {
        do {
            let (data, _) = try await AppModel.shared.call { try await API.shared.post("customers/\(id)/passcode/reveal") }
            shown = try JSONDecoder().decode(J.self, from: data).passcode.optString ?? "(none)"
        } catch { self.error = error.localizedDescription }
    }
}

struct EditCustomerSheet: View {
    let id: Int
    let page: Page
    let customer: J
    @State private var name = ""
    @State private var squareID = ""
    @State private var archived = false

    var body: some View {
        FormSheet(title: "Edit customer", page: page, send: {
            var form: Params = [("name", name), ("square_customer_id", squareID)]
            if archived { form.append(("archived", "1")) }
            return await page.submit("customers/\(id)/edit", form)
        }) {
            Section {
                TextField("Name", text: $name)
                TextField("Square customer ID", text: $squareID).textInputAutocapitalization(.never).autocorrectionDisabled().font(.body.monospaced())
                Toggle("Archived - hidden from search", isOn: $archived)
            }
            Section {
                ConfirmButton(title: "Sign out their phones", message: "Sign \(customer.name.string) out of the student site on every phone?", confirmLabel: "Sign out") {
                    page.act("customers/\(id)/passcode/signout")
                }
            }
        }
        .onAppear {
            name = customer.name.string
            squareID = customer.square_customer_id.string
            archived = customer.archived_at.optString != nil
        }
    }
}

struct KindSheet: View {
    let id: Int
    let page: Page
    @State var kind: String

    var body: some View {
        FormSheet(title: "Account type", action: "Change type", page: page,
                  send: { await page.submit("customers/\(id)/kind", [("kind", kind)]) }) {
            Picker("Account type", selection: $kind) {
                Text("Normal customer").tag("")
                Text("Admin (one of us)").tag("admin")
                Text("Test account").tag("test")
            }
            .pickerStyle(.inline)
            .labelsHidden()
        }
    }
}

// MARK: sign-in code

/// A one-time QR code that signs the customer in to the student site. Works once, briefly.
struct QRSheet: View {
    let code: CustomerView.QRCode
    @Environment(\.dismiss) private var dismiss
    @State private var left: Int

    init(code: CustomerView.QRCode) {
        self.code = code
        _left = State(initialValue: code.seconds)
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                SVGView(svg: code.svg).frame(width: 280, height: 280).opacity(left > 0 ? 1 : 0.15)
                Text(left > 0 ? "Scan with the phone camera. Works once, for \(left) more second\(left == 1 ? "" : "s")." : "Expired. Close this and make a new one.")
                    .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            .padding()
            .navigationTitle("Sign-in code")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task {
                while left > 0 && !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(1))
                    left -= 1
                }
            }
        }
        .presentationDetents([.medium])
    }
}

struct SVGView: UIViewRepresentable {
    let svg: String
    func makeUIView(context: Context) -> WKWebView {
        let web = WKWebView()
        web.isOpaque = false
        web.backgroundColor = .white
        web.scrollView.isScrollEnabled = false
        web.loadHTMLString("<html><body style='margin:0;background:#fff'><style>svg{width:100%;height:100%}</style>\(svg)</body></html>", baseURL: nil)
        return web
    }
    func updateUIView(_ view: WKWebView, context: Context) {}
}

import SwiftUI

/// Every action in Fizzy, searchable, each opening where it lives (the website's Tools index).
/// Nothing is only here: tools also sit on the page of the thing they change.
struct ToolsView: View {
    @State private var page = Page("tools")
    @State private var query = ""

    var body: some View {
        PageList(page: page, title: "Tools") { d in
            let items = d.index.array.filter { matches($0) }
            let sections = items.reduce(into: [String]()) { if !$0.contains($1.section.string) { $0.append($1.section.string) } }
            ForEach(sections, id: \.self) { section in
                Section(section == "Pages" ? "On their pages" : section) {
                    ForEach(items.filter { $0.section.string == section }, id: \.self) { item in row(item, d) }
                }
            }
            if items.isEmpty && !query.isEmpty { ContentUnavailableView.search(text: query) }
        }
        .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Find an action")
    }

    private func matches(_ item: J) -> Bool {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return true }
        return [item.label, item.tip, item.where].contains { $0.string.lowercased().contains(q) }
    }

    @ViewBuilder private func row(_ item: J, _ d: J) -> some View {
        let id = item.id.string
        let label = VStack(alignment: .leading, spacing: 2) {
            Text(item.label.string).foregroundStyle(ToolSpec.danger.contains(id) ? Color.red : Color.primary)
            Text(item.where.string).font(.caption).foregroundStyle(.secondary)
        }
        if id.hasPrefix("export_") {
            DownloadButton(title: item.label.string, path: "tools/export/\(id.dropFirst(7)).csv")
        } else if !id.isEmpty && !ToolSpec.entry(id, d).isNull {
            NavigationLink { ToolFormView(page: page, id: id, label: item.label.string, tip: item.tip.string) } label: { label }
        } else if let place = Place(href: item.href.string) {
            NavigationLink { place.view } label: { label }
        } else {
            label
        }
    }
}

/// What a tool's form asks for (the same fields as the website's).
enum Field {
    case person(name: String, label: String)
    case text(name: String, label: String, required: Bool = true, keyboard: UIKeyboardType = .default)
    case number(name: String, label: String, value: String = "")
    case date(name: String, label: String, value: String)   // value: "today", "week_ago" or a fixed day
    case area(name: String, label: String, required: Bool = false)
    case check(name: String, label: String)
    case select(name: String, label: String, options: [(String, String)])
    case confirm(word: String)
}

enum ToolSpec {
    static let danger: Set<String> = ["reset_points", "revoke_all", "write_off", "merge", "sign_out_all", "bulk_archive", "delete_test_accounts"]

    /// Reports with nothing to fill in: they run as soon as they're opened.
    static let instant: Set<String> = ["chase", "balances", "integrity"]

    /// Names for links, before Tools' data has loaded (the server's names are used once it has).
    static let names: [String: String] = [
        "bulk_points": "Give points to many", "bonus_points": "Bonus points for a period", "transfer_points": "Move points",
        "reset_points": "Reset all points", "pause_tabs": "Pause all tabs", "revoke_all": "Turn off all tabs",
        "manual_charge": "Add a tab charge", "write_off": "Write off", "extend_due": "More time to pay",
        "add_customer": "Add customer", "merge": "Merge two customers", "bulk_archive": "Archive customers",
        "restore": "Restore customers", "test_accounts": "Test accounts", "delete_test_accounts": "Delete test accounts",
        "takings": "Takings report", "chase": "Who to chase", "balances": "All balances", "leaderboard": "Leaderboard",
        "raffle": "Raffle", "sync_now": "Sync with Square now", "resync": "Re-sync from a date", "history": "Import sales history",
        "find_payment": "Find a payment", "export": "Export", "download_backup": "Download a backup",
        "integrity": "Check the database", "notice": "Noticeboard", "sign_out_all": "Sign everyone out",
    ]

    /// [id, label, tip] for a tool, from Tools' groups.
    static func entry(_ id: String, _ d: J) -> J {
        for group in d.tools.array { if let t = group[1].array.first(where: { $0[0].string == id }) { return t } }
        return .null
    }

    static func note(_ label: String = "Note (optional)", required: Bool = false) -> Field { .text(name: "note", label: label, required: required) }

    static func fields(_ id: String, _ d: J) -> [Field] {
        switch id {
        case "bulk_points": [.number(name: "delta", label: "Points (+ add / − take)"), note("Reason (required)", required: true),
                             .area(name: "people", label: "People - one per line (names, emails or student numbers)"),
                             .check(name: "everyone", label: "Everyone (all active customers) instead")]
        case "bonus_points": [.number(name: "multiplier", label: "Multiplier", value: "2"), .date(name: "start", label: "From", value: "today"),
                              .date(name: "end", label: "To", value: "today"), note()]
        case "transfer_points": [.person(name: "from", label: "From"), .person(name: "to", label: "To"), .number(name: "points", label: "Points"), note()]
        case "reset_points": [note("Reason"), .confirm(word: "RESET")]
        case "revoke_all": [note("Reason"), .confirm(word: "REVOKE")]
        case "manual_charge": [.person(name: "customer", label: "Customer"), .text(name: "amount", label: "Amount ($)", keyboard: .decimalPad),
                               .text(name: "items", label: "Items", required: false)]
        case "write_off": [.person(name: "customer", label: "Customer"),
                           .text(name: "amount", label: "Amount ($) - leave blank for everything owed", required: false, keyboard: .decimalPad),
                           note("Reason (required)", required: true)]
        case "extend_due": [.person(name: "customer", label: "Customer"), .number(name: "days", label: "Extra days", value: "7"), note()]
        case "add_customer": [.text(name: "name", label: "Name"), .text(name: "email", label: "Email", keyboard: .emailAddress)]
        case "test_accounts": [.number(name: "n", label: "How many", value: "1")]
        case "delete_test_accounts": [.confirm(word: "DELETE")]
        case "merge": [.person(name: "keep", label: "Keep this one"), .person(name: "dup", label: "Merge this duplicate into it")]
        case "bulk_archive": [.area(name: "people", label: "People - one per line", required: true), note()]
        case "restore": [.area(name: "people", label: "People - one per line (\(d.archived.count) archived)"), .check(name: "all", label: "Restore everyone archived")]
        case "takings": [.date(name: "start", label: "From", value: "today"), .date(name: "end", label: "To", value: "today")]
        case "leaderboard": [.number(name: "n", label: "How many", value: "10")]
        case "raffle": [.select(name: "pool", label: "Who's in the draw", options: [("bought", "Bought something between these dates"), ("points", "Anyone with points"), ("all", "Every active customer")]),
                        .date(name: "start", label: "From", value: "week_ago"), .date(name: "end", label: "To", value: "today"),
                        .number(name: "n", label: "Winners", value: "1"), .check(name: "weighted", label: "Weight by points")]
        case "resync": [.date(name: "start", label: "From", value: "week_ago")]
        case "history": [.date(name: "launch", label: "Fizzy's launch date (sales before this earn nothing)",
                               value: d.history_before.optString ?? "today"), .date(name: "start", label: "Import from", value: "2024-01-01")]
        case "find_payment": [.text(name: "payment_id", label: "Square payment ID")]
        case "notice": [.area(name: "notice", label: "Message (empty to clear)")]
        case "sign_out_all": [.confirm(word: "SIGN OUT")]
        default: []   // chase, balances, integrity, sync_now, pause_tabs, export, download_backup
        }
    }
}

/// A tool, opened from the page of the thing it changes. Its form needs Tools' data (the people to pick
/// from, today's date...), which other pages don't send, so it loads that; done, the page it came from reloads.
struct ToolLink: View {
    let id: String
    var label: String? = nil
    var systemImage: String? = nil
    var prefill: [String: String] = [:]
    var from: Page? = nil

    var body: some View {
        NavigationLink {
            ToolHost(id: id, prefill: prefill, from: from)   // titled with the tool's own name; `label` is just this link's wording
        } label: {
            let title = label ?? ToolSpec.names[id] ?? id
            // Coloured inside the label, so the list still lines its divider up with the text.
            if let systemImage, ToolSpec.danger.contains(id) {
                Label(title, systemImage: systemImage).foregroundStyle(.red)
            } else if let systemImage {
                Label(title, systemImage: systemImage)
            } else if ToolSpec.danger.contains(id) {
                Text(title).foregroundStyle(.red)
            } else {
                Text(title)
            }
        }
    }
}

struct ToolHost: View {
    let id: String
    var label: String?
    var prefill: [String: String] = [:]
    var from: Page?
    @State private var page = Page("tools")

    var body: some View {
        Group {
            if page.loaded {
                let t = ToolSpec.entry(id, page.data)
                ToolFormView(page: page, id: id, label: label ?? t[1].optString ?? ToolSpec.names[id] ?? id,
                             tip: t[2].string, prefill: prefill, from: from)
            } else if let error = page.error {
                ContentUnavailableView("Couldn't open it", systemImage: "wifi.exclamationmark", description: Text(error))
            } else {
                ProgressView()
            }
        }
        .task { if !page.loaded { _ = await page.load() } }
    }
}

struct ToolFormView: View {
    let page: Page
    let id: String
    let label: String
    let tip: String
    var prefill: [String: String] = [:]
    var from: Page? = nil
    @Environment(\.dismiss) private var dismiss
    @State private var text: [String: String] = [:]
    @State private var checks: Set<String> = []
    @State private var error: String?
    @State private var busy = false
    @State private var report: Pick?
    @State private var ranOnce = false

    private var d: J { page.data }
    private var fields: [Field] { ToolSpec.fields(id, d) }

    var body: some View {
        Form {
            if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
            Section { Text(tip).font(.footnote).foregroundStyle(.secondary) }
            if id == "export" {
                Section { ForEach(d.exports.strings, id: \.self) { DownloadButton(title: $0.replacingOccurrences(of: "_", with: " "), path: "tools/export/\($0).csv") } }
            } else if id == "download_backup" {
                Section { DownloadButton(title: "Download fizzy.db", path: "tools/backup.db") }
            } else {
                if id == "pause_tabs" {
                    Section {
                        Text("Tabs are currently ") + Text(d.cfg.tabs_paused.bool ? "PAUSED" : "on").bold().foregroundStyle(d.cfg.tabs_paused.bool ? .red : .primary)
                        TextField("Note (optional)", text: binding("note"))
                    }
                }
                if id == "sync_now" && !d.square_on.bool { Section { Text("Square isn't configured.").foregroundStyle(.red) } }
                let fs = fields
                if !fs.isEmpty {
                    Section {
                        ForEach(Array(fs.enumerated()), id: \.offset) { _, f in field(f) }
                    }
                }
                Section {
                    Button(role: ToolSpec.danger.contains(id) ? .destructive : nil) { run() } label: {
                        HStack { Spacer(); if busy { ProgressView() } else { Text(runLabel).bold() }; Spacer() }
                    }
                    .disabled(busy || !ready)
                }
            }
        }
        .navigationTitle(label)
        .navigationBarTitleDisplayMode(.inline)
        .scrollDismissesKeyboard(.interactively)
        .toolbar { KeyboardDoneButton() }
        .onAppear {
            defaults()
            if ToolSpec.instant.contains(id) && !ranOnce { ranOnce = true; run() }
        }
        .sheet(item: $report, onDismiss: { if ToolSpec.instant.contains(id) { dismiss() } }) { ReportSheet(report: $0.value) }
    }

    private var runLabel: String {
        switch id {
        case "pause_tabs": d.cfg.tabs_paused.bool ? "Turn tabs back on" : "Pause all tabs"
        case "notice": d.notice.optString == nil ? "Save note" : "Save"
        default: "Run"
        }
    }

    private func binding(_ name: String) -> Binding<String> {
        Binding(get: { text[name] ?? "" }, set: { text[name] = $0 })
    }

    @ViewBuilder private func field(_ f: Field) -> some View {
        switch f {
        case .person(let name, let label): PersonField(title: label, text: binding(name), people: d.people.array)
        case .text(let name, let label, _, let kb): TextField(label, text: binding(name)).keyboardType(kb).textInputAutocapitalization(kb == .emailAddress ? .never : .sentences)
        case .number(let name, let label, _):
            LabeledContent(label) {
                TextField(label, text: binding(name)).keyboardType(.numbersAndPunctuation)
                    .multilineTextAlignment(.trailing).monospacedDigit()
            }
        case .date(let name, let label, _): DayField(title: label, text: binding(name))
        case .area(let name, let label, _):
            VStack(alignment: .leading) {
                Text(label).font(.footnote).foregroundStyle(.secondary)
                TextEditor(text: binding(name)).frame(minHeight: 80).textInputAutocapitalization(name == "notice" ? .sentences : .words)
            }
        case .check(let name, let label):
            Toggle(label, isOn: Binding(get: { checks.contains(name) }, set: { if $0 { checks.insert(name) } else { checks.remove(name) } }))
        case .select(let name, let label, let options):
            VStack(alignment: .leading) {
                Text(label).font(.footnote).foregroundStyle(.secondary)
                Picker(label, selection: binding(name)) { ForEach(options, id: \.0) { Text($0.1).tag($0.0) } }
                    .labelsHidden().pickerStyle(.menu)
            }
        case .confirm(let word):
            TextField("Type \(word) to confirm", text: binding("confirm")).textInputAutocapitalization(.characters).autocorrectionDisabled()
        }
    }

    /// Required things filled in, so the button isn't pressable on an empty form.
    private var ready: Bool {
        for f in fields {
            switch f {
            case .person(let n, _): if (text[n] ?? "").isEmpty { return false }
            case .text(let n, _, let req, _): if req && (text[n] ?? "").isEmpty { return false }
            case .number(let n, _, _): if (text[n] ?? "").isEmpty { return false }
            case .area(let n, _, let req): if req && (text[n] ?? "").isEmpty { return false }
            case .confirm: if (text["confirm"] ?? "").isEmpty { return false }
            default: break
            }
        }
        if id == "bulk_points" && !checks.contains("everyone") && (text["people"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return false }
        if id == "bulk_points" && (text["note"] ?? "").isEmpty { return false }
        if id == "write_off" && (text["note"] ?? "").isEmpty { return false }
        return true
    }

    private func defaults() {
        guard text.isEmpty else { return }
        for f in fields {
            switch f {
            case .number(let n, _, let v): text[n] = v
            case .date(let n, _, let v): text[n] = v == "today" ? d.today.string : (v == "week_ago" ? d.week_ago.string : v)
            case .select(let n, _, let opts): text[n] = opts.first?.0 ?? ""
            case .area("notice", _, _): text["notice"] = d.notice.string.components(separatedBy: "\n— ").first ?? ""
            default: break
            }
        }
        if id == "pause_tabs" { text["paused"] = d.cfg.tabs_paused.bool ? "0" : "1" }
        for (k, v) in prefill { text[k] = v }
    }

    private func run() {
        busy = true
        error = nil
        var form: Params = []
        for (k, v) in text.sorted(by: { $0.key < $1.key }) { form.append((k, v)) }
        for c in checks { form.append((c, "1")) }
        Task {
            let o = await page.submit("tools/\(id)", form)
            busy = false
            if let body = o.body { report = Pick(value: body); return }
            // The website shows a refusal as "<tool name>: <what was wrong>"
            if let m = o.message, m.hasPrefix("\(label):") || !o.ok { error = m; page.flash = nil; return }
            if let from { from.flash = page.flash; Task { _ = await from.load() } }
            dismiss()
        }
    }
}

/// A table the server made (who to chase, balances, a raffle...).
struct ReportSheet: View {
    let report: J
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if let note = report.note.optString { Text(note).font(.footnote).foregroundStyle(.secondary) }
                ForEach(Array(report.rows.array.enumerated()), id: \.offset) { _, row in
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(Array(report.headers.array.enumerated()).filter { !row[$0.offset].string.isEmpty || !row[$0.offset].text.isNull }, id: \.offset) { i, h in
                            let cell = row[i]
                            HStack(alignment: .firstTextBaseline) {
                                Text(h.string).font(.caption).foregroundStyle(.secondary).frame(width: 90, alignment: .leading)
                                if let href = cell.href.optString, let id = Int(href.split(separator: "/").last ?? "") {
                                    NavigationLink(cell.text.string, value: CustomerID(id: id))
                                } else {
                                    Text(cell.string).textSelection(.enabled)
                                }
                            }
                        }
                    }
                }
                if report.rows.array.isEmpty { Text("Nothing to show.").foregroundStyle(.secondary) }
                ForEach(report.copy.array, id: \.self) { c in
                    if let t = c[1].optString {
                        VStack(alignment: .leading) {
                            Text(c[0].string).font(.caption).foregroundStyle(.secondary)
                            Text(t).textSelection(.enabled).font(.footnote)
                        }
                    }
                }
            }
            .navigationTitle(report.title.string)
            .navigationBarTitleDisplayMode(.inline)
            .fizzyDestinations()
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
        .passcodeHost()   // a customer opened from the report can need the passcode
    }
}

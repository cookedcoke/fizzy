import SwiftUI

/// Every tool on the website's Tools page, grouped the same way.
struct ToolsView: View {
    @State private var page = Page("tools")

    var body: some View {
        PageList(page: page, title: "Tools") { d in
            ForEach(Array(d.tools.array.enumerated()), id: \.offset) { _, group in
                Section(group[0].string) {
                    ForEach(group[1].array, id: \.self) { t in
                        let id = t[0].string
                        NavigationLink {
                            ToolFormView(page: page, id: id, label: t[1].string, tip: t[2].string)
                        } label: {
                            Text(t[1].string).foregroundStyle(ToolSpec.danger.contains(id) ? .red : .primary)
                        }
                    }
                }
            }
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
        case "approve_eligible": [note()]
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

struct ToolFormView: View {
    let page: Page
    let id: String
    let label: String
    let tip: String
    @Environment(\.dismiss) private var dismiss
    @State private var text: [String: String] = [:]
    @State private var checks: Set<String> = []
    @State private var error: String?
    @State private var busy = false
    @State private var report: Pick?

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
        .onAppear(perform: defaults)
        .sheet(item: $report) { ReportSheet(report: $0.value) }
    }

    private var runLabel: String {
        id == "pause_tabs" ? (d.cfg.tabs_paused.bool ? "Turn tabs back on" : "Pause all tabs") : "Run"
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
                        ForEach(Array(report.headers.array.enumerated()), id: \.offset) { i, h in
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

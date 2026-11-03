import SwiftUI

/// The website's Settings page: who can do what, tab rules, the customer display, updates,
/// passcodes, backups and devices.
struct ServerSettingsView: View {
    @State private var page = Page("settings")

    var body: some View {
        PageList(page: page, title: "Settings") { d in
            Section { Text("Fizzy version \(d.update.version.string)").font(.footnote).foregroundStyle(.secondary) }
            NavigationLink("Who can do what") { PermissionsForm(page: page) }
            NavigationLink("Tab rules") { TabRulesForm(page: page) }
            NavigationLink("Customer display") { DisplayForm(page: page) }
            NavigationLink("Term dates") { TextBlockForm(page: page, title: "Term dates", path: "settings/terms", field: "terms",
                                                         initial: d.terms.array.map(line).joined(separator: "\n"),
                                                         placeholder: "2026-10-13 to 2026-12-11 Term 4") }
            NavigationLink("Closed days") { TextBlockForm(page: page, title: "Closed days", path: "settings/closed", field: "days",
                                                          initial: d.closed.array.map(line).joined(separator: "\n"),
                                                          placeholder: "2026-12-12 to 2027-01-31 Summer holidays") }
            NavigationLink("Ask (Gemini)") { AskKeyForm(page: page) }
            NavigationLink("PIN") { PinForm(page: page) }
            NavigationLink("Maintenance mode") { MaintenanceForm(page: page) }
            updates(d)
            backups(d)
            NavigationLink("Security keys") { SecurityKeysView(page: page) }
                .badge(d.passkey_list.count)
            NavigationLink("Devices") { DevicesView(page: page) }
        }
    }

    private func line(_ r: J) -> String {
        let a = r[0].string, b = r[1].string, label = r[2].string
        return a + (b != a ? " to \(b)" : "") + (label.isEmpty ? "" : " \(label)")
    }

    @ViewBuilder private func updates(_ d: J) -> some View {
        let u = d.update
        Section("Updates") {
            LabeledContent("Running", value: u.version.string)
            if let at = u.at.optString {
                LabeledContent("Last update") {
                    Text("\(at) · " + (["ok": "worked", "failed": "failed, rolled back", "running": "running now", "current": "already up to date"][u.state.string] ?? u.state.string))
                        .multilineTextAlignment(.trailing)
                }
                if let m = u.message.optString { Text(m).font(.caption).foregroundStyle(.secondary) }
            }
            if u.watching.bool { LabeledContent("New", value: u.behind.int > 0 ? "\(u.behind.int) new change\(u.behind.int == 1 ? "" : "s") ready" : "Nothing new") }
            if u.unreadable.bool { Text("Can't read the updater's status.").font(.footnote).foregroundStyle(.orange) }
            else if !u.watching.bool { Text("Updater isn't running on the home PC.").font(.footnote).foregroundStyle(.orange) }
            else if u.requested.bool { Text("Update requested.").font(.footnote).foregroundStyle(.secondary) }
            else {
                ConfirmButton(title: "Update now", systemImage: "arrow.down.circle", message: "Update now? Down for about a minute.", confirmLabel: "Update", role: nil) {
                    page.act("settings/update")
                }
            }
        }
    }

    @ViewBuilder private func backups(_ d: J) -> some View {
        Section("Backups") {
            LabeledContent("Folder", value: d.backup_dir.optString ?? "Not set up yet")
            LabeledContent("Last", value: d.last_backup.optString.map { Fmt.when($0) } ?? "Never")
            Button { page.act("settings/backup") } label: { Label("Back up now", systemImage: "externaldrive.badge.checkmark") }
            DownloadButton(title: "Download the database", path: "tools/backup.db")
        }
    }
}

// MARK: who can do what

struct PermissionsForm: View {
    let page: Page
    @State private var perms: [String: String] = [:]
    @State private var bigPoints = ""
    @State private var bigDollars = ""
    @State private var error: String?
    @State private var busy = false
    @Environment(\.dismiss) private var dismiss

    /// Where each group starts, as on the website: an action listed here opens a new section.
    private let groupStarts = ["claim": "Everyday", "points": "Points and rewards", "tab_decisions": "Tabs", "customers": "Customers",
                               "ask": "Ask (Gemini)", "qr_login": "Customers", "money": "Money", "school_tools": "Big stuff",
                               "admin_accounts": "Special accounts"]
    private let levels = [("open", "Anyone"), ("pin", "PIN"), ("owner", "Owner")]

    private struct Group: Identifiable { let id: Int; let title: String; let actions: [(id: String, label: String)] }

    /// The actions in the server's order, cut into sections.
    private var groups: [Group] {
        var out: [Group] = []
        for a in page.data.actions.array {
            let aid = a[0].string
            if let title = groupStarts[aid] {
                out.append(Group(id: out.count, title: title, actions: []))
            } else if out.isEmpty {
                out.append(Group(id: 0, title: "Everyday", actions: []))
            }
            let g = out.removeLast()
            out.append(Group(id: g.id, title: g.title, actions: g.actions + [(aid, a[1].string)]))
        }
        return out
    }

    var body: some View {
        Form {
            if let error { Section { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) } }
            Section {
                Text("Pick who can do each thing. “Anyone” is anyone signed in; “PIN” asks for the counter PIN first; “Owner” needs the owner's password.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(groups) { g in
                Section(g.title) {
                    ForEach(g.actions, id: \.id) { a in row(a.label, key: a.id) }
                }
            }
            Section {
                row("Big changes by hand", key: "big")
                LabeledContent("More than (points)") {
                    TextField("500", text: $bigPoints).keyboardType(.numberPad).multilineTextAlignment(.trailing).monospacedDigit()
                }
                LabeledContent("Or more than ($)") {
                    TextField("50", text: $bigDollars).keyboardType(.numberPad).multilineTextAlignment(.trailing).monospacedDigit()
                }
            } header: {
                Text("Limits")
            } footer: {
                Text("A change bigger than either number, or anything for everyone at once, needs the level chosen above.")
            }
            Section {
                Button { Task { await save() } } label: {
                    HStack { Spacer(); if busy { ProgressView() } else { Text("Save").bold() }; Spacer() }
                }
                .disabled(busy)
            }
        }
        .navigationTitle("Who can do what")
        .navigationBarTitleDisplayMode(.inline)
        .scrollDismissesKeyboard(.interactively)
        .toolbar { KeyboardDoneButton() }
        .onAppear {
            for (k, v) in page.data.perms.dict where !["big_points", "big_dollars"].contains(k) { perms[k] = v.string }
            bigPoints = page.data.perms.big_points.string
            bigDollars = page.data.perms.big_dollars.string
        }
    }

    /// One thing and who can do it: its name, then the three choices under it, the same on every row.
    private func row(_ label: String, key: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(label)
            Picker(label, selection: Binding(get: { perms[key] ?? "open" }, set: { perms[key] = $0 })) {
                ForEach(levels, id: \.0) { Text($0.1).tag($0.0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
        }
        .padding(.vertical, 2)
    }

    private func save() async {
        busy = true
        error = nil
        var form: Params = perms.sorted { $0.key < $1.key }.map { ($0.key, $0.value) }
        form += [("big_points", bigPoints), ("big_dollars", bigDollars)]
        let o = await page.submit("settings/permissions", form)
        busy = false
        if o.ok { dismiss() } else { error = o.message; page.flash = nil }
    }
}

// MARK: simple forms

/// A settings form: fields, a Save button, back to the list when it worked.
struct SettingsForm<Content: View>: View {
    let page: Page
    let title: String
    var action = "Save"
    var ready = true
    let send: () async -> Outcome
    @ViewBuilder var content: Content
    @Environment(\.dismiss) private var dismiss
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        Form {
            if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
            content
            Section {
                Button {
                    busy = true
                    error = nil
                    Task {
                        let o = await send()
                        busy = false
                        if o.ok { dismiss() } else { error = o.message; page.flash = nil }
                    }
                } label: {
                    HStack { Spacer(); if busy { ProgressView() } else { Text(action).bold() }; Spacer() }
                }
                .disabled(!ready || busy)
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .scrollDismissesKeyboard(.interactively)
        .toolbar { KeyboardDoneButton() }
    }
}

struct TabRulesForm: View {
    let page: Page
    private func money(_ text: Binding<String>) -> some View {
        HStack(spacing: 1) {
            Spacer(minLength: 0)
            Text("$").foregroundStyle(.secondary)
            TextField("0.00", text: text).keyboardType(.decimalPad).fixedSize().monospacedDigit()
        }
    }
    @State private var limit = ""
    @State private var days = ""
    @State private var minScore = ""

    var body: some View {
        SettingsForm(page: page, title: "Tab rules", send: {
            await page.submit("settings", [("tab_limit", limit), ("due_days", days), ("min_score", minScore)])
        }) {
            Section {
                // Short labels, so none are cut off; the values don't need much room.
                LabeledContent("Highest limit") { money($limit) }
                LabeledContent("Days to pay") { TextField("0", text: $days).keyboardType(.numberPad).multilineTextAlignment(.trailing).monospacedDigit() }
                LabeledContent("Minimum score") { TextField("0", text: $minScore).keyboardType(.numberPad).multilineTextAlignment(.trailing).monospacedDigit() }
            } footer: {
                Text("Everyone whose tab score is at the minimum or over has a tab, sized by their score. The score is 0 to 100.")
            }
        }
        .onAppear {
            let c = page.data.cfg
            limit = dollars(c.tab_limit_cents.int); days = "\(c.tab_due_days.int)"
            minScore = "\(c.tab_min_score.int)"
        }
    }
}

struct DisplayForm: View {
    let page: Page
    @State private var message = ""
    @State private var shown: Set<String> = []
    @State private var owing = true

    var body: some View {
        let menu = page.data.display_menu.array
        SettingsForm(page: page, title: "Customer display", send: {
            var form: Params = [("message", message)]
            if !menu.isEmpty { form.append(("menu", "1")); for id in shown.sorted() { form.append(("show", id)) } }
            if owing { form.append(("owing", "1")) }
            return await page.submit("settings/display", form)
        }) {
            Section("Message") { TextField("None", text: $message) }
            if !menu.isEmpty {
                Section("On the shelf (up to 12)") {
                    ForEach(menu, id: \.self) { m in
                        let id = m.item_id.string
                        Toggle(isOn: Binding(get: { shown.contains(id) }, set: { if $0 { shown.insert(id) } else { shown.remove(id) } })) {
                            HStack { Text(m.item_name.string); Spacer(); Text(Fmt.money(m.price.int)).foregroundStyle(.secondary) }
                        }
                    }
                }
            }
            Section { Toggle("Show what they owe", isOn: $owing) }
        }
        .onAppear {
            let d = page.data
            message = d.display_message.string
            owing = d.display_owing.bool
            let hidden = Set(d.display_hidden.strings)
            shown = Set(d.display_menu.array.map { $0.item_id.string }.filter { !hidden.contains($0) })
        }
    }
}

/// Term dates and closed days: one per line.
struct TextBlockForm: View {
    let page: Page
    let title: String
    let path: String
    let field: String
    let initial: String
    let placeholder: String
    @State private var text = ""

    var body: some View {
        SettingsForm(page: page, title: title, send: { await page.submit(path, [(field, text)]) }) {
            Section {
                TextEditor(text: $text).frame(minHeight: 140).font(.body.monospaced())
            } footer: {
                Text("One per line, e.g. \(placeholder)")
            }
        }
        .onAppear { text = initial }
    }
}

struct AskKeyForm: View {
    let page: Page
    @State private var key = ""
    @State private var model = ""

    var body: some View {
        let g = page.data.gemini
        SettingsForm(page: page, title: "Ask (Gemini)", send: { await page.submit("settings/ask", [("key", key), ("model_name", model)]) }) {
            if g.env.bool {
                Section { Text("The key comes from the server's .env.").font(.footnote) }
            } else {
                Section {
                    SecureField(g.hint.optString != nil ? "Keep saved key (ends \(g.hint.string))" : "Paid key from aistudio.google.com", text: $key)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                }
            }
            Section("Model") { TextField(g.default.string, text: $model).textInputAutocapitalization(.never).autocorrectionDisabled() }
            if g.hint.optString != nil && !g.env.bool {
                Section {
                    ConfirmButton(title: "Remove key", message: "Remove the Gemini key? Ask stops working.", confirmLabel: "Remove") {
                        page.act("settings/ask", [("remove", "1")])
                    }
                }
            }
        }
        .onAppear { model = page.data.gemini.model.string }
    }
}

struct PinForm: View {
    let page: Page
    @State private var pin = ""

    var body: some View {
        let d = page.data
        SettingsForm(page: page, title: "PIN", action: d.pin_on.bool ? "Change PIN" : "Set PIN", ready: pin.count >= 4,
                     send: { await page.submit("settings/pin", [("new", pin)]) }) {
            if !d.admin_on.bool { Section { Text("No owner password is set on the server (FIZZY_ADMIN_PASSWORD).").font(.footnote).foregroundStyle(.orange) } }
            Section { SecureField(d.pin_on.bool ? "New PIN" : "PIN (4 to 8 digits)", text: $pin).keyboardType(.numberPad) }
            if d.pin_on.bool {
                Section {
                    ConfirmButton(title: "Remove PIN", message: "Remove the PIN?", confirmLabel: "Remove") { page.act("settings/pin", [("remove", "1")]) }
                }
            }
        }
    }
}

struct MaintenanceForm: View {
    let page: Page
    @State private var message = ""

    var body: some View {
        let d = page.data
        if d.admin_on.bool {
            SettingsForm(page: page, title: "Maintenance mode", action: "Turn on", send: { await page.submit("settings/maintenance", [("message", message)]) }) {
                Section {
                    TextField("Message (optional)", text: $message)
                } footer: {
                    Text("Everyone sees a “back soon” page until the owner turns it off. Square sales still come in.")
                }
            }
        } else {
            Form { Text("Needs an owner password on the server.").foregroundStyle(.orange) }
                .navigationTitle("Maintenance mode")
                .navigationBarTitleDisplayMode(.inline)
        }
    }
}

// MARK: keys and devices

struct SecurityKeysView: View {
    let page: Page
    @State private var ask: ConfirmAsk?

    var body: some View {
        let d = page.data
        List {
            if let flash = page.flash { FlashRow(flash: flash) }
            Section {
                Text("Adding a security key needs a browser, so do that on the Fizzy website (Settings > Security keys). They show here, and can be removed here.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section("Keys") {
                ForEach(d.passkey_list.array, id: \.self) { k in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(k.name.string) + Text(" \(k.rp_id.string)").font(.caption).foregroundStyle(.secondary)
                            Text("Added \(Fmt.when(k.created_at.string, "d MMM yyyy")) · \(k.created_by.string) · last used \(k.last_used_at.optString.map { Fmt.when($0) } ?? "never")")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .swipeActions(edge: .trailing) {
                        Button("Remove", role: .destructive) {
                            ask = ConfirmAsk(message: "Remove \(k.name.string)?", confirmLabel: "Remove") { page.act("settings/passkeys/\(k.id.int)/remove") }
                        }
                    }
                }
                if d.passkey_list.array.isEmpty { Text("None yet.").foregroundStyle(.secondary) }
            }
        }
        .navigationTitle("Security keys")
        .navigationBarTitleDisplayMode(.inline)
        .confirmAsk($ask)
    }
}

struct DevicesView: View {
    let page: Page
    @State private var ask: ConfirmAsk?
    @State private var name = ""

    var body: some View {
        let d = page.data
        List {
            if let flash = page.flash { FlashRow(flash: flash) }
            if !d.devices_on.bool {
                Text("Off (FIZZY_ACCESS_HOST is not set on the server).").font(.footnote).foregroundStyle(.secondary)
            } else {
                if let me = d.this_device.optString { Text("This device: \(me)").font(.footnote) }
                else if let e = d.access_email.optString { Text("\(e) via \(d.access_host.string)").font(.footnote) }
                if let link = d.new_link.optString {
                    Section("Open on the device within \(d.link_minutes.int) min") {
                        Text(link).font(.caption.monospaced()).textSelection(.enabled)
                        ShareLink(item: link) { Label("Share the link", systemImage: "square.and.arrow.up") }
                    }
                }
                Section("Register a device") {
                    TextField("e.g. Counter iPad", text: $name)
                    Button("Make link") { Task { if (await page.submit("settings/devices", [("name", name)])).ok { name = "" } } }
                        .disabled(name.isEmpty || page.busy)
                }
                Section("Devices") {
                    ForEach(d.device_list.array, id: \.self) { dev in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                HStack(spacing: 4) {
                                    Text(dev.name.string)
                                    if !dev.revoked_at.isNull { Tag(text: "Removed", color: .red) }
                                }
                                Text("Added \(Fmt.when(dev.created_at.string, "d MMM yyyy")) · \(dev.created_by.string) · last used \(dev.last_seen_at.optString.map { Fmt.when($0) } ?? "never")")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .swipeActions(edge: .trailing) {
                            if dev.revoked_at.isNull {
                                Button("Remove", role: .destructive) {
                                    ask = ConfirmAsk(message: "Remove \(dev.name.string)?", confirmLabel: "Remove") { page.act("settings/devices/\(dev.id.int)/revoke") }
                                }
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle("Devices")
        .navigationBarTitleDisplayMode(.inline)
        .confirmAsk($ask)
    }
}

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
            NavigationLink("Security keys · \(d.passkey_list.count)") { SecurityKeysView(page: page) }
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
    @Environment(\.dismiss) private var dismiss

    private let groups = ["claim": "Everyday", "points": "Points and rewards", "tab_decisions": "Tabs", "customers": "Customers", "ask": "Ask (Gemini)",
                          "qr_login": "Customers", "money": "Money", "school_tools": "Big stuff", "admin_accounts": "Special accounts"]
    private let levels = [("open", "Anyone signed in"), ("pin", "PIN"), ("owner", "Owner only")]

    var body: some View {
        let d = page.data
        Form {
            if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
            ForEach(d.actions.array.map { $0[0].string }, id: \.self) { aid in
                let label = d.actions.array.first { $0[0].string == aid }?[1].string ?? aid
                if let g = groups[aid] { Text(g).font(.footnote.bold()).foregroundStyle(.secondary).listRowBackground(Color.clear) }
                Picker(label, selection: Binding(get: { perms[aid] ?? "open" }, set: { perms[aid] = $0 })) {
                    ForEach(levels, id: \.0) { Text($0.1).tag($0.0) }
                }
            }
            Section("Limits") {
                Text("Big changes by hand").font(.footnote.bold())
                HStack { Text("More than"); TextField("points", text: $bigPoints).keyboardType(.numberPad).textFieldStyle(.roundedBorder); Text("points") }
                HStack { Text("or more than $"); TextField("dollars", text: $bigDollars).keyboardType(.numberPad).textFieldStyle(.roundedBorder) }
                Text("or anything for everyone at once").font(.caption).foregroundStyle(.secondary)
                Picker("Needs", selection: Binding(get: { perms["big"] ?? "open" }, set: { perms["big"] = $0 })) {
                    ForEach(levels, id: \.0) { Text($0.1).tag($0.0) }
                }
            }
            Section { Button("Save") { Task { await save() } }.disabled(page.busy) }
        }
        .navigationTitle("Who can do what")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            for (k, v) in d.perms.dict where !["big_points", "big_dollars"].contains(k) { perms[k] = v.string }
            bigPoints = d.perms.big_points.string
            bigDollars = d.perms.big_dollars.string
        }
    }

    private func save() async {
        var form: Params = perms.sorted { $0.key < $1.key }.map { ($0.key, $0.value) }
        form += [("big_points", bigPoints), ("big_dollars", bigDollars)]
        let o = await page.submit("settings/permissions", form)
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
                Button(action) {
                    busy = true
                    error = nil
                    Task {
                        let o = await send()
                        busy = false
                        if o.ok { dismiss() } else { error = o.message; page.flash = nil }
                    }
                }
                .disabled(!ready || busy)
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct TabRulesForm: View {
    let page: Page
    @State private var limit = ""
    @State private var days = ""
    @State private var unlock = ""
    @State private var score = ""

    var body: some View {
        SettingsForm(page: page, title: "Tab rules", send: {
            await page.submit("settings", [("tab_limit", limit), ("due_days", days), ("unlock_spend", unlock), ("score_needed", score)])
        }) {
            Section {
                LabeledContent("Highest tab limit ($)") { TextField("", text: $limit).keyboardType(.decimalPad).multilineTextAlignment(.trailing) }
                LabeledContent("Days to pay") { TextField("", text: $days).keyboardType(.numberPad).multilineTextAlignment(.trailing) }
                LabeledContent("Spend before eligible ($)") { TextField("", text: $unlock).keyboardType(.decimalPad).multilineTextAlignment(.trailing) }
                LabeledContent("Tab score needed (0 to 100)") { TextField("", text: $score).keyboardType(.numberPad).multilineTextAlignment(.trailing) }
            }
        }
        .onAppear {
            let c = page.data.cfg
            limit = dollars(c.tab_limit_cents.int); days = "\(c.tab_due_days.int)"
            unlock = dollars(c.unlock_spend_cents.int); score = "\(c.tab_score_needed.int)"
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
            Form { Text("Needs an owner password on the server.").foregroundStyle(.orange) }.navigationTitle("Maintenance mode")
        }
    }
}

// MARK: keys and devices

struct SecurityKeysView: View {
    let page: Page

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
                        Spacer()
                        ConfirmButton(title: "Remove", message: "Remove \(k.name.string)?", confirmLabel: "Remove") { page.act("settings/passkeys/\(k.id.int)/remove") }
                            .buttonStyle(.bordered).controlSize(.small)
                    }
                }
                if d.passkey_list.array.isEmpty { Text("None yet.").foregroundStyle(.secondary) }
            }
        }
        .navigationTitle("Security keys")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct DevicesView: View {
    let page: Page
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
                                Text(dev.name.string) + (dev.revoked_at.isNull ? Text("") : Text("  Removed").foregroundStyle(.red))
                                Text("Added \(Fmt.when(dev.created_at.string, "d MMM yyyy")) · \(dev.created_by.string) · last used \(dev.last_seen_at.optString.map { Fmt.when($0) } ?? "never")")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            if dev.revoked_at.isNull {
                                ConfirmButton(title: "Remove", message: "Remove \(dev.name.string)?", confirmLabel: "Remove") { page.act("settings/devices/\(dev.id.int)/revoke") }
                                    .buttonStyle(.bordered).controlSize(.small)
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle("Devices")
        .navigationBarTitleDisplayMode(.inline)
    }
}

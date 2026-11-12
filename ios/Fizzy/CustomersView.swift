import SwiftUI

/// Find a customer: the iOS version of Esc, type their name, Enter. With nothing typed it lists
/// who's been in lately, as the website does.
struct CustomersView: View {
    @Environment(AppModel.self) private var model
    @State private var page = Page("/customers")
    @State private var query = ""
    @State private var hits: [J] = []
    @State private var searched = ""
    @State private var error: String?
    @State private var jumpTo: CustomerID?

    var body: some View {
        List {
            if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
            if query.isEmpty {
                recent
            } else {
                ForEach(hits, id: \.self) { hit in
                    NavigationLink(value: CustomerID(id: hit.id.int)) {
                        Row(name: hit.name.string, email: hit.email.optString, points: hit.points.int,
                            owing: hit.owing.string, status: hit.status.string, statusKey: hit.status_key.string, reward: false)
                    }
                }
            }
        }
        .overlay {
            if !query.isEmpty && hits.isEmpty && searched == query && error == nil {
                ContentUnavailableView {
                    Label("No one matches “\(query)”", systemImage: "magnifyingglass")
                } description: {
                    Text("Someone who isn't in the school list, like a teacher? Add them.")
                } actions: {
                    ToolLink(id: "add_customer", label: "Add “\(query.trimmingCharacters(in: .whitespaces))”",
                             prefill: newPerson(query), from: page)
                        .buttonStyle(.borderedProminent)
                }
            }
            if query.isEmpty && !page.loaded && page.error == nil { ProgressView() }
        }
        .navigationTitle("Customers")
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Name, email or RNS number")
        .autocorrectionDisabled()
        .textInputAutocapitalization(.never)
        .onSubmit(of: .search) { if let first = hits.first { jump(first.id.int) } }
        .task(id: query) { await search() }
        .task { if !page.loaded { await page.load() } }
        .refreshable { await page.load() }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) { StaffMenu() }
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink { CustomersMoreView(page: page) } label: { Image(systemName: "ellipsis.circle") }
                    .accessibilityLabel("More for customers")
            }
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink { ToolHost(id: "add_customer", from: page) } label: { Image(systemName: "plus") }
                    .accessibilityLabel("Add customer")
            }
        }
        .navigationDestination(item: $jumpTo) { CustomerView(id: $0.id) }
    }

    @ViewBuilder private var recent: some View {
        let d = page.data
        if page.loaded {
            Section {
                ForEach(d.results.array, id: \.self) { s in
                    let c = s.customer
                    NavigationLink(value: CustomerID(id: c.id.int)) {
                        Row(name: c.name.string, email: c.email.optString, points: s.points.int, owing: Fmt.money(s.ledger.owing_cents.int),
                            status: s.status.label.string, statusKey: s.status.status.string,
                            reward: d.can_reward.array.contains { $0.int == c.id.int })
                    }
                }
            } header: {
                Text("Recently in · \(Fmt.number(d["count"].int)) active")
            }
        }
    }

    private func jump(_ id: Int) { query = ""; jumpTo = CustomerID(id: id) }

    /// Add customer, started from what was searched for: a student number fills in their school
    /// email, anything else the name (as the website does).
    private func newPerson(_ q: String) -> [String: String] {
        let t = q.trimmingCharacters(in: .whitespaces).lowercased()
        if t.range(of: #"^(rns)?\d{8}$"#, options: .regularExpression) != nil {
            return ["email": (t.hasPrefix("rns") ? t : "rns" + t) + "@reddamnorthshore.nsw.edu.au"]
        }
        return ["name": q.trimmingCharacters(in: .whitespaces)]
    }

    private func search() async {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { hits = []; error = nil; return }
        try? await Task.sleep(for: .milliseconds(200))  // wait for typing to pause
        guard !Task.isCancelled else { return }
        do {
            // A full student number goes straight to that person, like the counter's scanner.
            if q.range(of: #"^(?i)rns\d{8}$"#, options: .regularExpression) != nil,
               let id = try await model.call({ try await API.shared.scan(q) }) {
                jump(id)
                return
            }
            let found = try await model.call { try await API.shared.search(q) }
            guard !Task.isCancelled else { return }
            hits = found.array
            searched = query
            error = nil
        } catch is CancellationError {
        } catch let e as URLError where e.code == .cancelled {
        } catch {
            self.error = error.localizedDescription
        }
    }

    struct Row: View {
        let name: String
        let email: String?
        let points: Int
        let owing: String
        let status: String
        let statusKey: String
        let reward: Bool
        var body: some View {
            // Initials, then the name on one line with their id and tags under it; points (and any debt) on the right.
            HStack(spacing: 12) {
                Text(initials)
                    .font(.subheadline.weight(.semibold)).foregroundStyle(.white)
                    .frame(width: 38, height: 38)
                    .background(tint.gradient, in: Circle())
                // Two full-width lines (not two columns, which squeezed each other): who and their points,
                // then their id, tab and any debt.
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        Text(name).font(.body.weight(.medium)).lineLimit(1)
                        // Can afford a reward: an icon by the name (a third pill squeezed the id down to "r…").
                        if reward { Image(systemName: "gift.fill").font(.caption).foregroundStyle(.purple).accessibilityLabel("Can get a reward") }
                        Spacer(minLength: 6)
                        (Text(Fmt.number(points)).font(.body.weight(.semibold)) + Text(" pts").font(.caption).foregroundStyle(.secondary))
                            .monospacedDigit().lineLimit(1).fixedSize()
                    }
                    HStack(spacing: 6) {
                        if let email { Text(email.split(separator: "@").first.map(String.init) ?? email).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(1).fixedSize() }
                        Tag(text: status, color: statusColor(statusKey))
                        Spacer(minLength: 4)
                        if owing != "$0.00" { Text("owes \(owing)").font(.caption.weight(.medium)).foregroundStyle(.orange).lineLimit(1).fixedSize() }
                    }
                }
            }
            .padding(.vertical, 2)
        }

        private var initials: String {
            let parts = name.split(separator: " ")
            return ((parts.first?.prefix(1) ?? "") + (parts.count > 1 ? parts.last!.prefix(1) : "")).uppercased()
        }

        /// The same colour for the same person every time, from their name.
        private var tint: Color {
            let palette: [Color] = [.red, .orange, .pink, .purple, .indigo, .blue, .teal, .green]
            return palette[name.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) % 9973 } % palette.count]
        }
    }
}

/// Everything for customers as a whole, as on the website's Customers page, More: rarer, and the
/// ones that change a lot at once.
struct CustomersMoreView: View {
    let page: Page

    var body: some View {
        List {
            Section {
                NavigationLink { ImportView() } label: { Label("Import the school list", systemImage: "square.and.arrow.down") }
            } footer: {
                Text("The school's CSV. Adds new students and fixes names; never deletes anyone.")
            }
            Section("Export") {
                DownloadButton(title: "New people for Square (\(page.data.not_in_square.int))", path: "export/square-customers.csv", query: ["only": "new"])
                DownloadButton(title: "Everyone for Square", path: "export/square-customers.csv",
                               confirm: "This has everyone, including people already in Square. Importing it again gives Square a duplicate of each of them.")
                DownloadButton(title: "Customers spreadsheet", path: "tools/export/customers.csv")
            }
            Section {
                ToolLink(id: "bulk_points", from: page)
                ToolLink(id: "merge", from: page)
                ToolLink(id: "bulk_archive", from: page)
                ToolLink(id: "restore", from: page)
            }
            Section("Test accounts") {
                ToolLink(id: "test_accounts", label: "Make test accounts", from: page)
                ToolLink(id: "delete_test_accounts", from: page)
            }
        }
        .navigationTitle("More for customers")
        .navigationBarTitleDisplayMode(.inline)
    }
}

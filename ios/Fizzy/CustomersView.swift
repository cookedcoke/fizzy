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
            if !query.isEmpty && hits.isEmpty && searched == query && error == nil { ContentUnavailableView.search(text: query) }
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
        .toolbar { ToolbarItem(placement: .topBarLeading) { StaffMenu() } }
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
            } footer: {
                VStack(alignment: .leading, spacing: 8) {
                    DownloadButton(title: "Export new people for Square (\(d.not_in_square.int))", path: "export/square-customers.csv", query: ["only": "new"])
                    DownloadButton(title: "Export everyone", path: "export/square-customers.csv",
                                   confirm: "This has everyone, including people already in Square. Importing it again gives Square a duplicate of each of them.")
                }
                .font(.footnote)
            }
        }
    }

    private func jump(_ id: Int) { query = ""; jumpTo = CustomerID(id: id) }

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
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(name).font(.body.weight(.medium))
                    if let email { Text(email).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text("\(Fmt.number(points)) pts").monospacedDigit()
                    HStack(spacing: 4) {
                        if owing != "$0.00" { Text("owes \(owing)").foregroundStyle(.orange).lineLimit(1).fixedSize() }
                        Tag(text: status, color: statusColor(statusKey))
                        if reward { Tag(text: "Reward", color: .purple) }
                    }
                    .font(.caption)
                }
            }
        }
    }
}

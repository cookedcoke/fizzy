import SwiftUI

/// Search for a customer: the iOS version of Esc, type their name, Enter.
struct HomeView: View {
    @Environment(AppModel.self) private var model
    @State private var query = ""
    @State private var hits: [SearchHit] = []
    @State private var searched = ""
    @State private var error: String?
    @State private var path: [Int] = []
    @State private var showSettings = false

    var body: some View {
        NavigationStack(path: $path) {
            List {
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red)
                }
                ForEach(hits) { hit in
                    NavigationLink(value: hit.id) { HitRow(hit: hit) }
                }
            }
            .overlay {
                if query.isEmpty {
                    ContentUnavailableView("Find a customer", systemImage: "magnifyingglass",
                                           description: Text("Type part of their name, email or student number."))
                } else if hits.isEmpty && searched == query && error == nil {
                    ContentUnavailableView.search(text: query)
                }
            }
            .navigationTitle("Fizzy")
            .navigationDestination(for: Int.self) { CustomerView(id: $0) }
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Name, email or RNS number")
            .autocorrectionDisabled()
            .onSubmit(of: .search) { if let first = hits.first { path.append(first.id) } }
            .task(id: query) { await search() }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { StaffMenu() }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Settings", systemImage: "gear") { showSettings = true }
                }
            }
            .sheet(isPresented: $showSettings) { SettingsView() }
        }
    }

    private func search() async {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { hits = []; error = nil; return }
        try? await Task.sleep(for: .milliseconds(200))  // wait for typing to pause
        guard !Task.isCancelled else { return }
        do {
            // A scanned or typed student number goes straight to that person, like the counter's scanner.
            if q.range(of: #"^(?i)rns\d{8}$"#, options: .regularExpression) != nil, let id = try await API.shared.scan(q) {
                query = ""
                path.append(id)
                return
            }
            let found = try await API.shared.search(q)
            guard !Task.isCancelled else { return }
            hits = found
            searched = query
            error = nil
        } catch is CancellationError {
        } catch let e as URLError where e.code == .cancelled {
        } catch {
            model.handle(error)
            self.error = error.localizedDescription
        }
    }
}

private struct HitRow: View {
    let hit: SearchHit

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(hit.name).font(.body.weight(.medium))
                if let email = hit.email { Text(email).font(.caption).foregroundStyle(.secondary) }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text("\(hit.points) pts").monospacedDigit()
                HStack(spacing: 4) {
                    if hit.owing != "$0.00" { Text("owes \(hit.owing)").foregroundStyle(.orange) }
                    TabBadge(key: hit.status_key, label: hit.status)
                }
                .font(.caption)
            }
        }
    }
}

struct TabBadge: View {
    let key: String
    let label: String

    var body: some View {
        Text(label)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
    }

    private var color: Color {
        switch key {
        case "approved": .green
        case "eligible": .blue
        case "blocked_overdue", "rejected", "revoked": .red
        case "paused": .orange
        default: .secondary
        }
    }
}

/// Who's on the counter: switch to someone else, or sign out.
struct StaffMenu: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Menu {
            Section("On the counter") {
                ForEach(model.staffNames, id: \.self) { name in
                    Button { Task { await model.switchTo(name) } } label: {
                        if name == model.staff { Label(name, systemImage: "checkmark") } else { Text(name) }
                    }
                }
            }
            Button("Sign Out", systemImage: "rectangle.portrait.and.arrow.right", role: .destructive) {
                Task { await model.signOut() }
            }
        } label: {
            Label(model.staff, systemImage: "person.crop.circle").labelStyle(.titleAndIcon)
        }
    }
}

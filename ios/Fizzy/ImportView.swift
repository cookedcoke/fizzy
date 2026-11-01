import SwiftUI
import UniformTypeIdentifiers

/// Import the school directory: choose the CSV, check the preview, import it.
struct ImportView: View {
    @State private var page = Page("import")
    @State private var picking = false
    @State private var file: (name: String, data: Data)?
    @State private var preview: J?
    @State private var busy = false
    @State private var error: String?
    @State private var done: String?

    var body: some View {
        List {
            if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
            if let done { Label(done, systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
            Section {
                Text("A CSV with Name and Email.").font(.footnote).foregroundStyle(.secondary)
                Button { picking = true } label: { Label(file?.name ?? "Choose the CSV", systemImage: "doc.badge.plus") }
                if file != nil {
                    Button { Task { await check() } } label: { HStack { Text("Preview"); if busy && preview == nil { Spacer(); ProgressView() } } }.disabled(busy)
                }
            } header: { Text("1 · Choose the CSV") }
            if let p = preview?.preview, !p.isNull {
                Section("2 · Check and confirm") {
                    StatGrid(stats: [
                        Stat(title: "New", value: "\(p.counts.added.int)"),
                        Stat(title: "Name changes", value: "\(p.counts.updated.int)"),
                        Stat(title: "Unchanged", value: "\(p.counts.unchanged.int)"),
                        Stat(title: "Skipped", value: "\(p.errors.count)", alert: p.errors.count > 0),
                    ])
                    ForEach(p.errors.strings, id: \.self) { Text($0).font(.footnote).foregroundStyle(.red) }
                    ForEach(p.rows.array, id: \.self) { Line(left: $0.name.string, sub: $0.email.string, right: "") }
                    if p.total.int > 20 { Text("…and \(p.total.int - 20) more").foregroundStyle(.secondary) }
                    Button { Task { await importIt() } } label: {
                        HStack { Text("Import \(p.total.int) row\(p.total.int == 1 ? "" : "s")").bold(); if busy { Spacer(); ProgressView() } }
                    }
                    .disabled(busy)
                }
            }
        }
        .navigationTitle("Import")
        .navigationBarTitleDisplayMode(.inline)
        .fileImporter(isPresented: $picking, allowedContentTypes: [.commaSeparatedText, .plainText, .data]) { result in
            guard case .success(let url) = result else { return }
            let access = url.startAccessingSecurityScopedResource()
            defer { if access { url.stopAccessingSecurityScopedResource() } }
            if let data = try? Data(contentsOf: url) { file = (url.lastPathComponent, data); preview = nil; done = nil; error = nil }
            else { error = "Couldn't read that file." }
        }
    }

    private func part() -> [FilePart] { file.map { [FilePart(field: "file", filename: $0.name, mime: "text/csv", data: $0.data)] } ?? [] }

    private func check() async {
        busy = true; error = nil; done = nil
        defer { busy = false }
        do {
            let (data, _) = try await AppModel.shared.call { try await API.shared.post("import", [], files: part()) }
            preview = try JSONDecoder().decode(J.self, from: data)
        } catch { self.error = error.localizedDescription }
    }

    private func importIt() async {
        busy = true; error = nil
        defer { busy = false }
        // The website answers with a redirect to Customers; its message is on the next page we load.
        let o = await page.submit("import", [("confirm", "1")], files: part())
        if o.ok { done = page.flash?.text ?? "Imported."; preview = nil; file = nil } else { error = o.message }
    }
}

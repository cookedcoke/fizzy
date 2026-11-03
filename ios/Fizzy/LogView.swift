import SwiftUI

/// The latest 500 things anyone did in Fizzy.
struct LogView: View {
    @State private var page = Page("audit")

    var body: some View {
        PageList(page: page, title: "Activity log") { d in
            Section {
                DownloadButton(title: "Export all", path: "tools/export/audit_log.csv")
            }
            Section("Latest 500 actions") {
                ForEach(d.rows.array, id: \.self) { a in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(a.action.string.replacingOccurrences(of: "_", with: " ").capitalized).font(.subheadline.bold())
                            Spacer()
                            Text(a.staff.string).font(.caption).foregroundStyle(.secondary)
                        }
                        if !a.customer_id.isNull {
                            NavigationLink(a.name.string, value: CustomerID(id: a.customer_id.int)).font(.footnote)
                        }
                        if let d = a.details.optString { Text(d).font(.caption).foregroundStyle(.secondary) }
                        Text(Fmt.when(a.created_at.string)).font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                if d.rows.array.isEmpty { Text("Nothing yet.").foregroundStyle(.secondary) }
            }
        }
    }
}

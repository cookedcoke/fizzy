import SwiftUI

/// Points earned this month and all time. Names are first name and last initial, as on the website.
struct LeaderboardView: View {
    @Environment(Student.self) private var model
    @State private var which = 0

    var body: some View {
        let boards = model.page.boards.array
        let board = boards.indices.contains(which) ? boards[which] : .null
        List {
            Section {
                Picker("Board", selection: $which) {
                    ForEach(Array(boards.enumerated()), id: \.offset) { i, b in Text(b.title.string).tag(i) }
                }
                .pickerStyle(.segmented)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
            }
            Section {
                if board.rows.array.isEmpty {
                    Text(which == 0 ? "No points yet this month." : "No points yet.").foregroundStyle(.secondary)
                }
                ForEach(Array(board.rows.array.enumerated()), id: \.offset) { _, r in
                    HStack(spacing: 12) {
                        Text("\(r.rank.int)").font(.headline).monospacedDigit()
                            .frame(width: 32, height: 32)
                            .background(medal(r.rank.int).opacity(r.rank.int <= 3 ? 0.25 : 0.0), in: Circle())
                            .foregroundStyle(r.rank.int <= 3 ? medal(r.rank.int) : .secondary)
                        Text(r.name.string).fontWeight(r.me.bool ? .bold : .regular)
                        if r.me.bool { Text("(you)").foregroundStyle(.secondary) }
                        Spacer()
                        Text(Fmt.number(r.points.int)).monospacedDigit().foregroundStyle(.secondary)
                    }
                    .listRowBackground(r.me.bool ? Color("Brand").opacity(0.1) : nil)
                }
            } footer: {
                let mine = board.mine
                if !mine.isNull && mine.rank.int > board.rows.count {
                    Text("You're #\(mine.rank.int) of \(mine.of.int) with \(Fmt.number(mine.points.int)).")
                } else {
                    Text("Points earned from buying, including double points days. Badges and rewards don't count.")
                }
            }
        }
        .navigationTitle("Leaderboard")
        .pageChrome()
    }

    private func medal(_ rank: Int) -> Color {
        switch rank {
        case 1: Color(red: 0.85, green: 0.65, blue: 0.0)
        case 2: Color(red: 0.55, green: 0.57, blue: 0.62)
        case 3: Color(red: 0.72, green: 0.42, blue: 0.2)
        default: .secondary
        }
    }
}

/// Every badge: earned ones first, then progress on the rest.
struct BadgesView: View {
    @Environment(Student.self) private var model

    var body: some View {
        let all = model.page.badges.array
        let got = all.filter { $0.earned.bool }, todo = all.filter { !$0.earned.bool }
        List {
            if !got.isEmpty {
                Section("Earned · \(got.count) of \(all.count)") { ForEach(got, id: \.self) { BadgeRow(b: $0) } }
            }
            if !todo.isEmpty {
                Section("Still to get") { ForEach(todo, id: \.self) { BadgeRow(b: $0) } }
            }
        }
        .navigationTitle("Badges")
        .pageChrome()
    }
}

private struct BadgeRow: View {
    let b: J

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Text(b.icon.string).font(.system(size: 32))
                .frame(width: 48, height: 48)
                .background(Color(.secondarySystemFill), in: RoundedRectangle(cornerRadius: 12))
                .grayscale(b.earned.bool ? 0 : 1).opacity(b.earned.bool ? 1 : 0.6)
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(b.name.string).bold()
                    Spacer()
                    if b.points.int > 0 {
                        Text("+\(Fmt.number(b.points.int)) points").font(.caption.bold()).foregroundStyle(.green)
                    }
                }
                Text(b.how.string).font(.subheadline).foregroundStyle(.secondary)
                if !b.earned.bool, let count = b["count"].optString {
                    ProgressView(value: Double(b.progress.int), total: 100)
                    Text(count).font(.caption).foregroundStyle(.secondary).monospacedDigit()
                }
            }
        }
        .padding(.vertical, 2)
    }
}

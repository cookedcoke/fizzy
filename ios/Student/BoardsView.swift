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
            if board.rows.count >= 3 {
                Section { Podium(rows: Array(board.rows.array.prefix(3))).id(which) }
                    .listRowBackground(Color.clear).listRowInsets(EdgeInsets())
            }
            Section {
                if board.rows.array.isEmpty {
                    Text(which == 0 ? "No points yet this month." : "No points yet.").foregroundStyle(.secondary)
                }
                ForEach(Array(board.rows.array.dropFirst(board.rows.count >= 3 ? 3 : 0).enumerated()), id: \.offset) { _, r in
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
                    .scrollReveal()
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
        .animation(.snappy, value: which)
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

/// The top three on steps, first in the middle and tallest, rising into place.
private struct Podium: View {
    let rows: [J]
    @State private var up = false

    var body: some View {
        HStack(alignment: .bottom, spacing: 10) {
            ForEach([1, 0, 2], id: \.self) { i in step(rows[i], place: i + 1) }
        }
        .padding(.horizontal, 18).padding(.top, 34).padding(.bottom, 6)
        .onAppear { withAnimation(.spring(duration: 0.7, bounce: 0.35).delay(0.1)) { up = true } }
    }

    private func step(_ r: J, place: Int) -> some View {
        let colour: Color = place == 1 ? Color(red: 1, green: 0.76, blue: 0.24) : place == 2 ? Color(red: 0.78, green: 0.8, blue: 0.84) : Color(red: 0.86, green: 0.58, blue: 0.36)
        let height: CGFloat = place == 1 ? 96 : place == 2 ? 70 : 54
        let initials = r.name.string.split(separator: " ").compactMap(\.first).prefix(2).map(String.init).joined().uppercased()
        return VStack(spacing: 6) {
            ZStack(alignment: .top) {
                Text(initials).font(.system(.headline, design: .rounded).weight(.heavy))
                    .frame(width: 52, height: 52)
                    .background(colour.gradient, in: Circle())
                    .overlay(Circle().stroke(r.me.bool ? Color("Brand") : .white.opacity(0.6), lineWidth: r.me.bool ? 3 : 2))
                    .foregroundStyle(.black.opacity(0.75))
                if place == 1 {
                    Image(systemName: "crown.fill").font(.title3).foregroundStyle(.yellow).offset(y: -22)
                        .symbolEffect(.bounce, value: up)
                }
            }
            Text(r.me.bool ? "You" : String(r.name.string.split(separator: " ").first ?? "")).font(.subheadline.bold()).lineLimit(1)
            Text(Fmt.number(r.points.int)).font(.caption).monospacedDigit().foregroundStyle(.secondary)
            Text("\(place)").font(.system(.title, design: .rounded).weight(.black)).foregroundStyle(.white)
                .frame(maxWidth: .infinity).frame(height: up ? height : 0, alignment: .top).padding(.top, up ? 8 : 0)
                .background(colour.gradient, in: UnevenRoundedRectangle(topLeadingRadius: 14, topTrailingRadius: 14, style: .continuous))
                .clipped()
        }
        .frame(maxWidth: .infinity)
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
                .frame(width: 52, height: 52)
                .background(b.earned.bool ? AnyShapeStyle(LinearGradient(colors: [.yellow.opacity(0.35), .orange.opacity(0.2)], startPoint: .topLeading, endPoint: .bottomTrailing))
                            : AnyShapeStyle(Color(.secondarySystemFill)), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .modifier(EarnedShine(on: b.earned.bool))
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
        .scrollReveal()
    }
}

/// A slow light round an earned badge's tile.
private struct EarnedShine: ViewModifier {
    let on: Bool
    func body(content: Content) -> some View {
        if on { content.shineBorder([.yellow, .orange, .white, .yellow], width: 1.5, radius: 14) } else { content }
    }
}

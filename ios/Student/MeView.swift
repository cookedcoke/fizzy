import SwiftUI

/// The student site's home page: points, streak, rewards, tab and recent activity.
struct MeView: View {
    @Environment(Student.self) private var model
    /// The card whose code is being scanned. Kept here, not on the card's row: a page load rebuilds the
    /// rows, and a sheet on a rebuilt row closes (it did, on the first scan after opening the app).
    @State private var scanFor: String?
    @State private var result: String?
    @State private var failed = false
    @State private var cheer = 0
    /// All of the page, or one column of the iPhone Duo's two (see MeTab).
    var part: Part = .all
    enum Part { case all, left, right }

    var body: some View {
        let p = model.page, tab = p.tab
        // Money owed now (or a full tab) goes first; otherwise the tab comes after the fun parts.
        let urgent = tab.has_overdue.bool || (tab.status.string == "approved" && !tab.can_charge.bool)
        let showTab = tab.has_charges.bool || !["rejected", "revoked"].contains(tab.status.string)
        Group {
            switch part {
            case .all:
                List {
                    if urgent { TabSection(tab: tab) }
                    PointsSection(points: p.points.int, owner: p.name.string, page: p)
                    WhatsOnSection(page: p)
                    ForEach(p.cards.array, id: \.id.string) { card in CardSection(card: card) { scanFor = card.title.string } }
                    StreakSection(streak: p.streak)
                    if !p.offers.array.isEmpty { RewardsSection(offers: p.offers.array) }
                    if !urgent && showTab { TabSection(tab: tab) }
                    ActivitySection(items: p.activity.array)
                }
            case .left:
                List {
                    if urgent { TabSection(tab: tab) }
                    PointsSection(points: p.points.int, owner: p.name.string, page: p)
                    WhatsOnSection(page: p)
                    StreakSection(streak: p.streak)
                    ForEach(p.cards.array, id: \.id.string) { card in CardSection(card: card) { scanFor = card.title.string } }
                }
            case .right:
                List {
                    if !p.offers.array.isEmpty { RewardsSection(offers: p.offers.array) }
                    if !urgent && showTab { TabSection(tab: tab) }
                    ActivitySection(items: p.activity.array)
                }
            }
        }
        .overlay { ConfettiBurst(trigger: cheer).ignoresSafeArea() }
        .onAppear { cheerIfNewlyReady() }
        .onChange(of: model.page.offers) { _, _ in cheerIfNewlyReady() }
        .navigationTitle(part == .right ? "Rewards and tab" : "Hey \(p.first.string)")
        .pageChrome()
        .sheet(isPresented: Binding(get: { scanFor != nil }, set: { if !$0 { scanFor = nil } })) {
            CodeScanner(title: scanFor ?? "", guide: .poster) { code in scanFor = nil; Task { await send(code) } }
        }
        .alert(ScanTitle.of(result ?? "", ok: !failed), isPresented: Binding(get: { result != nil }, set: { if !$0 { result = nil } })) {
            Button("OK", role: .cancel) {}
        } message: { Text(result ?? "") }
    }

    /// Confetti the first time this phone sees a reward ready, not every visit.
    private func cheerIfNewlyReady() {
        guard part != .left else { return }
        let ready = model.page.offers.array.filter { $0.can_redeem.bool }.map(\.name.string).sorted()
        guard !ready.isEmpty else { return }
        let key = "ready-" + model.page.name.string
        let seen = Set(UserDefaults.standard.stringArray(forKey: key) ?? [])
        UserDefaults.standard.set(ready, forKey: key)
        if ready.contains(where: { !seen.contains($0) }) {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { cheer += 1 }
        }
    }

    private func send(_ code: String) async {
        do {
            result = try await StudentAPI.shared.scan(code)
            failed = false
        } catch {
            failed = true
            result = isOffline(error) ? "No signal. Scan it again when there is." : error.localizedDescription
        }
        await model.load()
    }
}

/// Their card with the balance in the middle, counting up from what this phone saw last time ("+120"), and the
/// streak, badges and this month's place in a row under it.
private struct PointsSection: View {
    let points: Int
    let owner: String
    var page: J = .null
    @State private var gain = 0
    @State private var shown = 0

    var body: some View {
        Group {
            Section {
                // Their card: Red or Black Coke for members, white for everyone else, with the points in the middle.
                MemberCard(member: page.member, holder: owner, points: shown, gain: gain)
                    .padding(.vertical, 4)
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets(top: 2, leading: 18, bottom: 28, trailing: 18))   // room for the shadow: the row clips it
            }
            Section {
                if !page.isNull { stats }
            } footer: {
                Text("1 point for every cent you spend. Tab buys earn theirs when you pay them off. Tap the card to turn it over.")
            }
        }
        .onAppear {
            // "+120 since last time": counted from what this phone saw last time, and the number counts up from there.
            let key = "points-" + owner
            let before = UserDefaults.standard.object(forKey: key) as? Int
            UserDefaults.standard.set(points, forKey: key)
            if let before, points > before {
                shown = before
                withAnimation(.spring(duration: 1.2).delay(0.3)) { shown = points; gain = points - before }
            } else {
                shown = points
            }
        }
        .onChange(of: points) { _, p in withAnimation(.spring(duration: 0.9)) { shown = p } }
    }

    /// Streak, badges and this month's place, in a strip along the bottom of the card.
    private var stats: some View {
        let month = page.boards[0]
        let mine = month.rows.array.first { $0.me.bool }
        let rank = mine?.rank.int ?? (month.mine.isNull ? 0 : month.mine.rank.int)
        let items: [(String, String)] = [
            ("Streak", "\(page.streak.days.int)d"),
            ("Badges", "\(page.badges_earned.int)/\(page.badges.count)"),
            (month.title.optString ?? "This month", rank > 0 ? "#\(rank)" : "-"),
        ]
        return HStack(spacing: 0) {
            ForEach(Array(items.enumerated()), id: \.offset) { i, item in
                VStack(alignment: .leading, spacing: 1) {
                    Text(item.0.uppercased()).font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                        .lineLimit(1).minimumScaleFactor(0.7)
                    Text(item.1).font(.system(.title3, design: .rounded).weight(.heavy)).monospacedDigit()
                        .lineLimit(1).minimumScaleFactor(0.7)
                        .contentTransition(.numericText())
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, i == 0 ? 20 : 12).padding(.vertical, 10)
                .overlay(alignment: .leading) { if i > 0 { Rectangle().fill(.separator).frame(width: 1) } }
            }
        }
        .listRowInsets(EdgeInsets())
    }
}

private struct StreakSection: View {
    let streak: J

    var body: some View {
        let days = streak.days.int, best = streak.best.int
        Section {
            Label {
                VStack(alignment: .leading, spacing: 2) {
                    Text(days > 0 ? "\(days)-day streak" : "No streak yet").bold()
                    Text((streak.today.bool ? "Bought today - nice." : days > 0 ? "Grab something today to keep it going."
                          : "Buy something on days the stall's open to start one.") + (best > days ? " Best: \(best)." : ""))
                        .font(.subheadline).foregroundStyle(.secondary)
                }
            } icon: {
                Image(systemName: days > 0 ? "flame.fill" : "snowflake").font(.title2)
                    .foregroundStyle(days > 0 ? AnyShapeStyle(LinearGradient(colors: [.yellow, .orange, .red], startPoint: .top, endPoint: .bottom)) : AnyShapeStyle(Color.cyan))
                    .symbolEffect(.pulse, options: .repeating, isActive: days > 0)
            }
        }
    }
}

/// Each reward is a card of its own, so a ready one can glow with a light round its edge without fighting the
/// list's own rounded corners (sharing one grouped card, the glow's corners and the card's never matched).
private struct RewardsSection: View {
    let offers: [J]
    static let radius: CGFloat = 22

    var body: some View {
        Section("Rewards") {
            // Ready ones first; otherwise the order the stall set.
            let sorted = offers.enumerated().sorted { a, b in
                a.element.can_redeem.bool != b.element.can_redeem.bool ? a.element.can_redeem.bool : a.offset < b.offset
            }.map(\.element)
            ForEach(Array(sorted.enumerated()), id: \.offset) { _, o in
                card(o)
                    .listRowInsets(EdgeInsets(top: 5, leading: 0, bottom: 5, trailing: 0))
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
            }
        }
        .tint(Color("Brand"))
    }

    private func card(_ o: J) -> some View {
        let ready = o.can_redeem.bool
        let shape = RoundedRectangle(cornerRadius: Self.radius, style: .continuous)
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(o.name.string).bold().lineLimit(2)
                Spacer(minLength: 4)
                if ready {
                    HStack(spacing: 3) {
                        Image(systemName: "sparkles").symbolEffect(.pulse, options: .repeating)
                        Text("Ready")
                    }
                    .font(.caption.bold()).lineLimit(1)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(.yellow.opacity(0.3), in: Capsule())
                    .foregroundStyle(Color(UIColor { $0.userInterfaceStyle == .dark ? .systemYellow : UIColor(red: 0.45, green: 0.3, blue: 0, alpha: 1) }))
                    .layoutPriority(1)
                }
                Text("\(Fmt.number(o.points_cost.int)) pts").foregroundStyle(.secondary).monospacedDigit().lineLimit(1).layoutPriority(1)
            }
            if ready {
                Text("It's yours - just ask at the stall.").font(.subheadline).foregroundStyle(.secondary)
            } else if o.limit_hit.bool {
                Text(o.per_customer_limit.int > 1 ? "You've used this one \(o.used.int) times - that's the limit."
                     : "You've used this one - that's the limit.").font(.subheadline).foregroundStyle(.secondary)
            } else {
                let cost = max(1, o.points_cost.int)
                // short can be more than the cost when points are below zero: an empty bar, not none
                ProgressView(value: Double(min(cost, max(0, cost - o.short.int))), total: Double(cost))
                Text("\(Fmt.number(o.short.int)) more points to go.").font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            shape.fill(Color(.secondarySystemGroupedBackground))
            if ready { shape.fill(LinearGradient(colors: [.clear, .yellow.opacity(0.22)], startPoint: .leading, endPoint: .trailing)) }
        }
        .modifier(ReadyShine(on: ready, radius: Self.radius))
    }
}

/// The light running round a ready reward's edge, on the card's own corners.
private struct ReadyShine: ViewModifier {
    let on: Bool
    let radius: CGFloat
    func body(content: Content) -> some View {
        if on { content.shineBorder(width: 2, radius: radius) } else { content }
    }
}

private struct TabSection: View {
    let tab: J

    var body: some View {
        let status = tab.status.string, overdue = tab.has_overdue.bool
        Section {
            VStack(alignment: .leading, spacing: 4) {
                Text(headline).font(.headline).foregroundStyle(overdue ? .red : (status == "approved" && tab.can_charge.bool ? .green : .primary))
                if let sub { Text(sub).font(.subheadline).foregroundStyle(.secondary) }
            }
            .padding(.vertical, 2)
            if tab.owing_cents.int > 0 || tab.has_charges.bool {
                HStack(alignment: .top) {
                    Figure(title: "You owe", value: Fmt.money(tab.owing_cents.int), alert: overdue,
                           sub: tab.next_due.optString.map { (overdue ? "Was due " : "Pay by ") + Fmt.when($0, "EEE d MMM") })
                    if status != "blocked_overdue" {
                        Figure(title: "Tab limit", value: Fmt.money(tab.limit_cents.int), sub: "\(tab.due_days.int) days to pay")
                    }
                }
                PayTabButton(tab: tab)
                if tab.balance_cents.int < 0 {
                    Text("You have \(Fmt.money(-tab.balance_cents.int)) in credit - it's used next time you put something on your tab.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                if tab.late_count.int > 0 {
                    Text("\(tab.late_count.int) late payment\(tab.late_count.int == 1 ? "" : "s") on record.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
            }
            if !["rejected", "revoked"].contains(status) { ScoreRow(tab: tab) }
        } header: {
            HStack { Text("Your tab"); if overdue { Text("needs paying").foregroundStyle(.red) } }
        }
    }

    private var headline: String {
        let t = tab
        switch t.status.string {
        case _ where t.has_overdue.bool && t.status.string != "blocked_overdue": return "\(Fmt.money(t.overdue_cents.int)) is overdue."
        case "approved": return t.can_charge.bool ? "You can put up to \(Fmt.money(t.available_cents.int)) on your tab." : "Your tab is full."
        case "blocked_overdue": return "Your tab is on hold - \(Fmt.money(t.overdue_cents.int)) is overdue."
        case "paused": return "Tabs are paused for everyone right now."
        case "low_score": return "Get your tab score to \(t.min_score.int) to unlock a tab."
        default: return "Tabs aren't available on your account."
        }
    }

    private var sub: String? {
        let t = tab
        if t.has_overdue.bool && t.status.string != "blocked_overdue" { return "Pay it at the stall." }
        switch t.status.string {
        case "approved": return t.can_charge.bool ? nil : "Pay some off to use it again."
        case "blocked_overdue": return "Pay it at the stall and it unlocks straight away."
        case "low_score": return "You're on \(t.score.score.int). See below for how it works."
        case "paused": return nil
        default: return "Ask at the stall if you have questions."
        }
    }
}

private struct Figure: View {
    let title: String
    let value: String
    var alert = false
    var sub: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.subheadline).foregroundStyle(.secondary)
            Text(value).font(.title2.bold()).monospacedDigit().foregroundStyle(alert ? .red : .primary)
            if let sub { Text(sub).font(.caption).foregroundStyle(.secondary) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The 0-100 tab score with every line explained, as on the website.
private struct ScoreRow: View {
    let tab: J
    @State private var open = false

    var body: some View {
        let sc = tab.score, score = sc.score.int, min = tab.min_score.int
        DisclosureGroup(isExpanded: Binding(get: { open || tab.status.string == "low_score" }, set: { open = $0 })) {
            ForEach(Array(sc.factors.array.enumerated()), id: \.offset) { i, f in
                HStack {
                    Text(f.label.string).font(.subheadline)
                    Spacer()
                    let pts = f.points.int
                    Text(i == 0 ? "\(pts)" : (pts >= 0 ? "+\(pts)" : "\(pts)")).font(.subheadline.bold()).monospacedDigit()
                        .foregroundStyle(pts < 0 ? .red : .primary)
                }
            }
            if !sc.tips.array.isEmpty {
                Text("To raise it: " + sc.tips.strings.joined(separator: " ")).font(.subheadline).foregroundStyle(.secondary)
            }
        } label: {
            HStack(spacing: 12) {
                ScoreRing(value: score, color: score >= 80 ? .green : score >= min ? .orange : .red, size: 52)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Your tab score").bold()
                    Text(tab.status.string == "approved"
                         ? "Gives you a \(Fmt.money(sc.limit_cents.int)) limit" + (sc.next.isNull ? "" : " · \(sc.next.score.int)+ gets \(Fmt.money(sc.next.limit_cents.int))")
                         : "Out of 100 · tap to see why")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
            }
        }
    }
}

private struct ActivitySection: View {
    let items: [J]
    @State private var more = false

    var body: some View {
        Section {
            if items.isEmpty {
                Text("Nothing yet - grab something at the stall to start earning points.").foregroundStyle(.secondary)
            } else {
                ForEach(Array((more ? items : Array(items.prefix(5))).enumerated()), id: \.offset) { _, a in row(a) }
                if items.count > 5 {
                    Button(more ? "Show less" : "Show more") { withAnimation { more.toggle() } }
                }
            }
        } header: {
            Text("Recent activity")
        } footer: {
            Text("Something look off? Ask us at the stall.")
        }
    }

    private func row(_ a: J) -> some View {
        let what = a.what.string, pts = a.points.isNull ? nil : a.points.int
        let (icon, tint): (String, Color) = what.lowercased().hasPrefix("badge") ? ("rosette", .orange)
            : what.lowercased().contains("reward") ? ("gift.fill", .pink)
            : pts == nil ? ("creditcard.fill", .blue)
            : (pts ?? 0) > 0 ? ("cup.and.saucer.fill", Color("Brand")) : ("minus.circle.fill", .secondary)
        return HStack(spacing: 12) {
            Image(systemName: icon).font(.subheadline.bold()).foregroundStyle(tint)
                .frame(width: 34, height: 34).background(tint.opacity(0.14), in: Circle())
            VStack(alignment: .leading, spacing: 1) {
                Text(what).lineLimit(2)
                Text(Fmt.when(a.at.string, "d MMM")).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if let pts {
                Text(Fmt.points(pts)).font(.system(.body, design: .rounded).bold()).monospacedDigit().foregroundStyle(pts > 0 ? .green : .primary)
            } else {
                Text(Fmt.money(a.money.int)).font(.system(.body, design: .rounded).bold()).monospacedDigit()
            }
        }
        .scrollReveal()
    }
}

/// A card from the server (a challenge today, maybe something else tomorrow): a title, text, steps
/// with ticks, progress, a prize and actions. Drawn only from that data, with the app's own controls,
/// so new cards need no app update. Anything the app doesn't know is left out, not guessed at.
struct CardSection: View {
    let card: J
    var scan: () -> Void = {}

    var body: some View {
        let steps = card.steps.array, done = card.done.bool
        // The orange header in a section of its own: sharing one would leave the rows under it square-topped.
        Section {
            HuntHero(card: card)
        } header: {
            HStack {
                Text(card.kicker.optString ?? "")
                Spacer()
                if let when = card.when.optString { Text(when).textCase(nil) }
            }
        }
        Section {
            if let text = (card.status.optString ?? card.text.optString) {
                Text(text).font(.subheadline).foregroundStyle(done ? .green : .secondary)
            }
            ForEach(Array(steps.enumerated()), id: \.offset) { _, step in HuntStepRow(step: step) }
            ForEach(Array(card.actions.array.enumerated()), id: \.offset) { _, action in
                if action.kind.string == "scan" {
                    Button { scan() } label: {
                        Label(action.label.optString ?? "Scan a code", systemImage: "qrcode.viewfinder").bold()
                    }
                }
            }
        }
    }
}

import SwiftUI

/// The student site's home page: points, streak, rewards, tab and recent activity.
struct MeView: View {
    @Environment(Student.self) private var model

    var body: some View {
        let p = model.page, tab = p.tab
        // Money owed now (or a full tab) goes first; otherwise the tab comes after the fun parts.
        let urgent = tab.has_overdue.bool || (tab.status.string == "approved" && !tab.can_charge.bool)
        let showTab = tab.has_charges.bool || !["rejected", "revoked"].contains(tab.status.string)
        List {
            if urgent { TabSection(tab: tab) }
            WhatsOnSection(page: p)
            PointsSection(points: p.points.int, owner: p.name.string)
            StreakSection(streak: p.streak)
            if !p.offers.array.isEmpty { RewardsSection(offers: p.offers.array) }
            if !urgent && showTab { TabSection(tab: tab) }
            ActivitySection(items: p.activity.array)
        }
        .navigationTitle("Hey \(p.first.string)")
        .pageChrome()
    }
}

private struct PointsSection: View {
    let points: Int
    let owner: String
    @State private var gain = 0

    var body: some View {
        Section {
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Your points").font(.subheadline).foregroundStyle(.secondary)
                    Spacer()
                    if gain > 0 {
                        Text("+\(Fmt.number(gain)) since last time").font(.subheadline.bold()).foregroundStyle(.green)
                    }
                }
                Text(Fmt.number(points))
                    .font(.system(size: 48, weight: .bold, design: .rounded)).monospacedDigit()
                    .contentTransition(.numericText())
            }
            .padding(.vertical, 4)
        } footer: {
            Text("1 point for every cent you spend. Tab buys earn theirs when you pay them off.")
        }
        .onAppear {
            // "+120 since last time": counted from what this phone saw last time.
            let key = "points-" + owner
            if let before = UserDefaults.standard.object(forKey: key) as? Int, points > before { gain = points - before }
            UserDefaults.standard.set(points, forKey: key)
        }
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
                    .foregroundStyle(days > 0 ? Color.orange : Color.cyan)
            }
        }
    }
}

private struct RewardsSection: View {
    let offers: [J]

    var body: some View {
        Section("Rewards") {
            ForEach(offers.sorted { $0.can_redeem.bool && !$1.can_redeem.bool }, id: \.self) { o in
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(o.name.string).bold()
                        Spacer()
                        if o.can_redeem.bool {
                            Text("Ready").font(.caption.bold()).padding(.horizontal, 8).padding(.vertical, 2)
                                .background(.green.opacity(0.18), in: Capsule()).foregroundStyle(.green)
                        }
                        Text("\(Fmt.number(o.points_cost.int)) pts").foregroundStyle(.secondary).monospacedDigit()
                    }
                    if o.can_redeem.bool {
                        Text("It's yours - just ask at the stall.").font(.subheadline).foregroundStyle(.secondary)
                    } else if o.limit_hit.bool {
                        Text(o.per_customer_limit.int > 1 ? "You've used this one \(o.used.int) times - that's the limit."
                             : "You've used this one - that's the limit.").font(.subheadline).foregroundStyle(.secondary)
                    } else {
                        let cost = max(1, o.points_cost.int)
                        ProgressView(value: Double(cost - o.short.int), total: Double(cost))
                        Text("\(Fmt.number(o.short.int)) more points to go.").font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 2)
            }
        }
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
                Text("\(score)").font(.title2.bold()).monospacedDigit()
                    .frame(width: 48, height: 48)
                    .background((score >= 80 ? Color.green : score >= min ? Color.orange : Color.red).opacity(0.18), in: Circle())
                    .foregroundStyle(score >= 80 ? Color.green : score >= min ? Color.orange : Color.red)
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
        HStack {
            Text(Fmt.when(a.at.string, "d MMM")).font(.subheadline).foregroundStyle(.secondary).frame(width: 52, alignment: .leading)
            Text(a.what.string).lineLimit(2)
            Spacer()
            if !a.points.isNull {
                Text(Fmt.points(a.points.int) + " pts").monospacedDigit().foregroundStyle(a.points.int > 0 ? .green : .primary)
            } else {
                Text(Fmt.money(a.money.int)).monospacedDigit()
            }
        }
    }
}

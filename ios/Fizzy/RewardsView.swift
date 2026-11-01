import SwiftUI

/// Rewards the stall is running, repeating double-points days, and custom badges and events.
struct RewardsView: View {
    @State private var page = Page("rewards")
    @State private var ask: ConfirmAsk?

    var body: some View {
        PageList(page: page, title: "Rewards") { d in
            Section {
                Text("Start rewards and double points days in Ask.").font(.footnote).foregroundStyle(.secondary)
            }
            Section("Live · \(d.live.count)") {
                ForEach(d.live.array, id: \.self) { r in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(r.name.string).bold()
                            Text("\(Fmt.number(r.points_cost.int)) pts" + (r.per_customer_limit.int > 0 ? " · \(r.per_customer_limit.int) per customer" : "")
                                 + (r.square_note.optString.map { " · In Square: \($0)" } ?? "")).font(.caption).foregroundStyle(.secondary)
                            Text("Since \(Fmt.when(r.created_at.string, "EEE d MMM")) · \(r.created_by.string) · redeemed \(r.used.int)"
                                 + (r.used.int > 0 ? " by \(r.people.int) \(r.people.int == 1 ? "person" : "people")" : "")).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .swipeActions(edge: .trailing) {
                        Button("End", role: .destructive) {
                            ask = ConfirmAsk(message: "End “\(r.name.string)”? It stops showing on customer pages.", confirmLabel: "End") { page.act("rewards/\(r.id.int)/end") }
                        }
                    }
                }
                if d.live.array.isEmpty { Text("No rewards running.").foregroundStyle(.secondary) }
            }
            if !d.rules.array.isEmpty {
                Section("Every week · \(d.rules.count)") {
                    ForEach(d.rules.array, id: \.self) { r in
                        let next = nextDay(of: r, in: d)
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(r.name.string).bold() + Text(" · ×\(Fmt.g(r.multiplier.double))").foregroundStyle(.secondary)
                                Text("Every \(d.day_names[r.weekday.int].string)" + (r.days.int > 1 ? " for \(r.days.int) days" : "")).font(.caption).foregroundStyle(.secondary)
                                if let next {
                                    Text((next.running ? "On now, until " : "Next: ") + Fmt.when(next.running ? next.end : next.start, "EEE d MMM")).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        .swipeActions(edge: .trailing) {
                            Button("Stop", role: .destructive) { stop(r) }
                            if let next {
                                Button(next.running ? "End today's" : "Skip next") { cancelDay(id: next.id, name: r.name.string, running: next.running, start: next.start) }.tint(.orange)
                            }
                        }
                    }
                }
            }
            Section("Custom badges and events · \(d.custom.count)") {
                ForEach(d.custom.array, id: \.self) { r in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(r.name.string).bold()
                            Tag(text: r.kind.string == "badge" ? "Badge" : "Event")
                            if r.paused.bool { Tag(text: "Paused", color: .orange) }
                        }
                        Text(r.summary.string + (r.kind.string == "badge" ? " · \(r.spec.points.int) points" + (r.spec.repeat.optString.map { $0 != "once" ? " each \($0)" : "" } ?? "") : ""))
                            .font(.footnote)
                        Text("\(r.students.int) student\(r.students.int == 1 ? "" : "s") · \(Fmt.number(r.points.int)) points given · made by \(r.created_by.string) \(Fmt.when(r.created_at.string, "d MMM"))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .swipeActions(edge: .trailing) {
                        Button("Delete", role: .destructive) {
                            ask = ConfirmAsk(message: "Delete \(r.name.string)? Points already given stay.") { page.act("rules/\(r.id.int)/delete") }
                        }
                        Button(r.paused.bool ? "Restart" : "Pause") { page.act("rules/\(r.id.int)/\(r.paused.bool ? "resume" : "pause")") }.tint(.orange)
                    }
                }
                if d.custom.array.isEmpty { Text("None yet. Make one in Ask.").font(.footnote).foregroundStyle(.secondary) }
            }
            if !d.events.array.isEmpty {
                Section("Double points days · \(d.events.count)") {
                    ForEach(d.events.array, id: \.self) { e in
                        let ended = !e.ended_at.isNull
                        let on = !ended && e.start_date.string <= d.today.string && d.today.string <= e.end_date.string
                        let soon = !ended && e.start_date.string > d.today.string
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                HStack {
                                    Text(e.name.string).bold() + Text(" · ×\(Fmt.g(e.multiplier.double))").foregroundStyle(.secondary)
                                    if on { Tag(text: "On now", color: .green) } else if soon { Tag(text: "Coming up", color: .blue) }
                                    if !e.rule_id.isNull { Tag(text: "Weekly") }
                                }
                                Text(Fmt.when(e.start_date.string, "EEE d MMM") + (e.end_date.string != e.start_date.string ? " to \(Fmt.when(e.end_date.string, "EEE d MMM"))" : "")
                                     + " · \(Fmt.number(e.given.int)) bonus given").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .swipeActions(edge: .trailing) {
                            if on || soon {
                                if !e.rule_id.isNull {
                                    Button("Stop repeating", role: .destructive) {
                                        if let r = d.rules.array.first(where: { $0.id.int == e.rule_id.int }) { stop(r) }
                                    }
                                    Button(soon ? "Skip" : "End") { cancelDay(id: e.id.int, name: e.name.string, running: on, start: e.start_date.string) }.tint(.orange)
                                } else {
                                    Button(soon ? "Delete" : "End", role: .destructive) { cancelDay(id: e.id.int, name: e.name.string, running: on, start: e.start_date.string) }
                                }
                            }
                        }
                    }
                }
            }
            if !d.ended.array.isEmpty {
                Section("Ended") {
                    ForEach(d.ended.array, id: \.self) { r in
                        Line(left: r.name.string, sub: "\(Fmt.when(r.created_at.string, "d MMM")) – \(Fmt.when(r.ended_at.string, "d MMM")) · redeemed \(r.used.int)",
                             right: "\(Fmt.number(r.points_cost.int)) pts")
                    }
                }
            }
        }
        .confirmAsk($ask)
    }

    /// The weekly bonus day's next (or running) occurrence.
    private struct Occurrence { let id: Int; let start: String; let end: String; let running: Bool }

    private func nextDay(of rule: J, in d: J) -> Occurrence? {
        let today = d.today.string
        let days = d.events.array
            .filter { $0.rule_id.int == rule.id.int && !$0.rule_id.isNull && $0.ended_at.isNull && $0.end_date.string >= today }
            .sorted { $0.start_date.string < $1.start_date.string }
        guard let e = days.first else { return nil }
        return Occurrence(id: e.id.int, start: e.start_date.string, end: e.end_date.string, running: e.start_date.string <= today)
    }

    private func stop(_ r: J) {
        ask = ConfirmAsk(message: "Stop \(r.name.string) repeating? Days coming up are deleted; one running now carries on.", confirmLabel: "Stop repeating") {
            page.act("events/rules/\(r.id.int)/stop")
        }
    }

    /// Delete a day that hasn't started, or end the one that's running. A weekly rule carries on after it.
    private func cancelDay(id: Int, name: String, running: Bool, start: String) {
        let word = running ? "End" : "Skip"
        ask = ConfirmAsk(message: running ? "End “\(name)” now? Bonus already earned stays."
                                          : "Skip “\(name)” on \(Fmt.when(start, "EEE d MMM"))? It's deleted; the schedule (if any) carries on.",
                         confirmLabel: word, role: .destructive) { page.act("events/\(id)/cancel") }
    }
}

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
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(r.name.string).bold() + Text(" · ×\(Fmt.g(r.multiplier.double))").foregroundStyle(.secondary)
                                Text("Every \(d.day_names[r.weekday.int].string)" + (r.days.int > 1 ? " for \(r.days.int) days" : "")).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .swipeActions(edge: .trailing) {
                            Button("Stop", role: .destructive) {
                                ask = ConfirmAsk(message: "Stop \(r.name.string) repeating? Any coming up go; one running now carries on.", confirmLabel: "Stop") { page.act("events/rules/\(r.id.int)/stop") }
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
                                }
                                Text(Fmt.when(e.start_date.string, "EEE d MMM") + (e.end_date.string != e.start_date.string ? " to \(Fmt.when(e.end_date.string, "EEE d MMM"))" : "")
                                     + " · \(Fmt.number(e.given.int)) bonus given").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .swipeActions(edge: .trailing) {
                            if on || soon {
                                Button(soon ? "Delete" : "End", role: .destructive) {
                                    ask = ConfirmAsk(message: "\(soon ? "Delete" : "End") “\(e.name.string)”?" + (soon ? "" : " It stops now; bonus already earned stays."),
                                                     confirmLabel: soon ? "Delete" : "End") { page.act("events/\(e.id.int)/cancel") }
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
}

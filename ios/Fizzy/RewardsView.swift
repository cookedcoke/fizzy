import SwiftUI

/// Rewards the stall is running, repeating double-points days, and custom badges and events.
struct RewardsView: View {
    @State private var page = Page("rewards")
    @State private var sheet: Sheet?

    enum Sheet: String, Identifiable {
        case reward, event
        var id: String { rawValue }
    }

    var body: some View {
        PageList(page: page, title: "Rewards") { d in
            Section {
                Button { sheet = .reward } label: { Label("Start a reward", systemImage: "gift") }
                Button { sheet = .event } label: { Label("Schedule a double points day", systemImage: "sparkles") }
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
                        Spacer()
                        ConfirmButton(title: "End", message: "End “\(r.name.string)”? It stops showing on customer pages.", confirmLabel: "End") {
                            page.act("rewards/\(r.id.int)/end")
                        }
                        .buttonStyle(.bordered).controlSize(.small)
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
                            Spacer()
                            ConfirmButton(title: "Stop", message: "Stop \(r.name.string) repeating? Any coming up go; one running now carries on.", confirmLabel: "Stop") {
                                page.act("events/rules/\(r.id.int)/stop")
                            }
                            .buttonStyle(.bordered).controlSize(.small)
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
                        HStack {
                            Button(r.paused.bool ? "Restart" : "Pause") { page.act("rules/\(r.id.int)/\(r.paused.bool ? "resume" : "pause")") }
                                .buttonStyle(.bordered).controlSize(.small)
                            ConfirmButton(title: "Delete", message: "Delete \(r.name.string)? Points already given stay.", confirmLabel: "Delete") {
                                page.act("rules/\(r.id.int)/delete")
                            }
                            .buttonStyle(.bordered).controlSize(.small)
                        }
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
                            Spacer()
                            if on || soon {
                                ConfirmButton(title: soon ? "Delete" : "End", message: "\(soon ? "Delete" : "End") “\(e.name.string)”?" + (soon ? "" : " It stops now; bonus already earned stays."),
                                              confirmLabel: soon ? "Delete" : "End") { page.act("events/\(e.id.int)/cancel") }
                                    .buttonStyle(.bordered).controlSize(.small)
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
        .sheet(item: $sheet) { which in
            switch which {
            case .reward: NewRewardSheet(page: page)
            case .event: NewEventSheet(page: page)
            }
        }
    }
}

struct NewRewardSheet: View {
    let page: Page
    @State private var name = ""
    @State private var points = ""
    @State private var squareNote = ""
    @State private var limit = ""
    @State private var announce = false

    var body: some View {
        FormSheet(title: "Start a reward", action: "Start", ready: !name.isEmpty && !points.isEmpty, page: page, send: {
            var form: Params = [("name", name), ("points", points), ("square_note", squareNote), ("limit", limit)]
            if announce { form.append(("announce", "1")) }
            return await page.submit("rewards", form)
        }) {
            Section {
                TextField("Name, e.g. Free drink", text: $name)
                TextField("Points it costs", text: $points).keyboardType(.numberPad)
                TextField("How to ring it up in Square (optional)", text: $squareNote)
                TextField("Limit per customer (optional)", text: $limit).keyboardType(.numberPad)
                if page.data.discord_on.bool { Toggle("Announce it on Discord", isOn: $announce) }
            }
        }
    }
}

struct NewEventSheet: View {
    let page: Page
    @State private var name = ""
    @State private var multiplier = "2"
    @State private var start = Fmt.day(Date())
    @State private var end = Fmt.day(Date())
    @State private var weekly = false
    @State private var announce = false

    var body: some View {
        FormSheet(title: "Double points day", action: "Schedule", ready: !name.isEmpty, page: page, send: {
            var form: Params = [("name", name), ("multiplier", multiplier), ("start", start), ("end", end)]
            if weekly { form.append(("weekly", "1")) }
            if announce { form.append(("announce", "1")) }
            return await page.submit("events", form)
        }) {
            Section {
                TextField("Name", text: $name)
                TextField("Multiplier", text: $multiplier).keyboardType(.decimalPad)
                DayField(title: "From", text: $start)
                DayField(title: "To", text: $end)
                Toggle("Repeat every week", isOn: $weekly)
                if page.data.discord_on.bool { Toggle("Announce it on Discord", isOn: $announce) }
            }
        }
    }
}

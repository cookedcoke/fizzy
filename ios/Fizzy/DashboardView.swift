import Charts
import SwiftUI

/// Today: sales, tabs, and what needs doing. Refreshes itself every minute, like the website.
struct DashboardView: View {
    @State private var page = Page("/")
    @State private var assigning: Pick?

    var body: some View {
        PageList(page: page, title: "Today", staffMenu: true) { d in
            banners(d)
            today(d)
            tabs(d)
            attention(d)
            overdue(d)
            eligible(d)
            sales(d)
        }
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                if !Task.isCancelled { await page.load() }
            }
        }
        .sheet(item: $assigning) { payment in
            AssignSheet(page: page, paymentID: payment.value.square_payment_id.string, people: page.data.people.array)
        }
    }

    // MARK: sections

    @ViewBuilder private func banners(_ d: J) -> some View {
        if let notice = d.notice.optString { Label(notice, systemImage: "megaphone").foregroundStyle(.orange) }
        if d.tabs_paused.bool { Label("All tabs are paused. Turn them back on in Tools.", systemImage: "pause.circle.fill").foregroundStyle(.red) }
        if !d.square_on.bool {
            Label("Square isn't connected yet, so points and tabs won't update by themselves. Ask the owner.", systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red)
        } else if let e = d.sync_error.optString {
            Label("Last Square sync failed: \(e)", systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red)
        }
        if !d.backup[0].bool { Label(d.backup[1].string, systemImage: "externaldrive.badge.exclamationmark").foregroundStyle(.red) }
        if !d.events_on.array.isEmpty || !d.events_soon.array.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(d.events_on.array, id: \.self) { e in
                    Label("\(e.name.string) (×\(Fmt.g(e.multiplier.double))) is on today", systemImage: "sparkles").bold().foregroundStyle(.green)
                }
                if !d.events_soon.array.isEmpty {
                    Text("Coming up: " + d.events_soon.array.map { "\($0.name.string) \(Fmt.when($0.start_date.string, "EEE d MMM"))" }.joined(separator: ", "))
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
        }
        if !d.live_rewards.array.isEmpty {
            Label("Rewards on: " + d.live_rewards.array.map { "\($0.name.string) (\(Fmt.number($0.points_cost.int)) pts)" }.joined(separator: ", "), systemImage: "gift")
                .font(.footnote).foregroundStyle(.green)
        }
    }

    @ViewBuilder private func today(_ d: J) -> some View {
        let s = d.sales, t = s.today
        if let closed = d.closed.isNull ? nil : d.closed {
            Section("\(closed.reason.string), so the stall's closed today") {
                let n = closed.next_in.int
                Text("Opens " + (n == 1 ? "tomorrow" : Fmt.when(closed.next.string, n < 7 ? "EEEE" : "EEEE d MMMM")) + (n > 1 ? " · in \(n) days" : ""))
                    .font(.headline)
                if !closed.last.isNull {
                    let l = closed.last
                    Text("Last open \(Fmt.when(l.day.string, "EEEE d MMMM")): \(Fmt.money(l.sold_cents.int)) sold over \(l.orders.int) order\(l.orders.int == 1 ? "" : "s"), \(Fmt.money(l.taken_cents.int)) taken.")
                        .font(.subheadline)
                    if !closed.top.array.isEmpty {
                        Text("Best sellers that day: " + closed.top.array.map { "\($0.name.string) (\($0.qty.int))" }.joined(separator: ", "))
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }
        } else {
            Section("Today · \(Fmt.when(t.day.string, "EEEE d MMMM"))") {
                let lw = s.vs_last_week_cents.int
                let weekday = Fmt.when(t.day.string, "EEEE")
                let vs = lw != 0
                    ? "\(t.sold_cents.int >= lw ? "▲" : "▼") \(abs(Int((Double(t.sold_cents.int - lw) * 100 / Double(lw)).rounded())))% vs last \(weekday) by now"
                    : "Nothing last \(weekday) by now"
                let pr = s.profit
                StatGrid(stats: [
                    Stat(title: "Sold", value: Fmt.money(t.sold_cents.int), sub: vs),
                    Stat(title: "Taken", value: Fmt.money(t.taken_cents.int), sub: "money in, after refunds"),
                    Stat(title: "Orders", value: "\(t.orders.int)", sub: "\(Fmt.money(t.avg_cents.int)) average"),
                    Stat(title: "Customers", value: "\(t.customers.int)", sub: "+ \(t.walkups.int) with no customer"),
                    pr.revenue.int != 0
                        ? Stat(title: "Profit", value: Fmt.money(pr.profit.int),
                               sub: "\(pr.margin.int)% margin" + (pr.uncosted.int != 0 ? " · \(pr.uncosted.int) without a cost" : ""))
                        : Stat(title: "Profit", value: "—", sub: "Set item costs to see it"),
                    Stat(title: "Put on tabs", value: Fmt.money(t.tab_cents.int), sub: "\(Fmt.money(t.repaid_cents.int)) repaid today"),
                ])
            }
            Section("Sold by hour") {
                Chart {
                    ForEach(s.hours.array, id: \.self) { h in
                        BarMark(x: .value("Hour", hourLabel(h.hour.int)), y: .value("Sold", Double(h.last_week.int) / 100))
                            .foregroundStyle(by: .value("When", "Last \(Fmt.when(t.day.string, "EEEE"))"))
                            .position(by: .value("When", "Last \(Fmt.when(t.day.string, "EEEE"))"))
                        BarMark(x: .value("Hour", hourLabel(h.hour.int)), y: .value("Sold", Double(h.cents.int) / 100))
                            .foregroundStyle(by: .value("When", "Today"))
                            .position(by: .value("When", "Today"))
                    }
                }
                .chartForegroundStyleScale(["Today": Color("Brand"), "Last \(Fmt.when(t.day.string, "EEEE"))": Color.gray.opacity(0.4)])
                .chartXAxis { AxisMarks(values: .automatic(desiredCount: 6)) }
                .chartYAxis { dollarAxis }
                .frame(height: 160)
            }
        }
        Section("Last 7 school days · \(Fmt.money(s.week_sold_cents.int)) sold · \(s.week_orders.int) orders") {
            Chart(s.week.array, id: \.self) { w in
                BarMark(x: .value("Day", Fmt.when(w.day.string, "EEE d")), y: .value("Sold", Double(w.sold_cents.int) / 100))
                    .foregroundStyle(Color("Brand"))
                    .annotation(position: .top) {
                        if w.sold_cents.int > 0 { Text(Fmt.money(w.sold_cents.int)).font(.caption2).monospacedDigit().foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.7) }
                    }
            }
            .chartYAxis { dollarAxis }
            .frame(height: 150)
        }
    }

    private func hourLabel(_ h: Int) -> String { "\((h + 11) % 12 + 1)\(h < 12 ? "am" : "pm")" }

    @ViewBuilder private func tabs(_ d: J) -> some View {
        Section("Tabs") {
            StatGrid(stats: [
                Stat(title: "Total owed", value: Fmt.money(d.total_owed.int)),
                Stat(title: "Overdue", value: Fmt.money(d.total_overdue.int), sub: "\(d.overdue.count) charge\(d.overdue.count == 1 ? "" : "s")", alert: d.total_overdue.int > 0),
                Stat(title: "Due in 2 days", value: "\(d.due_soon.count)", sub: "charge\(d.due_soon.count == 1 ? "" : "s")"),
                Stat(title: "Awaiting approval", value: "\(d.eligible.count)", sub: "eligible for a tab"),
                Stat(title: "Square sync", value: d.last_sync.optString.map { Fmt.when($0) } ?? "Never"),
                Stat(title: "Last backup", value: d.backup[0].bool ? d.backup[1].string : "Problem", alert: !d.backup[0].bool),
            ])
        }
    }

    @ViewBuilder private func attention(_ d: J) -> some View {
        if !d.breaches.array.isEmpty || !d.unlinked_tabs.array.isEmpty {
            Section {
                ForEach(d.breaches.array, id: \.self) { b in
                    VStack(alignment: .leading, spacing: 6) {
                        NavigationLink(value: CustomerID(id: b.customer_id.int)) {
                            Line(left: b.name.string, sub: Fmt.when(b.charged_at.string), right: Fmt.money(b.amount_cents.int))
                        }
                        Text("Tab used against the rules: \(b.breach.string)").font(.footnote).foregroundStyle(.red)
                    }
                    .swipeActions(edge: .trailing) {
                        Button("Dismiss") { page.act("breach/\(b.id.int)/ack") }.tint(.gray)
                    }
                }
                ForEach(d.unlinked_tabs.array, id: \.self) { u in
                    VStack(alignment: .leading, spacing: 6) {
                        Line(left: "\(u.kind.string == "tab_charge" ? "Tab sale" : "Tab repayment") · no customer", sub: Fmt.when(u.created_at.string), right: Fmt.money(u.amount_cents.int))
                        if let items = u["items"].optString { Text(items).font(.footnote).foregroundStyle(.secondary) }
                        Button("Assign to…") { assigning = Pick(value: u) }.buttonStyle(.bordered).controlSize(.small)
                    }
                }
            } header: {
                Text("Needs attention · \(d.breaches.count + d.unlinked_tabs.count)").foregroundStyle(.red)
            }
        }
    }

    @ViewBuilder private func overdue(_ d: J) -> some View {
        if d.overdue.array.isEmpty && d.due_soon.array.isEmpty {
            Label("No tabs overdue or due soon. \(Fmt.money(d.total_owed.int)) owed in total.", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
        }
        if !d.overdue.array.isEmpty {
            Section("Overdue · \(d.overdue.count)") {
                ForEach(d.overdue.array, id: \.self) { r in
                    NavigationLink(value: CustomerID(id: r.customer.id.int)) {
                        Line(left: r.customer.name.string, sub: "Was due \(Fmt.when(r.charge.due_at.string, "EEE d MMM"))",
                             right: Fmt.money(r.charge.unpaid_cents.int), rightColor: .red)
                    }
                }
            }
        }
        if !d.due_soon.array.isEmpty {
            Section("Due in the next 2 days · \(d.due_soon.count)") {
                ForEach(d.due_soon.array, id: \.self) { r in
                    NavigationLink(value: CustomerID(id: r.customer.id.int)) {
                        Line(left: r.customer.name.string, sub: "Due \(Fmt.when(r.charge.due_at.string))", right: Fmt.money(r.charge.unpaid_cents.int))
                    }
                }
            }
        }
    }

    @ViewBuilder private func eligible(_ d: J) -> some View {
        if !d.eligible.array.isEmpty {
            Section("Eligible for a tab · \(d.eligible.count)") {
                ForEach(Array(d.eligible.array.prefix(8)), id: \.self) { c in
                    VStack(alignment: .leading, spacing: 6) {
                        NavigationLink(value: CustomerID(id: c.id.int)) {
                            Line(left: c.name.string, sub: "\(Fmt.money(c.spent.int)) spent", right: "")
                        }
                        HStack {
                            Button("Approve") { page.act("customers/\(c.id.int)/tab", [("decision", "approve")]) }
                                .buttonStyle(.borderedProminent).controlSize(.small)
                            ConfirmButton(title: "Reject", message: "Reject \(c.name.string) for a tab?", confirmLabel: "Reject") {
                                page.act("customers/\(c.id.int)/tab", [("decision", "reject")])
                            }
                            .buttonStyle(.bordered).controlSize(.small)
                        }
                    }
                }
                if d.eligible.count > 8 { Text("\(d.eligible.count - 8) more · use “Approve all eligible” in Tools").font(.footnote).foregroundStyle(.secondary) }
            }
        }
    }

    @ViewBuilder private func sales(_ d: J) -> some View {
        let s = d.sales
        Section("Top items") {
            ForEach(s.top_today.array, id: \.self) { Line(left: $0.name.string, right: "\($0.qty.int) sold") }
            // and line 221:
            ForEach(s.top_week.array, id: \.self) { Line(left: $0.name.string, right: "\($0.qty.int) sold") }
            if s.top_today.array.isEmpty { Text("Nothing yet today.").foregroundStyle(.secondary) }
        }
        Section("Top items · last 7 school days") {
            ForEach(s.top_week.array, id: \.self) { Line(left: $0.name.string, right: "\($0.qty.int)") }
            if s.top_week.array.isEmpty { Text("Nothing yet.").foregroundStyle(.secondary) }
        }
        Section("Latest sales") {
            ForEach(s.recent.array, id: \.self) { c in
                if c.customer_id.isNull { saleRow(c) }
                else { NavigationLink(value: CustomerID(id: c.customer_id.int)) { saleRow(c) } }
            }
            if s.recent.array.isEmpty { Text("No sales yet today.").foregroundStyle(.secondary) }
        }
        Section("Top customers") {
            ForEach(Array(d.top.array.enumerated()), id: \.offset) { i, c in
                NavigationLink(value: CustomerID(id: c.id.int)) {
                    Line(left: "\(i + 1). \(c.name.string)", right: Fmt.money(c.spent.int), rightSub: "\(Fmt.number(c.points.int)) pts")
                }
            }
            if d.top.array.isEmpty { Text("No sales yet.").foregroundStyle(.secondary) }
        }
    }

    /// Money on a chart's y axis: "$20", not "20".
    private var dollarAxis: some AxisContent {
        AxisMarks { v in
            AxisGridLine()
            AxisTick()
            AxisValueLabel { if let d = v.as(Double.self) { Text(d == d.rounded() ? "$\(Int(d))" : String(format: "$%.2f", d)) } }
        }
    }

    @ViewBuilder private func saleRow(_ c: J) -> some View {
        let items = c["items"].array.isEmpty ? "Tab repayment" : c["items"].array.map { "\($0.qty.int)× \($0.name.string)" }.joined(separator: ", ")
        let amount = c.sold_cents.int != 0 ? c.sold_cents.int : c.repay_cents.int
        let tag = c.tab_cents.int != 0 ? "tab" : (c.sold_cents.int == 0 ? "repaid" : nil)
        HStack(alignment: .top) {
            Text(Fmt.when(c.at.string, "HH:mm")).foregroundStyle(.secondary).monospacedDigit()
            VStack(alignment: .leading, spacing: 2) {
                if c.customer_id.isNull { Text("No customer").foregroundStyle(.secondary) } else { Text(c.customer.string) }
                Text(items).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing) {
                Text(Fmt.money(amount)).monospacedDigit()
                if let tag { Tag(text: tag == "tab" ? (c.tab_cents.int == c.sold_cents.int ? "On tab" : "\(Fmt.money(c.tab_cents.int)) on tab") : "Tab repaid", color: tag == "tab" ? .blue : .green) }
            }
        }
    }
}

/// Say whose a payment was.
struct AssignSheet: View {
    let page: Page
    let paymentID: String
    let people: [J]
    @State private var who = ""

    var body: some View {
        FormSheet(title: "Assign payment", action: "Assign", ready: !who.isEmpty, page: page,
                  send: { await page.submit("square/payments/\(paymentID)/assign", [("customer", who)]) }) {
            Section {
                PersonField(title: "Who was it for?", text: $who, people: people)
            } footer: {
                Text("Type a name and tap the match.")
            }
        }
    }
}

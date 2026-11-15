import Charts
import SwiftUI

/// Tent mode: the iPhone Duo folded into a tent on the counter, its outer screen showing how the day is going.
/// The figures stay put on the left; the card on the right turns its own pages (top items, the week, today by
/// the hour) so a glance always finds the headline in the same place. Swipe the card to turn it yourself.
struct TentView: View {
    @Environment(\.dismiss) private var dismiss
    /// Shown by folding the phone into a tent, not from a button: no close button then, opening the phone leaves.
    var folded = false
    @State private var page = Page("/")
    @State private var card = 0
    @State private var updated: Date?

    private var d: J { page.data }
    private var isClosed: Bool { !d.closed.isNull }
    private var cards: [String] { isClosed ? ["Best sellers", "This week"] : ["Top items", "This week", "By the hour"] }

    var body: some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 14) {
                header
                if page.loaded { isClosed ? AnyView(closedFigures) : AnyView(figures) }
                else { ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity) }
            }
            .frame(maxWidth: .infinity)
            if page.loaded { pager.frame(maxWidth: .infinity) }
        }
        .padding(16)
        .background(Color(.systemBackground).ignoresSafeArea())
        .environment(\.colorScheme, .dark)
        .preferredColorScheme(.dark)
        .overlay(alignment: .topTrailing) {
            if !folded {
                Button("Done") { dismiss() }.buttonStyle(.bordered).padding(16)
            }
        }
        .statusBarHidden()
        .persistentSystemOverlays(.hidden)
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        .onDisappear { UIApplication.shared.isIdleTimerDisabled = false }
        .task {
            while !Task.isCancelled {
                if await page.load() { updated = Date() }
                try? await Task.sleep(for: .seconds(60))
            }
        }
        .task {   // turn the card every 12 seconds
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(12))
                withAnimation { card = (card + 1) % max(1, cards.count) }
            }
        }
    }

    // MARK: left: the figures

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 0) {
                Text("Today").font(.title2.bold())
                Text(Date.now, format: .dateTime.weekday(.wide).day().month(.wide))
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            Spacer()
            if let updated {
                Label { Text(updated, format: .dateTime.hour().minute()) } icon: {
                    Image(systemName: page.offlineSince == nil ? "arrow.clockwise" : "wifi.slash")
                }
                .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var figures: some View {
        let t = d.sales.today
        let lw = d.sales.vs_last_week_cents.int
        let vs: String? = lw > 0 ? "\(t.sold_cents.int >= lw ? "▲" : "▼") \(abs(Int((Double(t.sold_cents.int - lw) * 100 / Double(lw)).rounded())))% vs last week" : nil
        return VStack(spacing: 10) {
            Tile(title: "Sold", value: Fmt.money(t.sold_cents.int), sub: vs, big: true)
            Grid(horizontalSpacing: 10, verticalSpacing: 10) {
                GridRow {
                    Tile(title: "Orders", value: "\(t.orders.int)")
                    Tile(title: "Average", value: Fmt.money(t.avg_cents.int))
                }
                GridRow {
                    Tile(title: "Customers", value: "\(t.customers.int)")
                    Tile(title: "Taken", value: Fmt.money(t.taken_cents.int))
                }
            }
        }
    }

    private var closedFigures: some View {
        let c = d.closed, l = c.last, n = c.next_in.int
        return VStack(spacing: 10) {
            Tile(title: "\(c.reason.string) · closed",
                 value: n == 1 ? "Open tomorrow" : "Open " + Fmt.when(c.next.string, n < 7 ? "EEEE" : "EEE d MMM"), big: true)
            if !l.isNull {
                Grid(horizontalSpacing: 10, verticalSpacing: 10) {
                    GridRow {
                        Tile(title: "Sold " + Fmt.when(l.day.string, "EEEE"), value: Fmt.money(l.sold_cents.int))
                        Tile(title: "Orders", value: "\(l.orders.int)")
                    }
                }
            }
        }
    }

    // MARK: right: the card that turns

    private var pager: some View {
        TabView(selection: $card) {
            ForEach(cards.indices, id: \.self) { i in
                VStack(alignment: .leading, spacing: 10) {
                    Text(cards[i]).font(.headline)
                    content(i).frame(maxHeight: .infinity)
                }
                .padding(16)
                .padding(.bottom, 18)   // room for the page dots
                .tag(i)
            }
        }
        .tabViewStyle(.page(indexDisplayMode: .always))
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
    }

    @ViewBuilder private func content(_ i: Int) -> some View {
        let s = d.sales
        switch i {
        case 0:
            let top = Array((isClosed ? d.closed.top : s.top_today).array.prefix(5))
            if top.isEmpty { empty("Nothing sold yet") } else {
                let most = Double(top.map { $0.qty.int }.max() ?? 1)
                VStack(spacing: 8) {
                    ForEach(top, id: \.self) { item in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(item.name.string).lineLimit(1)
                                Spacer()
                                Text("\(item.qty.int)").bold().monospacedDigit()
                            }
                            .font(.subheadline)
                            ProgressView(value: Double(item.qty.int), total: most)
                        }
                    }
                    Spacer(minLength: 0)
                }
            }
        case 1:
            Chart(s.week.array, id: \.self) { w in
                BarMark(x: .value("Day", Fmt.when(w.day.string, "EEE")), y: .value("Sold", Double(w.sold_cents.int) / 100), width: .ratio(0.6))
                    .foregroundStyle(Color("Brand").gradient)
                    .cornerRadius(4)
            }
            .chartYAxis { AxisMarks(position: .leading) { v in AxisGridLine(); AxisValueLabel { if let n = v.as(Double.self) { Text("$\(Int(n))") } } } }
        default:
            let hours = s.hours.array
            if hours.isEmpty { empty("No sales yet") } else {
                Chart(hours, id: \.self) { h in
                    BarMark(x: .value("Hour", hourLabel(h.hour.int)), y: .value("Sold", Double(h.last_week.int) / 100))
                        .foregroundStyle(by: .value("When", "Last week")).position(by: .value("When", "Last week")).cornerRadius(3)
                    BarMark(x: .value("Hour", hourLabel(h.hour.int)), y: .value("Sold", Double(h.cents.int) / 100))
                        .foregroundStyle(by: .value("When", "Today")).position(by: .value("When", "Today")).cornerRadius(3)
                }
                .chartForegroundStyleScale(["Today": Color("Brand"), "Last week": Color.secondary.opacity(0.4)])
                .chartLegend(position: .top, alignment: .leading)
            }
        }
    }

    private func empty(_ s: String) -> some View {
        ContentUnavailableView(s, systemImage: "cart").frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func hourLabel(_ h: Int) -> String { h == 12 ? "12pm" : h > 12 ? "\(h - 12)pm" : "\(h)am" }
}

/// One figure on a rounded card.
private struct Tile: View {
    let title: String
    let value: String
    var sub: String?
    var big = false

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            Text(value)
                .font(.system(big ? .largeTitle : .title2, design: .rounded).weight(.bold))
                .monospacedDigit().lineLimit(1).minimumScaleFactor(0.5)
                .contentTransition(.numericText())
            if let sub { Text(sub).font(.caption).foregroundStyle(.secondary) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .padding(14)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}

// MARK: folding into a tent

/// Folded into a tent (the hinge not fully open, the app on the narrow outer screen, turned on its side), the
/// staff app becomes the sales panel. Open the phone, or stand it upright, and it goes back to what it was.
struct TentWhenFolded: ViewModifier {
    @State private var hinge: Int = 0   // UIHinge.Status raw value; 0 unknown (no hinge, or before iOS 27.1)
    @Environment(\.horizontalSizeClass) private var size

    func body(content: Content) -> some View {
        GeometryReader { geo in
            let tent = (hinge == 1 || hinge == 2) && size == .compact && geo.size.width > geo.size.height
            ZStack {
                content
                if tent { TentView(folded: true).transition(.opacity) }
            }
            .animation(.easeInOut(duration: 0.3), value: tent)
        }
        .background(HingeReader(status: $hinge))
    }
}

/// Reports the hinge's status through the system's hinge interaction (iOS 27.1 and later).
private struct HingeReader: UIViewRepresentable {
    @Binding var status: Int

    func makeUIView(context: Context) -> UIView {
        let v = UIView()
        v.isUserInteractionEnabled = false
        if #available(iOS 27.1, *) {
            let binding = $status
            v.addInteraction(UIHingeInteraction { _, update in
                binding.wrappedValue = update.hinge?.status.rawValue ?? 0
            })
        }
        return v
    }

    func updateUIView(_ uiView: UIView, context: Context) {}
}

extension View {
    func tentWhenFolded() -> some View { modifier(TentWhenFolded()) }
}

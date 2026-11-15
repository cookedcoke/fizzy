import Charts
import CoreMotion
import SwiftUI

/// Tent mode: the iPhone Duo folded into a tent on the counter, its outer screen showing how the day is going.
/// The figures stay put on the left; the card on the right turns its own pages (top items, the week, today by
/// the hour) so a glance always finds the headline in the same place. Swipe the card to turn it yourself.
struct TentView: View {
    @State private var page = Page("/")
    @State private var card = 0
    @State private var updated: Date?

    private var d: J { page.data }
    private var isClosed: Bool { !d.closed.isNull }
    private var cards: [String] { isClosed ? ["Best sellers", "This week"] : ["Top items", "This week", "By the hour"] }

    /// One gap for everything: between cards, around the edge, and inside each card.
    static let gap: CGFloat = 12
    static let radius: CGFloat = 20

    var body: some View {
        GeometryReader { geo in
            // The outer screen keeps a side inset for its camera. Only the title row moves over for it; the cards
            // run the full width beneath, with the same small margin all round.
            content(camera: geo.safeAreaInsets, screenHeight: geo.size.height + geo.safeAreaInsets.bottom)
                .ignoresSafeArea(.container, edges: [.horizontal, .bottom])
        }
        .background(Color(.systemBackground).ignoresSafeArea())
        .environment(\.colorScheme, .dark)
        .preferredColorScheme(.dark)
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

    /// Where the outer screen's camera is, from the system's occlusion region (iOS 27.1); nil without one.
    @State private var cameraRect: CGRect?

    /// In the tent the outer screen's camera is at the bottom right: the cards stop exactly at the top of it.
    /// Otherwise one small margin all round.
    private func content(camera inset: EdgeInsets, screenHeight: CGFloat) -> some View {
        let cam = cameraRect
        let cameraLow = cam.map { $0.midY > screenHeight / 2 } ?? false
        let edge = Self.gap * 1.5
        let bottom = cameraLow ? screenHeight - cam!.minY : max(edge, inset.bottom)
        return VStack(spacing: Self.gap) {
            header
            if page.loaded {
                HStack(spacing: Self.gap) {
                    Group { if isClosed { closedFigures } else { figures } }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    pager.frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .padding([.top, .horizontal], Self.gap * 1.5)
        .padding(.bottom, bottom)
        .background(CameraReader(rect: $cameraRect))
    }

    // MARK: left: the figures

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("Today").font(.title3.bold())
            Text(Date.now, format: .dateTime.weekday(.wide).day().month(.wide)).foregroundStyle(.secondary)
            Spacer()
            if let updated {
                Label { Text(updated, format: .dateTime.hour().minute()) } icon: {
                    Image(systemName: page.offlineSince == nil ? "arrow.clockwise" : "wifi.slash")
                }
                .font(.footnote).foregroundStyle(.secondary)
            }
        }
    }

    private var figures: some View {
        let t = d.sales.today
        let lw = d.sales.vs_last_week_cents.int
        let vs: String? = lw > 0 ? "\(t.sold_cents.int >= lw ? "▲" : "▼") \(abs(Int((Double(t.sold_cents.int - lw) * 100 / Double(lw)).rounded())))% vs last week" : nil
        return VStack(spacing: Self.gap) {
            Tile(title: "Sold", value: Fmt.money(t.sold_cents.int), sub: vs, big: true)
            Grid(horizontalSpacing: Self.gap, verticalSpacing: Self.gap) {
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
        return VStack(spacing: Self.gap) {
            Tile(title: "\(c.reason.string) · closed",
                 value: n == 1 ? "Open tomorrow" : "Open " + Fmt.when(c.next.string, n < 7 ? "EEEE" : "EEE d MMM"), big: true)
            if !l.isNull {
                Grid(horizontalSpacing: Self.gap, verticalSpacing: Self.gap) {
                    GridRow {
                        Tile(title: Fmt.when(l.day.string, "EEEE"), value: Fmt.money(l.sold_cents.int))
                        Tile(title: "Orders", value: "\(l.orders.int)")
                    }
                }
            }
        }
    }

    // MARK: right: the card that turns

    private var pager: some View {
        let i = min(card, cards.count - 1)
        return VStack(alignment: .leading, spacing: Self.gap) {
            HStack {
                Text(cards[i]).font(.subheadline).foregroundStyle(.secondary)
                Spacer()
                HStack(spacing: 6) {
                    ForEach(cards.indices, id: \.self) { j in
                        Circle().fill(j == i ? Color.primary : Color.secondary.opacity(0.4)).frame(width: 6, height: 6)
                    }
                }
            }
            content(i)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .id(i)
                .transition(.opacity)
        }
        .padding(16)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: Self.radius, style: .continuous))
        .contentShape(Rectangle())
        .onTapGesture { withAnimation { card = (i + 1) % cards.count } }
        .gesture(DragGesture(minimumDistance: 30).onEnded { g in
            let step = g.translation.width < 0 ? 1 : cards.count - 1
            withAnimation { card = (i + step) % cards.count }
        })
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

    /// Like a widget: what it is at the top, the figure at the bottom.
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            Spacer(minLength: 4)
            Text(value)
                .font(.system(big ? .largeTitle : .title2, design: .rounded).weight(.bold))
                .monospacedDigit().lineLimit(1).minimumScaleFactor(0.5)
                .contentTransition(.numericText())
            if let sub { Text(sub).font(.footnote).foregroundStyle(.secondary).lineLimit(1) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .padding(16)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: TentView.radius, style: .continuous))
    }
}

// MARK: folding into a tent

/// Folded into a tent (the hinge not fully open, the app on the narrow outer screen, turned on its side), the
/// staff app becomes the sales panel. Open the phone, or stand it upright, and it goes back to what it was.
struct TentWhenFolded: ViewModifier {
    @State private var hinge: Int = 0   // UIHinge.Status raw value; 0 unknown (no hinge, or before iOS 27.1)
    @State private var still = Stillness()
    @State private var camera: CGRect?
    @Environment(\.horizontalSizeClass) private var size

    func body(content: Content) -> some View {
        GeometryReader { geo in
            // Standing as a tent, the outer screen's camera is at the bottom right; turned the other way up it's at the
            // top left, which isn't a tent.
            let cameraLow = camera.map { $0.midY > geo.size.height / 2 } ?? false
            let tent = (hinge == 1 || hinge == 2) && size == .compact && geo.size.width > geo.size.height
                && cameraLow && still.isStill
            ZStack {
                content
                if tent { TentView().transition(.opacity) }
            }
            .animation(.easeInOut(duration: 0.3), value: tent)
        }
        .background(HingeReader(status: $hinge))
        .background(CameraReader(rect: $camera).ignoresSafeArea())
        .onAppear { still.start() }
        .onDisappear { still.stop() }
    }
}

/// Like StandBy, which waits until the iPhone is on its side and stationary: still means the motion sensors have
/// read almost nothing for 3 seconds. Once it's showing, a nudge along the table (a slide or a twist) is let go:
/// only lifting or tipping it, or a long shove, ends it. With no motion sensors (the simulator) it counts as still.
@Observable
@MainActor
final class Stillness {
    private(set) var isStill = !CMMotionManager().isDeviceMotionAvailable
    private let motion = CMMotionManager()
    private var quietSince: Date?
    private var movingSince: Date?

    func start() {
        guard motion.isDeviceMotionAvailable, !motion.isDeviceMotionActive else { return }
        motion.deviceMotionUpdateInterval = 0.1
        motion.startDeviceMotionUpdates(to: .main) { [weak self] m, _ in
            guard let self, let m else { return }
            // Split the motion by gravity: along it is up and down off the table, across it is sliding on the table.
            let g = m.gravity, a = m.userAcceleration, r = m.rotationRate
            let gl = max((g.x * g.x + g.y * g.y + g.z * g.z).squareRoot(), 0.01)
            let (ux, uy, uz) = (g.x / gl, g.y / gl, g.z / gl)
            let up = a.x * ux + a.y * uy + a.z * uz                                   // in g
            let slide = ((a.x - up * ux) * (a.x - up * ux) + (a.y - up * uy) * (a.y - up * uy)
                + (a.z - up * uz) * (a.z - up * uz)).squareRoot()
            let twist = r.x * ux + r.y * uy + r.z * uz                                // turning flat on the table, rad/s
            let tip = ((r.x - twist * ux) * (r.x - twist * ux) + (r.y - twist * uy) * (r.y - twist * uy)
                + (r.z - twist * uz) * (r.z - twist * uz)).squareRoot()               // tilting off it

            let moving: Bool
            if self.isStill {   // showing: a nudge is fine, being picked up or tipped isn't
                moving = slide > 0.3 || abs(up) > 0.1 || tip > 0.6 || abs(twist) > 2
            } else {            // to start, it has to be properly settled
                moving = slide > 0.05 || abs(up) > 0.03 || tip > 0.15 || abs(twist) > 0.3
            }
            if moving {
                self.quietSince = nil
                let since = self.movingSince ?? Date()
                self.movingSince = since
                // a knock is a moment; being picked up keeps going
                if self.isStill, Date().timeIntervalSince(since) >= 0.4 { self.isStill = false }
            } else {
                self.movingSince = nil
                let since = self.quietSince ?? Date()
                self.quietSince = since
                if !self.isStill, Date().timeIntervalSince(since) >= 3 { self.isStill = true }
            }
        }
    }

    func stop() { motion.stopDeviceMotionUpdates() }
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

/// Reports the camera's rect in this view's space: the system's occlusion region, less its margins.
private struct CameraReader: UIViewRepresentable {
    @Binding var rect: CGRect?

    final class Reader: UIView {
        var report: (CGRect?) -> Void = { _ in }
        override func safeAreaInsetsDidChange() { super.safeAreaInsetsDidChange(); setNeedsLayout() }
        override func layoutSubviews() {
            super.layoutSubviews()
            var found: CGRect?
            if #available(iOS 27.1, *), let r = reservedRegions(kind: .occlusion).first {
                found = r.frame.inset(by: r.margins)
            }
            report(found)
        }
    }

    func makeUIView(context: Context) -> Reader {
        let v = Reader()
        v.isUserInteractionEnabled = false
        return v
    }

    func updateUIView(_ v: Reader, context: Context) {
        let binding = $rect
        v.report = { r in if binding.wrappedValue != r { DispatchQueue.main.async { binding.wrappedValue = r } } }
        v.setNeedsLayout()
    }
}

extension View {
    func tentWhenFolded() -> some View { modifier(TentWhenFolded()) }
}

import SwiftUI

// The app's own effects, drawn natively: a light running round a border, a burst of confetti, bubbles rising in
// a card and a gauge ring. Every one stops moving for anyone who has asked for less motion.

/// A band of light that runs round a rounded shape's edge, for something worth noticing (a reward that's ready).
struct ShineBorder: ViewModifier {
    var colors: [Color] = [.yellow, Color("Brand"), .orange, .yellow]
    var width: CGFloat = 2
    var radius: CGFloat = 22
    var period: Double = 4
    @Environment(\.accessibilityReduceMotion) private var still

    func body(content: Content) -> some View {
        content.overlay {
            // 30 frames a second is plenty for a slow sweep, and half the work (several can be on screen at once)
            TimelineView(.animation(minimumInterval: 1 / 30, paused: still)) { tl in
                let turn = still ? 0 : tl.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: period) / period
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(AngularGradient(colors: colors + [colors[0]], center: .center, angle: .degrees(turn * 360)),
                                  lineWidth: width)
            }
            .allowsHitTesting(false)
        }
    }
}

extension View {
    func shineBorder(_ colors: [Color] = [.yellow, Color("Brand"), .orange, .yellow], width: CGFloat = 2, radius: CGFloat = 22) -> some View {
        modifier(ShineBorder(colors: colors, width: width, radius: radius))
    }
}

/// Confetti that bursts once each time `trigger` changes, then lies still and disappears.
struct ConfettiBurst: View {
    var trigger: Int
    var colors: [Color] = [Color("Brand"), .yellow, .orange, .white]
    @State private var start: Date?
    @State private var pieces: [Piece] = []
    @Environment(\.accessibilityReduceMotion) private var still

    struct Piece {
        let x, vx, vy, spin, size: Double
        let color: Color
        let round: Bool
    }

    var body: some View {
        TimelineView(.animation(paused: start == nil)) { tl in
            Canvas { ctx, size in
                guard let start else { return }
                let t = tl.date.timeIntervalSince(start)
                for p in pieces {
                    let x = size.width * p.x + p.vx * t * 60
                    let y = size.height * 0.28 + p.vy * t * 60 + 0.5 * 900 * t * t   // thrown up, then falls
                    guard y < size.height + 20 else { continue }
                    var c = ctx
                    c.opacity = max(0, 1 - t / 2.4)
                    c.translateBy(x: x, y: y)
                    c.rotate(by: .radians(p.spin * t))
                    let r = CGRect(x: -p.size / 2, y: -p.size / 4, width: p.size, height: p.round ? p.size : p.size / 2)
                    c.fill(p.round ? Path(ellipseIn: r) : Path(r), with: .color(p.color))
                }
            }
        }
        .allowsHitTesting(false)
        .onChange(of: trigger) { _, _ in fire() }
        .task(id: start) {
            // gone once it has all fallen
            guard start != nil else { return }
            try? await Task.sleep(for: .seconds(2.6))
            start = nil
        }
    }

    private func fire() {
        guard !still else { return }
        pieces = (0..<90).map { _ in
            Piece(x: .random(in: 0.3...0.7), vx: .random(in: -6...6), vy: .random(in: -14 ... -6),
                  spin: .random(in: -9...9), size: .random(in: 6...11), color: colors.randomElement()!, round: Bool.random())
        }
        start = .now
    }
}

/// Soft bubbles rising through a card, like a fizzy drink.
struct Fizz: View {
    var count = 14
    var color: Color = .white
    @Environment(\.accessibilityReduceMotion) private var still

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: still)) { tl in
            Canvas { ctx, size in
                let t = still ? 0 : tl.date.timeIntervalSinceReferenceDate
                for i in 0..<count {
                    let seed = Double(i) * 12.9898
                    let speed = 0.05 + 0.06 * abs(sin(seed))              // of the height a second
                    let r = 2.5 + 7 * abs(sin(seed * 1.7))
                    let phase = (t * speed + abs(sin(seed * 3.1))).truncatingRemainder(dividingBy: 1)
                    let x = size.width * (0.55 + 0.42 * abs(sin(seed * 2.3))) + 6 * sin(t * 1.3 + seed)
                    let y = size.height * (1.1 - 1.2 * phase)
                    ctx.opacity = 0.22 * sin(.pi * phase)
                    ctx.fill(Path(ellipseIn: CGRect(x: x - r, y: y - r, width: r * 2, height: r * 2)), with: .color(color))
                }
            }
        }
        .allowsHitTesting(false)
    }
}

/// A score round a ring that fills on first sight.
struct ScoreRing: View {
    let value: Int
    var total = 100
    let color: Color
    var size: CGFloat = 56
    @State private var shown = 0.0

    var body: some View {
        ZStack {
            Circle().stroke(color.opacity(0.16), lineWidth: 6)
            Circle().trim(from: 0, to: shown)
                .stroke(color, style: StrokeStyle(lineWidth: 6, lineCap: .round))
                .rotationEffect(.degrees(-90))
            Text("\(value)").font(.system(.title3, design: .rounded).bold()).monospacedDigit().foregroundStyle(color)
        }
        .frame(width: size, height: size)
        .onAppear { withAnimation(.spring(duration: 1.1, bounce: 0.2).delay(0.15)) { shown = Double(min(value, total)) / Double(total) } }
        .onChange(of: value) { _, v in withAnimation(.spring(duration: 0.8)) { shown = Double(min(v, total)) / Double(total) } }
    }
}

/// A row fades and rises into place the first time it appears. (A scroll transition would suit better, but
/// rows inside a List never count as fully on screen to it, so they stayed half faded.)
struct RiseIn: ViewModifier {
    var delay: Double = 0
    @State private var shown = false
    @Environment(\.accessibilityReduceMotion) private var still

    func body(content: Content) -> some View {
        content
            .opacity(shown || still ? 1 : 0)
            .offset(y: shown || still ? 0 : 10)
            .onAppear { withAnimation(.spring(duration: 0.5).delay(delay)) { shown = true } }
    }
}

extension View {
    func scrollReveal(_ delay: Double = 0) -> some View { modifier(RiseIn(delay: delay)) }
}

/// A solid colour with a soft band of light sweeping across it every few seconds (the sign-in button).
struct Shimmer: View {
    let base: Color
    @Environment(\.accessibilityReduceMotion) private var still

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30, paused: still)) { tl in
            let t = tl.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 3) / 3   // a sweep every 3 s
            GeometryReader { geo in
                ZStack {
                    base
                    LinearGradient(colors: [.clear, .white.opacity(0.35), .clear], startPoint: .leading, endPoint: .trailing)
                        .frame(width: geo.size.width * 0.35)
                        .offset(x: still ? -geo.size.width : (t * 1.8 - 0.4) * geo.size.width - geo.size.width * 0.5)
                        .blur(radius: 6)
                }
            }
        }
    }
}

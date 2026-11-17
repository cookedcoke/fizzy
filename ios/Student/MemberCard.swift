import CoreMotion
import SwiftUI

// Red Coke / Black Coke: the member card, drawn like the real thing. The art (brand/member-cards) is the
// card's base and a glossy copy of its triangles; everything that moves is drawn here, from the phone's
// tilt: a band of light that shows the gloss pattern as it passes, a glare, metal type that catches the
// light and an edge that shines where the light hits it. Tap to turn it over.

/// The phone's tilt, -1...1 each way, shared by every card on screen and smoothed so it drifts, not jitters.
@Observable @MainActor
final class Tilt {
    static let shared = Tilt()
    var x = 0.0
    var y = 0.0
    @ObservationIgnored private let motion = CMMotionManager()
    @ObservationIgnored private var users = 0
    @ObservationIgnored private var rest: (Double, Double)?

    func start() {
        users += 1
        guard users == 1, motion.isDeviceMotionAvailable else { return }
        motion.deviceMotionUpdateInterval = 1 / 30
        motion.startDeviceMotionUpdates(to: .main) { [weak self] data, _ in
            guard let self, let g = data?.gravity else { return }
            // Measured from how the phone was held when the card appeared, so any comfortable angle is "flat".
            let base = self.rest ?? (g.x, g.y)
            if self.rest == nil { self.rest = base }
            let tx = max(-1, min(1, (g.x - base.0) * 2.6)), ty = max(-1, min(1, (g.y - base.1) * 2.6))
            self.x += (tx - self.x) * 0.18
            self.y += (ty - self.y) * 0.18
            // the resting angle slowly follows the hand, so after a while the light settles back
            self.rest = (base.0 + (g.x - base.0) * 0.01, base.1 + (g.y - base.1) * 0.01)
        }
    }

    func stop() {
        users = max(0, users - 1)
        guard users == 0 else { return }
        motion.stopDeviceMotionUpdates()
        rest = nil
        x = 0; y = 0
    }
}

struct MemberCard: View {
    /// Red / Black Coke, or null for everyone else (a white card).
    let member: J
    let holder: String
    var points = 0
    var gain = 0
    @State private var drag = CGSize.zero
    @State private var flipped = false
    @State private var landed = false
    @Environment(\.accessibilityReduceMotion) private var still
    private var tilt: Tilt { Tilt.shared }

    private var tier: String { member.isNull ? "white" : member.tier.string }
    private var black: Bool { tier == "black" }
    private var white: Bool { tier == "white" }
    private var art: String { "Member" + tier.capitalized }
    private var pct: String { "\(member.pct.int)%" }
    private var ink: Color { Color(red: 0.086, green: 0.075, blue: 0.06) }

    var body: some View {
        // The card's own sizes are those of the 1536-wide artwork, scaled to however wide it's shown.
        GeometryReader { geo in
            let k = geo.size.width / 1536
            // where the light is: the phone's tilt, or a finger dragging the card
            let lx = still ? 0 : max(-1, min(1, tilt.x + drag.width / 160))
            let ly = still ? 0 : max(-1, min(1, tilt.y + drag.height / 160))
            let shape = RoundedRectangle(cornerRadius: 58 * k, style: .continuous)
            Flip(angle: flipped ? 180 : 0,
                 front: front(k: k, lx: lx, ly: ly).clipShape(shape).overlay { edge(k: k, lx: lx, ly: ly) },
                 back: back(k: k, lx: lx, ly: ly).clipShape(shape).overlay { edge(k: k, lx: lx, ly: ly) })
            .rotation3DEffect(.degrees(lx * 9), axis: (0, 1, 0), perspective: 0.45)
            .rotation3DEffect(.degrees(-ly * 7), axis: (1, 0, 0), perspective: 0.45)
            .rotation3DEffect(.degrees(landed || still ? 0 : -70), axis: (1, 0, 0), anchor: .bottom, perspective: 0.6)
            .scaleEffect(landed || still ? 1 : 0.86)
            .opacity(landed || still ? 1 : 0)
            .shadow(color: (white ? Color.black : black ? Color.black : Color(red: 0.55, green: 0, blue: 0.04)).opacity(white ? 0.16 : black ? 0.32 : 0.28),
                    radius: 9, x: -lx * 6, y: 9 - ly * 4)
        }
        .aspectRatio(1536 / 969, contentMode: .fit)
        .contentShape(Rectangle())
        .onTapGesture {
            withAnimation(.spring(duration: 0.7, bounce: 0.18)) { flipped.toggle() }
        }
        .gesture(DragGesture(minimumDistance: 4)
            .onChanged { v in if !still { drag = v.translation } }
            .onEnded { _ in withAnimation(.spring(duration: 0.6, bounce: 0.35)) { drag = .zero } })
        .sensoryFeedback(.impact(weight: .medium), trigger: flipped)
        .sensoryFeedback(.impact(weight: .light), trigger: landed)
        .onAppear {
            Tilt.shared.start()
            withAnimation(.spring(duration: 0.9, bounce: 0.25).delay(0.1)) { landed = true }
        }
        .onDisappear { Tilt.shared.stop() }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(white ? "Your card. \(Fmt.number(points)) points."
                            : "\(member.name.string) member card. \(Fmt.number(points)) points. \(pct) off everything until \(Fmt.when(member.ends_on.string, "d MMMM")).")
        .accessibilityHint("Turns the card over.")
        .accessibilityAddTraits(.isButton)
    }

    // MARK: front

    private func front(k: CGFloat, lx: Double, ly: Double) -> some View {
        ZStack(alignment: .topLeading) {
            Image(art).resizable()
            // The gloss pattern, seen only where a band of light crosses it; the band slides with the tilt.
            Image(art + "Gloss").resizable()
                .mask {
                    LinearGradient(stops: [.init(color: .clear, location: 0.0), .init(color: .white.opacity(0.85), location: 0.47),
                                           .init(color: .white, location: 0.5), .init(color: .white.opacity(0.4), location: 0.56),
                                           .init(color: .clear, location: 0.72)],
                                   startPoint: UnitPoint(x: -0.6 + lx * 0.9, y: -0.4 + ly * 0.6),
                                   endPoint: UnitPoint(x: 1.0 + lx * 0.9, y: 1.1 + ly * 0.6))
                }
                .opacity(black ? 0.9 : white ? 0.7 : 0.45)
                .blendMode(black ? .screen : .softLight)
            // Glare: a soft spot of light where the light falls.
            RadialGradient(colors: [.white.opacity(black ? 0.16 : white ? 0.2 : 0.22), .clear], center: UnitPoint(x: 0.3 + lx * 0.5, y: 0.2 + ly * 0.5),
                           startRadius: 0, endRadius: 900 * k)
                .blendMode(.plusLighter)
            type(k: k, lx: lx, ly: ly)
        }
    }

    private func type(k: CGFloat, lx: Double, ly: Double) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Spacer()
                if !white { metal(Text(pct).font(.custom("AvenirNext-Medium", size: 104 * k)), lx: lx) }
            }
            .frame(height: 120 * k)
            .padding(.top, 70 * k)
            Spacer(minLength: 0)
            // the points, in the middle of the card
            VStack(spacing: 6 * k) {
                metal(Text(Fmt.number(points)).font(.custom("AvenirNext-Medium", size: 170 * k)).monospacedDigit(), lx: lx)
                    .contentTransition(.numericText(value: Double(points)))
                    .lineLimit(1).minimumScaleFactor(0.5)
                HStack(spacing: 14 * k) {
                    Text("POINTS").font(.system(size: 26 * k, design: .monospaced)).tracking(8 * k)
                        .foregroundStyle(white ? ink.opacity(0.55) : .white.opacity(0.65))
                    if gain > 0 {
                        Text("+\(Fmt.number(gain))").font(.system(size: 24 * k, weight: .semibold, design: .monospaced))
                            .padding(.horizontal, 12 * k).padding(.vertical, 4 * k)
                            .background(white ? AnyShapeStyle(ink.opacity(0.08)) : AnyShapeStyle(.white.opacity(0.16)), in: Capsule())
                            .foregroundStyle(white ? ink.opacity(0.75) : .white.opacity(0.9))
                            .transition(.scale.combined(with: .opacity))
                    }
                }
            }
            .frame(maxWidth: .infinity)
            .offset(y: -40 * k)
            Spacer(minLength: 0)
            metal(Text(white ? "MY CCC" : member.name.string.uppercased()).font(.custom("AvenirNext-Medium", size: 90 * k)).tracking(20 * k), lx: lx)
                .lineLimit(1).minimumScaleFactor(0.6)
            if black {
                Rectangle().fill(Color(red: 0.84, green: 0.09, blue: 0.12)).frame(width: 118 * k, height: 4 * k).padding(.top, 10 * k)
            }
        }
        .padding(.leading, 92 * k).padding(.trailing, 96 * k).padding(.bottom, (black ? 96 : 110) * k)
    }

    private var line: String {
        "MEMBER  ·  \(pct) OFF EVERYTHING" + (member.term.optString.map { "  ·  \($0.uppercased())" } ?? "")
    }

    /// Black Coke's type is polished silver, with its bright streak following the light; Red Coke's is plain white.
    @ViewBuilder private func metal(_ text: Text, lx: Double, soft: Bool = false) -> some View {
        if black {
            text.foregroundStyle(LinearGradient(
                stops: [.init(color: Color(white: soft ? 0.86 : 0.98), location: 0), .init(color: Color(white: 0.58), location: 0.42 + lx * 0.25),
                        .init(color: Color(white: 0.97), location: 0.55 + lx * 0.25), .init(color: Color(white: 0.5), location: 1)],
                startPoint: .top, endPoint: .bottom))
                .shadow(color: .black.opacity(0.7), radius: 0.5, y: 1.5)
        } else if white {
            text.foregroundStyle(ink).shadow(color: .white.opacity(0.8), radius: 0, y: 1.5)   // printed, a hair embossed
        } else {
            text.foregroundStyle(.white).shadow(color: .black.opacity(0.18), radius: 6, y: 3)
        }
    }

    // MARK: back

    private func back(k: CGFloat, lx: Double, ly: Double) -> some View {
        ZStack {
            Image(art).resizable()
                .scaleEffect(x: -1, y: 1)
                .overlay(Color.black.opacity(black ? 0.35 : white ? 0.03 : 0.25))
            // the magnetic stripe, as on a real card
            VStack(spacing: 0) {
                Rectangle().fill(LinearGradient(colors: [Color(white: 0.05), Color(white: 0.12), Color(white: 0.04)], startPoint: .top, endPoint: .bottom))
                    .frame(height: 150 * k).padding(.top, 90 * k)
                Spacer()
            }
            if white {
                VStack(alignment: .leading, spacing: 18 * k) {
                    Spacer()
                    Text("Get 10% or 20% off everything.")
                        .font(.custom("AvenirNext-Medium", size: 48 * k)).foregroundStyle(ink)
                    Text("Red Coke is $5 for 10% off, Black Coke $12 for 20% off, all term. Ask at the stall and this card turns red or black.")
                        .font(.system(size: 28 * k, design: .monospaced)).foregroundStyle(ink.opacity(0.7))
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(alignment: .lastTextBaseline) {
                        Text(holder.uppercased()).font(.system(size: 36 * k, design: .monospaced)).tracking(5 * k).foregroundStyle(ink)
                            .lineLimit(1).minimumScaleFactor(0.5)
                        Spacer(minLength: 20 * k)
                        Text("COOKED COKE CO").font(.system(size: 26 * k, design: .monospaced)).tracking(3 * k)
                            .foregroundStyle(ink.opacity(0.55)).lineLimit(1).fixedSize()
                    }
                    .padding(.top, 12 * k)
                }
                .padding(.horizontal, 92 * k).padding(.bottom, 70 * k)
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                VStack(alignment: .leading, spacing: 18 * k) {
                    Spacer()
                    Text("\(pct) off everything at the stall, all term.")
                        .font(.custom("AvenirNext-Medium", size: 48 * k)).foregroundStyle(.white)
                    Text("Say you're \(member.name.string) when you pay, and the discount comes off before anything goes on your tab. Free rewards stay free.")
                        .font(.system(size: 28 * k, design: .monospaced)).foregroundStyle(.white.opacity(0.75))
                        .fixedSize(horizontal: false, vertical: true)
                    Text(line).font(.system(size: 26 * k, design: .monospaced)).tracking(4 * k)
                        .foregroundStyle(.white.opacity(0.6)).lineLimit(1).minimumScaleFactor(0.5)
                        .padding(.top, 12 * k)
                    HStack(alignment: .lastTextBaseline) {
                        metal(Text(holder.uppercased()).font(.system(size: 36 * k, design: .monospaced)).tracking(5 * k), lx: lx, soft: true)
                            .lineLimit(1).minimumScaleFactor(0.5)
                        Spacer(minLength: 20 * k)
                        Text("VALID TO  \(Fmt.when(member.ends_on.string, "d MMM yyyy").uppercased())")
                            .font(.system(size: 26 * k, design: .monospaced)).tracking(3 * k)
                            .foregroundStyle(.white.opacity(0.6)).lineLimit(1).fixedSize()
                    }
                }
                .padding(.horizontal, 92 * k).padding(.bottom, 70 * k)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    // MARK: edge

    /// A polished edge: bright where the light is, dark opposite; on Black Coke a faint spectrum runs along it.
    private func edge(k: CGFloat, lx: Double, ly: Double) -> some View {
        let shape = RoundedRectangle(cornerRadius: 58 * k, style: .continuous)
        let angle = Angle.radians(atan2(-0.4 - ly, -0.6 - lx))
        return ZStack {
            shape.strokeBorder(AngularGradient(stops: [
                .init(color: .white.opacity(black ? 0.85 : 0.6), location: 0), .init(color: .white.opacity(0.08), location: 0.25),
                .init(color: .black.opacity(white ? 0.14 : 0.3), location: 0.5), .init(color: .white.opacity(0.08), location: 0.75),
                .init(color: .white.opacity(black ? 0.85 : 0.6), location: 1)],
                center: .center, angle: angle), lineWidth: max(1.2, 4 * k))
            if black {
                shape.strokeBorder(AngularGradient(colors: ([.red, .orange, .yellow, .green, .cyan, .blue, .purple, .red] as [Color]).map { $0.opacity(0.22) },
                                                   center: .center, angle: angle * 2), lineWidth: max(0.8, 2 * k))
                    .blendMode(.plusLighter)
            }
        }
        .allowsHitTesting(false)
    }
}

/// Turns a card over: whichever face is towards you is the only one drawn, swapping the instant the card is
/// edge-on (so neither face shows through the other, mirrored, part-way round).
private struct Flip<Front: View, Back: View>: View, Animatable {
    var angle: Double
    let front: Front
    let back: Back

    var animatableData: Double {
        get { angle }
        set { angle = newValue }
    }

    var body: some View {
        let a = (angle.truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360)
        let showsBack = a > 90 && a < 270
        ZStack {
            front.opacity(showsBack ? 0 : 1)
            back.rotation3DEffect(.degrees(180), axis: (0, 1, 0)).opacity(showsBack ? 1 : 0)
        }
        .rotation3DEffect(.degrees(angle), axis: (0, 1, 0), perspective: 0.45)
    }
}

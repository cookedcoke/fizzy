import SwiftUI

/// The top of a hunt (or any challenge) card, in My CCC and the App Clip alike: the ad's orange, the title,
/// the prize as a tag, one bar per stop filling in as they're found, and the run's clock on a timed hunt.
struct HuntHero: View {
    let card: J

    static let orange = Color(red: 1, green: 0.42, blue: 0.1)
    static let deep = Color(red: 0.82, green: 0.24, blue: 0.04)

    var body: some View {
        let total = max(card.progress.total.int, 1), have = min(card.progress.done.int, total)
        let run = card.run
        let finished = card.done.bool || run.finished.bool
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 10) {
                Text(card.title.string).font(.title3.weight(.heavy)).lineLimit(2)
                Spacer(minLength: 8)
                if !run.isNull && !finished && run.started_ms.double > 0 {
                    Clock(startMs: run.started_ms.double)
                } else if finished, let time = run.elapsed.optString {
                    Label(time, systemImage: "stopwatch.fill").font(.headline.monospacedDigit())
                }
            }
            if let prize = card.prize.optString {
                Label(prize, systemImage: "gift.fill")
                    .font(.subheadline.weight(.semibold))
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(.white.opacity(0.22), in: Capsule())
            }
            if total > 1 {
                HStack(spacing: total > 16 ? 2 : 4) {
                    ForEach(0..<total, id: \.self) { i in
                        Capsule().fill(i < have ? Color.white : Color.white.opacity(0.28)).frame(height: 8)
                    }
                }
                .animation(.spring(response: 0.4, dampingFraction: 0.7), value: have)
                HStack {
                    Text(finished ? "All \(total) found" : "\(have) of \(total) found").monospacedDigit()
                    Spacer()
                    if run.rank.int > 0 { Text("#\(run.rank.int) so far") }
                }
                .font(.subheadline.weight(.semibold))
            }
        }
        .foregroundStyle(.white)
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(LinearGradient(colors: [Self.orange, Self.deep], startPoint: .topLeading, endPoint: .bottomTrailing),
                    in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .listRowInsets(EdgeInsets())
        .listRowBackground(Color.clear)
    }

    /// The run's time so far, ticking each second.
    private struct Clock: View {
        let startMs: Double
        var body: some View {
            TimelineView(.periodic(from: .now, by: 1)) { tl in
                let s = max(0, Int(tl.date.timeIntervalSince1970 - startMs / 1000))
                Label(s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60) : String(format: "%d:%02d", s / 60, s % 60),
                      systemImage: "stopwatch.fill")
                    .font(.headline.monospacedDigit())
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(.black.opacity(0.18), in: Capsule())
            }
        }
    }
}

/// One stop on a hunt card: found ones ticked, the next clue stood out in orange.
struct HuntStepRow: View {
    let step: J

    var body: some View {
        let done = step.done.bool
        let clue = !done && step.detail.string == "Your next clue"
        HStack(spacing: 12) {
            ZStack {
                Circle().fill(done ? Color.green.opacity(0.15) : (clue ? HuntHero.orange.opacity(0.15) : Color.secondary.opacity(0.12)))
                Image(systemName: done ? "checkmark" : (clue ? "magnifyingglass" : "qrcode"))
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(done ? Color.green : (clue ? HuntHero.orange : Color.secondary))
            }
            .frame(width: 30, height: 30)
            VStack(alignment: .leading, spacing: 2) {
                if clue {
                    Text("Your next clue").font(.caption.weight(.semibold)).foregroundStyle(HuntHero.orange).textCase(.uppercase)
                    Text(step.label.string).font(.body.weight(.semibold))
                } else {
                    Text(step.label.string).foregroundStyle(done ? .primary : .secondary)
                    if let detail = step.detail.optString, !done {
                        Text(detail).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(.vertical, clue ? 4 : 0)
    }
}

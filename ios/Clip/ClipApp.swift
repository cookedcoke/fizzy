import StoreKit
import SwiftUI

/// The My CCC App Clip: a hunt poster's code, opened from the camera with nothing installed. It plays
/// the hunt without signing in (progress is kept against this phone by the server), and once every
/// stop is found it shows the claim code to give at the stall. The same links open the website
/// anywhere the clip can't, and the full app when it's installed.
@main
struct ClipApp: App {
    var body: some Scene {
        WindowGroup {
            HuntView()
                .tint(Color("Brand"))
        }
    }
}

@Observable
@MainActor
final class Hunt {
    var cards: [J] = []
    var message: String?
    var ok = true
    var loading = false
    var problem: String?

    /// Where the last code came from, so the clip can show progress again when opened without one.
    private var base: URL? {
        get { UserDefaults.standard.url(forKey: "huntBase") }
        set { UserDefaults.standard.set(newValue, forKey: "huntBase") }
    }

    private let session: URLSession = {
        let c = URLSessionConfiguration.default
        c.httpCookieStorage = .shared   // the server keeps this phone's progress against its session cookie
        c.requestCachePolicy = .reloadIgnoringLocalCacheData
        c.timeoutIntervalForRequest = 20
        c.httpAdditionalHeaders = ["X-Fizzy-Format": "json"]
        return URLSession(configuration: c)
    }()

    /// A code's link (from the camera, or the clip's own scanner): ask the server what it means.
    func open(_ url: URL) async {
        if var c = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            c.path = ""; c.query = nil; c.fragment = nil
            base = c.url
        }
        await fetch(url)
    }

    /// Opened without a code: what this phone has found so far.
    func refresh() async {
        guard let base else { return }
        // The short-link and student hosts answer /hunt; anywhere else the student pages are under /me.
        let host = base.host ?? ""
        await fetch(base.appendingPathComponent(host.hasPrefix("link.") || host.hasPrefix("my.") ? "hunt" : "me/hunt"))
    }

    private func fetch(_ url: URL) async {
        loading = true
        defer { loading = false }
        do {
            restoreCookies(for: url)
            let (data, _) = try await session.data(from: url)
            saveCookies(for: url)
            let page = try JSONDecoder().decode(J.self, from: data)
            if !page.cards.isNull { cards = page.cards.array }
            message = page.message.optString
            ok = page.ok.bool
            problem = nil
        } catch {
            problem = isOffline(error) ? "No signal. Try the code again when there is." : "Couldn't reach Cooked Coke. Try again."
        }
    }

    /// The session cookie is this phone's progress. iOS writes cookies to disk lazily, and a clip can be
    /// closed seconds after a scan, so keep our own copy.
    private func saveCookies(for url: URL) {
        let cookies = HTTPCookieStorage.shared.cookies(for: url) ?? []
        let saved = cookies.compactMap { $0.properties }.map { $0.reduce(into: [String: Any]()) { $0[$1.key.rawValue] = $1.value } }
        UserDefaults.standard.set(saved, forKey: "huntCookies")
    }

    private func restoreCookies(for url: URL) {
        guard HTTPCookieStorage.shared.cookies(for: url)?.isEmpty != false,
              let saved = UserDefaults.standard.array(forKey: "huntCookies") as? [[String: Any]] else { return }
        for p in saved {
            let props = Dictionary(uniqueKeysWithValues: p.map { (HTTPCookiePropertyKey($0.key), $0.value) })
            if let c = HTTPCookie(properties: props) { HTTPCookieStorage.shared.setCookie(c) }
        }
    }
}

struct HuntView: View {
    @State private var hunt = Hunt()
    @State private var scanning = false
    @State private var offerApp = false

    var body: some View {
        NavigationStack {
            List {
                if let problem = hunt.problem {
                    Label(problem, systemImage: "wifi.exclamationmark").foregroundStyle(.red)
                }
                if let message = hunt.message {
                    Label(message, systemImage: hunt.ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                        .foregroundStyle(hunt.ok ? .green : .red)
                }
                ForEach(Array(hunt.cards.enumerated()), id: \.offset) { _, card in
                    HuntSection(card: card)
                }
                if hunt.cards.isEmpty && !hunt.loading && hunt.problem == nil {
                    ContentUnavailableView("Scan a hunt code", systemImage: "qrcode.viewfinder",
                                           description: Text("Find a Cooked Coke hunt poster and scan its code."))
                }
                Section {
                } footer: {
                    Text("Kept on this phone, so scan every code with it. Have My CCC? Sign in there and it moves to your account.")
                }
            }
            .navigationTitle("Hunt")
            .toolbar {
                // Up top so it's always in reach, however long the list of stops.
                ToolbarItem(placement: .topBarTrailing) {
                    Button { scanning = true } label: { HStack(spacing: 6) { Image(systemName: "qrcode.viewfinder"); Text("Scan") }.bold() }
                        .buttonStyle(.borderedProminent)
                }
            }
            .overlay { if hunt.loading && hunt.cards.isEmpty { ProgressView() } }
            .refreshable { await hunt.refresh() }
            .sheet(isPresented: $scanning) {
                CodeScanner(title: "Scan the next code", read: { URL(string: $0)?.scheme?.lowercased().hasPrefix("http") == true ? $0 : nil }) { text in
                    scanning = false
                    if let url = URL(string: text) { Task { await hunt.open(url) } }
                }
            }
            // When they've finished, offer the full app (rewards, points and tabs) without leaving the clip.
            .appStoreOverlay(isPresented: $offerApp) { SKOverlay.AppClipConfiguration(position: .bottom) }
            .onChange(of: hunt.cards.contains { $0.claim.optString != nil }) { _, finished in if finished { offerApp = true } }
        }
        .onContinueUserActivity(NSUserActivityTypeBrowsingWeb) { activity in
            if let url = activity.webpageURL { Task { await hunt.open(url) } }
        }
        .task {
            #if DEBUG
            // Xcode's App Clip testing variable, for a launch that doesn't deliver the link itself.
            if let s = ProcessInfo.processInfo.environment["_XCAppClipURL"], let url = URL(string: s) { await hunt.open(url); return }
            #endif
            if hunt.cards.isEmpty { await hunt.refresh() }
        }
    }
}

/// One hunt: its stops with ticks, then the claim code once they're all found.
private struct HuntSection: View {
    let card: J

    var body: some View {
        let steps = card.steps.array
        Section {
            if let claim = card.claim.optString, !card.claimed.bool {
                VStack(spacing: 6) {
                    Text("Finished! Show this at the stall").font(.headline)
                    Text(claim).font(.system(size: 52, weight: .bold, design: .rounded)).kerning(6).monospaced()
                        .textSelection(.enabled)
                    Text("Your claim code. Staff type it in to give you \(card.prize.string.lowercased()).")
                        .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity).padding(.vertical, 8)
            } else if card.claimed.bool {
                Label("Prize claimed. Nice one!", systemImage: "gift.fill").foregroundStyle(.green)
            } else {
                let done = card.progress.done.int, total = max(card.progress.total.int, 1)
                HStack(spacing: 14) {
                    Gauge(value: Double(done), in: 0...Double(total)) {
                        EmptyView()
                    } currentValueLabel: {
                        Text("\(done)").monospacedDigit()
                    }
                    .gaugeStyle(.accessoryCircularCapacity)
                    .tint(.green)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(done) of \(total) found").font(.headline).monospacedDigit()
                        Text("Find them all for \(card.prize.string.lowercased())").font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 4)
            }
            ForEach(Array(steps.enumerated()), id: \.offset) { _, step in
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(step.label.string)
                        if let detail = step.detail.optString, !step.done.bool {
                            Text(detail).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                } icon: {
                    Image(systemName: step.done.bool ? "checkmark.circle.fill" : "qrcode")
                        .foregroundStyle(step.done.bool ? Color.green : Color.secondary)
                }
            }
        } header: {
            Text(card.title.string)
        } footer: {
            if let text = card.text.optString { Text(text) }
        }
    }
}

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
    /// A scan's answer, shown as an alert (as My CCC does) until it's dismissed.
    var popup: String?
    var ok = true
    var loading = false
    var problem: String?

    /// Where the last code came from, so the clip can show progress again when opened without one.
    private var base: URL? {
        get { UserDefaults.standard.url(forKey: "huntBase") }
        set { UserDefaults.standard.set(newValue, forKey: "huntBase") }
    }
    var baseHost: String? { base?.host?.lowercased() }
    /// Bumped by every request: only the latest one's answer is shown (a launch can start two at once).
    private var latest = 0
    /// Something has been asked for already (the link the clip was opened with), so there's no need to refresh.
    var started: Bool { latest > 0 }

    /// The hosts whose codes are ours: the posters' short links and the student site.
    nonisolated static let hosts: Set<String> = ["link.cookedcoke.co", "my.cookedcoke.co"]

    /// A scanned code the clip can use: a hunt code's link on one of our hosts (or the one the clip was opened
    /// from, for a test server). Not a counter sign-in code (…/q/…), and not anyone else's link.
    nonisolated static func accepts(_ text: String, alsoHost: String?) -> Bool {
        guard let url = URL(string: text), let scheme = url.scheme?.lowercased(), scheme == "https" || scheme == "http",
              let host = url.host?.lowercased(), hosts.contains(host) || host == alsoHost else { return false }
        return StudentAPI.huntCode(in: url) != nil
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
        var c = URLComponents(url: url, resolvingAgainstBaseURL: false)
        c?.path = ""; c?.query = nil; c?.fragment = nil
        guard let root = c?.url else { return }
        // Not a code (the link host's start page, or /hunt): what this phone has found there.
        await fetch(StudentAPI.huntCode(in: url) != nil ? url : Self.huntPage(root), base: root)
    }

    /// Opened without a code: what this phone has found so far.
    func refresh() async {
        guard let base else { return }
        await fetch(Self.huntPage(base), base: nil)
    }

    /// The short-link and student hosts answer /hunt; anywhere else the student pages are under /me.
    nonisolated static func huntPage(_ root: URL) -> URL {
        let host = root.host ?? ""
        return root.appendingPathComponent(host.hasPrefix("link.") || host.hasPrefix("my.") ? "hunt" : "me/hunt")
    }

    /// A timed hunt asks for a nickname (all the board shows) and a class, so staff can find a winner. Returns an error to show, or nil.
    func saveName(nickname: String, klass: String) async -> String? {
        guard let base else { return "Scan a hunt poster first." }
        var req = URLRequest(url: Self.huntPage(base).appendingPathComponent("name"))
        req.httpMethod = "POST"
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        var form = URLComponents()
        form.queryItems = [URLQueryItem(name: "nickname", value: nickname), URLQueryItem(name: "klass", value: klass)]
        req.httpBody = form.percentEncodedQuery?.data(using: .utf8)
        do {
            restoreCookies(for: req.url!)
            let (data, _) = try await session.data(for: req)
            saveCookies(for: req.url!)
            let answer = try JSONDecoder().decode(J.self, from: data)
            if answer.ok.bool { await refresh(); return nil }
            return answer.message.optString ?? "Couldn't save that. Try again."
        } catch {
            return isOffline(error) ? "No signal. Try again when there is." : "Couldn't reach Cooked Coke. Try again."
        }
    }

    /// `base`: where to come back to for progress, saved once this answer has been read.
    private func fetch(_ url: URL, base newBase: URL?) async {
        latest += 1
        let mine = latest
        loading = true
        defer { if mine == latest { loading = false } }
        do {
            restoreCookies(for: url)
            let (data, _) = try await session.data(from: url)
            saveCookies(for: url)
            let page = try JSONDecoder().decode(J.self, from: data)
            guard mine == latest else { return }   // a newer request's answer is the one to show
            if let newBase { base = newBase }
            if !page.cards.isNull { cards = page.cards.array }
            message = page.message.optString
            ok = page.ok.bool
            popup = message
            problem = nil
        } catch {
            guard mine == latest else { return }
            problem = isOffline(error) ? "No signal. Try the code again when there is." : "Couldn't reach Cooked Coke. Try again."
        }
    }

    private static func cookieKey(_ url: URL) -> String { "huntCookies." + (url.host?.lowercased() ?? "") }

    /// The session cookie is this phone's progress. iOS writes cookies to disk lazily, and a clip can be
    /// closed seconds after a scan, so keep our own copy, one per host. Only plain values are kept (UserDefaults
    /// takes nothing else): text, dates and numbers, with any URL as text.
    private func saveCookies(for url: URL) {
        let cookies = HTTPCookieStorage.shared.cookies(for: url) ?? []
        let saved: [[String: Any]] = cookies.compactMap { $0.properties }.map { props in
            props.reduce(into: [String: Any]()) { out, p in
                switch p.value {
                case let v as String: out[p.key.rawValue] = v
                case let v as Date: out[p.key.rawValue] = v
                case let v as NSNumber: out[p.key.rawValue] = v
                case let v as URL: out[p.key.rawValue] = v.absoluteString
                default: break
                }
            }
        }
        guard PropertyListSerialization.propertyList(saved, isValidFor: .binary) else { return }
        UserDefaults.standard.set(saved, forKey: Self.cookieKey(url))
    }

    private func restoreCookies(for url: URL) {
        guard HTTPCookieStorage.shared.cookies(for: url)?.isEmpty != false,
              let saved = (UserDefaults.standard.array(forKey: Self.cookieKey(url))
                           ?? UserDefaults.standard.array(forKey: "huntCookies")) as? [[String: Any]] else { return }   // or 1.1's one copy
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
                    HuntSection(card: card, hunt: hunt)
                }
                if hunt.cards.isEmpty && !hunt.loading && hunt.problem == nil {
                    ContentUnavailableView("Scan a hunt code", systemImage: "qrcode.viewfinder",
                                           description: Text("Find a Cooked Coke hunt poster and scan its code."))
                }
                Section {
                } footer: {
                    Text("Kept on this phone, so scan every code with it. Found them all? Show your claim code at the stall and staff put the prize on your account.")
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
                CodeScanner(title: "Scan the next code", guide: .poster,
                            read: { [alsoHost = hunt.baseHost] in Hunt.accepts($0, alsoHost: alsoHost) ? $0 : nil }) { text in
                    scanning = false
                    if let url = URL(string: text) { Task { await hunt.open(url) } }
                }
            }
            // When they've finished, offer the full app (rewards, points and tabs) without leaving the clip.
            .alert(hunt.ok ? "Scanned" : "Not quite", isPresented: Binding(get: { hunt.popup != nil }, set: { if !$0 { hunt.popup = nil } })) {
                Button("OK", role: .cancel) {}
            } message: { Text(hunt.popup ?? "") }
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
            // Opened from a poster, the link's own answer is on its way (and is newer): don't ask twice.
            if hunt.cards.isEmpty && !hunt.started { await hunt.refresh() }
        }
    }
}

/// One hunt: its stops with ticks, then the claim code once they're all found.
private struct HuntSection: View {
    let card: J
    let hunt: Hunt
    @State private var nickname = ""
    @State private var klass = ""
    @State private var nameProblem: String?
    @State private var saving = false

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
            if card.run.needs_name.bool {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Add your nickname").font(.headline)
                    Text("Only your nickname shows on the board. Your class or year is so staff can find you if you win.")
                        .font(.footnote).foregroundStyle(.secondary)
                    TextField("Nickname", text: $nickname).textFieldStyle(.roundedBorder).autocorrectionDisabled()
                    TextField("Class or year (e.g. 8B)", text: $klass).textFieldStyle(.roundedBorder).autocorrectionDisabled()
                    if let nameProblem { Text(nameProblem).font(.footnote).foregroundStyle(.red) }
                    Button {
                        saving = true
                        Task {
                            nameProblem = await hunt.saveName(nickname: nickname, klass: klass)
                            saving = false
                        }
                    } label: { Text("Save").frame(maxWidth: .infinity) }
                    .buttonStyle(.borderedProminent).disabled(saving || nickname.count < 2 || klass.isEmpty)
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

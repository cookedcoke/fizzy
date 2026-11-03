import SwiftUI

@main
struct StudentApp: App {
    @State private var model = Student()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .tint(Color("Brand"))
        }
    }
}

@Observable
@MainActor
final class Student {
    enum Phase { case loading, signedOut, signedIn }

    var phase = Phase.loading
    var page: J = .null          // the signed-in student's page
    var whatsOn: J = .null       // live rewards and double points days, for the sign-in screen
    var offline = false          // showing the saved copy: no signal
    var problem: String?
    private var loading = false

    /// Fetches the page. A session that's run out signs in again by itself if the passcode was saved.
    func load() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            var home = try await StudentAPI.shared.home()
            if !home.signed_in.bool, let saved = SavedSignIn.load() {
                do {
                    try await StudentAPI.shared.signIn(who: saved.who, passcode: saved.passcode)
                    home = try await StudentAPI.shared.home()
                } catch StudentError.message {
                    SavedSignIn.clear()   // the passcode changed: sign in again by hand
                }
            }
            show(home)
            offline = false
            problem = nil
        } catch where isOffline(error) {
            offline = true
            if phase == .loading {
                if let saved = SavedPage.load() { page = saved; phase = .signedIn } else { phase = .signedOut }
            }
        } catch {
            problem = error.localizedDescription
            if phase == .loading { phase = SavedPage.load() == nil ? .signedOut : .signedIn; page = SavedPage.load() ?? .null }
        }
    }

    private func show(_ home: J) {
        if home.signed_in.bool {
            page = home
            SavedPage.save(home)
            phase = .signedIn
        } else {
            whatsOn = home
            page = .null
            SavedPage.clear()
            phase = .signedOut
        }
    }

    func signIn(who: String, passcode: String, remember: Bool) async throws {
        try await StudentAPI.shared.signIn(who: who, passcode: passcode)
        if remember { SavedSignIn.save(who: who, passcode: passcode) } else { SavedSignIn.clear() }
        await load()
    }

    func useCode(_ token: String) async throws {
        SavedSignIn.clear()
        try await StudentAPI.shared.useCode(token)
        await load()
    }

    /// A hunt poster's link, opened with the app installed (link.cookedcoke.co). Signed in, it counts on
    /// their account; not, it's kept against this phone like on the website, and moves over when they sign in.
    /// Nil for a link that isn't a code (the link host's start page, /hunt): the app just opens.
    func openLink(_ url: URL) async -> String? {
        guard let code = StudentAPI.huntCode(in: url) else { return nil }
        // A link that launched the app arrives while it's still finding out who's signed in: wait for that,
        // or a signed-in student's find would be kept as a guest's.
        while phase == .loading {
            if Task.isCancelled { return nil }
            try? await Task.sleep(for: .milliseconds(100))
        }
        do {
            if phase == .signedIn {
                let message = try await StudentAPI.shared.scan(url.absoluteString)
                await load()
                return message
            }
            return try await StudentAPI.shared.guestScan(code)
        } catch {
            return isOffline(error) ? "No signal. Scan it again when there is." : error.localizedDescription
        }
    }

    func signOut() async {
        SavedSignIn.clear()
        SavedPage.clear()
        await StudentAPI.shared.signOut()
        page = .null
        phase = .signedOut
        await load()
    }
}

struct RootView: View {
    @Environment(Student.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var linkResult: String?

    var body: some View {
        Group {
            switch model.phase {
            case .loading: ProgressView()
            case .signedOut: SignInView()
            case .signedIn: MainTabs()
            }
        }
        .animation(.default, value: model.phase)
        .task { await model.load() }
        .onChange(of: scenePhase) { _, now in
            if now == .active, model.phase != .loading { Task { await model.load() } }
        }
        .onContinueUserActivity(NSUserActivityTypeBrowsingWeb) { activity in
            if let url = activity.webpageURL {
                Task { if let result = await model.openLink(url) { linkResult = result } }
            }
        }
        .alert("Hunt", isPresented: Binding(get: { linkResult != nil }, set: { if !$0 { linkResult = nil } })) {
            Button("OK", role: .cancel) {}
        } message: { Text(linkResult ?? "") }
    }
}

struct MainTabs: View {
    var body: some View {
        TabView {
            NavigationStack { MeView() }
                .tabItem { Label("Me", systemImage: "person.crop.circle.fill") }
            NavigationStack { LeaderboardView() }
                .tabItem { Label("Leaderboard", systemImage: "trophy.fill") }
            NavigationStack { BadgesView() }
                .tabItem { Label("Badges", systemImage: "rosette") }
        }
    }
}

/// Shared by every tab: refresh on pull, the no-signal note, and the account menu.
struct PageChrome: ViewModifier {
    @Environment(Student.self) private var model

    func body(content: Content) -> some View {
        content
            .refreshable { await model.load() }
            .safeAreaInset(edge: .top) {
                if model.offline {
                    Label("No signal. Showing what you saw last.", systemImage: "wifi.slash")
                        .font(.footnote).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity).padding(.vertical, 6).background(.bar)
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Text(model.page.name.string)
                        Button("Sign out", systemImage: "rectangle.portrait.and.arrow.right", role: .destructive) {
                            Task { await model.signOut() }
                        }
                    } label: {
                        Image(systemName: initialSymbol(model.page.first.string)).font(.title2)
                            .accessibilityLabel("Account")
                    }
                }
            }
    }
}

/// "n.circle.fill" for Nora; the person icon for a name that doesn't start with a letter.
func initialSymbol(_ name: String) -> String {
    guard let c = name.lowercased().first, c.isASCII, c.isLetter else { return "person.crop.circle.fill" }
    return "\(c).circle.fill"
}

extension View {
    func pageChrome() -> some View { modifier(PageChrome()) }
}

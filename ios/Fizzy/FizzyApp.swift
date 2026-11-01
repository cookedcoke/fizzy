import SwiftUI

@main
struct FizzyApp: App {
    private let model = AppModel.shared
    private let queue = OfflineQueue.shared   // starts watching for signal to send what's waiting

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .tint(Color("Brand"))
        }
    }
}

/// Someone has to type the passcode before a change can go through. The change waits for it.
final class PasscodeAsk: Identifiable {
    let id = UUID()
    let need: String   // "pin" or "owner"
    private var continuation: CheckedContinuation<Bool, Never>?
    init(need: String, continuation: CheckedContinuation<Bool, Never>) { self.need = need; self.continuation = continuation }
    func finish(_ ok: Bool) { continuation?.resume(returning: ok); continuation = nil }
}

@Observable
@MainActor
final class AppModel {
    static let shared = AppModel()

    enum Phase { case loading, signedOut, signedIn }

    var phase = Phase.loading
    var staff = ""
    var staffNames: [String] = []
    var problem: String?
    var passcodeAsk: PasscodeAsk?
    var sheetDepth = 0                 // sheets up right now: the passcode prompt shows from the top one
    private var alsoWaiting: [CheckedContinuation<Bool, Never>] = []

    /// Works out whether the saved cookies still sign us in.
    func start() async {
        problem = nil
        do {
            let me = try await API.shared.me()
            staff = me.staff.string
            staffNames = me.staff_names.strings
            phase = .signedIn
            UserDefaults.standard.set(staff, forKey: "signedInAs")
            UserDefaults.standard.set(staffNames, forKey: "staffNames")
        } catch where isOffline(error) && UserDefaults.standard.string(forKey: "signedInAs") != nil {
            // No signal (a shop, say): carry on as whoever was signed in. Pages open from their saved
            // copies and restocks wait on the phone; the server still checks the sign-in when they're sent.
            staff = UserDefaults.standard.string(forKey: "signedInAs") ?? ""
            staffNames = UserDefaults.standard.stringArray(forKey: "staffNames") ?? [staff]
            phase = .signedIn
        } catch {
            phase = .signedOut
            if case APIError.signedOut = error { PageCache.clear() } else { problem = error.localizedDescription }
            staffNames = (try? await API.shared.staffNames()) ?? staffNames
        }
    }

    func signIn(who: String, password: String) async throws {
        try await API.shared.signIn(who: who, password: password)
        await start()
    }

    func signOut() async {
        AskLive.shared.reset()
        await API.shared.signOut()
        PageCache.clear()
        UserDefaults.standard.removeObject(forKey: "signedInAs")
        staff = ""
        phase = .signedOut
    }

    /// Someone else on the counter. Only once Fizzy knows: what's recorded is whoever its session says.
    /// Returns what went wrong, if it couldn't.
    func switchTo(_ who: String) async -> String? {
        guard who != staff else { return nil }
        do {
            _ = try await call { try await API.shared.post("switch", [("who", who)]) }
        } catch {
            return isOffline(error) ? "No signal, so Fizzy can't be told who's on the counter. Try again when there is." : error.localizedDescription
        }
        AskLive.shared.reset()
        staff = who
        UserDefaults.standard.set(who, forKey: "signedInAs")
        return nil
    }

    /// Runs a request. Signed out sends us to sign in; "enter the passcode first" asks for it and
    /// tries again, like the website does.
    func call<T>(_ op: () async throws -> T) async throws -> T {
        while true {
            do {
                return try await op()
            } catch APIError.passcode(let need) {
                if !(await askPasscode(need)) { throw APIError.cancelled }
            } catch APIError.signedOut {
                phase = .signedOut
                PageCache.clear()
                UserDefaults.standard.removeObject(forKey: "signedInAs")
                throw APIError.signedOut
            }
        }
    }

    private func askPasscode(_ need: String) async -> Bool {
        if passcodeAsk != nil {   // a second change needing it while the prompt is up: one answer does for both
            return await withCheckedContinuation { alsoWaiting.append($0) }
        }
        let ok = await withCheckedContinuation { continuation in
            passcodeAsk = PasscodeAsk(need: need, continuation: continuation)
        }
        let others = alsoWaiting
        alsoWaiting = []
        for c in others { c.resume(returning: ok) }
        return ok
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Group {
            switch model.phase {
            case .loading: ProgressView().task { await model.start() }
            case .signedOut: LoginView()
            case .signedIn: MainTabs()
            }
        }
        .animation(.default, value: model.phase)
        // The passcode prompt, unless a sheet is up: only one sheet per presenter, so the sheet hosts it then.
        .sheet(item: Binding(get: { model.sheetDepth == 0 ? model.passcodeAsk : nil }, set: { if $0 == nil { model.passcodeAsk = nil } })) { ask in
            PasscodeSheet(ask: ask)
        }
    }
}

struct CustomerID: Hashable { let id: Int }

struct MainTabs: View {
    var body: some View {
        TabView {
            NavigationStack { DashboardView().fizzyDestinations() }
                .tabItem { Label("Today", systemImage: "chart.bar.fill") }
            NavigationStack { CustomersView().fizzyDestinations() }
                .tabItem { Label("Customers", systemImage: "person.2.fill") }
            NavigationStack { TransactionsView().fizzyDestinations() }
                .tabItem { Label("Transactions", systemImage: "list.bullet.rectangle") }
            NavigationStack { MoreView().fizzyDestinations() }
                .tabItem { Label("More", systemImage: "ellipsis.circle") }
        }
    }
}

extension View {
    /// Every screen can link to a customer.
    func fizzyDestinations() -> some View {
        navigationDestination(for: CustomerID.self) { CustomerView(id: $0.id) }
    }
}

/// The rest of the website's pages.
struct MoreView: View {
    var body: some View {
        List {
            Section {
                link("Square", "creditcard") { SquareView() }
                link("Rewards", "gift") { RewardsView() }
                link("Profit", "chart.line.uptrend.xyaxis") { ProfitView() }
                link("Money", "banknote") { MoneyView() }
            }
            Section {
                link("Tools", "wrench.and.screwdriver") { ToolsView() }
                link("Ask", "sparkles") { AskView() }
                link("Import", "square.and.arrow.down") { ImportView() }
            }
            Section {
                link("Settings", "gearshape") { ServerSettingsView() }
                link("Activity log", "clock.arrow.circlepath") { LogView() }
                link("This app", "iphone") { AppSettingsView() }
            }
        }
        .navigationTitle("More")
        .toolbar { ToolbarItem(placement: .topBarLeading) { StaffMenu() } }
    }

    private func link<D: View>(_ title: String, _ icon: String, @ViewBuilder _ destination: @escaping () -> D) -> some View {
        NavigationLink { destination() } label: { Label(title, systemImage: icon) }
    }
}

/// Who's on the counter: switch to someone else, or sign out ("Lock" on the website).
struct StaffMenu: View {
    @Environment(AppModel.self) private var model

    /// "w.circle.fill" for Will. Falls back to the person icon for a name that doesn't start with a letter.
    private var initialSymbol: String {
        guard let c = model.staff.lowercased().first, c.isASCII, c.isLetter else { return "person.crop.circle.fill" }
        return "\(c).circle.fill"
    }
    @State private var problem: String?

    var body: some View {
        Menu {
            Section("On the counter") {
                ForEach(model.staffNames, id: \.self) { name in
                    Button { Task { problem = await model.switchTo(name) } } label: {
                        if name == model.staff { Label(name, systemImage: "checkmark") } else { Text(name) }
                    }
                }
            }
            Button("Lock", systemImage: "lock", role: .destructive) { Task { await model.signOut() } }
        } label: {
            // Everyone on the counter has a different initial, so it says who's signed in at a glance.
            // A symbol, not text, so the toolbar keeps the button round rather than a pill.
            Image(systemName: initialSymbol)
                .font(.title2)
                .accessibilityLabel("On the counter: \(model.staff)")
        }
        .alert("Couldn't switch", isPresented: Binding(get: { problem != nil }, set: { if !$0 { problem = nil } })) {
            Button("OK", role: .cancel) {}
        } message: { Text(problem ?? "") }
    }
}

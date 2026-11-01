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
            if case APIError.signedOut = error {} else { problem = error.localizedDescription }
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

    func switchTo(_ who: String) async {
        AskLive.shared.reset()
        _ = try? await API.shared.post("switch", [("who", who)])
        staff = who
    }

    /// Runs a request. Signed out sends us to sign in; "enter the passcode first" asks for it and
    /// tries again, like the website does.
    func call<T>(_ op: () async throws -> T) async throws -> T {
        while true {
            do {
                return try await op()
            } catch APIError.passcode(let need) {
                if !(await askPasscode(need)) { throw APIError.message("Cancelled. Nothing was changed.") }
            } catch APIError.signedOut {
                phase = .signedOut
                UserDefaults.standard.removeObject(forKey: "signedInAs")
                throw APIError.signedOut
            }
        }
    }

    private func askPasscode(_ need: String) async -> Bool {
        await withCheckedContinuation { continuation in
            passcodeAsk = PasscodeAsk(need: need, continuation: continuation)
        }
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
        .sheet(item: $model.passcodeAsk, onDismiss: nil) { ask in
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

    var body: some View {
        Menu {
            Section("On the counter") {
                ForEach(model.staffNames, id: \.self) { name in
                    Button { Task { await model.switchTo(name) } } label: {
                        if name == model.staff { Label(name, systemImage: "checkmark") } else { Text(name) }
                    }
                }
            }
            Button("Lock", systemImage: "lock", role: .destructive) { Task { await model.signOut() } }
        } label: {
            Label(model.staff, systemImage: "person.crop.circle").labelStyle(.titleAndIcon)
        }
    }
}

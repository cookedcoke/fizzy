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
                .readableWidth()
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
            NavigationStack { AskView().fizzyDestinations() }
                .tabItem { Label("Fizz", systemImage: "sparkles") }
            NavigationStack { SalesView().fizzyDestinations() }
                .tabItem { Label("Sales", systemImage: "list.bullet.rectangle") }
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

/// Sales, as on the website: Transactions and Square as two halves of one page.
struct SalesView: View {
    enum Half: String, CaseIterable { case transactions = "Transactions", square = "Square" }
    @State var half: Half = .transactions

    var body: some View {
        Group {
            switch half {
            case .transactions: TransactionsView()
            case .square: SquareView()
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            Picker("Sales", selection: $half) { ForEach(Half.allCases, id: \.self) { Text($0.rawValue) } }
                .pickerStyle(.segmented).padding(.horizontal, 20).padding(.bottom, 8).background(.bar)   // the list's own margin
        }
    }
}

/// Money, as on the website: Money (restocks, payouts) and Profit as two halves of one page.
struct MoneyHub: View {
    enum Half: String, CaseIterable { case money = "Money", profit = "Profit" }
    @State var half: Half = .money

    var body: some View {
        Group {
            switch half {
            case .money: MoneyView()
            case .profit: ProfitView()
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            Picker("Money", selection: $half) { ForEach(Half.allCases, id: \.self) { Text($0.rawValue) } }
                .pickerStyle(.segmented).padding(.horizontal, 20).padding(.bottom, 8).background(.bar)   // the list's own margin
        }
    }
}

/// Where the More menu goes. Links here are values, like the customer links further in: a stack
/// that mixes value links with closure links can show the same page again instead of the new one.
enum MoreDestination: Hashable {
    case tabs, rewards, money, display, settings, log, tools, thisApp
}

/// The website's other pages: Tabs, Rewards and Money from its header, then its More menu.
struct MoreView: View {
    var body: some View {
        List {
            Section("The stall") {
                link("Tabs", "Who owes, who to chase, tab rules", "creditcard.fill", .blue, .tabs)
                link("Rewards", "Points rewards, events and hunts", "gift.fill", .purple, .rewards)
                link("Money", "Restocks, payouts and profit", "banknote.fill", .green, .money)
            }
            Section("Running it") {
                link("Customer display", "What the counter screen shows", "tv.fill", .indigo, .display)
                link("Settings", "Updates, backups, Fizz and devices", "gearshape.fill", .gray, .settings)
                link("Activity log", "Everything staff have changed", "clock.arrow.circlepath", .orange, .log)
                link("Tools", "Fix-ups, and where everything lives", "wrench.and.screwdriver.fill", .teal, .tools)
            }
            Section {
                link("This app", "Fizzy's address and this device", "iphone", Color("Brand"), .thisApp)
            }
        }
        .navigationTitle("More")
        .toolbar { ToolbarItem(placement: .topBarLeading) { StaffMenu() } }
        .navigationDestination(for: MoreDestination.self) { d in
            switch d {
            case .tabs: TabsView()
            case .rewards: RewardsView()
            case .money: MoneyHub()
            case .display: DisplaySettingsView()
            case .settings: ServerSettingsView()
            case .log: LogView()
            case .tools: ToolsView()
            case .thisApp: AppSettingsView()
            }
        }
    }

    /// A coloured icon tile, the name and a line on what's in there, as the Settings app does it.
    private func link(_ title: String, _ about: String, _ icon: String, _ color: Color, _ to: MoreDestination) -> some View {
        NavigationLink(value: to) {
            HStack(spacing: 14) {
                Image(systemName: icon)
                    .font(.system(size: 16, weight: .semibold)).foregroundStyle(.white)
                    .frame(width: 34, height: 34)
                    .background(color.gradient, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                    Text(about).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            .padding(.vertical, 2)
        }
    }
}

/// A website address as the screen that shows it (for Tools' list of where everything lives).
enum Place {
    case dashboard, fizz, customers, transactions, square, tabs, rewards, money, profit, display, settings, log

    init?(href: String) {
        let path = String(href.split(separator: "#").first ?? "").split(separator: "?").first.map(String.init) ?? ""
        switch (path, href.contains("#ask")) {
        case (_, true): self = .fizz
        case ("/", _), ("", _): self = .dashboard
        case ("/customers", _): self = .customers
        case ("/transactions", _), ("/sales", _): self = .transactions
        case ("/square", _): self = .square
        case ("/tabs", _): self = .tabs
        case ("/rewards", _): self = .rewards
        case ("/money", _): self = .money
        case ("/profit", _): self = .profit
        case ("/display/settings", _): self = .display
        case ("/settings", _): self = .settings
        case ("/audit", _): self = .log
        default: return nil
        }
    }

    @ViewBuilder var view: some View {
        switch self {
        case .dashboard: DashboardView()
        case .fizz: AskView()
        case .customers: CustomersView()
        case .transactions: SalesView(half: .transactions)
        case .square: SalesView(half: .square)
        case .tabs: TabsView()
        case .rewards: RewardsView()
        case .money: MoneyHub(half: .money)
        case .profit: MoneyHub(half: .profit)
        case .display: DisplaySettingsView()
        case .settings: ServerSettingsView()
        case .log: LogView()
        }
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

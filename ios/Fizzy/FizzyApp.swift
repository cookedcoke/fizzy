import SwiftUI

@main
struct FizzyApp: App {
    @State private var model = AppModel()

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
final class AppModel {
    enum Phase { case loading, signedOut, signedIn }

    var phase = Phase.loading
    var staff = ""
    var staffNames: [String] = []
    var problem: String?

    /// Works out whether the saved cookies still sign us in.
    func start() async {
        problem = nil
        do {
            let me = try await API.shared.me()
            staff = me.staff
            staffNames = me.staff_names
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
        await API.shared.signOut()
        staff = ""
        phase = .signedOut
    }

    func switchTo(_ who: String) async {
        do {
            try await API.shared.post("switch", ["who": who])
            staff = who
        } catch {
            handle(error)
        }
    }

    /// Anything that turns out to be "signed out" sends us back to sign in.
    func handle(_ error: Error) {
        if case APIError.signedOut = error { phase = .signedOut }
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Group {
            switch model.phase {
            case .loading: ProgressView().task { await model.start() }
            case .signedOut: LoginView()
            case .signedIn: HomeView()
            }
        }
        .animation(.default, value: model.phase)
    }
}

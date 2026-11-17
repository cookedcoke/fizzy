import SwiftUI

struct SignInView: View {
    @Environment(Student.self) private var model
    @State private var who = UserDefaults.standard.string(forKey: "lastWho") ?? ""
    @State private var passcode = ""
    @State private var remember = true
    @State private var busy = false
    @State private var problem: String?
    @State private var scanning = false
    @State private var settings = false
    @State private var landed = false
    @State private var tries = 0
    private var ready: Bool { !who.trimmingCharacters(in: .whitespaces).isEmpty && !passcode.isEmpty }
    @FocusState private var field: Field?

    enum Field { case who, passcode }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(spacing: 10) {
                        Image("Logo").resizable().scaledToFit().frame(width: 72, height: 72)
                            .scaleEffect(landed ? 1 : 0.5).rotationEffect(.degrees(landed ? 0 : -14)).opacity(landed ? 1 : 0)
                            .onAppear { withAnimation(.spring(duration: 0.7, bounce: 0.45)) { landed = true } }
                        Text("My CCC").font(.largeTitle.bold())
                        Text("Your Cooked Coke Co. points, tab and badges.").foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }
                    .frame(maxWidth: .infinity)
                    .listRowBackground(Color.clear)
                }
                Section {
                    TextField("Name or student number", text: $who)
                        .textContentType(.username).autocorrectionDisabled().textInputAutocapitalization(.words)
                        .focused($field, equals: .who).submitLabel(.next).onSubmit { field = .passcode }
                    SecureField("Passcode", text: $passcode)
                        .textContentType(.password).focused($field, equals: .passcode).submitLabel(.go)
                        .onSubmit { Task { await signIn() } }
                    Toggle("Stay signed in", isOn: $remember)
                } footer: {
                    Text("No passcode yet? Ask at the Cooked Coke Co. stall.")
                }
                if let problem {
                    Section { Label(problem, systemImage: "exclamationmark.circle.fill").foregroundStyle(.red) }
                        .keyframeAnimator(initialValue: 0.0, trigger: tries) { v, x in v.offset(x: x) } keyframes: { _ in
                            KeyframeTrack { SpringKeyframe(10, duration: 0.07); SpringKeyframe(-8, duration: 0.07); SpringKeyframe(5, duration: 0.07); SpringKeyframe(0, duration: 0.1) }
                        }
                }
                Section {
                    Button {
                        Task { await signIn() }
                    } label: {
                        HStack { Spacer(); if busy { ProgressView().tint(.white) } else { Text("Let me in").font(.headline) }; Spacer() }
                            .padding(.vertical, 14)
                            .foregroundStyle(.white)
                            .background { Shimmer(base: Color("Brand")) }
                            .clipShape(Capsule())
                            .shadow(color: Color("Brand").opacity(0.35), radius: 10, y: 5)
                    }
                    .buttonStyle(.plain)
                    .opacity(ready ? 1 : 0.55)
                    .disabled(busy || !ready)
                    .listRowBackground(Color.clear).listRowInsets(EdgeInsets())
                }
                Section {
                    Button {
                        scanning = true
                    } label: {
                        Label("Scan a sign-in code", systemImage: "qrcode.viewfinder")
                    }
                } footer: {
                    Text("Staff can show a sign-in code at the counter. It works once, for a minute.")
                }
                WhatsOnSection(page: model.whatsOn, showRewards: true)
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Server", systemImage: "gearshape") { settings = true }
                }
            }
            .sheet(isPresented: $scanning) {
                CodeScanner(title: "Scan the code", read: CodeScanner.signInToken) { token in scanning = false; Task { await useCode(token) } }
            }
            .sheet(isPresented: $settings) { ServerSheet() }
        }
    }

    private func signIn() async {
        let name = who.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty, !passcode.isEmpty, !busy else { return }
        busy = true
        problem = nil
        defer { busy = false }
        do {
            try await model.signIn(who: name, passcode: passcode, remember: remember)
            UserDefaults.standard.set(name, forKey: "lastWho")
            passcode = ""
        } catch {
            problem = isOffline(error) ? "No signal. Try again when there is." : error.localizedDescription
            tries += 1
        }
    }

    private func useCode(_ token: String) async {
        busy = true
        problem = nil
        defer { busy = false }
        do { try await model.useCode(token) } catch {
            problem = isOffline(error) ? "No signal. Try again when there is." : error.localizedDescription
            tries += 1
        }
    }
}

/// Double points days and rewards that are on: shown before signing in, and on the Me tab.
struct WhatsOnSection: View {
    let page: J
    var showRewards = false

    var body: some View {
        let on = page.events_on.array, soon = page.events_soon.array, custom = page.custom_events.array
        let rewards = showRewards ? page.rewards.array : []
        if !on.isEmpty || !soon.isEmpty || !custom.isEmpty || !rewards.isEmpty {
            Section("What's on") {
                if !on.isEmpty {
                    ForEach(on, id: \.self) { e in
                        Label {
                            Text("**\(e.name.string)** is on \(e.start_date.string == e.end_date.string ? "today" : "until \(Fmt.when(e.end_date.string, "EEE d MMM"))") - \(Fmt.g(e.multiplier.double))× points on everything.")
                        } icon: { Image(systemName: "bolt.fill").foregroundStyle(.orange) }
                    }
                } else if let e = soon.first {
                    Label {
                        Text("Coming up: **\(e.name.string)** on \(Fmt.when(e.start_date.string, "EEEE d MMM")) - \(Fmt.g(e.multiplier.double))× points.")
                    } icon: { Image(systemName: "calendar") }
                }
                ForEach(custom, id: \.self) { e in
                    Label {
                        Text((e.soon.bool ? "From \(Fmt.when(e.start.string, "EEE d MMM")): " : "") + "**\(e.name.string)** - \(e.summary.string)")
                    } icon: { Image(systemName: e.soon.bool ? "calendar" : "bolt.fill").foregroundStyle(e.soon.bool ? Color.secondary : .orange) }
                }
                ForEach(rewards, id: \.self) { r in
                    Label {
                        Text("**\(r.name.string)** for \(Fmt.number(r.points_cost.int)) points")
                    } icon: { Image(systemName: "gift.fill").foregroundStyle(.pink) }
                }
            }
        }
    }
}

/// Which server the app talks to. Only for testing; students never need it.
struct ServerSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(Student.self) private var model
    @State private var text = UserDefaults.standard.string(forKey: "server") ?? ""
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField(StudentAPI.defaultServer, text: $text)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                } header: {
                    Text("Server")
                } footer: {
                    Text(problem ?? "Leave blank for \(StudentAPI.defaultServer). A test server's student site is its address with /me on the end.")
                        .foregroundStyle(problem == nil ? Color.secondary : .red)
                }
            }
            .navigationTitle("Server").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        if text.trimmingCharacters(in: .whitespaces).isEmpty {
                            UserDefaults.standard.removeObject(forKey: "server")
                        } else if let s = StudentAPI.normalise(text) {
                            UserDefaults.standard.set(s, forKey: "server")
                        } else {
                            problem = "That isn't a web address."
                            return
                        }
                        SavedSignIn.clear()
                        dismiss()
                        Task { await model.load() }
                    }
                }
            }
        }
    }
}

import SwiftUI

struct LoginView: View {
    @Environment(AppModel.self) private var model
    @AppStorage("lastStaff") private var who = ""
    @State private var password = ""
    @State private var error: String?
    @State private var busy = false
    @State private var showSettings = false
    @FocusState private var passwordFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(spacing: 6) {
                        Image("Logo").resizable().scaledToFit().frame(width: 72, height: 72)
                        Text("Fizzy").font(.title.bold())
                        Text("Cooked Coke Co. · staff only").font(.subheadline).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                    .listRowBackground(Color.clear)
                }

                if let message = error ?? model.problem {
                    Section { Label(message, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                }

                Section {
                    Picker("Who's on the counter", selection: $who) {
                        ForEach(model.staffNames, id: \.self) { Text($0).tag($0) }
                    }
                    SecureField("Password", text: $password)
                        .textContentType(.password)
                        .focused($passwordFocused)
                        .submitLabel(.go)
                        .onSubmit(signIn)
                }

                Section {
                    Button(action: signIn) {
                        HStack { Spacer(); if busy { ProgressView() } else { Text("Sign in").bold() }; Spacer() }
                    }
                    .disabled(busy || password.isEmpty || who.isEmpty)
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Settings", systemImage: "gear") { showSettings = true }
                }
            }
            .sheet(isPresented: $showSettings, onDismiss: { Task { await model.start() } }) { SettingsView() }
            .refreshable { await model.start() }
            .onAppear {
                if !model.staffNames.contains(who) { who = model.staffNames.first ?? "" }
                passwordFocused = true
            }
            .onChange(of: model.staffNames) { if !model.staffNames.contains(who) { who = model.staffNames.first ?? "" } }
        }
    }

    private func signIn() {
        guard !password.isEmpty, !who.isEmpty, !busy else { return }
        busy = true
        error = nil
        Task {
            do {
                try await model.signIn(who: who, password: password)
                password = ""
            } catch {
                self.error = error.localizedDescription
            }
            busy = false
        }
    }
}

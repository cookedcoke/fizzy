import SwiftUI

/// Ask (Gemini): questions about the stall's data, and changes it proposes that staff can accept.
struct AskView: View {
    @State private var page = Page("ask")
    @State private var text = ""
    @State private var sending = false
    @State private var sentText: String?       // the question being answered, until it's saved
    @State private var answer = ""
    @State private var status: String?
    @State private var error: String?
    @FocusState private var focused: Bool

    var body: some View {
        let d = page.data
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if let flash = page.flash { FlashRow(flash: flash) }
                    if let e = page.error { Label(e, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                    if page.loaded && !d.enabled.bool {
                        Label("Ask is off. Put the Gemini API key in Settings to turn it on.", systemImage: "sparkles").foregroundStyle(.orange)
                    }
                    if page.loaded && d.timeline.array.isEmpty && sentText == nil && d.enabled.bool {
                        Text("Try asking:").font(.footnote).foregroundStyle(.secondary)
                        ForEach(d.examples.strings, id: \.self) { ex in
                            Button { text = ex; focused = true } label: {
                                Text(ex).font(.subheadline).multilineTextAlignment(.leading).padding(10)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    ForEach(Array(d.timeline.array.enumerated()), id: \.offset) { _, t in item(t) }
                    if let sentText { bubble(sentText, mine: true) }
                    if sending {
                        if !answer.isEmpty { bubble(answer, mine: false) }
                        else { HStack { ProgressView(); Text(status ?? "Thinking…").font(.footnote).foregroundStyle(.secondary) } }
                    }
                    if let error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                    Color.clear.frame(height: 1).id("end")
                }
                .padding()
            }
            .onChange(of: answer) { proxy.scrollTo("end") }
            .onChange(of: page.data) { proxy.scrollTo("end") }
            .onChange(of: sentText) { proxy.scrollTo("end") }
        }
        .safeAreaInset(edge: .bottom) { composer(d) }
        .navigationTitle("Ask")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if !d.timeline.array.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    ConfirmButton(title: "New chat", message: "Clear this chat?", confirmLabel: "Clear") { page.act("ask/clear") }
                }
            }
        }
        .refreshable { await page.load() }
        .task { await page.load() }
    }

    @ViewBuilder private func item(_ t: J) -> some View {
        if t.kind.string == "msg" {
            bubble(t.text.string, mine: t.role.string == "user")
        } else {
            let p = t.p
            VStack(alignment: .leading, spacing: 8) {
                Tag(text: ["create_rule": "New badge or event", "update_rule": "Change a rule", "pause_rule": "Pause a rule", "resume_rule": "Restart a rule",
                           "delete_rule": "Delete a rule", "double_points": "Double points", "reward": "New reward", "display_message": "Display message"][p.action.string] ?? p.action.string,
                    color: p.status.string == "done" ? .green : .secondary)
                Text(p.summary.string).font(.subheadline.bold())
                if p.status.string == "pending" {
                    HStack {
                        Button("Do it") { page.act("ask/apply/\(p.id.int)") }.buttonStyle(.borderedProminent)
                        Button("No thanks") { page.act("ask/dismiss/\(p.id.int)") }.buttonStyle(.bordered)
                    }
                } else {
                    Text((p.status.string == "done" ? "Done" : "Not done") + (p.done_at.optString.map { " · \(Fmt.when($0))" } ?? "")).font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
        }
    }

    private func bubble(_ s: String, mine: Bool) -> some View {
        let md = (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(s)
        return Text(md)
            .textSelection(.enabled)
            .padding(10)
            .foregroundStyle(mine ? Color.white : Color.primary)
            .background(mine ? Color("Brand") : Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
            .frame(maxWidth: .infinity, alignment: mine ? .trailing : .leading)
    }

    @ViewBuilder private func composer(_ d: J) -> some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Ask a question…", text: $text, axis: .vertical)
                .lineLimit(1...5)
                .focused($focused)
                .padding(10)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 18))
            Button(action: send) {
                Image(systemName: "arrow.up.circle.fill").font(.system(size: 34))
            }
            .disabled(sending || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !d.enabled.bool)
        }
        .padding(.horizontal).padding(.vertical, 8)
        .background(.bar)
    }

    /// The answer arrives as it's written, as server-sent events.
    private func send() {
        let question = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty else { return }
        text = ""
        sentText = question
        answer = ""
        status = nil
        error = nil
        sending = true
        Task {
            do {
                try await AppModel.shared.call {
                    for try await event in API.shared.stream("ask/stream", [("text", question)]) {
                        switch event.t.string {
                        case "delta": answer += event.text.string; status = nil
                        case "status": status = event.text.string
                        case "reset": answer = ""
                        case "error": throw APIError.message(event.text.string)
                        default: break
                        }
                    }
                }
            } catch {
                self.error = error.localizedDescription
            }
            await page.load()   // the saved conversation, with any change it proposes
            sentText = nil
            answer = ""
            sending = false
        }
    }
}

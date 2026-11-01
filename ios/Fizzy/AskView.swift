import SwiftUI

/// The answer being written. It lives outside the screen, so leaving the page doesn't stop it, and the
/// server writes it in the background anyway: when the connection drops, or the page is opened again
/// later, the app joins the answer where it's up to (GET ask/stream replays it from the start).
@Observable @MainActor
final class AskLive {
    static let shared = AskLive()

    var question: String?
    var answer = ""
    var status: String?
    var error: String?
    var active = false
    var lost = false            // the question never reached Fizzy: it goes back in the box
    var finished = 0            // goes up when an answer ends, so the screen reloads the saved chat
    private var task: Task<Void, Never>?

    func send(_ question: String) {
        begin(question)
        task = Task { await run(asking: question) }
    }

    /// Joins a question Fizzy is still answering: asked before the page was left, or on the website.
    func join(_ pending: J) {
        guard !active else { return }
        begin(pending.text.string)
        answer = pending.answer.string
        status = pending.status.optString
        task = Task { await run(asking: nil) }
    }

    private func begin(_ q: String) {
        task?.cancel()
        question = q
        answer = ""
        status = nil
        error = nil
        lost = false
        active = true
    }

    private func run(asking: String?) async {
        var ask = asking             // the first attempt asks; every later one joins what's being written
        var received = false         // any of the answer seen, in this or an earlier attempt
        var tries = 0
        while !Task.isCancelled {
            var fresh = ask == nil   // a join replays from the start
            do {
                try await AppModel.shared.call {
                    let events = ask.map { API.shared.stream("ask/stream", [("text", $0)]) }
                        ?? API.shared.stream("ask/stream", method: "GET")
                    for try await event in events {
                        if fresh { answer = ""; status = nil; fresh = false }
                        switch event.t.string {
                        case "delta": answer += event.text.string; status = nil; received = true
                        case "status": status = event.text.string; received = true
                        case "reset": answer = ""; received = true
                        case "error": throw APIError.message(event.text.string)
                        case "none":
                            if !received {
                                lost = true
                                throw APIError.message("Couldn't reach Fizzy. Check the connection and try again.")
                            }
                        default: break
                        }
                    }
                }
                break
            } catch APIError.message(let message) {
                self.error = message
                break
            } catch APIError.signedOut {
                break
            } catch is CancellationError {
                return
            } catch {
                // The connection dropped. Fizzy is still writing: join again in a moment.
                ask = nil
                tries += 1
                if tries > 12 {
                    self.error = "Lost the connection. The answer will be here once it's done: pull down to refresh."
                    break
                }
                try? await Task.sleep(for: .seconds(min(10, 2 * tries)))
            }
        }
        active = false
        finished += 1
    }
}

/// Ask (Gemini): questions about the stall's data, and changes it proposes that staff can accept.
struct AskView: View {
    @State private var page = Page("ask")
    @State private var text = ""
    @FocusState private var focused: Bool
    private var live: AskLive { .shared }

    var body: some View {
        let d = page.data
        let timeline = d.timeline.array
        // The question is saved the moment it's asked, so after a reload it's already in the chat.
        let questionSaved = timeline.last.map { $0.kind.string == "msg" && $0.role.string == "user" && $0.text.string == live.question } ?? false
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if let flash = page.flash { FlashRow(flash: flash) }
                    if let e = page.error { Label(e, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                    if page.loaded && !d.enabled.bool {
                        Label("Ask is off. Put the Gemini API key in Settings to turn it on.", systemImage: "sparkles").foregroundStyle(.orange)
                    }
                    if page.loaded && timeline.isEmpty && !live.active && d.enabled.bool {
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
                    ForEach(Array(timeline.enumerated()), id: \.offset) { _, t in item(t) }
                    if let q = live.question, !questionSaved, live.active || live.error != nil { bubble(q, mine: true) }
                    if live.active {
                        if !live.answer.isEmpty { bubble(live.answer, mine: false) }
                        else {
                            HStack(spacing: 8) { ProgressView(); Text(live.status ?? "Thinking…").font(.footnote).foregroundStyle(.secondary) }
                                .padding(.horizontal, 12).padding(.vertical, 9)
                                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
                        }
                    }
                    if let error = live.error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                    Color.clear.frame(height: 1).id("end")
                }
                .padding()
            }
            .onChange(of: live.answer) { proxy.scrollTo("end") }
            .onChange(of: live.status) { proxy.scrollTo("end") }
            .onChange(of: live.active) { proxy.scrollTo("end") }
            .onChange(of: page.data) { proxy.scrollTo("end"); joinIfPending() }
        }
        .safeAreaInset(edge: .bottom) { composer(d) }
        .navigationTitle("Ask")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if !timeline.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    ConfirmButton(title: "New chat", message: "Clear this chat?", confirmLabel: "Clear") { page.act("ask/clear") }
                        .disabled(live.active)
                }
            }
        }
        .refreshable { await page.load() }
        .task {
            await page.load()
            settle()
        }
        .onChange(of: live.finished) {
            settle()
            Task { await page.load() }   // the saved conversation, with any change it proposes
        }
    }

    /// An answer still being written when the page opens (asked here earlier, or on the website) is joined.
    private func joinIfPending() {
        let p = page.data.pending
        if !p.isNull, !live.active { live.join(p) }
    }

    /// A question that never reached Fizzy goes back in the box to send again.
    private func settle() {
        if live.lost, let q = live.question {
            if text.isEmpty { text = q }
            live.question = nil
            live.lost = false
        }
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

    /// What was asked, as typed; what Fizzy said, with its markdown drawn (tables, lists, headings, code).
    @ViewBuilder private func bubble(_ s: String, mine: Bool) -> some View {
        Group {
            if mine { Text(s).textSelection(.enabled) } else { MarkdownView(MD.stripEmoji(s)) }
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
        .foregroundStyle(mine ? Color.white : Color.primary)
        .background(mine ? Color("Brand") : Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
        .frame(maxWidth: .infinity, alignment: mine ? .trailing : .leading)
    }

    @ViewBuilder private func composer(_ d: J) -> some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField(live.active ? "Fizzy is answering…" : "Ask a question…", text: $text, axis: .vertical)
                .lineLimit(1...5)
                .focused($focused)
                .padding(10)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 18))
            Button(action: send) {
                Image(systemName: "arrow.up.circle.fill").font(.system(size: 34))
            }
            .accessibilityLabel("Send")
            .disabled(live.active || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !d.enabled.bool)
        }
        .padding(.horizontal).padding(.vertical, 8)
        .background(.bar)
    }

    private func send() {
        let question = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, !live.active else { return }
        text = ""
        live.send(question)
    }
}

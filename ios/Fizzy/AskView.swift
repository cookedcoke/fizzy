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
    var accepted = false        // Fizzy has the question (the stream's "start" event): it's saved, and being answered
    var lost = false            // it never got there: back in the box
    var finished = 0            // goes up when an answer ends, so the screen reloads the saved chat
    var changed = 0             // goes up when the saved chat changed mid-answer (the question saved, a part finished)
    var settled = ""            // a part Fizzy finished before looking things up: saved, shown until the reload brings it
    private var jobID: String?
    private var nonce = ""      // this device's tag for the question, echoed in the stream's "start" event
    private var task: Task<Void, Never>?

    func send(_ question: String) {
        begin(question)
        nonce = UUID().uuidString
        task = Task { await run(asking: question) }
    }

    /// Joins a question Fizzy is still answering: asked before the page was left, or on the website.
    func join(_ pending: J) {
        guard !active else { return }
        begin(pending.text.string)
        accepted = true
        jobID = pending.id.optString ?? "?"
        answer = pending.answer.string
        status = pending.status.optString
        task = Task { await run(asking: nil) }
    }

    /// Signing out or switching person: whatever was being followed was theirs.
    func reset() {
        task?.cancel()
        task = nil
        question = nil
        answer = ""
        status = nil
        error = nil
        settled = ""
        active = false
        accepted = false
        lost = false
        jobID = nil
    }

    /// The screen has the saved chat after an answer ended: the live copy can go. The error stays until the next question.
    func shown() {
        guard !active else { return }
        question = nil
        answer = ""
        status = nil
        settled = ""
        lost = false
    }

    private func begin(_ q: String) {
        task?.cancel()
        question = q
        answer = ""
        status = nil
        error = nil
        settled = ""
        active = true
        accepted = false
        lost = false
        jobID = nil
    }

    private func run(asking: String?) async {
        var ask = asking             // the first attempt asks; every later one joins what's being written
        var tries = 0
        while !Task.isCancelled {
            let joining = ask == nil
            do {
                try await AppModel.shared.call {
                    let events = ask.map { API.shared.stream("ask/stream", [("text", $0), ("client", nonce)]) }
                        ?? API.shared.stream("ask/stream", method: "GET")
                    var buffer = ""                              // a join replays from the start...
                    var caughtUp = !joining || answer.isEmpty    // ...silently, until it has caught up with what's on screen
                    var complete = false
                    stream: for try await event in events {
                        switch event.t.string {
                        case "start":
                            // Which question this is. Joining with no id of our own, it's ours only if it carries our
                            // tag: the question may never have got there, and this be an older answer.
                            let id = event.id.string
                            let ours = jobID.map { $0 == id } ?? (joining ? event.client.string == nonce : true)
                            if !ours {
                                complete = true
                                if jobID == nil {
                                    lost = true
                                    throw APIError.message("Couldn't reach Fizzy. Check the connection and try again.")
                                }
                                break stream                     // ours is finished and gone: the saved chat has it
                            }
                            jobID = id
                            if !accepted { accepted = true; changed += 1 }   // saved: the chat shows it from now on
                        case "delta":
                            buffer += event.text.string
                            if !caughtUp, buffer.count >= answer.count { caughtUp = true }
                            if caughtUp { answer = buffer; status = nil }
                        case "status":
                            if caughtUp || buffer.count >= answer.count {   // replayed steps from before what's on screen stay quiet
                                caughtUp = true
                                answer = buffer
                                status = event.text.string
                            }
                        case "reset":   // it wrote a bit, then went to look things up: that bit is saved as its own message
                            if caughtUp {
                                settled += (settled.isEmpty ? "" : "\n\n") + buffer   // kept on screen until the reload brings it
                                answer = ""
                                changed += 1
                            } else {
                                caughtUp = answer.isEmpty                     // a part already in the chat: nothing to redraw
                            }
                            buffer = ""
                        case "done":
                            complete = true
                            break stream
                        case "error":
                            complete = true
                            throw APIError.message(event.text.string)
                        case "none":    // nothing being answered: the question never got there, or the server restarted
                            complete = true
                            if jobID == nil {
                                lost = true
                                throw APIError.message("Couldn't reach Fizzy. Check the connection and try again.")
                            }
                            break stream
                        default:
                            break
                        }
                    }
                    if !complete { throw URLError(.networkConnectionLost) }   // cut off before the end: join again
                }
                break
            } catch APIError.message(let message) {
                self.error = message
                if !accepted { lost = true }     // refused before it was taken (busy, not set up): back in the box
                break
            } catch APIError.cancelled {
                self.error = APIError.cancelled.localizedDescription
                lost = true                      // the passcode prompt was put away: back in the box
                break
            } catch APIError.signedOut {
                break
            } catch is CancellationError {
                return
            } catch {
                // The connection dropped (or a proxy answered for Fizzy). Join again in a moment: a question
                // Fizzy never took gets a few goes, an answer it's writing many more.
                tries += 1
                if tries > (accepted ? 12 : 3) {
                    if accepted {
                        self.error = "Lost the connection. The answer will be here once it's done: pull down to refresh."
                    } else {
                        self.error = "Couldn't reach Fizzy. Check the connection and try again."
                        lost = true
                    }
                    break
                }
                ask = nil
                try? await Task.sleep(for: .seconds(min(10, 2 * tries)))
            }
        }
        active = false
        finished += 1
    }
}

/// Fizz (Gemini, the website calls its page Ask): questions about the stall's data, and changes it proposes that staff can accept.
struct AskView: View {
    @State private var page = Page("ask")
    @State private var text = ""
    @State private var atEnd = true          // the page follows the answer only while its end is in view, like the website
    @FocusState private var focused: Bool
    private var live: AskLive { .shared }

    var body: some View {
        let d = page.data
        let timeline = d.timeline.array
        let liveAnswer = live.answer.trimmingCharacters(in: .whitespacesAndNewlines)
        // Once Fizzy has the question it's in the saved chat (the reload that "accepted" triggers brings it).
        let questionInChat = live.accepted && timeline.contains { $0.kind.string == "msg" && $0.role.string == "user" && $0.text.string == live.question }
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if let flash = page.flash { FlashRow(flash: flash) }
                    if let when = page.offlineSince {
                        Label("No connection. Showing what was saved \(when.formatted(.relative(presentation: .named))).", systemImage: "wifi.slash")
                            .foregroundStyle(.orange)
                    }
                    if let e = page.error { Label(e, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                    if page.loaded && !d.enabled.bool {
                        Label("Fizz is off. Put the Gemini API key in Settings to turn it on.", systemImage: "sparkles").foregroundStyle(.orange)
                    }
                    if page.loaded && timeline.isEmpty && live.question == nil && d.enabled.bool {
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
                    if let q = live.question, !questionInChat { bubble(q, mine: true) }
                    if !live.settled.isEmpty { bubble(live.settled, mine: false) }
                    if !liveAnswer.isEmpty {
                        bubble(live.answer, mine: false, streaming: live.active)
                    } else if live.active {
                        HStack(spacing: 8) { ProgressView(); Text(live.status ?? "Thinking…").font(.footnote).foregroundStyle(.secondary) }
                            .padding(.horizontal, 12).padding(.vertical, 9)
                            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
                    }
                    if let error = live.error { Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red) }
                    Color.clear.frame(height: 1).id("end")
                        .onAppear { atEnd = true }
                        .onDisappear { atEnd = false }
                }
                .padding()
                // A tap on the chat (not on a button in it) puts the keyboard away.
                .background(Color.clear.contentShape(Rectangle()).onTapGesture { focused = false })
            }
            // Even a short chat can be dragged, so dragging down always puts the keyboard away too.
            .scrollBounceBehavior(.always)
            .overlay { if !page.loaded && page.error == nil { ProgressView() } }
            .onChange(of: live.answer) { if atEnd { proxy.scrollTo("end") } }
            .onChange(of: live.status) { if atEnd { proxy.scrollTo("end") } }
            .onChange(of: live.question) { if live.question != nil { proxy.scrollTo("end") } }   // asking: always down to it
            .onChange(of: page.data) { if atEnd { proxy.scrollTo("end") }; joinIfPending() }
        }
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) { composer(d) }
        .navigationTitle("Fizz")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if !timeline.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    ConfirmButton(title: "New chat", systemImage: "square.and.pencil", message: "Clear this chat?", confirmLabel: "Clear") { page.act("ask/clear") }
                        .labelStyle(.iconOnly)
                        .disabled(live.active)
                }
            }
        }
        .refreshable { await page.load() }
        .task {
            await page.load()
            settleAfterLoad()
        }
        .onChange(of: live.changed) {
            Task {
                await page.load()   // the question, or a part of the answer, is in the saved chat now
                if landed { live.settled = "" }
            }
        }
        .onChange(of: live.finished) {
            Task {
                await page.load()   // the saved conversation, with any change it proposes
                settleAfterLoad()
            }
        }
    }

    /// The last load brought the saved chat (not a copy from the cache, not an error).
    private var landed: Bool { page.loaded && page.offlineSince == nil && page.error == nil }

    /// After an answer ended and the chat reloaded: a lost question goes back in the box, and the live
    /// copy goes once the saved chat is really on screen (a cached copy wouldn't have the answer yet).
    private func settleAfterLoad() {
        guard !live.active else { return }
        settle()
        if landed { live.shown() }
    }

    /// An answer still being written when the page opens (asked here earlier, or on the website) is joined.
    /// Not from a saved copy of the page: that answer is long done.
    private func joinIfPending() {
        let p = page.data.pending
        if !p.isNull, page.offlineSince == nil, !live.active { live.join(p) }
        else if p.isNull, !live.active, live.question != nil, landed { live.shown() }   // a later load after a reload that missed
    }

    /// A question that never reached Fizzy goes back in the box, to send again.
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
                    .controlSize(.small)
                    .disabled(page.busy)
                } else {
                    Text((p.status.string == "done" ? "Done" : "Not done") + (p.done_at.optString.map { " · \(Fmt.when($0))" } ?? "")).font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
        }
    }

    /// What was asked, as typed, in ink like the website; what Fizzy said, with its markdown drawn.
    @ViewBuilder private func bubble(_ s: String, mine: Bool, streaming: Bool = false) -> some View {
        Group {
            if mine { Text(s).textSelection(.enabled) } else { MarkdownView(MD.stripEmoji(s), streaming: streaming) }
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
        .foregroundStyle(mine ? Color(.systemBackground) : Color.primary)
        .background(mine ? Color(.label) : Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
        .frame(maxWidth: 720, alignment: mine ? .trailing : .leading)
        .frame(maxWidth: .infinity, alignment: mine ? .trailing : .leading)
    }

    @ViewBuilder private func composer(_ d: J) -> some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField(live.active ? "Fizz is answering…" : "Ask Fizz anything about the stall…", text: $text, axis: .vertical)
                .lineLimit(1...5)
                .focused($focused)
                .padding(10)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 18))
            if focused {
                Button { focused = false } label: {
                    Image(systemName: "keyboard.chevron.compact.down").font(.system(size: 20)).frame(height: 34)
                }
                .accessibilityLabel("Hide keyboard")
                .foregroundStyle(.secondary)
            }
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

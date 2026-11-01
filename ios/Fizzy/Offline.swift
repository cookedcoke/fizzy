// Working with bad signal (restocking in a shop): restocks are kept on the phone until they
// can be sent, and pages open from the last copy that loaded.
import Foundation
import Network
import UIKit

/// A change waiting to be sent. Its photo, if any, is kept in a file next to the queue.
struct Pending: Codable, Identifiable {
    struct File: Codable { let field: String; let filename: String; let mime: String; let stored: String }
    var id = UUID()
    let title: String
    let path: String
    let form: [[String]]          // [name, value] pairs, in order
    let files: [File]
    let created: Date
    var problem: String?          // the server said no: needs changing on the website, or deleting
    var needsPasscode = false
}

@Observable
@MainActor
final class OfflineQueue {
    static let shared = OfflineQueue()

    private(set) var items: [Pending] = []
    private(set) var sending = false
    var lastSent: String?

    private let dir: URL = {
        let d = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("pending", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }()
    private var listFile: URL { dir.appendingPathComponent("queue.json") }
    private let monitor = NWPathMonitor()

    private init() {
        if let data = try? Data(contentsOf: listFile), let saved = try? JSONDecoder().decode([Pending].self, from: data) { items = saved }
        // Send as soon as there's a connection again, and whenever the app comes back to the front.
        monitor.pathUpdateHandler = { path in
            if path.status == .satisfied { Task { @MainActor in await OfflineQueue.shared.send() } }
        }
        monitor.start(queue: DispatchQueue(label: "fizzy.network"))
        NotificationCenter.default.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in await OfflineQueue.shared.send() }
        }
        Task {
            while true {   // and every half minute while something's waiting, for signal that comes and goes
                try? await Task.sleep(for: .seconds(30))
                if !items.isEmpty { await send() }
            }
        }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(items) { try? data.write(to: listFile, options: .atomic) }
    }

    func add(title: String, path: String, form: Params, files: [FilePart]) {
        let stored = files.map { f -> Pending.File in
            let name = UUID().uuidString
            try? f.data.write(to: dir.appendingPathComponent(name), options: .atomic)
            return Pending.File(field: f.field, filename: f.filename, mime: f.mime, stored: name)
        }
        items.append(Pending(title: title, path: path, form: form.map { [$0.0, $0.1] }, files: stored, created: Date()))
        save()
    }

    func remove(_ p: Pending) {
        for f in p.files { try? FileManager.default.removeItem(at: dir.appendingPathComponent(f.stored)) }
        items.removeAll { $0.id == p.id }
        save()
    }

    /// Sends what's waiting, oldest first. Stops at the first one that can't go yet, so they
    /// arrive in order. `asking` is true when someone tapped Send: then a passcode is asked for.
    func send(asking: Bool = false) async {
        guard !sending, !items.isEmpty else { return }
        sending = true
        defer { sending = false }
        for p in items where p.problem == nil && (asking || !p.needsPasscode) {
            let files = p.files.compactMap { f -> FilePart? in
                guard let data = try? Data(contentsOf: dir.appendingPathComponent(f.stored)) else { return nil }
                return FilePart(field: f.field, filename: f.filename, mime: f.mime, data: data)
            }
            let form: Params = p.form.map { ($0[0], $0[1]) }
            do {
                if asking {
                    try await AppModel.shared.call { try await API.shared.post(p.path, form, files: files) }
                } else {
                    try await API.shared.post(p.path, form, files: files)
                }
                // The post answers with a redirect; the message it left says whether it worked.
                let page = try await API.shared.get("money")
                if page.flash_kind.string == "bad", let m = page.flash.optString {
                    update(p.id) { $0.problem = m }
                    continue
                }
                lastSent = "\(p.title) sent."
                remove(p)
            } catch APIError.passcode {
                update(p.id) { $0.needsPasscode = true }
                return
            } catch {
                return   // still no signal (or signed out): try again later
            }
        }
    }

    private func update(_ id: UUID, _ change: (inout Pending) -> Void) {
        guard let i = items.firstIndex(where: { $0.id == id }) else { return }
        change(&items[i])
        save()
    }
}

/// True when a request failed for want of a connection, rather than the server saying no.
func isOffline(_ error: Error) -> Bool {
    guard let e = error as? URLError else { return false }
    return [.notConnectedToInternet, .networkConnectionLost, .timedOut, .cannotConnectToHost, .cannotFindHost,
            .dnsLookupFailed, .dataNotAllowed, .internationalRoamingOff, .secureConnectionFailed].contains(e.code)
}

/// The last copy of each page that loaded, so a screen still opens with no signal.
enum PageCache {
    private static let dir: URL = {
        let d = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("pages", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }()

    private static func file(_ path: String, _ query: [String: String]) -> URL {
        let key = API.shared.baseURL.absoluteString + path + query.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }.joined(separator: "&")
        let safe = key.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) ? String($0) : "_" }.joined()
        return dir.appendingPathComponent(String(safe.suffix(150)) + ".json")
    }

    static func save(_ data: Data, _ path: String, _ query: [String: String]) {
        try? data.write(to: file(path, query), options: .atomic)
    }

    /// The saved copy and when it was saved.
    static func load(_ path: String, _ query: [String: String]) -> (J, Date)? {
        let f = file(path, query)
        guard let data = try? Data(contentsOf: f), let page = try? JSONDecoder().decode(J.self, from: data),
              let date = (try? f.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate else { return nil }
        return (page, date)
    }

    /// Signing out shouldn't leave the stall's data on the phone.
    static func clear() {
        try? FileManager.default.removeItem(at: dir)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }
}

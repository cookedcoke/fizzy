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
    var host: String?             // which Fizzy it's for (the address can point at a test server)
    var staff: String?            // who saved it: Fizzy records whoever is signed in when it's sent
    var problem: String?          // the server said no: needs changing on the website, or deleting
    var needsPasscode = false
    var lastError: String?        // couldn't be sent last time for a reason other than no signal
    var lastTry: Date?
}

@Observable
@MainActor
final class OfflineQueue {
    static let shared = OfflineQueue()

    private(set) var items: [Pending] = []
    private(set) var sending = false
    private(set) var sendingID: UUID?   // the one on its way right now
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

    private func save() -> Bool {
        guard let data = try? JSONEncoder().encode(items) else { return false }
        do { try data.write(to: listFile, options: .atomic) } catch { return false }
        return true
    }

    /// Keeps a change on the phone. False if it couldn't be written: then it isn't saved anywhere.
    func add(title: String, path: String, form: Params, files: [FilePart]) -> Bool {
        var stored: [Pending.File] = []
        for f in files {
            let name = UUID().uuidString
            do { try f.data.write(to: dir.appendingPathComponent(name), options: .atomic) } catch { return false }
            stored.append(Pending.File(field: f.field, filename: f.filename, mime: f.mime, stored: name))
        }
        items.append(Pending(title: title, path: path, form: form.map { [$0.0, $0.1] }, files: stored, created: Date(),
                             host: API.shared.baseURL.host, staff: AppModel.shared.staff))
        return save()
    }

    func remove(_ p: Pending) {
        for f in p.files { try? FileManager.default.removeItem(at: dir.appendingPathComponent(f.stored)) }
        items.removeAll { $0.id == p.id }
        _ = save()
    }

    /// Sends what's waiting, oldest first. Stops at the first one that can't go yet, so they
    /// arrive in order. `asking` is true when someone tapped Send: then a passcode is asked for,
    /// and something saved by someone else goes too (as whoever is signed in now).
    func send(asking: Bool = false) async {
        guard !sending, !items.isEmpty else { return }
        sending = true
        defer { sending = false; sendingID = nil }
        let host = API.shared.baseURL.host
        let who = AppModel.shared.staff
        for id in items.map(\.id) {
            guard let p = items.first(where: { $0.id == id }), p.problem == nil else { continue }   // deleted meanwhile
            if let h = p.host, h != host { continue }                                         // saved for another Fizzy
            if !asking {
                if p.needsPasscode { continue }
                if let s = p.staff, s != who { continue }                                      // theirs to send, or a tap
                if let t = p.lastTry, p.lastError != nil, Date().timeIntervalSince(t) < 120 { continue }   // give it a couple of minutes
            }
            var files: [FilePart] = []
            var missing = false
            for f in p.files {
                guard let data = try? Data(contentsOf: dir.appendingPathComponent(f.stored)) else { missing = true; break }
                files.append(FilePart(field: f.field, filename: f.filename, mime: f.mime, data: data))
            }
            if missing { update(id) { $0.problem = "Its receipt photo is missing from this phone." }; continue }
            let form: Params = p.form.map { ($0[0], $0[1]) }
            sendingID = id
            do {
                if asking {
                    try await AppModel.shared.call { try await API.shared.post(p.path, form, files: files) }
                } else {
                    try await API.shared.post(p.path, form, files: files)
                }
                // It's in (a redirect). The message it left says whether Fizzy took it or said no. If that can't
                // be read yet, it stays until it can: sending it again is safe (a restock has its own id).
                let page: J
                do { page = try await API.shared.get("money") } catch { update(id) { $0.lastTry = Date() }; return }
                guard items.contains(where: { $0.id == id }) else { continue }   // deleted while on its way: it's in anyway
                if page.flash_kind.string == "bad", let m = page.flash.optString {
                    update(id) { $0.problem = m }
                    continue
                }
                lastSent = "\(p.title) sent."
                remove(p)
            } catch APIError.passcode {
                update(id) { $0.needsPasscode = true }
                return
            } catch APIError.cancelled {   // the passcode prompt was put away: nothing wrong, stop here
                update(id) { $0.needsPasscode = true }
                return
            } catch APIError.signedOut {
                return
            } catch where isOffline(error) {
                return   // still no signal: try again later
            } catch {
                update(id) { $0.lastError = error.localizedDescription; $0.lastTry = Date() }   // Fizzy had a problem: say so, try later
            }
        }
    }

    private func update(_ id: UUID, _ change: (inout Pending) -> Void) {
        guard let i = items.firstIndex(where: { $0.id == id }) else { return }
        change(&items[i])
        _ = save()
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

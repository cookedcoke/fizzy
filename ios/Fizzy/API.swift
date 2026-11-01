// Talks to the Fizzy server. Every staff page answers with the data it's drawn from when asked
// (`X-Fizzy-Format: json`); changes go through the same form posts the website uses. A post
// answers with a redirect and leaves its message in the session, which the next page load hands
// back as `flash`.
import Foundation

enum APIError: LocalizedError {
    case signedOut
    case notRegistered(String)
    case passcode(need: String)   // "pin" or "owner"
    case message(String)
    case server(Int)

    var errorDescription: String? {
        switch self {
        case .signedOut: "You're signed out."
        case .notRegistered(let host): "This device isn't registered with Fizzy, so it was sent to \(host). Register it in Settings."
        case .passcode: "Enter the passcode first. Nothing was changed."
        case .message(let m): m
        case .server(let code): code == 404 ? "Not found." : "Fizzy had a problem (\(code)). Try again."
        }
    }
}

struct FilePart {
    let field: String
    let filename: String
    let mime: String
    let data: Data
}

typealias Params = [(String, String)]

final class API: NSObject, URLSessionTaskDelegate {
    static let shared = API()
    static let defaultBaseURL = "https://fizzy.cookedcoke.co"

    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.default
        config.httpCookieStorage = .shared
        config.httpShouldSetCookies = true
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 30
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
        config.httpAdditionalHeaders = ["User-Agent": "FizzyiOS/\(version)", "X-Fizzy-Format": "json"]
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()

    var baseURL: URL {
        let saved = UserDefaults.standard.string(forKey: "baseURL") ?? ""
        return URL(string: saved.isEmpty ? Self.defaultBaseURL : saved)!
    }

    /// "fizzy.example.com", "http://localhost:8000" or "https://test.cookedcoke.co/" -> a base URL.
    static func normalise(_ text: String) -> String? {
        var s = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return nil }
        if !s.contains("://") { s = (s.hasPrefix("localhost") || s.hasPrefix("127.") ? "http://" : "https://") + s }
        guard var c = URLComponents(string: s), let scheme = c.scheme, ["http", "https"].contains(scheme), c.host != nil else { return nil }
        c.path = ""; c.query = nil; c.fragment = nil
        return c.string
    }

    // Redirects are answers here (signed out, saved, not registered), so don't follow them.
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest) async -> URLRequest? { nil }

    // MARK: requests

    func url(_ path: String, _ query: [String: String] = [:]) -> URL {
        var c = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { c.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) } }
        return c.url!
    }

    /// Looks at the answer for the ways a request can be turned away.
    private func check(_ http: HTTPURLResponse, _ data: Data, for request: URLRequest) throws {
        if (300..<400).contains(http.statusCode), let location = http.value(forHTTPHeaderField: "Location") {
            let to = URL(string: location, relativeTo: request.url)
            if let host = to?.host, host != baseURL.host { throw APIError.notRegistered(host) }
            if to?.path == "/login" { throw APIError.signedOut }
        }
        if http.statusCode == 423 {
            let need = (try? JSONDecoder().decode(J.self, from: data))?.need.string ?? "pin"
            throw APIError.passcode(need: need)
        }
        if http.statusCode >= 400 { throw APIError.server(http.statusCode) }
    }

    private func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.server(0) }
        try check(http, data, for: request)
        return (data, http)
    }

    func get(_ path: String, _ query: [String: String] = [:]) async throws -> J {
        let (data, http) = try await send(URLRequest(url: url(path, query)))
        guard http.statusCode == 200 else { throw APIError.server(http.statusCode) }
        let page = try JSONDecoder().decode(J.self, from: data)
        if page.page.optString != nil { PageCache.save(data, path, query) }
        if let tz = page.tz.optString, let zone = TimeZone(identifier: tz) { Fmt.timeZone = zone }
        return page
    }

    private func enc(_ s: String) -> String {
        let safe = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        return s.addingPercentEncoding(withAllowedCharacters: safe) ?? ""
    }

    /// A form post (multipart if there are files). Returns the answer so callers can tell a
    /// redirect (done) from a page (a report to show).
    @discardableResult
    func post(_ path: String, _ form: Params = [], files: [FilePart] = [], query: [String: String] = [:]) async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: url(path, query))
        request.httpMethod = "POST"
        // The server checks posts come from its own pages.
        request.setValue(baseURL.absoluteString + "/", forHTTPHeaderField: "Referer")
        request.setValue(baseURL.absoluteString, forHTTPHeaderField: "Origin")
        if files.isEmpty {
            request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
            request.httpBody = form.map { "\(enc($0.0))=\(enc($0.1))" }.joined(separator: "&").data(using: .utf8)
        } else {
            let boundary = "fizzy-\(UUID().uuidString)"
            request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            var body = Data()
            func add(_ s: String) { body.append(s.data(using: .utf8)!) }
            for (k, v) in form {
                add("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(k)\"\r\n\r\n\(v)\r\n")
            }
            for f in files {
                add("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(f.field)\"; filename=\"\(f.filename)\"\r\nContent-Type: \(f.mime)\r\n\r\n")
                body.append(f.data)
                add("\r\n")
            }
            add("--\(boundary)--\r\n")
            request.httpBody = body
        }
        return try await send(request)
    }

    /// Saves a file the server sends (an export, the backup, a receipt) and says where.
    func download(_ path: String, _ query: [String: String] = [:]) async throws -> URL {
        let request = URLRequest(url: url(path, query))
        let (temp, response) = try await session.download(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.server(0) }
        try check(http, Data(), for: request)
        var name = (path as NSString).lastPathComponent
        if let cd = http.value(forHTTPHeaderField: "Content-Disposition"),
           let r = cd.range(of: #"filename="?([^";]+)"#, options: .regularExpression) {
            name = String(cd[r]).replacingOccurrences(of: "filename=", with: "").replacingOccurrences(of: "\"", with: "")
        }
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let dest = dir.appendingPathComponent(name)
        try FileManager.default.moveItem(at: temp, to: dest)
        return dest
    }

    /// Server-sent events (the Ask answer as it's written): one decoded JSON object per event.
    /// Server-sent events, one JSON object each. POST asks; GET joins an answer already under way.
    func stream(_ path: String, _ form: Params = [], method: String = "POST") -> AsyncThrowingStream<J, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var request = URLRequest(url: url(path))
                    request.httpMethod = method
                    request.timeoutInterval = 300   // between bytes: the server sends a comment line every 15 quiet seconds
                    request.setValue(baseURL.absoluteString + "/", forHTTPHeaderField: "Referer")
                    request.setValue(baseURL.absoluteString, forHTTPHeaderField: "Origin")
                    if method == "POST" {
                        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
                        request.httpBody = form.map { "\(enc($0.0))=\(enc($0.1))" }.joined(separator: "&").data(using: .utf8)
                    }
                    let (bytes, response) = try await session.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else { throw APIError.server(0) }
                    try check(http, Data(), for: request)
                    for try await line in bytes.lines where line.hasPrefix("data:") {
                        let json = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        if let d = json.data(using: .utf8), let event = try? JSONDecoder().decode(J.self, from: d) {
                            continuation.yield(event)
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // MARK: sign in

    func staffNames() async throws -> [String] {
        let (data, _) = try await send(URLRequest(url: url("api/staff-names")))
        return try JSONDecoder().decode([String].self, from: data)
    }

    func me() async throws -> J { try await get("api/me") }

    /// A page the server sends as plain text (what Square sent for a payment).
    func text(_ path: String) async throws -> String {
        let (data, http) = try await send(URLRequest(url: url(path)))
        guard http.statusCode == 200 else { throw APIError.server(http.statusCode) }
        return String(decoding: data, as: UTF8.self)
    }

    func search(_ q: String) async throws -> J { try await get("api/search", ["q": q]) }

    func signIn(who: String, password: String) async throws {
        let (data, http) = try await post("login", [("who", who), ("password", password), ("next", "/")])
        if http.statusCode == 303 { return }
        // The sign-in page came back with what was wrong with it.
        if let page = try? JSONDecoder().decode(J.self, from: data), let e = page.error.optString { throw APIError.message(e) }
        throw APIError.message("Couldn't sign in.")
    }

    func signOut() async { _ = try? await post("logout") }

    /// Student card barcode -> customer id, or nil if no one has that number.
    func scan(_ code: String) async throws -> Int? {
        let request = URLRequest(url: url("scan", ["code": code]))
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse,
              let loc = http.value(forHTTPHeaderField: "Location"), let path = URL(string: loc, relativeTo: baseURL)?.path,
              path.hasPrefix("/customers/") else { return nil }
        return Int(path.dropFirst("/customers/".count))
    }

    /// `need` is "pin" or "owner", as the server asked.
    func unlock(passcode: String, need: String) async throws {
        let (data, _) = try await post("settings/pin/unlock", [("pin", passcode), ("need", need == "owner" ? "2" : "1")])
        let answer = try JSONDecoder().decode(J.self, from: data)
        if !answer.ok.bool { throw APIError.message(answer.error.optString ?? "Wrong passcode.") }
    }

    /// Uses a one-time device registration link (Settings > Devices in Fizzy).
    func register(link: URL) async throws {
        var request = URLRequest(url: link)
        request.httpMethod = "POST"
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.server(0) }
        if http.statusCode == 410 { throw APIError.message("That link has expired or was already used. Make a new one in Settings > Devices.") }
        guard http.statusCode == 303 else { throw APIError.server(http.statusCode) }
    }
}

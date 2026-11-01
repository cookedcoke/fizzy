// Talks to the Fizzy server. Reads come from the /api/... JSON endpoints; changes go through the
// same form posts the staff pages use. Those answer with a redirect and leave their message in
// the session, which the next /api/customers/{id} hands back as `flash`.
import Foundation

enum APIError: LocalizedError {
    case signedOut
    case notRegistered(String)
    case message(String)
    case server(Int)

    var errorDescription: String? {
        switch self {
        case .signedOut: "You're signed out."
        case .notRegistered(let host): "This device isn't registered with Fizzy, so it was sent to \(host). Register it in Settings."
        case .message(let m): m
        case .server(let code): code == 404 ? "Not found." : "Fizzy had a problem (\(code)). Try again."
        }
    }
}

final class API: NSObject, URLSessionTaskDelegate {
    static let shared = API()
    static let defaultBaseURL = "https://fizzy.cookedcoke.co"

    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.default
        config.httpCookieStorage = .shared
        config.httpShouldSetCookies = true
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 20
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
        config.httpAdditionalHeaders = ["User-Agent": "FizzyiOS/\(version)"]
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

    private func url(_ path: String, _ query: [String: String] = [:]) -> URL {
        var c = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { c.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) } }
        return c.url!
    }

    private func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.server(0) }
        if (300..<400).contains(http.statusCode), let location = http.value(forHTTPHeaderField: "Location") {
            let to = URL(string: location, relativeTo: request.url)
            if let host = to?.host, host != baseURL.host { throw APIError.notRegistered(host) }
            if to?.path == "/login" { throw APIError.signedOut }
        }
        return (data, http)
    }

    func get<T: Decodable>(_ path: String, _ query: [String: String] = [:]) async throws -> T {
        let (data, http) = try await send(URLRequest(url: url(path, query)))
        guard http.statusCode == 200 else { throw APIError.server(http.statusCode) }
        return try JSONDecoder().decode(T.self, from: data)
    }

    /// A form post. Returns the response so callers can tell a redirect (done) from a page (error).
    @discardableResult
    func post(_ path: String, _ form: [String: String]) async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: url(path))
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        // The server checks posts come from its own pages.
        request.setValue(baseURL.absoluteString + "/", forHTTPHeaderField: "Referer")
        request.setValue(baseURL.absoluteString, forHTTPHeaderField: "Origin")
        let safe = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        func enc(_ s: String) -> String { s.addingPercentEncoding(withAllowedCharacters: safe) ?? "" }
        request.httpBody = form.map { "\(enc($0.key))=\(enc($0.value))" }.joined(separator: "&").data(using: .utf8)
        let (data, http) = try await send(request)
        if http.statusCode >= 400 { throw APIError.server(http.statusCode) }
        return (data, http)
    }

    // MARK: Fizzy

    func staffNames() async throws -> [String] { try await get("api/staff-names") }
    func me() async throws -> Me { try await get("api/me") }

    func signIn(who: String, password: String) async throws {
        let (data, http) = try await post("login", ["who": who, "password": password, "next": "/"])
        if http.statusCode == 303 { return }
        // The sign-in page came back with its error in <div class="err">.
        let page = String(decoding: data, as: UTF8.self)
        if let r = page.range(of: #"(?<=<div class="err">)[^<]*"#, options: .regularExpression) {
            throw APIError.message(String(page[r]).htmlUnescaped)
        }
        throw APIError.message("Couldn't sign in.")
    }

    func signOut() async {
        _ = try? await post("logout", [:])
    }

    /// A change to one customer: posts the form, then loads their page. The post redirects back
    /// to the customer whether it worked or not, so a "bad" message is how a refusal shows.
    func act(_ id: Int, _ path: String, _ form: [String: String]) async throws -> Customer {
        try await post("customers/\(id)/\(path)", form)
        let c = try await customer(id)
        if c.flash_kind == "bad", let message = c.flash { throw APIError.message(message) }
        return c
    }

    func search(_ q: String) async throws -> [SearchHit] { try await get("api/search", ["q": q]) }
    func customer(_ id: Int) async throws -> Customer { try await get("api/customers/\(id)") }
    func claimable(_ id: Int) async throws -> Claimable { try await get("api/customers/\(id)/claimable") }
    func newSales(_ id: Int, since: String) async throws -> [Sale] { try await get("api/customers/\(id)/new-sales", ["since": since]) }

    /// Student card barcode -> customer id, or nil if no one has that number.
    func scan(_ code: String) async throws -> Int? {
        let (_, http) = try await send(URLRequest(url: url("scan", ["code": code])))
        guard let loc = http.value(forHTTPHeaderField: "Location"), let path = URL(string: loc, relativeTo: baseURL)?.path,
              path.hasPrefix("/customers/") else { return nil }
        return Int(path.dropFirst("/customers/".count))
    }

    /// Answers with the passcode check's own message on failure.
    func unlock(passcode: String) async throws {
        let (data, _) = try await post("settings/pin/unlock", ["pin": passcode])
        struct Answer: Decodable { let ok: Bool; let error: String? }
        let a = try JSONDecoder().decode(Answer.self, from: data)
        if !a.ok { throw APIError.message(a.error ?? "Wrong passcode.") }
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

extension String {
    var htmlUnescaped: String {
        replacingOccurrences(of: "&#39;", with: "'").replacingOccurrences(of: "&quot;", with: "\"")
            .replacingOccurrences(of: "&lt;", with: "<").replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&amp;", with: "&")
    }
}

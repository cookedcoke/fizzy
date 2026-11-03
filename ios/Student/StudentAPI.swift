// Talks to the student site (my.cookedcoke.co). Its pages answer with their data when asked
// (`X-Fizzy-Format: json`): the home page, signing in with a passcode or a counter code, signing out.
import Foundation
import Security

enum StudentError: LocalizedError {
    case message(String)
    case server(Int)

    var errorDescription: String? {
        switch self {
        case .message(let m): m
        case .server(let code): "Cooked Coke had a problem (\(code)). Try again."
        }
    }
}

final class StudentAPI {
    static let shared = StudentAPI()
    static let defaultServer = "https://my.cookedcoke.co"

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.httpCookieStorage = .shared
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 20
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
        config.httpAdditionalHeaders = ["User-Agent": "CookedCokeiOS/\(version)", "X-Fizzy-Format": "json"]
        return URLSession(configuration: config)
    }()

    /// The student site's address. On a test server it's the staff address plus /me.
    var server: String {
        let saved = UserDefaults.standard.string(forKey: "server") ?? ""
        return saved.isEmpty ? Self.defaultServer : saved
    }

    /// "my.example.com" or "http://localhost:8000/me/" -> "https://my.example.com", "http://localhost:8000/me".
    static func normalise(_ text: String) -> String? {
        var s = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return nil }
        if !s.contains("://") { s = (s.hasPrefix("localhost") || s.hasPrefix("127.") ? "http://" : "https://") + s }
        guard var c = URLComponents(string: s), let scheme = c.scheme, ["http", "https"].contains(scheme), c.host != nil else { return nil }
        c.query = nil; c.fragment = nil
        while c.path.hasSuffix("/") { c.path.removeLast() }
        return c.string
    }

    private func url(_ path: String) -> URL { URL(string: server + path)! }

    private func answer(_ request: URLRequest) async throws -> J {
        let (data, response) = try await session.data(for: request)
        return try decode(data, response)
    }

    private func decode(_ data: Data, _ response: URLResponse) throws -> J {
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        let body = (try? JSONDecoder().decode(J.self, from: data)) ?? .null
        if let error = body.error.optString { throw StudentError.message(error) }
        guard code == 200, !body.isNull else { throw StudentError.server(code) }
        return body
    }

    /// The home page: everything about the signed-in student, or (signed out) what's on.
    func home() async throws -> J { try await answer(URLRequest(url: url("/"))) }

    func signIn(who: String, passcode: String) async throws {
        var request = URLRequest(url: url("/login"))
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        let safe = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        func enc(_ s: String) -> String { s.addingPercentEncoding(withAllowedCharacters: safe) ?? "" }
        request.httpBody = "who=\(enc(who))&passcode=\(enc(passcode))".data(using: .utf8)
        _ = try await answer(request)
    }

    /// A sign-in code from the counter: the token at the end of its link (…/q/<token>).
    func useCode(_ token: String) async throws {
        _ = try await answer(URLRequest(url: url("/q/" + token)))
    }

    /// Whatever a challenge's Scan button read. The server says what it meant ("Found it: the library. 2 of 5 done.").
    /// The site's session lasts 12 hours: if it's run out, sign in again with the saved passcode and send it once more.
    func scan(_ code: String) async throws -> String {
        var request = URLRequest(url: url("/scan"))
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        let safe = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        request.httpBody = "code=\(code.addingPercentEncoding(withAllowedCharacters: safe) ?? "")".data(using: .utf8)
        let (data, response) = try await session.data(for: request)
        if (response as? HTTPURLResponse)?.statusCode == 401, let saved = SavedSignIn.load() {
            try await signIn(who: saved.who, passcode: saved.passcode)
            return try await answer(request).message.string
        }
        return try decode(data, response).message.string
    }

    /// A hunt poster's code (from its link), opened with no one signed in: this server's student site keeps
    /// it against this phone, and moves it onto their account when they sign in.
    func guestScan(_ code: String) async throws -> String {
        let safe = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
        let (data, _) = try await session.data(from: url("/c/" + (code.addingPercentEncoding(withAllowedCharacters: safe) ?? "")))
        let body = (try? JSONDecoder().decode(J.self, from: data)) ?? .null
        return body.message.optString ?? body.error.optString ?? "Couldn't read that code."
    }

    /// The code in a hunt poster's link (https://link.cookedcoke.co/<code>, the student site's …/c/<code>, or
    /// a test server's …/me/c/<code>), or nil for any other page (the start page, /hunt, a /q/ sign-in code).
    /// As the server's codes: 8 to 40 letters, numbers, - or _.
    static func huntCode(in url: URL) -> String? {
        let parts = url.pathComponents.filter { $0 != "/" }
        let before = Array(parts.dropLast())
        guard let code = parts.last, before.isEmpty || before == ["c"] || before == ["me", "c"],
              (8...40).contains(code.count),
              code.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) && $0.isASCII || $0 == "-" || $0 == "_" })
        else { return nil }
        return code
    }

    func signOut() async {
        var request = URLRequest(url: url("/logout"))
        request.httpMethod = "POST"
        _ = try? await session.data(for: request)
        for cookie in HTTPCookieStorage.shared.cookies ?? [] { HTTPCookieStorage.shared.deleteCookie(cookie) }
    }
}

/// The name and passcode, kept in the Keychain so the app can sign in again by itself when the
/// site's session runs out (it lasts 12 hours). Only for "Stay signed in"; a counter code can't be reused.
enum SavedSignIn {
    private static let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                               kSecAttrService as String: "co.cookedcoke.student.signin"]

    static func save(who: String, passcode: String) {
        clear()
        guard let data = try? JSONEncoder().encode([who, passcode]) else { return }
        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }

    static func load() -> (who: String, passcode: String)? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data,
              let pair = try? JSONDecoder().decode([String].self, from: data), pair.count == 2 else { return nil }
        return (pair[0], pair[1])
    }

    static func clear() { SecItemDelete(query as CFDictionary) }
}

/// The last copy of the home page, so it opens with no signal.
enum SavedPage {
    private static var file: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("me.json")
    }

    static func save(_ page: J) {
        guard let data = try? JSONEncoder().encode(page) else { return }
        try? data.write(to: file, options: .completeFileProtection)
    }

    static func load() -> J? { (try? Data(contentsOf: file)).flatMap { try? JSONDecoder().decode(J.self, from: $0) } }
    static func clear() { try? FileManager.default.removeItem(at: file) }
}

func isOffline(_ error: Error) -> Bool {
    guard let e = error as? URLError else { return false }
    return [.notConnectedToInternet, .networkConnectionLost, .timedOut, .cannotFindHost, .cannotConnectToHost,
            .dataNotAllowed, .internationalRoamingOff].contains(e.code)
}

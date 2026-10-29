import Foundation

/// The app's few settings, kept in UserDefaults (co.cookedcoke.fizzy).
enum Settings {
    static let defaultBaseURL = URL(string: "https://fizzy.cookedcoke.co")!
    private static let baseURLKey = "BaseURL"

    /// Where Fizzy lives. Change it in Fizzy > Settings to point the app at a test server.
    static var baseURL: URL {
        get {
            if let s = UserDefaults.standard.string(forKey: baseURLKey), let url = parseBaseURL(s) {
                return url
            }
            return defaultBaseURL
        }
        set {
            if newValue == defaultBaseURL {
                UserDefaults.standard.removeObject(forKey: baseURLKey)
            } else {
                UserDefaults.standard.set(newValue.absoluteString, forKey: baseURLKey)
            }
        }
    }

    static var staffURL: URL { URL(string: "/", relativeTo: baseURL)!.absoluteURL }
    static var displayURL: URL { URL(string: "/display", relativeTo: baseURL)!.absoluteURL }

    /// "fizzy.example.com", "http://localhost:8000" or "https://test.cookedcoke.co/" -> a base
    /// URL with no path, or nil if it isn't a web address.
    static func parseBaseURL(_ text: String) -> URL? {
        var s = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return nil }
        if !s.contains("://") { s = "https://" + s }
        guard var parts = URLComponents(string: s),
              let scheme = parts.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = parts.host, !host.isEmpty else { return nil }
        parts.scheme = scheme
        parts.host = host.lowercased()
        parts.path = ""
        parts.query = nil
        parts.fragment = nil
        return parts.url
    }

    /// Hosts that open inside the app. Everything else opens in the default browser.
    static var appHosts: Set<String> {
        var hosts: Set<String> = ["fizzy.cookedcoke.co", "my.cookedcoke.co"]
        if let host = baseURL.host?.lowercased() { hosts.insert(host) }
        return hosts
    }

    static func opensInApp(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else { return false }
        if scheme == "about" || scheme == "blob" || scheme == "data" { return true }
        guard scheme == "http" || scheme == "https", let host = url.host?.lowercased() else { return false }
        return appHosts.contains(host)
    }
}

import AppKit
import WebKit

enum WebViews {
    /// Both web views use the default data store, so signing in once in the staff window also
    /// signs in the customer display.
    static func configuration() -> WKWebViewConfiguration {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        // Lets the Fizzy pages tell they're running in the Mac app, e.g. "... FizzyMac/1.0".
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0"
        config.applicationNameForUserAgent = "Version/17.0 Safari/605.1.15 FizzyMac/\(version)"
        return config
    }

    static func make(_ config: WKWebViewConfiguration) -> WKWebView {
        let webView = WKWebView(frame: .zero, configuration: config)
        if #available(macOS 13.3, *) {
            webView.isInspectable = true  // Safari > Develop > [this Mac] > Fizzy, for checking the pages
        }
        return webView
    }
}

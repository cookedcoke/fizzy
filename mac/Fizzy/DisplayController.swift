import AppKit
import WebKit
import os

/// The customer display: a borderless window filling the second screen, showing /display.
final class DisplayController: NSObject {
    let webView: WKWebView
    let window: NSWindow

    private let log = Logger(subsystem: "co.cookedcoke.fizzy", category: "display")
    /// True while the placeholder is up instead of /display (not there yet, signed out, offline).
    private(set) var showingPlaceholder = false
    private var retryTimer: Timer?
    private static let retrySeconds: TimeInterval = 30

    init(configuration: WKWebViewConfiguration) {
        webView = WebViews.make(configuration)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 800, height: 600),
                          styleMask: [.borderless], backing: .buffered, defer: false)
        super.init()
        window.isReleasedWhenClosed = false
        window.backgroundColor = .white
        window.contentView = webView
        webView.navigationDelegate = self
        webView.allowsMagnification = false
    }

    // MARK: Loading

    func load() {
        log.info("Loading \(Settings.displayURL.absoluteString, privacy: .public)")
        webView.load(URLRequest(url: Settings.displayURL))
    }

    /// Tries /display again if the placeholder is up, e.g. after someone signs in on the staff screen.
    func retryIfNeeded() {
        if showingPlaceholder { load() }
    }

    private func isDisplayPage(_ url: URL?) -> Bool {
        guard let url, let host = url.host?.lowercased() else { return false }
        return host == Settings.baseURL.host?.lowercased()
            && (url.path == "/display" || url.path.hasPrefix("/display/"))
    }

    private func showPlaceholder(because reason: String) {
        log.info("Showing the placeholder: \(reason, privacy: .public)")
        if !showingPlaceholder {
            showingPlaceholder = true
            webView.loadHTMLString(Self.placeholderHTML, baseURL: nil)
        }
        retryTimer?.invalidate()
        retryTimer = Timer.scheduledTimer(withTimeInterval: Self.retrySeconds, repeats: false) { [weak self] _ in
            self?.retryIfNeeded()
        }
    }

    // MARK: Screens

    /// Puts the window over the whole of `screen`, or hides it when there's no screen for it.
    func place(on screen: NSScreen?) {
        guard let screen else {
            window.orderOut(nil)
            return
        }
        window.setFrame(screen.frame, display: true)
        window.orderFrontRegardless()
    }

    static let placeholderHTML: String = {
        var logo = ""
        if let url = Bundle.main.url(forResource: "logo", withExtension: "svg"),
           let data = try? Data(contentsOf: url) {
            logo = "<img src=\"data:image/svg+xml;base64,\(data.base64EncodedString())\" alt=\"\">"
        }
        return """
        <!doctype html><meta charset="utf-8">
        <style>
          html, body { margin: 0; height: 100%; background: #fff; cursor: none; overflow: hidden;
                       -webkit-user-select: none; user-select: none; }
          body { display: flex; flex-direction: column; align-items: center; justify-content: center;
                 font: 600 4vh -apple-system, "Helvetica Neue", sans-serif; color: #555; }
          img { width: 30vh; height: 30vh; margin-bottom: 4vh; }
        </style>
        \(logo)
        <div>Customer display</div>
        """
    }()
}

extension DisplayController: WKNavigationDelegate {
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        guard action.targetFrame?.isMainFrame ?? false, let url = action.request.url else {
            decisionHandler(.allow)  // pictures and frames inside the page
            return
        }
        if url.scheme == "about" || isDisplayPage(url) {
            decisionHandler(.allow)
        } else {
            // Somewhere other than /display, e.g. sent to the sign-in page. Customers shouldn't see it.
            decisionHandler(.cancel)
            showPlaceholder(because: "sent to \(url.absoluteString)")
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void) {
        guard response.isForMainFrame, let http = response.response as? HTTPURLResponse else {
            decisionHandler(.allow)
            return
        }
        if http.statusCode == 404 {
            decisionHandler(.cancel)
            showPlaceholder(because: "/display isn't there yet (404)")
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if isDisplayPage(webView.url) {
            showingPlaceholder = false
            retryTimer?.invalidate()
            log.info("Display page loaded")
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    private func failed(_ error: Error) {
        let nsError = error as NSError
        if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled { return }
        if nsError.domain == "WebKitErrorDomain" && nsError.code == 102 { return }  // we cancelled it
        showPlaceholder(because: "didn't load: \(nsError.localizedDescription)")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        log.error("Display's web process stopped; reloading")
        showingPlaceholder = false
        load()
    }
}

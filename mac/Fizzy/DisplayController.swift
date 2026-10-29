import AppKit
import WebKit
import os

/// The customer display window. It can't be clicked, focused or typed into, so nothing aimed at
/// the staff window (Esc, Cmd+W, Cmd+Q) can reach it, and it stays out of Cmd+` and Mission Control.
final class DisplayWindow: NSWindow {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    init() {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 800, height: 600),
                   styleMask: [.borderless], backing: .buffered, defer: false)
        isReleasedWhenClosed = false
        ignoresMouseEvents = true
        // Above the menu bar and Dock on its screen.
        level = .screenSaver
        collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary]
        isExcludedFromWindowsMenu = true
        canHide = false  // Fizzy > Hide Fizzy (Cmd+H) leaves the display up
        hidesOnDeactivate = false
        hasShadow = false
        animationBehavior = .none
        backgroundColor = .white
    }
}

/// The customer display: a window filling the second screen, showing /display.
final class DisplayController: NSObject {
    let webView: WKWebView
    let window: NSWindow
    /// Kept off the display's screen.
    weak var staffWindow: NSWindow?

    private let log = Logger(subsystem: "co.cookedcoke.fizzy", category: "display")
    /// True while the placeholder is up instead of /display (not there yet, signed out, offline).
    private(set) var showingPlaceholder = false
    private var placeholderLoad: WKNavigation?
    private var retryTimer: Timer?
    private static let retrySeconds: TimeInterval = 30

    init(configuration: WKWebViewConfiguration) {
        webView = WebViews.make(configuration)
        window = DisplayWindow()
        super.init()
        window.contentView = webView
        webView.navigationDelegate = self
        webView.allowsMagnification = false

        // Screens plugged in or out, rearranged, or woken up: put the display back where it goes.
        let center = NotificationCenter.default
        center.addObserver(self, selector: #selector(screensChanged),
                           name: NSApplication.didChangeScreenParametersNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(screensChanged),
                                                          name: NSWorkspace.screensDidWakeNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(screensChanged),
                                                          name: NSWorkspace.didWakeNotification, object: nil)
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
            placeholderLoad = webView.loadHTMLString(Self.placeholderHTML, baseURL: nil)
        }
        retryTimer?.invalidate()
        retryTimer = Timer.scheduledTimer(withTimeInterval: Self.retrySeconds, repeats: false) { [weak self] _ in
            self?.retryIfNeeded()
        }
    }

    // MARK: Screens

    /// The screen picked with Display > Move display to other screen, by its display UUID.
    private static let screenKey = "DisplayScreen"
    private var pendingPlace: DispatchWorkItem?

    @objc private func screensChanged() {
        // These come in bursts while a screen connects, so wait for things to settle.
        pendingPlace?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.place() }
        pendingPlace = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 1, execute: work)
    }

    /// The screen the display belongs on: the one picked with "Move display to other screen" if
    /// it's plugged in, otherwise the first screen that isn't the main one. None with one screen.
    private func chooseScreen() -> NSScreen? {
        let screens = NSScreen.screens
        guard screens.count > 1 else { return nil }
        if let saved = UserDefaults.standard.string(forKey: Self.screenKey),
           let screen = screens.first(where: { $0.uuid == saved }) {
            return screen
        }
        return screens[1]
    }

    /// Covers the right screen, or hides the display until a second screen is plugged in.
    func place() {
        guard let screen = chooseScreen() else {
            if window.isVisible { log.info("Only one screen; display hidden until another is plugged in") }
            window.orderOut(nil)
            return
        }
        log.info("Display on \(screen.localizedName, privacy: .public)")
        window.setFrame(screen.frame, display: true)
        window.orderFrontRegardless()
        moveStaffWindow(offOf: screen)
    }

    /// Display > Move display to other screen. Swaps with the staff window if they'd overlap.
    func moveToNextScreen() {
        let screens = NSScreen.screens
        guard screens.count > 1 else {
            NSSound.beep()
            log.info("Can't move the display: there's only one screen")
            return
        }
        let current = screens.firstIndex { $0.uuid == window.screen?.uuid } ?? 0
        let next = screens[(current + 1) % screens.count]
        UserDefaults.standard.set(next.uuid, forKey: Self.screenKey)
        place()
    }

    /// If the staff window is on the display's screen it would be hidden behind it, so move it
    /// to another screen, keeping its size where it fits.
    private func moveStaffWindow(offOf screen: NSScreen) {
        guard let staff = staffWindow, staff.screen == screen || staff.screen == nil,
              let other = NSScreen.screens.first(where: { $0 != screen }) else { return }
        let area = other.visibleFrame
        var frame = staff.frame
        frame.size.width = min(frame.width, area.width)
        frame.size.height = min(frame.height, area.height)
        frame.origin.x = area.midX - frame.width / 2
        frame.origin.y = area.midY - frame.height / 2
        staff.setFrame(frame, display: true)
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
        // webView.url can already be a /display retry that's still loading, so go by the navigation.
        if navigation !== placeholderLoad && isDisplayPage(webView.url) {
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

extension NSScreen {
    /// Stays the same for a screen across plugging in and out, unlike its number.
    var uuid: String? {
        guard let number = deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber,
              let uuid = CGDisplayCreateUUIDFromDisplayID(number.uint32Value)?.takeRetainedValue() else { return nil }
        return CFUUIDCreateString(nil, uuid) as String
    }
}

import Foundation
import WebKit
import os

/// Carries messages from the staff pages to the customer display, inside the app.
///
/// The staff pages call `window.webkit.messageHandlers.fizzyDisplay.postMessage(msg)`, and the
/// display page gets the same `msg` through `window.fizzyDisplay(msg)`. The app doesn't look
/// inside the messages, so new kinds of message only need changes to the Fizzy pages.
final class DisplayBridge: NSObject, WKScriptMessageHandler {
    static let name = "fizzyDisplay"

    private weak var display: WKWebView?
    private let log = Logger(subsystem: "co.cookedcoke.fizzy", category: "bridge")

    init(display: WKWebView) {
        self.display = display
    }

    /// Registers the "fizzyDisplay" handler on the staff web view's configuration.
    func install(in config: WKWebViewConfiguration) {
        // The content controller keeps a strong reference, so go through a weak box.
        config.userContentController.add(WeakHandler(self), name: Self.name)
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        // Only Fizzy's own pages get to drive the display.
        let origin = message.frameInfo.securityOrigin
        guard message.frameInfo.isMainFrame, Settings.appHosts.contains(origin.host.lowercased()) else {
            log.error("Ignored a message from \(origin.host, privacy: .public)")
            return
        }
        guard JSONSerialization.isValidJSONObject([message.body]),
              let data = try? JSONSerialization.data(withJSONObject: message.body, options: [.fragmentsAllowed]),
              let json = String(data: data, encoding: .utf8) else {
            log.error("Ignored a message that isn't plain JSON: \(String(describing: message.body), privacy: .public)")
            return
        }
        send(json)
    }

    /// Sends a message (already JSON) to the display page, e.g. from the Display menu.
    func send(_ json: String) {
        log.info("Forwarding to display: \(json, privacy: .public)")
        display?.evaluateJavaScript("void (window.fizzyDisplay && window.fizzyDisplay(\(json)))") { [log] _, error in
            if let error {
                log.error("Display didn't take the message: \(error.localizedDescription, privacy: .public)")
            }
        }
    }
}

/// WKUserContentController holds its handlers strongly; this breaks the cycle.
final class WeakHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(controller, didReceive: message)
    }
}

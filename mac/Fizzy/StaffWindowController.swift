import AppKit
import WebKit
import os

/// The normal window on the main screen where staff use Fizzy.
final class StaffWindowController: NSWindowController, NSWindowDelegate, NSMenuItemValidation {
    let webView: WKWebView
    /// Called after each page finishes loading, e.g. so the display can retry once someone signs in.
    var onPageLoaded: (() -> Void)?

    /// The page that failed to load, so View > Reload Page tries it again instead of the error page.
    private var lastFailedURL: URL?
    private static let zoomKey = "StaffZoom"
    private static let printHandler = "fizzyPrint"
    private let log = Logger(subsystem: "co.cookedcoke.fizzy", category: "staff")

    init(configuration: WKWebViewConfiguration) {
        webView = WebViews.make(configuration)
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1200, height: 800),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.title = "Fizzy"
        // Blend the title bar into Fizzy's dark header instead of a white strip above it.
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.backgroundColor = NSColor(srgbRed: 0x15 / 255.0, green: 0x17 / 255.0, blue: 0x1B / 255.0, alpha: 1)
        window.appearance = NSAppearance(named: .darkAqua)
        window.minSize = NSSize(width: 480, height: 360)
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.contentView = webView
        window.center()
        // Remembers the size and position between launches.
        window.setFrameAutosaveName("StaffWindow")
        super.init(window: window)
        window.delegate = self

        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        // window.print() does nothing in a Mac web view, so Fizzy's Print buttons ask the app instead.
        let content = webView.configuration.userContentController
        content.add(WeakHandler(self), name: Self.printHandler)
        content.addUserScript(WKUserScript(
            source: "window.print = function () { window.webkit.messageHandlers.\(Self.printHandler).postMessage({}); };",
            injectionTime: .atDocumentStart, forMainFrameOnly: true))
        let zoom = UserDefaults.standard.double(forKey: Self.zoomKey)
        if zoom > 0 { webView.pageZoom = zoom }
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    func loadHome() {
        webView.load(URLRequest(url: Settings.staffURL))
    }

    /// Opens an address typed into File > Open Link, e.g. a one-time device registration link.
    func open(_ url: URL) {
        if Settings.opensInApp(url) {
            webView.load(URLRequest(url: url))
        } else {
            NSWorkspace.shared.open(url)
        }
    }

    // MARK: Menu actions (View menu)

    @objc func goHome(_ sender: Any?) { loadHome() }
    @objc func reloadPage(_ sender: Any?) {
        if let url = lastFailedURL {
            webView.load(URLRequest(url: url))
        } else if webView.url == nil || webView.url?.scheme == "about" {
            loadHome()
        } else {
            webView.reload()
        }
    }
    @objc func zoomIn(_ sender: Any?) { setZoom(webView.pageZoom * 1.1) }
    @objc func zoomOut(_ sender: Any?) { setZoom(webView.pageZoom / 1.1) }
    @objc func actualSize(_ sender: Any?) { setZoom(1) }

    private func setZoom(_ zoom: CGFloat) {
        webView.pageZoom = min(max(zoom, 0.5), 3)
        UserDefaults.standard.set(Double(webView.pageZoom), forKey: Self.zoomKey)
    }

    /// File > Print, and the pages' own Print buttons.
    @objc func printPage(_ sender: Any?) {
        showWindow(nil)
        guard let window, window.attachedSheet == nil else { return }
        let info = NSPrintInfo.shared.copy() as! NSPrintInfo
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.isVerticallyCentered = false
        let operation = webView.printOperation(with: info)
        operation.showsPrintPanel = true
        operation.showsProgressPanel = true
        // Without a frame, WKWebView prints blank pages.
        operation.view?.frame = webView.bounds
        operation.runModal(for: window, delegate: nil, didRun: nil, contextInfo: nil)
    }

    @objc func goBack(_ sender: Any?) { webView.goBack() }
    @objc func goForward(_ sender: Any?) { webView.goForward() }

    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        switch item.action {
        case #selector(goBack(_:)): return webView.canGoBack
        case #selector(goForward(_:)): return webView.canGoForward
        default: return true
        }
    }

    // MARK: Downloads

    /// Where finished downloads have been saved, by download.
    private var downloadPaths: [ObjectIdentifier: URL] = [:]

    private func showSaved(_ url: URL) {
        // Makes the Downloads stack in the Dock bounce, like Safari.
        DistributedNotificationCenter.default().post(
            name: Notification.Name("com.apple.DownloadFileFinished"), object: url.path)
        guard let window else { return }
        let alert = NSAlert()
        alert.messageText = "Saved to Downloads"
        alert.informativeText = url.lastPathComponent
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Show in Finder")
        alert.beginSheetModal(for: window) { response in
            if response == .alertSecondButtonReturn {
                NSWorkspace.shared.activateFileViewerSelecting([url])
            }
        }
    }

    private func showError(_ title: String, _ detail: String) {
        guard let window else { return }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = detail
        alert.beginSheetModal(for: window)
    }
}

// MARK: - Print requests from the pages

extension StaffWindowController: WKScriptMessageHandler {
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        let host = message.frameInfo.securityOrigin.host.lowercased()
        guard message.name == Self.printHandler, Settings.appHosts.contains(host) else { return }
        printPage(nil)
    }
}

// MARK: - Navigation

extension StaffWindowController: WKNavigationDelegate {
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload {
            decisionHandler(.download)
            return
        }
        let url = action.request.url
        let mainFrame = action.targetFrame?.isMainFrame ?? true
        if let url, mainFrame, !Settings.opensInApp(url) {
            // Other sites (and mailto: and the like) open in the default browser or app.
            log.info("Opening outside the app: \(url.absoluteString, privacy: .public)")
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void) {
        let http = response.response as? HTTPURLResponse
        let disposition = http?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        if disposition.lowercased().hasPrefix("attachment") || !response.canShowMIMEType {
            decisionHandler(.download)
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView.url?.scheme?.hasPrefix("http") == true { lastFailedURL = nil }
        onPageLoaded?()
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        showLoadError(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        showLoadError(error)
    }

    private func showLoadError(_ error: Error) {
        let nsError = error as NSError
        // Cancelled loads (a new page started, or a link sent to the browser) aren't problems.
        if nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled { return }
        if nsError.domain == "WebKitErrorDomain" && nsError.code == 102 { return }  // frame load interrupted
        log.error("Page didn't load: \(nsError.localizedDescription, privacy: .public)")
        let html = """
        <!doctype html><meta charset="utf-8">
        <style>
          body { font: 16px -apple-system, sans-serif; color: #333; background: #fff; margin: 0;
                 display: flex; align-items: center; justify-content: center; height: 100vh; text-align: center; }
          @media (prefers-color-scheme: dark) { body { color: #ddd; background: #1e1e1e; } }
          h1 { font-size: 22px; font-weight: 600; }
          p { color: #888; max-width: 30em; }
        </style>
        <div><h1>Fizzy didn't load</h1>
        <p>\(Self.escape(nsError.localizedDescription))</p>
        <p>Check the internet connection, then choose View &gt; Reload Page.</p></div>
        """
        lastFailedURL = nsError.userInfo[NSURLErrorFailingURLErrorKey] as? URL ?? lastFailedURL ?? Settings.staffURL
        webView.loadHTMLString(html, baseURL: nil)
    }

    static func escape(_ s: String) -> String {
        s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        log.error("Staff page's web process stopped; reloading")
        webView.reload()
    }
}

// MARK: - Downloads

extension StaffWindowController: WKDownloadDelegate {
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping @MainActor (URL?) -> Void) {
        let folder = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        let url = Self.unusedFile(in: folder, named: suggestedFilename)
        downloadPaths[ObjectIdentifier(download)] = url
        log.info("Downloading to \(url.path, privacy: .public)")
        completionHandler(url)
    }

    func downloadDidFinish(_ download: WKDownload) {
        if let url = downloadPaths.removeValue(forKey: ObjectIdentifier(download)) {
            showSaved(url)
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        downloadPaths.removeValue(forKey: ObjectIdentifier(download))
        showError("The download didn't finish", error.localizedDescription)
    }

    /// "takings.csv", then "takings 2.csv", "takings 3.csv" ... so nothing is overwritten.
    static func unusedFile(in folder: URL, named name: String) -> URL {
        let safe = name.replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: "-")
        let base = safe.isEmpty ? "download" : safe
        var url = folder.appendingPathComponent(base)
        let stem = (base as NSString).deletingPathExtension
        let ext = (base as NSString).pathExtension
        var n = 2
        while FileManager.default.fileExists(atPath: url.path) {
            url = folder.appendingPathComponent(ext.isEmpty ? "\(stem) \(n)" : "\(stem) \(n).\(ext)")
            n += 1
        }
        return url
    }
}

// MARK: - Pop-ups, alerts and file pickers

extension StaffWindowController: WKUIDelegate {
    /// Links that want a new window (target="_blank", window.open): Fizzy pages open in this
    /// window, other sites in the browser.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url {
            if Settings.opensInApp(url) {
                webView.load(action.request)
            } else {
                NSWorkspace.shared.open(url)
            }
        }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        present(alert) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        present(alert) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = field
        present(alert) { completionHandler($0 == .alertFirstButtonReturn ? field.stringValue : nil) }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        if let window {
            panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
        } else {
            completionHandler(panel.runModal() == .OK ? panel.urls : nil)
        }
    }

    private func present(_ alert: NSAlert, _ done: @escaping (NSApplication.ModalResponse) -> Void) {
        if let window, window.isVisible {
            alert.beginSheetModal(for: window, completionHandler: done)
        } else {
            done(alert.runModal())
        }
    }
}

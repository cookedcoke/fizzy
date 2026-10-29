import AppKit
import WebKit
import os

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var staff: StaffWindowController!
    private var display: DisplayController!
    private var bridge: DisplayBridge!
    private lazy var settings = SettingsWindowController()
    private let keepAwake = KeepAwake()
    /// Stops App Nap slowing the display's timers while someone's using another app.
    private var activity: NSObjectProtocol?
    private let log = Logger(subsystem: "co.cookedcoke.fizzy", category: "app")

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = MainMenu.build(self)

        display = DisplayController(configuration: WebViews.configuration())
        bridge = DisplayBridge(display: display.webView)
        let staffConfig = WebViews.configuration()
        bridge.install(in: staffConfig)
        staff = StaffWindowController(configuration: staffConfig)
        staff.onPageLoaded = { [weak self] in self?.display.retryIfNeeded() }

        settings.onChange = { [weak self] url in
            self?.log.info("Fizzy address changed to \(url.absoluteString, privacy: .public)")
            self?.staff.loadHome()
            self?.display.load()
        }

        staff.showWindow(nil)
        staff.loadHome()
        display.load()
        display.staffWindow = staff.window
        display.place()
        keepAwake.start()
        activity = ProcessInfo.processInfo.beginActivity(options: .userInitiated, reason: "Customer display")
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        // Logging out, restarting or shutting down: don't hold that up with a question.
        let reason = NSAppleEventManager.shared().currentAppleEvent?
            .attributeDescriptor(forKeyword: kAEQuitReason)?.enumCodeValue
        if let reason, [kAELogOut, kAEReallyLogOut, kAEShowRestartDialog, kAERestart,
                        kAEShowShutdownDialog, kAEShutDown].contains(reason) {
            return .terminateNow
        }
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Quit Fizzy?"
        alert.informativeText = "The customer display will turn off."
        // Esc means Cancel and Return does nothing, so a stray key press can't quit.
        alert.addButton(withTitle: "Cancel")
        let quit = alert.addButton(withTitle: "Quit")
        quit.keyEquivalent = ""
        if #available(macOS 11.0, *) { quit.hasDestructiveAction = true }
        return alert.runModal() == .alertSecondButtonReturn ? .terminateNow : .terminateCancel
    }

    func applicationWillTerminate(_ notification: Notification) {
        keepAwake.stop()
        if let activity { ProcessInfo.processInfo.endActivity(activity) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    /// Clicking the Dock icon brings back the staff window if it was closed.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        staff.showWindow(nil)
        return true
    }

    // MARK: Menu actions

    @objc func showStaffWindow(_ sender: Any?) {
        staff.showWindow(nil)
    }

    @objc func hideDisplay(_ sender: Any?) {
        bridge.send(#"{"type":"hide"}"#)
    }

    @objc func showDisplay(_ sender: Any?) {
        bridge.send(#"{"type":"idle"}"#)
    }

    @objc func reloadDisplay(_ sender: Any?) {
        display.load()
    }

    @objc func moveDisplay(_ sender: Any?) {
        display.moveToNextScreen()
    }

    @objc func showSettings(_ sender: Any?) {
        settings.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func openLink(_ sender: Any?) {
        staff.showWindow(nil)
        let alert = NSAlert()
        alert.messageText = "Open Link"
        alert.informativeText = "Paste a Fizzy link, for example a device registration link."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24))
        field.placeholderString = Settings.staffURL.absoluteString
        alert.accessoryView = field
        alert.addButton(withTitle: "Open")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = field
        guard let window = staff.window else { return }
        alert.beginSheetModal(for: window) { [weak self] response in
            guard response == .alertFirstButtonReturn else { return }
            var text = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty { return }
            if !text.contains("://") { text = "https://" + text }
            if let url = URL(string: text) { self?.staff.open(url) }
        }
    }
}

import AppKit
import WebKit
import os

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var staff: StaffWindowController!
    private var display: DisplayController!
    private lazy var settings = SettingsWindowController()
    private let log = Logger(subsystem: "co.cookedcoke.fizzy", category: "app")

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = MainMenu.build(self)

        staff = StaffWindowController(configuration: WebViews.configuration())
        display = DisplayController(configuration: WebViews.configuration())
        staff.onPageLoaded = { [weak self] in self?.display.retryIfNeeded() }

        settings.onChange = { [weak self] url in
            self?.log.info("Fizzy address changed to \(url.absoluteString, privacy: .public)")
            self?.staff.loadHome()
            self?.display.load()
        }

        staff.showWindow(nil)
        staff.loadHome()
        display.load()
        display.place(on: displayScreen())
        NSApp.activate(ignoringOtherApps: true)
    }

    /// The screen for the customer display: any screen other than the main one, or none if
    /// there's only one screen.
    private func displayScreen() -> NSScreen? {
        let screens = NSScreen.screens
        guard screens.count > 1 else { return nil }
        let staffScreen = staff.window?.screen ?? screens[0]
        return screens.first { $0 != staffScreen }
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

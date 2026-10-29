import AppKit

/// Fizzy > Settings: the address of the Fizzy server.
final class SettingsWindowController: NSWindowController {
    /// Called with the new address after it's saved.
    var onChange: ((URL) -> Void)?

    private let field = NSTextField()
    private let message = NSTextField(wrappingLabelWithString: "")

    init() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 170),
                              styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Settings"
        window.isReleasedWhenClosed = false
        super.init(window: window)

        let label = NSTextField(labelWithString: "Fizzy address")
        label.font = .boldSystemFont(ofSize: NSFont.systemFontSize)
        field.placeholderString = Settings.defaultBaseURL.absoluteString
        field.target = self
        field.action = #selector(save(_:))
        message.textColor = .secondaryLabelColor
        message.stringValue = "Change this only to try the app with a test server. Both windows reload when you save."

        let reset = NSButton(title: "Use Default", target: self, action: #selector(useDefault(_:)))
        let save = NSButton(title: "Save", target: self, action: #selector(save(_:)))
        save.keyEquivalent = "\r"
        let buttons = NSStackView(views: [reset, NSView(), save])
        buttons.orientation = .horizontal

        let stack = NSStackView(views: [label, field, message, buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 20, left: 20, bottom: 20, right: 20)
        stack.translatesAutoresizingMaskIntoConstraints = false
        let content = NSView()
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            stack.topAnchor.constraint(equalTo: content.topAnchor),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: content.bottomAnchor),
            field.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -40),
            message.widthAnchor.constraint(equalTo: field.widthAnchor),
            buttons.widthAnchor.constraint(equalTo: field.widthAnchor),
        ])
        window.contentView = content
        window.center()
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func showWindow(_ sender: Any?) {
        field.stringValue = Settings.baseURL.absoluteString
        message.textColor = .secondaryLabelColor
        message.stringValue = "Change this only to try the app with a test server. Both windows reload when you save."
        super.showWindow(sender)
        window?.makeFirstResponder(field)
    }

    @objc private func useDefault(_ sender: Any?) {
        field.stringValue = Settings.defaultBaseURL.absoluteString
        save(sender)
    }

    @objc private func save(_ sender: Any?) {
        guard let url = Settings.parseBaseURL(field.stringValue) else {
            message.textColor = .systemRed
            message.stringValue = "That isn't a web address. Try something like https://fizzy.cookedcoke.co"
            return
        }
        let changed = url != Settings.baseURL
        Settings.baseURL = url
        window?.close()
        if changed { onChange?(url) }
    }
}

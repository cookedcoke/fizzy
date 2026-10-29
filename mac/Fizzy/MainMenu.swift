import AppKit

/// The menu bar, built in code. Items with no target go to the first responder (the staff
/// window controller or the web view); the rest go to the app delegate.
enum MainMenu {
    static func build(_ app: AppDelegate) -> NSMenu {
        let main = NSMenu()

        let appMenu = submenu(of: main, "Fizzy")
        appMenu.addItem(item("About Fizzy", #selector(NSApplication.orderFrontStandardAboutPanel(_:))))
        appMenu.addItem(.separator())
        appMenu.addItem(item("Settings…", #selector(AppDelegate.showSettings(_:)), ",", target: app))
        appMenu.addItem(.separator())
        appMenu.addItem(item("Hide Fizzy", #selector(NSApplication.hide(_:)), "h"))
        appMenu.addItem(item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]))
        appMenu.addItem(item("Show All", #selector(NSApplication.unhideAllApplications(_:))))
        appMenu.addItem(.separator())
        appMenu.addItem(item("Quit Fizzy", #selector(NSApplication.terminate(_:)), "q"))

        let file = submenu(of: main, "File")
        file.addItem(item("Open Link…", #selector(AppDelegate.openLink(_:)), "l", target: app))
        file.addItem(.separator())
        file.addItem(item("Close Window", #selector(NSWindow.performClose(_:)), "w"))

        let edit = submenu(of: main, "Edit")
        edit.addItem(item("Undo", Selector(("undo:")), "z"))
        edit.addItem(item("Redo", Selector(("redo:")), "z", [.command, .shift]))
        edit.addItem(.separator())
        edit.addItem(item("Cut", #selector(NSText.cut(_:)), "x"))
        edit.addItem(item("Copy", #selector(NSText.copy(_:)), "c"))
        edit.addItem(item("Paste", #selector(NSText.paste(_:)), "v"))
        edit.addItem(item("Select All", #selector(NSText.selectAll(_:)), "a"))

        let view = submenu(of: main, "View")
        view.addItem(item("Back", #selector(StaffWindowController.goBack(_:)), "["))
        view.addItem(item("Forward", #selector(StaffWindowController.goForward(_:)), "]"))
        view.addItem(item("Home", #selector(StaffWindowController.goHome(_:))))
        view.addItem(item("Reload Page", #selector(StaffWindowController.reloadPage(_:)), "r"))
        view.addItem(.separator())
        view.addItem(item("Actual Size", #selector(StaffWindowController.actualSize(_:)), "0"))
        view.addItem(item("Zoom In", #selector(StaffWindowController.zoomIn(_:)), "+"))
        view.addItem(item("Zoom Out", #selector(StaffWindowController.zoomOut(_:)), "-"))
        view.addItem(.separator())
        view.addItem(item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]))

        let display = submenu(of: main, "Display")
        display.addItem(item("Hide customer display", #selector(AppDelegate.hideDisplay(_:)), "h", [.command, .shift], target: app))
        display.addItem(item("Show customer display", #selector(AppDelegate.showDisplay(_:)), target: app))
        display.addItem(.separator())
        display.addItem(item("Reload display", #selector(AppDelegate.reloadDisplay(_:)), target: app))
        display.addItem(item("Move display to other screen", #selector(AppDelegate.moveDisplay(_:)), target: app))

        let window = submenu(of: main, "Window")
        window.addItem(item("Minimise", #selector(NSWindow.performMiniaturize(_:)), "m"))
        window.addItem(item("Zoom", #selector(NSWindow.performZoom(_:))))
        window.addItem(.separator())
        window.addItem(item("Fizzy", #selector(AppDelegate.showStaffWindow(_:)), "1", target: app))
        NSApplication.shared.windowsMenu = window

        return main
    }

    @discardableResult
    static func submenu(of main: NSMenu, _ title: String) -> NSMenu {
        let menu = NSMenu(title: title)
        let holder = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        holder.submenu = menu
        main.addItem(holder)
        return menu
    }

    static func item(_ title: String, _ action: Selector, _ key: String = "",
                     _ modifiers: NSEvent.ModifierFlags = .command, target: AnyObject? = nil) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.keyEquivalentModifierMask = modifiers
        item.target = target
        return item
    }
}

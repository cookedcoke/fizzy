# Fizzy for the counter

A small desktop app for the counter, built with Electron. It opens two windows:

- **Staff window** (main screen): Fizzy as usual. Sign in, search customers, claim sales.
- **Customer display** (second screen): full screen, facing the customers. It shows
  `/display` on the Fizzy server.

Both windows share the same cookies, so signing in once in the staff window also signs in
the display.

Needs macOS 12 or later. Runs on Apple silicon and Intel Macs.

## Build it

You need Node.js (from nodejs.org, or `brew install node`). In this folder:

```sh
npm install
npm run dist
```

That makes `mac/dist/mac-universal/Fizzy.app`. Drag it into Applications. It's about
500 MB, because it carries its own copy of Chrome for both kinds of Mac.

To try changes without packaging, run `npm start`.

If you had the earlier Swift version of Fizzy in Applications, delete it first. They use
the same app ID.

## Open it the first time

The app isn't signed with an Apple developer account, so macOS may warn about it.

- **Built on the same Mac:** it just opens. No warning.
- **Copied from another Mac** (AirDrop, USB stick, download): macOS blocks the first open.
  - macOS 14 and earlier: right-click (or Control-click) Fizzy.app, choose **Open**, then
    **Open** again.
  - macOS 15 and later: right-click > Open no longer gets past the warning. Double-click the
    app, click **Done**, then go to System Settings > Privacy & Security, scroll down, click
    **Open Anyway** next to the message about Fizzy, and confirm.

You only need to do this once.

## Signing in

- Sign in with the staff password. Security keys (passkeys) may not work in this app, so
  don't rely on them.
- The app keeps its own cookies, separate from your browser. If the counter has to be a
  registered device, register it again inside the app: make a registration link in Fizzy
  (Settings > Devices), then in the app choose **File > Open Link** (Cmd+L) and paste it.

## Using it

The menu bar:

| Menu | Item | What it does |
| --- | --- | --- |
| Display | Hide customer display (Cmd+Shift+H) | Hides what the display is showing (the same as Display: Hidden in Fizzy's header) |
| Display | Show customer display | Puts the display back on Auto (Display: Auto in Fizzy's header) |
| Display | Reload display | Loads the display page again |
| Display | Move display to other screen | Puts the display on the next screen and remembers it |
| File | Open Link (Cmd+L) | Opens a pasted Fizzy link in the staff window |
| File | Print (Cmd+P) | Prints the staff page. Fizzy's own Print buttons work too |
| View | Back, Forward, Home, Reload Page, zoom | For the staff window |
| Fizzy | Settings (Cmd+,) | Change the Fizzy address, e.g. to a test server |

Good to know:

- The customer display can't be clicked, typed into or closed, so Esc, Cmd+W and Cmd+Q
  never reach it.
- Fizzy > Hide Fizzy (Cmd+H) hides only the staff window. The display stays up.
- If the second screen is unplugged, the display hides. When a second screen is plugged back
  in, the display goes back to it by itself. With only one screen, the display stays hidden.
- If the display ends up on the wrong screen, use **Move display to other screen**. If the
  staff window was on that screen, it moves to the other one.
- Closing the staff window (Cmd+W) doesn't quit. Click Fizzy in the Dock, or choose
  Window > Fizzy (Cmd+1), to get it back.
- Quitting asks "Quit Fizzy? The customer display will turn off." Return and Esc both
  cancel, so a stray key press can't quit. Click **Quit** to quit.
- While Fizzy is open, the Mac won't dim its screens, start the screen saver or sleep.
- The staff window remembers its size and position.
- Links to other websites open in your default browser. Only `fizzy.cookedcoke.co`,
  `my.cookedcoke.co` and the address in Settings open inside the app.
- Downloads (CSV exports, backups) save to your Downloads folder. A message tells you
  when each one is saved.

## Settings

**Fizzy > Settings** has one setting: the Fizzy address (default `https://fizzy.cookedcoke.co`).
Change it to try the app with a test server, e.g. `http://localhost:8000`. Both windows
reload when you save. **Use Default** puts it back.

Settings are kept in `~/Library/Application Support/Fizzy/settings.json`.

## For the Fizzy web pages

How the staff pages talk to the display. The messages go straight from one window to the
other inside the app, not through the server.

- A staff page sends a message with:

  ```js
  window.webkit?.messageHandlers?.fizzyDisplay?.postMessage({type: "show", customer: 123});
  ```

  The `?.` keeps it safe in a normal browser, where `window.webkit` doesn't exist.

- The app passes the message on unchanged to the display page, by calling:

  ```js
  window.fizzyDisplay && window.fizzyDisplay(msg)
  ```

  So `/display` should define `window.fizzyDisplay = (msg) => { ... }`.

- The app doesn't look inside messages. New kinds of message only need changes to the
  pages. The menu items send `{"type":"hide"}` and `{"type":"idle"}`.
- Messages must be plain JSON (objects, arrays, strings, numbers, true/false, null).
- Only messages from Fizzy's own pages (the hosts above) are passed on.
- Messages sent while the display page is loading are lost. The display page should work
  out what to show by itself when it loads, e.g. start idle.
- The app adds `FizzyMac/1.0.0` to the browser's user agent, so pages can tell they're in
  the app.
- `window.print()` works as normal.

### Until /display exists

While `/display` returns "page not found" (404), the display shows a placeholder: the
Cooked Coke logo and "Customer display". It also shows the placeholder (instead of the
page customers shouldn't see) if `/display` sends it somewhere else, such as the sign-in
page, or if the server can't be reached. It tries `/display` again every 30 seconds and
each time a page loads in the staff window, so signing in brings the display up.

### Checking messages get through

Every message passed to the display is logged in `~/Library/Logs/Fizzy/main.log`. To watch
them, run this in Terminal while the app is open:

```sh
tail -f ~/Library/Logs/Fizzy/main.log
```

You'll see lines like `[bridge] Forwarding to display: {"type":"show","customer":123}`.

To look inside the pages, use View > Developer Tools (staff window) or
Display > Display Developer Tools.

## The code

- `src/main.js`: starts things up, the quit question, the menu bar, passing messages on.
- `src/staff.js`: the staff window, links, downloads, its size and position.
- `src/display.js`: the display window, which screen it's on, the placeholder.
- `src/staff-preload.js`: gives the staff pages `window.webkit.messageHandlers.fizzyDisplay`.
- `src/store.js`: settings. `src/log.js`: the log file.
- `src/settings.html`, `src/openlink.html`, `src/placeholder.html`: the app's own pages.

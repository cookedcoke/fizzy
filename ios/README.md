# Fizzy for iPhone and iPad

A native SwiftUI app for the counter. It talks to the same Fizzy server as the website, using
the staff password, and has everything the website's staff pages have:

| Tab | What's in it |
| --- | --- |
| **Today** | The dashboard: sales, charts, tabs owing, what needs attention (dismiss alerts, assign payments with nobody on them), who's eligible for a tab (approve or reject), top items, latest sales, top customers. Refreshes every minute. |
| **Customers** | Search by name, email or student number (a full `rns12345678` jumps straight to them), or see who's been in lately. Export new people for Square. |
| **A customer** | Can they put it on their tab, and how much; points; what they owe; tab charges, repayments, points history, Square payments, tab score. Claim sale, record a repayment, adjust points, approve/reject/revoke a tab, redeem or undo a reward, set or show their student passcode, edit them, change account type, show a sign-in QR code. |
| **Transactions** | Every Square sale with filters (dates, kind, search) and assigning sales with nobody on them. |
| **More** | Square (sync, change how a payment is counted, raw payload), Rewards (start and end rewards and double-points days, pause custom rules), Profit (item costs), Money (restocks with receipt photos, payouts with the split calculator), Tools (all of them), Ask (Gemini, with answers as they're written, drawn with their tables, lists and headings), Import (school directory CSV), Settings (who can do what, tab rules, display, terms, closed days, Gemini key, PIN, maintenance, backups, updates, devices), Activity log, and this app's own settings. |

Like the website, a new sale rung up without a customer pops up within a few seconds while a
customer's page is open: **Claim** or **Not theirs**. Nothing is claimed without a tap.

**Ask keeps going without you.** Fizzy writes the answer in the background, so leaving the page, switching
apps or losing signal doesn't lose it. Come back whenever: the page joins the answer where it's up to, and
once it's done the saved conversation shows it. One question at a time per person.

Passcodes work as on the website: if a change needs the PIN or the owner's password, the app
asks for it, then carries on with the change.

**Working with bad signal** (restocking in a shop): pages open from the last copy that loaded, with a
note saying how old it is, and the app opens even with no connection. A restock added with no signal
is kept on the phone (receipt photo included) under "Waiting to send" on Money, and sends by itself when
signal comes back. Each restock carries an id, so one sent twice (the answer got lost) is only saved once.
If the server needs the passcode, it waits for you to tap "Send now". Item names are suggested from the
saved Square list. Other changes (claims, repayments and so on) still need a connection.

Needs iOS 17 or later.

### Not in the app

- **Adding a security key** (passkey). It needs the browser. Existing keys are listed and can
  be removed.
- **The customer display** (`/display`) and the Mac app's second-screen features. This is the
  staff app.

## Build it

You need Xcode and [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`).
In this folder:

```sh
xcodegen
open Fizzy.xcodeproj
```

Pick your team under Signing & Capabilities to run it on a device. The Xcode project is made
from `project.yml`, so change that rather than the project.

## This app's settings

More > This app (or the gear on the sign-in screen):

- **Fizzy address**: default `https://fizzy.cookedcoke.co`. Change it to try a test server,
  e.g. `http://192.168.1.20:8000` (plain http only works on the local network).
- **Counter device**: if Fizzy only lets registered devices in, make a registration link in
  Fizzy (Settings > Devices) and paste it here. Only do this on a device that stays at the counter.

## How it talks to the server

Every staff page also answers with the data it's drawn from, when the request has the header
`X-Fizzy-Format: json` (see `render` in `fizzy/web.py`). The app uses those pages directly, so
it always shows what the website would. Secrets (passcodes, key material) are never included.

Changes go through the website's own form posts (`/customers/{id}/claim`, `/tools/{tool}`,
`/settings/...`), so the rules, passcode checks and activity log are exactly the same. A post
redirects and leaves a message in the session; the next page load hands it back as `flash`, which
is how the app shows "Sale claimed for ..." or why something was refused. When a change needs a
passcode the server answers `423` with `{"need": "pin"}` (or `"owner"`) to JSON requests.

Two small endpoints exist just for the app: `/api/staff-names` (for the sign-in screen) and
`/api/me`. Search uses the existing `/api/search`.

Ask: `POST /ask/stream` asks and streams the answer as server-sent events; the server answers in the
background, so `GET /ask/stream` joins the same answer from the start (after losing the connection, or
when the page is opened again), and the Ask page's `pending` says what's still being answered.

The app sends `FizzyiOS/<version>` as its user agent.

## The code

- `Fizzy/API.swift`: requests, sign-in, cookies, redirects, downloads, the Ask stream.
- `Fizzy/JSON.swift`: the generic JSON value pages are read from, and date and money formatting.
- `Fizzy/Markdown.swift`: Gemini's markdown as native views (headings, lists, tables, code, quotes).
- `Fizzy/Offline.swift`: the waiting-to-send queue, and the saved copies of pages.
- `Fizzy/Components.swift`: loading a page, posting a change, shared controls, the passcode prompt.
- `Fizzy/FizzyApp.swift`: signed in or out, the tabs, who's on the counter.
- One file per screen: `DashboardView`, `CustomersView`, `CustomerView`, `TransactionsView`,
  `SquareView`, `RewardsView`, `ProfitView`, `MoneyView`, `ToolsView`, `AskView`, `ImportView`,
  `ServerSettingsView`, `LogView`, `LoginView`, `SettingsView` (this app's own).

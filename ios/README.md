# Fizzy for iPhone and iPad

A native SwiftUI app for the counter. It talks to the same Fizzy server as the website, using
the staff password, and does the counter jobs:

- **Sign in** as whoever's on the counter, and switch person from the menu at the top left.
- **Find a customer** by name, email or student number. A full student number (`rns12345678`)
  jumps straight to them.
- **Their page**: can they put it on their tab (and up to how much), points, what they owe,
  unpaid tab charges, rewards and points history.
- **Claim sale**: asks Square for the latest sales with no customer, tap one to give it to them.
- **New sale prompt**: while a customer's page is open, a sale rung up without a customer pops
  up within a few seconds. *Claim* or *Not theirs*. It only offers sales from about when the
  page was opened, and never claims anything without a tap.
- **Record repayment**, **Adjust points**, **Tab decision**: the same forms as the website. If
  the server refuses (a bad amount, the passcode is needed), the message stays on the form.

Needs iOS 17 or later.

## Build it

You need Xcode and [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`).
In this folder:

```sh
xcodegen
open Fizzy.xcodeproj
```

Pick your team under Signing & Capabilities to run it on a device. The Xcode project is made
from `project.yml`, so change that rather than the project.

## Settings

The gear button has:

- **Fizzy address**: default `https://fizzy.cookedcoke.co`. Change it to try a test server,
  e.g. `http://192.168.1.20:8000` (plain http only works on the local network).
- **Counter device**: if Fizzy only lets registered devices in, make a registration link in
  Fizzy (Settings > Devices) and paste it here.

## How it talks to the server

Reads use JSON endpoints added for this app in `fizzy/app.py` ("for the iOS app"):
`/api/staff-names`, `/api/me`, `/api/customers/{id}` and `/api/customers/{id}/claimable`, plus
the existing `/api/search` and `/api/customers/{id}/new-sales`.

Changes go through the website's own form posts (`/customers/{id}/claim`, `/repay`, `/points`,
`/tab`), so the rules, passcode checks and audit log are exactly the same. Those posts redirect
and leave a message in the session; `/api/customers/{id}` hands it back as `flash`, which is how
the app shows "Sale claimed for ..." or the reason something was refused.

The app sends `FizzyiOS/<version>` as its user agent.

## The code

- `Fizzy/API.swift`: requests, sign-in, cookies, redirects.
- `Fizzy/Models.swift`: the JSON shapes.
- `Fizzy/FizzyApp.swift`: signed in or out, who's on the counter.
- `Fizzy/LoginView.swift`, `HomeView.swift` (search), `CustomerView.swift`, `Sheets.swift`
  (claim, repay, points, tab, passcode), `SettingsView.swift`.

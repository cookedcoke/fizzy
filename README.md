# Fizzy

Customer directory, points and pay-later tabs for **Cooked Coke Co.**, the student food and
drink stall at Reddam House North Shore. Staff use it at **fizzy.cookedcoke.co**; students can
check their own points, rewards and tab on a separate, read-only [student site](#6-student-site).
It runs on a home PC through a Cloudflare Tunnel (no open ports at home).

Square stays the till. Fizzy reads Square's sales, gives points, keeps track of who owes
what, and answers the one question that matters at the counter: *can this person put it on
their tab?*

**Contents**

1. [At the counter](#1-at-the-counter)
2. [Rules](#2-rules)
3. [How Square and Fizzy fit together](#3-how-square-and-fizzy-fit-together)
4. [Pages](#4-pages)
5. [Rewards (points shop)](#5-rewards-points-shop)
6. [Student site](#6-student-site)
7. [Tools](#7-tools)
8. [Search and keyboard shortcuts](#8-search-and-keyboard-shortcuts)
9. [Setting up](#9-setting-up)
10. [Running it](#10-running-it)
11. [Configuration reference](#11-configuration-reference)
12. [Data and privacy](#12-data-and-privacy)
13. [Troubleshooting](#13-troubleshooting)
14. [Development](#14-development)

---

## 1. At the counter

**The quick way: claim it in Fizzy (type the name once)**
1. In Fizzy: `Esc`, type their name, `Enter`. Their page shows points, tab and rewards.
2. On the Square Terminal, ring it up **without adding a customer** (with the Fizzy Tab
   discount or the Tab repayment item if needed) and take payment.
3. Back in Fizzy, press `K` (**Claim sale**). The newest sale with no customer is at the top:
   `Enter` gives it to them (or `1`–`6` for an earlier one). Points, tab charges and
   repayments land straight away.

Fizzy asks Square for the latest sales when you press `K`, so there's no waiting. If a tab sale
is more than they're allowed, the claim page says so before you claim it. Forget to claim?
Nothing breaks: the sale waits on the **Square** page to be assigned later.

**Or attach the customer in Square** (the steps below), and Fizzy picks it up by itself.

**Normal sale**
1. On the Square Terminal, **attach the customer's profile**, then take payment as usual.
2. Points arrive in Fizzy automatically (within seconds with webhooks, otherwise within 5 minutes).

**Someone wants to put it on their tab**
1. In Fizzy: `Esc`, type their name, then `Enter` (or the result's number, `1`–`8`).
2. Read the answer at the top of their page:
   - **Green tick:** they can, up to the amount shown.
   - **Red cross:** no (overdue, rejected, revoked, or at the limit). The line underneath says why.
   - **Grey dash:** not yet (hasn't spent $30, waiting for approval, or tabs are paused).
3. If green: on the Terminal, ring up the items, **attach their profile**, apply the **Fizzy
   Tab** discount, and charge the **$0 as cash**. Fizzy adds it to their tab automatically.
   - Only part of it on the tab? Apply the discount to just those items and charge the rest
     as normal.

**Someone pays off their tab**
1. On the Terminal: **attach their profile**, add the **Tab repayment** item, and type the
   amount (it has no set price; their Fizzy page shows what they owe).
2. Take **cash or card**. They can buy something in the same sale too.
   Fizzy splits it: the repayment pays off the tab, and the rest is a normal sale with points.
3. Fizzy records the repayment automatically. There's nothing to do in Fizzy.
4. Money that doesn't go through Square (rare) can be recorded on the customer's page under
   **Record without Square**.

**Who's on the counter**: the name in the top-right is recorded against everything you do.
Switch it with `W` or the dropdown. **Lock** signs the device out.

---

## 2. Rules

| Rule | Default | Change it in |
|---|---|---|
| Most a customer can owe | $10 | Settings |
| Days to pay each tab charge | 7 | Settings (applies to new charges) |
| Spend needed before eligible for a tab | $30 | Settings |
| Points | 1 per cent paid ($3 = 300 points) | fixed |

**Tab status**

| Status | Meaning | Can use tab? |
|---|---|---|
| Locked | Hasn't spent $30 yet | No |
| Eligible | Spent $30, waiting for a staff decision | No, until approved |
| Approved | Staff said yes | **Yes**, up to the limit |
| Blocked (overdue) | Approved, but has a charge past its due date | No. Clears itself when paid |
| Paused (all tabs) | Approved, but tabs are paused for everyone (Tools) | No |
| Rejected / Revoked | Staff said no / took it away | No |

- **Staff have the final say.** Approve, reject or revoke in one click, with an optional
  note. Every decision is kept in the customer's history.
- **What counts as "spent":** completed sales with the customer attached, plus tab
  repayments, minus refunds. Manual point adjustments don't count.
- **Points on tab purchases** are earned when the tab is **repaid**, not when charged.
  Points always match the money that actually paid off tabs: if a repayment is refunded or
  re-marked later, the points it earned come back off automatically.
  If someone has credit (paid more than they owed), it pays their next tab sale
  straight away and earns its points then.
  Money that leaves a customer in credit earns nothing, and neither do write-offs.
- **Repayments pay off the oldest charge first.** Paying more than owed leaves them in credit.
- **Late payments are remembered.** A charge paid after its due date stays on the
  customer's page as a "late payment on record", even after it's cleared.
- **Refunds and exchanges** in Square are reversed automatically:
  - a refunded sale loses its points;
  - items returned from a tab sale reduce what's owed;
  - if they'd already repaid those items, the points from the repayment come off.
- **Rule breaks are flagged, not blocked.** Square can't stop a tab sale. So if someone
  not allowed, over the limit or overdue gets one anyway, it's still recorded as owing and
  appears in red under **Needs attention** on the dashboard.

---

## 3. How Square and Fizzy fit together

```
Square Terminal ── sale (customer attached) ───────────────► Fizzy: points
                ── sale with "Fizzy Tab" discount ─────────► Fizzy: tab charge, due in 7 days
                ── "Tab repayment" item (cash / card) ─────► Fizzy: repayment + points
                ── refund / return ────────────────────────► Fizzy: reverses the above
```

Fizzy reads Square through its API: webhooks for near-instant updates, plus polling every
5 minutes as a safety net. It never changes anything in Square. **Everything goes through
Square**, so Square's reports and the till always match.

- **What gets read:** everything Square has *updated* since the last sync, so sales the
  Terminal took offline and uploaded later, and refunds that finish processing later, are
  still picked up.
- **Problem items:** if one sale can't be processed (for example, a network blip while
  fetching it), everything else still syncs. The problem item is listed on the **Square**
  page and fetched again on every sync until it goes through, so nothing is lost.
- **Duplicate Square profiles:** if a Square profile uses an email that's already someone
  else's in Fizzy, it's listed there too instead of being added.
- **Failing syncs:** a sync that fails waits until the next 5-minute check before retrying.
- **Overlapping syncs:** a sync requested while another is running (a webhook, **Sync now**,
  **Re-sync**) runs straight after it, so nothing is dropped.

### Tab sales: the "Fizzy Tab" discount

A tab sale is rung up normally with a **100% discount called "Fizzy Tab"**, so Square records
a **$0 sale** (charged as $0 cash): the items, the customer, and a discount equal to their price.

- **Fizzy:** sees the discount and puts that amount on the customer's tab.
- **Inventory:** Square counts the stock as sold.
- **Paying part now:** apply the discount to only some items. The discounted part goes on
  the tab, and what's paid is a normal sale with points.
- **Your own name for it:** you name the discount, so nothing else in Square gets mistaken for a
  tab.

Fizzy finds these sales through Square's order history, because a $0 sale has no payment.

### Repayments: the "Tab repayment" item

Paying off a tab is a normal Square sale of an item called **Tab repayment**, a catalogue item
with a blank price so you type the amount each time, paid by cash or card. Fizzy records it as a repayment, oldest charge first, and gives
the points then. If the same sale also has other items, Fizzy splits it: the repayment part
pays the tab, and the rest is a normal sale.

### How the money adds up

Each dollar is counted in Square's **net sales** exactly once, when it's actually paid:

| When | Square shows | Net sales |
|---|---|---|
| $3 Coke on the tab | Coke $3, Fizzy Tab discount −$3 | $0 |
| Tab paid off | Tab repayment $3 (cash or card) | $3 |
| **Total** | | **$3** |

- **Net sales and money collected** are right: the till and the bank match Square.
- **Gross sales** include both the Coke and the repayment ($6 here), because Square counts
  an item's full price before discounts. Use **net** sales for takings.
- **The "Fizzy Tab" discount total** in Square's reports is what went on tabs in that period.
- **Tabs never repaid** never become sales in Square. They show in Fizzy as owing, or as
  written off.
- **Item reports** show the Coke when it was sold (at $0 net), and the money arrives later as
  "Tab repayment".
- **GST:** if Cooked Coke Co. is ever registered for GST, check the GST setting on the Tab
  repayment item with whoever does the books. (Below $75,000 a year, registration is
  optional.)

### Why not Square House Accounts?

Square AU does have **House Account** in the payment types list. It's Square's own pay-later
system, and it doesn't suit this:

| | Square House Accounts | Fizzy tabs |
|---|---|---|
| Setup | An account created one customer at a time in Square, with a spending limit and a billing method | Everyone is imported once; staff approve with one click |
| Collecting money | Square invoices (one-off or recurring) or an automatic charge to a card on file. Square says house accounts can't take repayment in instalments, and a sale can't be split between House Account and another payment type | Cash or card at the counter, any amount, any time |
| Overdue | Square sends reminders; you lock accounts by hand | Blocked automatically until paid, with a permanent late record |
| Points, the $30 rule, approvals | None | Built in |
| Your experience | It's the invoice flow you already shut down because people paid late | Built around a 7-day, $10 counter tab |

House Account suits a business billing regular adult customers monthly. For students paying
a few dollars back at the counter, it's the wrong tool, so leave it disabled.

Source: [Square AU help: Create and manage Square House Accounts](https://squareup.com/help/au/en/article/8034-create-and-charge-square-house-accounts).

### Customers in Square

Fizzy links Square customer profiles to Fizzy customers by a reference ID (`fizzy-<id>`,
added by Fizzy's export), or by school email. Profiles created in Square for people not in
the school list (e.g. teachers) get added to Fizzy automatically. A sale with no profile
attached earns no points, and a tab sale with no profile attached shows under **Needs
attention**.

### Fixing a mistake

On the **Square** page, every payment has a **Counted as** dropdown (Sale / Tab charge /
Tab repayment). Changing it undoes what Fizzy did with that payment and redoes it the new
way, including any refunds. **Raw** shows exactly what Square sent.

- **Forgot the discount**, so a tab sale was charged as a normal sale: refund it in Square
  and ring it up again with the discount. Or, if the money really wasn't paid, use
  **Tools → Add tab charge by hand**.
- **Forgot to attach the customer (or attached the wrong one):** type the right person into
  the **Assign to…** box next to the payment, then press `Enter`. Tab sales and repayments
  with no customer show that box right on the dashboard under **Needs attention**; any
  payment has it on the **Square** page (*Only unlinked*). Fizzy then does what it would
  have done:
  - a sale → points;
  - a tab sale → added to their tab;
  - a repayment → clears their tab.

  If it was the wrong person, it's moved off them. If you'd already added the tab charge or
  repayment by hand, Fizzy links to that instead of counting it twice.
- **Repayment recorded twice:** Fizzy refuses a hand-entered repayment that matches one
  already rung up in Square in the last 24 hours. If it really is a second payment, tick
  *This is a separate payment*.

---

## 4. Pages

| Page | What's on it |
|---|---|
| **Dashboard** | Search; noticeboard; warnings (Square not connected, tabs paused). **Today's sales:** sold, taken, orders and average, customers, put on tabs and repaid; sold by hour against the same day last week; the last 7 school days; top items; latest sales. It refreshes itself every minute while nobody's touching it. **Tabs:** totals owed and overdue; **Needs attention** (rule breaks, tab sales with no customer); overdue list; due in the next 2 days; top customers; people waiting for tab approval |
| **Customer** | Can-they-use-a-tab answer; points, owing, overdue, spend (with a progress bar to $30); record repayment; approve / reject / revoke; adjust points; tab charges with due dates and status; repayments; points history; Square payments; decision history; activity log; edit (name, Square ID, archive) |
| **Customers** | Search with a results table; download the Square import CSV |
| **Claim sale** (`K` on a customer) | The latest Square sales with no customer; `Enter` gives the newest to this customer |
| **Rewards** | Start and end reward events; see how many were redeemed |
| **Square** | Connection status, last sync, **Sync now**; how payments are recognised; recent payments with the **Counted as** override and raw data; filter to payments with no customer |
| **Tools** | Occasional jobs (next section) |
| **Import** | Upload the school CSV: preview, then confirm |
| **Settings** | Tab limit, days to pay, spend threshold; backups (back up now, download a copy) |
| **Log** | Every action by every staff member, newest first; export to CSV |

**The sales numbers**, all worked out from what Square sync has stored:
- **Sold:** the value of goods sold, including what went on tabs.
- **Taken:** money actually received - Square sales and tab repayments, plus repayments
  recorded by hand, minus refunds. A tab sale adds nothing until it's paid off.
- **Orders:** checkouts with something sold (a repayment on its own isn't an order).
- **vs last week:** compared with the same weekday last week, up to the same time of day.

The desktop header also shows the time (and the date on wider screens).

---

## 5. Rewards (points shop)

Run a reward whenever you like, e.g. **Free Coke for 1000 points**, or **$2 off for 500**. It
stays live until you end it.

**Starting one:** **Rewards** in the top bar → *Start a reward*. Give it:
- a name;
- the points it costs;
- what staff should do in Square, e.g. "apply the Points: Free Coke discount";
- optionally, a limit per customer.

**At the counter:** while a reward is live, it shows on every customer's page.
- **They have enough points:** it's green, with a **Redeem** button (shortcut `O`). Offer it;
  they can take it or leave it.
- **They don't:** it shows how many more points they need, which is worth telling them too.
- **Search results** show a green **Reward** tag next to anyone who can redeem something.

**Redeeming** takes the points off, logs who did it, and reminds staff which Square discount
to apply. Ring the sale up in Square with that discount; the customer still earns points on
anything they pay for. A redemption made by mistake can be undone from the customer's page
for 12 hours.

**In Square,** make a matching discount for each reward (Items → Discounts):
- *Points: Free Coke* at 100% on the item;
- *Points: $2 off* as a fixed amount.

Don't use the Fizzy Tab discount for rewards: that would put the sale on a tab. Fizzy
refuses a reward whose instructions mention it.

**Ending one:** press **End** on the Rewards page. Its history (how many redeemed, by how
many people) is kept. Redeeming doesn't count as spending, so it doesn't affect the $30 tab
threshold. 1 point = 1 cent spent, so 1000 points = $10 spent.

## 6. Student site

Students can check their own **points, rewards, tab and recent activity**, plus a
**leaderboard**, on their phone. It has its own, more playful look (cream, brand red, the
Bricolage Grotesque font, bundled under the SIL Open Font License in `fizzy/static/`); the
staff site keeps its plain console style. It's read-only: nothing they do there changes anything.

**Setting a passcode (at the stall only).** On the customer's page, open **Student site
passcode**, ask them what they'd like, and type it in once (at least 4 characters). It's a
convenience, not a vault, so tell them not to reuse a password from anywhere else. Setting it
in person is how Fizzy knows it's theirs. **Forgot it?** Staff can look it up: it's on the
customer's page, blurred until you click **Show**. Or just set a new one. To change it, they
ask at the stall. **Remove** turns their sign-in off.
There's no "forgot passcode" or sign-up online, on purpose: nobody can take over someone
else's account from home.

**Signing in.** On the student site they enter their **full name** (or student number) and
passcode. The name box is forgiving: capitals, spaces, apostrophes and hyphens don't matter
(*O'Donnell* = *odonnell*), surname first or no middle name is fine, and a typo or two is
allowed in longer names. It never suggests or lists names, though: the passcode still has to
be right, and a wrong name and a wrong passcode get the same answer, so the site can't be used
to find out who's a customer. Student numbers must be exact. After 5 wrong
tries an account is locked for 15 minutes (name and student number count together), and each
network gets 20 tries. Only a few sign-ins are checked at once, so a flood of guesses can't
slow down the staff side. Sessions last 12 hours; setting a new passcode or
removing it signs them out everywhere.

**What they see:**
- points, and every live reward: ready now (*ask at the stall*) or how many points to go;
- their tab: what they can put on it, what they owe and when it's due, or how much more to
  spend to unlock one;
- recent activity in plain words (staff notes and names are never shown);
- **Leaderboard**, this month and all time, ranked by points *earned* (redeeming rewards
  doesn't drop you down). Only students with a passcode are on it - getting one is opting
  in. Names are shortened to first name + last initial, e.g. *Nora F.*, and it's only shown
  to signed-in students. Everyone also sees their own rank.

Before signing in, anyone can see the list of rewards that are on, and nothing else.

**Setting it up:**
1. Cloudflare → your tunnel → add a public hostname, e.g. `my.cookedcoke.co` →
   `http://fizzy:8000`. Don't put Cloudflare Access in front of it.
2. Set `FIZZY_PUBLIC_HOST=my.cookedcoke.co` in `.env`, then `docker compose up -d`.

The sign-in limits use the visitor address Cloudflare passes on (`CF-Connecting-IP`), so
keep the student site behind the tunnel. That host only serves the student pages; every staff page is "not found" there, and it
isn't affected by the device lock. Without `FIZZY_PUBLIC_HOST`, the student pages are at
`/me` on the staff site (handy for trying it out, but behind the device lock if that's on).

## 7. Tools

**Tools** in the top bar holds the occasional jobs. Hover or tap **i** to see what a tool
does, and click its name to open it. Tools in red change a lot at once or can't be undone,
and the biggest ones make you type a word (`RESET`, `REVOKE`, `SIGN OUT`) to confirm.
Everything is written to the activity log. Wherever a tool needs a customer, the box
suggests names as you type. Pick from that list (it fills in "Name <email>"), or type an exact
name, email or student number. Anything less exact never acts on its own: the tool stops and
says who you might mean, so a typo can't hit the wrong student.

| Section | Tool | Use it for |
|---|---|---|
| Points | Give points to many | Event prizes: a list of names/emails/student numbers, or everyone |
| | Bonus points for a period | Double-points day: tops up points on purchases between two dates. Tab purchases count by the date they were bought, for the part already repaid. Don't run the same dates twice |
| | Move points | Points went to the wrong person |
| | Reset all points | End of year. Spend history and tab eligibility are kept |
| Tabs | Pause all tabs | Holidays, end of term. Approvals come back when unpaused |
| | Approve all eligible | Approve everyone who's reached $30 with no decision yet |
| | Revoke all tabs | End of year |
| | Add tab charge by hand | Only if the sale isn't in Square at all; otherwise it'd be counted twice |
| | Write off a debt | Clear some or all of what someone owes. Earns no points |
| | Give more time to pay | Push back the due date on all of someone's unpaid charges |
| Customers | Add a customer | New student, teacher, anyone not in the school list |
| | Merge duplicates | Two records for one person: everything moves to the one you keep, including the duplicate's Square profile (its future sales land on the kept person) and its tab approval if the kept one had none |
| | Archive many | Left school or graduated: hidden from search, history kept |
| | Restore archived | Bring them back |
| Reports | Takings summary | Counting up: Square sales, what went on tabs, tab repayments, refunds, items returned from tabs (not money), and anything recorded without Square |
| | Who to chase | Everyone owing, oldest first, with their emails ready to copy |
| | All balances | Everyone who owes or is in credit |
| | Points leaderboard | Printable top-N for events |
| Fun | Random winner | Giveaway draw from buyers in a date range, anyone with points, or everyone. Can be weighted by points |
| Square | Sync now / Re-sync from a date / Find a payment | After an outage, to backfill, or to look up one payment |
| Data | Export CSV / Download backup / Health check | Spreadsheets, a copy of the database, and a list of things to tidy up |
| Staff | Noticeboard | A message for the others at the top of the dashboard |
| | Sign everyone out | A device was lost. Also change `FIZZY_PASSWORD` if the password leaked |

---

## 8. Search and keyboard shortcuts

### Search

Search runs in the browser: each page loads the customer list once, so typing never waits
on the network. That takes about 1–3 ms per keystroke for the full school list. In
Chrome/Edge the highlighted result is pre-loaded in the background, so `Enter` opens it
straight away.

Matching works like [fzf](https://github.com/junegunn/fzf), plus typo tolerance:

| You type | Finds | Why |
|---|---|---|
| `nor` | Nora …, Norah … | Start of a name |
| `zhang nora` | Nora Zhang | Words in any order |
| `nozh` | **No**ra **Zh**ang | Letters in order with gaps, like fzf. Word starts and unbroken runs score higher |
| `odonnell` | Jemima O'Donnell | Punctuation doesn't matter |
| `zhnag`, `wiliam`, `jaiden` | Zhang, William, Jayden | Misspellings: swapped, missing or wrong letters (1 mistake, or 2 in long words) |
| `0830937` or `rns0830937` | that student | Student number |

Matched letters are underlined in the results. The search in the top bar, on the Customers
page and in the Tools customer boxes all use the same matching. The Python
(`fizzy/fuzzy.py`) and browser (`fizzy/static/fuzzy.js`) copies are tested to rank
identically.

### Shortcuts

Press **`?`** on any page for the list. Buttons and sections with a shortcut show its key
in a small grey box (e.g. **Approve `A`**), and hovering over anything with a shortcut, including
the top-bar links, names it. Shortcuts don't fire while you're typing in a box; `Esc` gets
you out of the box first. The key boxes are hidden on phones.

| Key | Does |
|---|---|
| `/` or `S` | Search |
| `↑` `↓` `Enter` | Pick a search result |
| `1`–`8` | Open that search result (results are numbered). While searching by student number, use `Alt`+number, since plain digits are part of the number |
| `Esc` | Leave the box you're in, then press again to go back to the dashboard |
| `K` | Customer: claim the sale just rung up in Square with no customer (`Enter` claims the newest, `1`–`6` pick one) |
| `R` | Customer: record repayment (amount pre-filled; `Enter` saves) |
| `P` | Customer: adjust points (`Tab` to the reason, `Enter` saves) |
| `O` | Customer: redeem the reward they can have (asks first) |
| `A` | Customer: approve tab |
| `X` | Customer: reject, or revoke if approved (asks first) |
| `N` | Customer: note for the tab decision |
| `H` `L` `E` | Customer: decision history, activity log, edit |
| `W` | Switch who's on the counter |
| `U` | Square page: sync now |
| `1`–`8` | Tools page: jump to a section |
| `G` then `D` `C` `S` `R` `T` `I` `L` `,` | Go to Dashboard, Customers, Square, Rewards, Tools, Import, Log, Settings |
| `Ctrl` + `Enter` | Submit the form you're in |
| `?` | Show shortcuts |

---

## 9. Setting up

Do these once, in order.

### 9.1 Fizzy itself

See [Running it](#10-running-it). You'll need a `.env` file. Copy `.env.example` and fill in
at least `FIZZY_PASSWORD` and `FIZZY_SECRET_KEY`:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"   # makes a secret key
```

### 9.2 Import the school list

**Import** page (or `python -m fizzy import directory.csv`). The CSV needs a name column
(`Cleaned Name` or `Name`) and `Email`. Only those two are stored; every other column is
ignored. Emails must look like `rns<digits>@reddamnorthshore.nsw.edu.au`. Re-importing a
newer list adds new students and updates changed names, and never deletes anyone.

### 9.3 Put everyone into Square

1. **Customers → Square import CSV: new only** downloads everyone who isn't linked to a
   Square profile yet, in Square's customer-import format. The first time, that's everyone.
   Names are split into first and last at the first space; fix any odd ones in Square
   afterwards.
2. Square Dashboard → Customers → Import customers → upload it.
3. On the next sync, Fizzy links each Square profile back to its Fizzy customer.

**Adding people later** (new students, a new school list): do the same. *New only* leaves out
everyone already in Square, so nobody gets a duplicate profile. Wait for a sync between
imports, or people imported but not yet linked will be in the next file too. **Everyone**
exports the whole list; importing it again would give Square a second copy of everyone, so
only use it for the very first import.

### 9.4 Square: the discount, the item, and payment types

In the Square Dashboard → Items:

1. **Discounts → Create discount:** name **Fizzy Tab**, **100%**, applied manually. (Fizzy
   matches any discount whose name contains "fizzy tab".)
2. **Items → Create item:** name **Tab repayment**, **leave the price blank** (so Square asks
   for the amount at checkout), and don't track inventory. The name must be exactly that,
   though capitals don't matter. Use this item rather than Square's "Custom amount" key,
   because Fizzy recognises repayments by the item's name.

Square app → Settings → Checkout → Payment types:
- **Other Payment Types**: not used by Fizzy. A sale paid with Other counts as a normal sale.
- **House Account**: leave disabled ([why](#why-not-square-house-accounts)).

### 9.5 Square API access

[Square Developer Dashboard](https://developer.squareup.com/apps) → create an application →
**Production**:

| Setting | Where | `.env` |
|---|---|---|
| Access token | Credentials | `SQUARE_ACCESS_TOKEN` |
| Location ID | Locations | `SQUARE_LOCATION_ID` |
| Webhook (optional) | Webhooks → Add subscription. URL `https://fizzy.cookedcoke.co/square/webhook`; events `order.created`, `order.updated`, `payment.created`, `payment.updated`, `refund.created`, `refund.updated`, `customer.created`, `customer.updated` | Signature key → `SQUARE_WEBHOOK_SIGNATURE_KEY` |

The token needs read access to Payments, Orders, Customers and Merchant profile (locations).
Fizzy never writes to Square.

`SQUARE_SYNC_FROM=YYYY-MM-DD` sets how far back the **first** sync reaches. Use it to give
points for sales made before Fizzy existed; otherwise it starts from the day before.

### 9.6 Test before going live

Test this once on the real Terminal, with a real customer attached each time. After each step, open Fizzy's **Square** page and
press **Sync now**.

1. **Normal sale:** 50c cash. It shows as *Sale*, and the customer gets 50 points.
2. **Tab sale:** a 50c item with the **Fizzy Tab** discount, with $0 charged as cash. It
   shows as *Tab charge* with tender "Fizzy Tab discount", and the customer owes $0.50. The
   $0 cash payment is ignored, so no 0-point entries appear.
3. **Part tab:** two 50c items, the discount on one only, and 50c paid by card. The customer
   owes another $0.50, and the paid part gives 50 points.
4. **Repayment by card:** a **Tab repayment** of $1.00, paid by card. Owing goes to $0, and
   points go up by 100.
5. **Repayment plus a purchase:** a 50c tab sale first, then a Tab repayment of 50c plus a
   50c item in one cash sale. Fizzy shows two rows: *Tab repayment* 50c and *Sale* 50c.
6. **Return from a tab sale:** return the item from step 2 in Square. The tab drops by 50c.
7. **Refund a normal sale:** refund step 1. The 50 points come off.

If step 2 doesn't appear, check the discount's name contains "Fizzy Tab", the customer was
attached, and the token can read Orders. If webhooks are on, check the order events are
subscribed. Polling catches it within 5 minutes regardless.

---

## 10. Running it

### Docker on the home PC (recommended)

Needs Docker Desktop (Windows/macOS) or Docker Engine (Linux).

```sh
git clone <repo> fizzy && cd fizzy
cp .env.example .env              # fill it in
docker compose up -d --build
docker compose logs -f fizzy      # watch it start
```

`docker-compose.yml` runs two containers: **fizzy** (the app) and **cloudflared** (the
tunnel). No ports are opened on the PC.

On Linux, the `data` and `backups` folders on the PC can be owned by root or your own user.
The container fixes their ownership when it starts, then runs Fizzy as an unprivileged
`fizzy` user (uid 10001).

**Updating:** `git pull && docker compose up -d --build`. The database lives in `./data` and
survives rebuilds. New tables and columns are added automatically on start, so updating
never needs a manual database step.

### Cloudflare Tunnel

1. Cloudflare Zero Trust → Networks → Tunnels → **Create a tunnel** (cloudflared). Copy the
   token into `CLOUDFLARE_TUNNEL_TOKEN`.
2. Add a public hostname: `fizzy.cookedcoke.co` → service `http://fizzy:8000`. For the
   [student site](#6-student-site), add a second one (e.g. `my.cookedcoke.co`) to the same
   service.
3. Optional: a Cloudflare WAF rate-limit rule on `/login`. Fizzy limits wrong passwords
   itself:
   - **Browsers that have signed in before** (like the counter) are only limited by their
     own mistakes, so nobody else on the school network can lock them out.
   - **Unknown browsers** get 10 tries per 15 minutes per network, with an overall cap.
4. Don't put Cloudflare Access in front of `fizzy.cookedcoke.co` itself. Square's webhook
   has to reach `/square/webhook` there, and Fizzy checks every request against Square's
   signature. To restrict which devices can use Fizzy, see the next section.

To try it locally without the tunnel, add `ports: ["127.0.0.1:8000:8000"]` to the `fizzy`
service, set `FIZZY_SECURE_COOKIES=0`, and open http://localhost:8000.

### Locking it to the counter device (optional)

A web app can't see a device's **MAC address**: it never leaves your local network, so it
doesn't survive the trip through the internet and Cloudflare. Fizzy does the next best
thing with **registered devices**:

| Device | Goes to | What it needs |
|---|---|---|
| Registered (e.g. the counter iPad) | `fizzy.cookedcoke.co` | The staff password |
| Anything else | Automatically sent to `fizzy-remote.cookedcoke.co` | Cloudflare Access (an emailed one-time code for Will, Mike or Frankie), **then** the staff password |

- **How a device is registered:** it opens a one-time link and gets a secret cookie that
  lasts about a year. The cookie is refreshed each time the device is used.
- **On `fizzy-remote`:** Fizzy checks Cloudflare's signed sign-in token itself, so the
  address can't be reached around Access.
- **The log** records each sign-in with the device name or Access email.

**Setup**

1. **Second hostname:** in the same tunnel, add a public hostname `fizzy-remote.cookedcoke.co`
   → `http://fizzy:8000`. Use a single-level subdomain like this one, because Cloudflare's
   free certificate doesn't cover `x.fizzy.cookedcoke.co`.
2. **Access application:** Zero Trust → Access → Applications → **Add an application** →
   Self-hosted.
   - Domain: `fizzy-remote.cookedcoke.co`.
   - Policy: **Allow**, with your three email addresses.
   - Login method: **One-time PIN**.
   - Session duration: whatever suits, e.g. 24 hours.
3. **Copy two values into `.env`:**
   - The application's **Audience (AUD) tag** → `CF_ACCESS_AUD`.
   - Your team domain → `CF_ACCESS_TEAM_DOMAIN`, e.g. `cookedcoke.cloudflareaccess.com`
     (Zero Trust → Settings → Custom Pages shows the team name).
4. **Turn it on:** set `FIZZY_ACCESS_HOST=fizzy-remote.cookedcoke.co`, then `docker compose up -d`.
5. **Register the counter device:**
   1. On any device, open `fizzy-remote.cookedcoke.co`, then Settings → **Devices** →
      name it → **Make link**.
   2. Open that link on the counter device within 15 minutes and press **Register this
      device**.
   3. Or, from the PC: `docker compose exec -u fizzy fizzy python -m fizzy device-link "Counter iPad"`.

**Good to know**

- **What counts as registered** is that browser on that device. Another browser, a private
  window, or clearing site data means registering again.
- **Lost or replaced device:** Settings → Devices → **Remove**. Also use **Tools → Sign
  everyone out** if it was signed in.
- **Turning it off:** remove `FIZZY_ACCESS_HOST`, then `docker compose up -d` (a plain
  `restart` doesn't re-read `.env`). Everything goes back to
  password-only.
- **Heavier alternative:** Cloudflare's WARP client with device posture checks can tie
  access to specific managed devices, but it needs an app installed and managed on each
  device. The registered-device cookie is simpler for one counter screen.

### Backups

- **Automatic:** once a day Fizzy writes a complete snapshot to `FIZZY_BACKUP_DIR` (in
  Docker, `./backups`) and keeps the newest 30.
- **External SSD:** change the left side of the backups volume in `docker-compose.yml`,
  e.g. `- /mnt/ssd/fizzy-backups:/backups` or `- E:/fizzy-backups:/backups`, then
  `docker compose up -d`.
- **By hand:** Settings → **Back up now** or **Download a copy**; or
  `python -m fizzy backup DIR`.
- **Restore:** stop Fizzy (`docker compose stop fizzy`), copy the backup over
  `data/fizzy.db`, delete `data/fizzy.db-wal` and `data/fizzy.db-shm` if they exist, then
  start it again.

Backups contain students' names and emails. Keep them somewhere private.

### Without Docker (Linux, systemd)

```sh
sudo useradd -r fizzy && sudo mkdir -p /opt/fizzy && sudo chown fizzy /opt/fizzy
sudo -u fizzy git clone <repo> /opt/fizzy && cd /opt/fizzy
sudo -u fizzy python3 -m venv .venv && sudo -u fizzy .venv/bin/pip install -r requirements.txt
sudo -u fizzy cp .env.example .env   # edit; set FIZZY_BACKUP_DIR
sudo cp deploy/fizzy.service /etc/systemd/system/ && sudo systemctl enable --now fizzy
```

Then install `cloudflared` as a service with the hostname pointing at `http://localhost:8000`.

### Command line

```sh
python -m fizzy import directory.csv   # import the school CSV
python -m fizzy backup /path/to/dir    # write a backup now
python -m fizzy sync                   # run one Square sync
python -m fizzy device-link "Counter iPad"   # registration link for the device lock
```

In Docker, run them inside the container as the `fizzy` user (`-u fizzy` matters: files
written as root could stop the app writing to its database). Paths are inside the
container, so put files in `./data` (that's `/data` inside):

```sh
cp directory.csv data/
docker compose exec -u fizzy fizzy python -m fizzy import /data/directory.csv
docker compose exec -u fizzy fizzy python -m fizzy backup /backups
```

---

## 11. Configuration reference

All configuration is environment variables, in `.env`. Never commit `.env`. A setting left
blank (`KEY=`) counts as not set and uses its default.

| Variable | Default | What it does |
|---|---|---|
| `FIZZY_PASSWORD` | *(required)* | The shared staff password |
| `FIZZY_SECRET_KEY` | *(required)* | Signs login cookies. Changing it signs everyone out |
| `FIZZY_STAFF` | `Will,Mike,Frankie` | Names in the "on counter" list |
| `FIZZY_TZ` | `Australia/Sydney` | Time zone for display and due dates |
| `FIZZY_DB` | `data/fizzy.db` | Database file (Docker: `/data/fizzy.db`) |
| `FIZZY_BACKUP_DIR` | *(off)* | Daily backup folder (Docker: `/backups`) |
| `FIZZY_BACKUP_KEEP` | `30` | How many daily backups to keep |
| `FIZZY_SECURE_COOKIES` | `1` | Set `0` only for plain `http://localhost` testing |
| `SQUARE_ACCESS_TOKEN` | *(off)* | Square production access token. Without it, nothing syncs |
| `SQUARE_ENVIRONMENT` | `production` | `sandbox` for Square's test environment |
| `SQUARE_LOCATION_ID` | *(all)* | Only read this location's sales |
| `SQUARE_WEBHOOK_SIGNATURE_KEY` | *(off)* | Turns on webhooks |
| `SQUARE_WEBHOOK_URL` | `https://fizzy.cookedcoke.co/square/webhook` | Must exactly match the URL in Square, or signatures fail |
| `SQUARE_POLL_SECONDS` | `300` | How often to poll Square |
| `SQUARE_SYNC_FROM` | *(1 day back)* | `YYYY-MM-DD`: how far back the first sync reaches |
| `SQUARE_TAB_DISCOUNT` | `fizzy tab` | A sale with a discount whose name contains this goes on the tab (the discounted amount) |
| `SQUARE_REPAYMENT_ITEM` | `tab repayment` | Item name that marks part of a sale as a tab repayment |
| `SQUARE_TAB_TENDERS` | *(off)* | Optional extra: payment types that also mean "on the tab", e.g. `EXTERNAL` (Other Payment Types) or `SQUARE_ACCOUNT` (House Account). Only if the discount can't be used |
| `FIZZY_HOST` | `fizzy.cookedcoke.co` | Main address; used in device registration links |
| `FIZZY_PUBLIC_HOST` | *(off)* | The student site's address, e.g. `my.cookedcoke.co`. Only student pages are served there |
| `FIZZY_ACCESS_HOST` | *(off)* | Turns on the device lock. Unregistered devices are sent here (behind Cloudflare Access) |
| `CF_ACCESS_TEAM_DOMAIN` | — | Your Access team domain, e.g. `cookedcoke.cloudflareaccess.com` |
| `CF_ACCESS_AUD` | — | The Access application's Audience (AUD) tag |
| `CLOUDFLARE_TUNNEL_TOKEN` | — | Used by the `cloudflared` container |

The tab limit, days to pay and spend threshold live in the database and are changed on the
**Settings** page, not in `.env`.

---

## 12. Data and privacy

Most customers are under 18, so Fizzy keeps the minimum:

- **Stored:** name, school email, Square customer ID, tab decisions (with notes), tab
  charges and repayments, points history, and Square payment and order details (amount,
  items, payment type, time).
- **Not stored:** year group, phone numbers, addresses, or anything else in the school CSV.
- **Who can see it:** staff with the password (optionally only from registered devices or
  through Cloudflare Access). Each student can see their own points, tab and activity on the
  student site, once staff set a passcode for them at the stall. Other signed-in students
  see them only as *first name + last initial* with points earned, on the leaderboard, and
  only if they have a passcode.
- **Passcodes** are stored encrypted with `FIZZY_SECRET_KEY`, so staff can look one up but a
  copy of the database or a backup alone doesn't reveal them. They're never written to the
  logs. Changing `FIZZY_SECRET_KEY` makes existing passcodes unreadable: set new ones.
- **Never committed to git:** `.env`, the database, backups and CSVs (see `.gitignore`).
- **Leaving school:** archive them (Tools → Archive many). Their history stays for the
  records, hidden from search.

---

## 13. Troubleshooting

| Problem | Check |
|---|---|
| "Square isn't connected" on the dashboard | `SQUARE_ACCESS_TOKEN` is set in `.env`, then `docker compose up -d` |
| "Last Square sync failed" | The error text is shown on the Square page. Usually an expired or wrong token, or the wrong `SQUARE_ENVIRONMENT` |
| A sale didn't give points | The customer probably wasn't attached on the Terminal. **Square** page → *Only unlinked* → **Assign to…** |
| A tab sale isn't on their tab | No customer attached: it's under **Needs attention**, so assign it there. No discount: see [Fixing a mistake](#fixing-a-mistake) |
| A repayment didn't clear the tab | No customer attached: assign it under **Needs attention**. Otherwise the item must be called exactly **Tab repayment** |
| A customer shows "Not in Square yet" | Import the Square CSV (8.3), then sync. Or paste their Square customer ID into **Edit customer** |
| Webhooks not arriving | `SQUARE_WEBHOOK_URL` must match Square's subscription URL exactly; no Cloudflare Access on `/square/webhook`. Polling still catches everything within 5 minutes |
| Can't log in after an update | Check `FIZZY_SECURE_COOKIES=1` and that you're using `https://` |
| Counter device keeps getting sent to `fizzy-remote` | Its registration was lost (browser data cleared, different browser) or removed. Register it again |
| "Cloudflare Access required" on `fizzy-remote` | Check `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` match the Access application, and that Access is actually in front of that hostname |
| Everything slow at the counter | Fizzy pages render in ~5 ms; check the PC is awake and the tunnel is up (`docker compose logs cloudflared`) |
| Something looks wrong with the data | **Tools → Health check** |

---

## 14. Development

```sh
pip install -r requirements.txt pytest
python -m pytest                      # rules, Square processing, tools, search (needs node for the JS check)
FIZZY_PASSWORD=dev FIZZY_SECRET_KEY=dev FIZZY_SECURE_COOKIES=0 uvicorn fizzy.app:app --reload
```

**Stack:** Python 3.13, FastAPI, Jinja templates, SQLite (WAL mode). One process with a
background thread for Square polling and backups; server-rendered pages; two small
dependency-free scripts; no build step. Runs comfortably on a Raspberry Pi.

| File | What's in it |
|---|---|
| `fizzy/logic.py` | Tab and points rules: repayment order, overdue, status, refunds |
| `fizzy/square.py` | Square API client, payment/refund processing (safe to repeat), webhook check |
| `fizzy/fuzzy.py` | Name matching (fzf scoring + typo tolerance) |
| `fizzy/static/fuzzy.js` | Browser copy of `fuzzy.py`. **Change both together**; a test compares them |
| `fizzy/static/app.js` | In-browser search, keyboard shortcuts, `?` panel |
| `fizzy/tools.py` | The Tools page: each tool is a `TOOLS` entry plus an `h_<id>` handler |
| `fizzy/app.py` | Routes, login, background jobs |
| `fizzy/customers.py` | CSV import, search, Square export |
| `fizzy/db.py` | Schema, settings, audit log, backups |
| `fizzy/devices.py` | Registered devices and Cloudflare Access token checks |
| `fizzy/web.py` | Shared page helpers |
| `fizzy/templates/`, `fizzy/static/style.css` | Pages and styling |

**Conventions**
- Money is stored as integer **cents**, and times as UTC ISO strings, shown in `FIZZY_TZ`.
- Square payments and refunds are processed at most once (keyed by Square ID), so webhooks
  and polling can overlap safely.
- Anything that changes data writes to `audit_log`.
- Keyboard shortcuts: add `data-key="x"` (and optionally `data-key-label`, `data-confirm`) to
  any button, field or `<details>`. It then works, gets a hover tip and appears in the `?`
  panel automatically. Add a visible `<kbd>X</kbd>` in the template next to it. Avoid `s`,
  `f`, `g` and `/`, which are taken.

---

## License

[MIT](LICENSE) © 2026 Cooked Coke Co.

The MIT licence covers the code. The Cooked Coke Co. name and logo (`fizzy/static/logo.svg`,
the favicons) are the business's own branding and aren't included in that licence.

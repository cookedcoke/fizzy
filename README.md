# Fizzy

Customer directory, points and pay-later tabs for **Cooked Coke Co.**, the student food and
drink stall at Reddam House North Shore. Staff use it at **fizzy.cookedcoke.co**; students can
check their own points, rewards and tab on a separate, read-only [student site](#7-student-site).
It runs on a home PC through a Cloudflare Tunnel (no open ports at home).

Square stays the till. Fizzy reads Square's sales, gives points, keeps track of who owes
what, and answers the one question that matters at the counter: *can this person put it on
their tab?*

**Contents**

1. [At the counter](#1-at-the-counter)
2. [Rules](#2-rules)
3. [How Square and Fizzy fit together](#3-how-square-and-fizzy-fit-together)
4. [Pages](#4-pages)
5. [Profit](#5-profit)
6. [Rewards (points shop)](#6-rewards-points-shop)
7. [Student site](#7-student-site)
8. [Tools](#8-tools)
9. [Search](#9-search)
10. [Setting up](#10-setting-up)
11. [Running it](#11-running-it)
12. [Configuration reference](#12-configuration-reference)
13. [Data and privacy](#13-data-and-privacy)
14. [Troubleshooting](#14-troubleshooting)
15. [Development](#15-development)

---

## 1. At the counter

**The quick way: claim it in Fizzy (type the name once)**
1. In Fizzy: type their name in the search box and pick them. Their page shows points, tab and rewards.
2. On the Square Terminal, ring it up **without adding a customer** (with the Fizzy Tab
   discount or the Tab repayment item if needed) and take payment.
3. **Then** press **Claim sale** on their page. Fizzy gives them the sale rung up in Square in the
   **30 seconds before** the press. Points, tab charges and repayments land straight away.

Always ring it up first, then press. Pressing first links nothing (use **Pick a sale** for that).

You don't have to wait on their page. If Square hasn't sent the sale yet, a bar under the header on
every page says *Waiting for Square to send Mia Patel's sale*. Serve the next person: Mia's sale
links as soon as Square sends it (Fizzy keeps watching for 3 minutes), then the bar says *Mia
Patel's sale is in: $4.50 paid*, or *No sale came in for Mia Patel* with a **Pick a sale** link.
Pressed it by mistake? **Undo** on the bar stops the wait. (A sale it already linked stays linked; fix
that on the Square page.) Pressing again while it's waiting does nothing new. A second purchase works:
ring it up, then press again. But once a press has finished, pressing again is a new press: it takes
the next sale rung up in the 30 seconds before it, even the next person's. So press once.

Each sale goes to the first press in the 30 seconds after it was rung up, by Square's times, so it
doesn't matter what order or how late Square sends sales. The one thing it can't sort out: a sale
nobody pressed for (say, someone not in Fizzy) rung up in the same 30 seconds as Mia's, that Square
sends before hers. Mia's press takes it (the newest in), so fix that on the Square page.

**A teacher, or someone without an account (or who'd rather not record it)?** Mark their sale
**No account** before the next person's press: on the *New sale* pop-up, on **Pick a sale**, or on
the **Square** page. Otherwise the next press may take it. A No account sale still counts in takings,
profit and reports, but nobody gets it: no points, no tab, never offered to claim. Marked one by
mistake? **Undo** next to it on the Square page.

**Pick a sale** (next to Claim sale) lists the latest sales from the last 6 hours with no customer,
newest first: click one to give it to them. Use it when the button missed one (older ones are on the
**Square** page). If a tab sale is more than they're allowed, the list says so before you claim it.

**Or let it pop up:** if their page is open when you take the payment, a *New sale* box pops up
within a few seconds: **Claim** gives it to them, **Not theirs** dismisses it, **No account** marks it
as nobody's (above). (It only offers sales
from about the time the page was opened, never older ones, never claims anything without a click,
and stays away while a Claim sale press is waiting for anyone, so the two never argue.)

Forget to claim? Nothing breaks: the sale waits on the **Square** page to be assigned later.

**Or attach the customer in Square** (the steps below), and Fizzy picks it up by itself.

**Normal sale**
1. On the Square Terminal, **attach the customer's profile**, then take payment as usual.
2. Points arrive in Fizzy automatically (within seconds with webhooks, otherwise within 5 minutes).

**Someone wants to put it on their tab**
1. In Fizzy: type their name in the search box and pick them (arrow keys and `Enter` work too).
2. Read the answer at the top of their page:
   - **Green tick:** they can, up to the amount shown.
   - **Red cross:** no (overdue, tab turned off, or at the limit). The line underneath says why.
   - **Grey dash:** not yet (tab score under the minimum, or tabs are paused).
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
Switch it with the dropdown. **Lock** signs the device out.

---

### Install it like an app

**Phones can't open the staff site:** a phone browser gets a page pointing it at the Fizzy iPhone
app (`ios/`), which is built for a small screen. iPads, Android tablets, computers and the Mac app
are unaffected, and the student site works on any phone.

Fizzy (on an iPad) and the student site (on a phone) can be added to the home screen. They then open full
screen with their own icon, like an app. On iPhone or iPad: Safari → Share → **Add to Home Screen**. On
Android: Chrome → ⋮ → **Install app**. Pages are always loaded fresh (nothing about students is
stored on the phone). If the home PC or the internet is down, you get a "You're offline" page
with a Try again button instead of a browser error.

On iPhone, a home-screen app keeps its own sign-in, separate from Safari's, so sign in again
inside it (and register the device again if you use device registration).

### Owner password and who can do what

There are three levels: **anyone signed in**, the **PIN**, and the **owner password**. Each one
can do everything below it. The owner password lives only in `.env` on the home PC as
`FIZZY_ADMIN_PASSWORD`, so nobody can change or remove it from inside Fizzy.

**Settings → Who can do what** (owner only) sets the level for each kind of action: claiming
sales, giving points, rewards, tabs, restocks, tools, downloads, imports, settings and so on.
It also sets what counts as a **big change by hand** (by default more than 500 points, more
than $50, or anything for everyone at once) and the level that needs. The PIN and that table
are always owner only. Things set to "PIN" are open while no PIN is set, and "Owner only"
falls back to the PIN while there's no owner password.

The pop-up just says **Enter passcode**. Typing the PIN where the owner's is needed is simply
a wrong passcode. The PIN stays unlocked for 10 minutes, the owner password for 5, and both
lock again when someone else is put on the counter. Every owner unlock and wrong try is in the
activity log.

### Customer display

A second screen facing the queue. Open `/display` on it (the Mac app in `mac/` does this for you and
locks the window so it can't be closed by accident). It shows "Say your name when you pay", double
points days and rewards between customers. When you open a customer on the staff screen it shows
their first name, points, any reward they can have now and whether they can use their tab, straight
away (the staff page sends it directly, not through the server). When a Square sale lands, it shows
"+300 points" within a few seconds. It goes back to the idle screen 90 seconds after the last change,
or when you go to any other page. It also shows what they owe on their tab, including anything overdue (turn that off in Settings, Customer display). Never shown: surnames or the score breakdown.

**Choosing what it shows.** The **Display** menu in the staff header (a little screen icon on
smaller screens) picks what the display shows. **Auto** (the default) is the above: a customer's
page shows them, any other page shows the idle screens taking turns. Or hold it on one screen:
**Welcome**, **On the shelf**, **Popular today**, **Top this month** or **Coming up** (one with nothing
on it yet shows Welcome instead), **Rotate all** (the idle screens taking turns, whatever page is
open), or **Hidden** (just the logo). While it's on anything but Auto, moving between pages, customers'
pages too, doesn't change the display. The choice belongs to the device driving the display (it's
kept in that browser, and every staff tab open in it shows the same choice), every page sends it
again as it opens, and the display remembers it too, so reloading it comes back on the same screen. The Mac app's Display menu does the same: Hide is
Hidden, Show is Auto.

**Sign-in code.** On a customer's page, press **Sign-in code** (or Q) and a QR code appears on the
display. Scanning it signs them in to the student site without their passcode (or before they have
one). It only appears when you press the button, works once, lasts a minute, and the display says
"Signed in on a phone" when it's used, so a photo taken from the queue is no good and a stolen one
gets noticed. It shows even when the display is held on one screen or hidden: that customer and
the code come up, and the held screen comes back once the code is used or runs out. **Edit
customer, Sign out their phones** signs them out everywhere.

### Security keys

A security key (a YubiKey or similar) or a phone passkey can sign in instead of the staff password,
and one key can be shared by everyone on the counter. Add it in **Settings, Security keys** (owner
passcode), then press **Use a security key** on the sign-in page. You still pick who's on the
counter, and the PIN and owner passcode work the same as before. A key only works on the address it
was added on, so add it once on fizzy.cookedcoke.co and again on the Cloudflare Access address if you
use that. Lost a key? Remove it in Settings and it stops working straight away.

### Scanning student cards

Student ID cards have a barcode with the student's RNS number. Plug a USB barcode scanner
(any cheap "keyboard" one) into the counter PC and scan a card on any Fizzy page: that student's
page opens. No setup and nothing to link, because Fizzy already knows everyone's RNS number from
the school list. If the cursor is in a text box, the number goes into that box instead (in the
search box, Enter still opens the student).

## 2. Rules

| Rule | Default | Change it in |
|---|---|---|
| Most a customer can owe | $10 | Settings |
| Days to pay each tab charge | 7 | Settings (applies to new charges) |
| Tab score needed to have a tab | 20 (new customers start on 30) | Settings |
| Points | 1 per cent paid ($3 = 300 points) | fixed |

**Tab status**

| Status | Meaning | Can use tab? |
|---|---|---|
| Tab on (shown as *Tab $5.00*) | Tab score at or over the minimum | **Yes**, up to the limit their score gives |
| Score too low | Tab score under the minimum (late payments, write-offs) | No. Comes back as the score does |
| Blocked (overdue) | Has a charge past its due date | No. Clears itself when paid |
| Paused (all tabs) | Tabs are paused for everyone (Tabs, Pause all tabs) | No |
| Tab off | Staff turned it off | No, until staff turn it back on |

- **Nobody waits for approval.** Everyone has a tab sized by their tab score, from their first
  day: $2.50 for a new customer with the default $10 maximum.
- **Staff have the final say.** On the customer page, **Tab limit** puts someone in a limit band
  ($2.50, $5, $7.50 or $10) or sets a score adjustment by hand. Either way it's a *Staff adjustment*
  line in their tab score, so the limit always matches the score: paying late still brings it
  down. **Turn tab off** stops their tab whatever the score. Every change, with its note, is kept
  in the customer's tab history.
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

### Tab score

Every customer has a **tab score** from 0 to 100, worked out from their own history at the stall
and shown, with every point explained, on their customer page and on the student site:

| | |
|---|---|
| Everyone starts at | 40 |
| Spending | +1 per $2.50, up to +20 |
| Buying on different days | +1 a day, up to +15 |
| Each tab paid off on time | +5, up to +25 |
| Each late payment in the last 2 months / before that | −12 / −5 |
| Money overdue right now | −25 |
| Each debt written off | −15 |
| First purchase under 2 weeks ago | −10 |

| Staff adjustment | Whatever staff set on the customer page, −100 to +100 |

It does two things:

- **Who has a tab:** everyone whose score is at or over the **Minimum tab score** (20 by default,
  in Settings), unless staff turned their tab off.
- **How much they can owe:** their limit comes from their score. 80+ gets the
  **Highest tab limit** from Settings, 65+ three quarters of it, 50+ half, below that a quarter
  (rounded down to 50c). Paying on time raises it; paying late, or owing overdue money, lowers it
  straight away.

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
| Setup | An account created one customer at a time in Square, with a spending limit and a billing method | Everyone is imported once; everyone has a small tab that grows with their tab score |
| Collecting money | Square invoices (one-off or recurring) or an automatic charge to a card on file. Square says house accounts can't take repayment in instalments, and a sale can't be split between House Account and another payment type | Cash or card at the counter, any amount, any time |
| Overdue | Square sends reminders; you lock accounts by hand | Blocked automatically until paid, with a permanent late record |
| Points, the tab score, limits that follow it | None | Built in |
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
  **Tab by hand → Add tab charge by hand** on their page.
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

### Where things live

Every action is on the page of the thing it changes: something for one person is on their page,
something for customers as a whole is on Customers, and so on. Rare or dangerous ones sit lower down on
that page, under **More**. The header has six tabs, then **More** (Settings, Log, Tools). **Tools**
lists every action Fizzy has, with a search box, each linking to where it lives; every tool form is on
Tools too, so you can run it from either place (from its own page, you land back there afterwards).

| Page | What's on it |
|---|---|
| **Dashboard** | Search; noticeboard (**Add a note** / **Edit note**); warnings (Square not connected, tabs paused). **Fizz**, a small chat about the stall (see Fizz below; **Full screen** opens `/ask`). **Today's sales:** sold, taken, orders and average, customers, put on tabs and repaid; sold by hour against the same day last week; the last 7 school days; top items; latest sales. It refreshes itself every minute while nobody's touching it (not while a note or a question is being written, or an answer is coming). **Tabs:** totals owed and overdue; **Needs attention** (rule breaks, tab sales with no customer); overdue list; due in the next 2 days; top customers |
| **Customers** | Search with a results table; **+ Add customer** (a search that finds nobody offers *Add "what you typed"*, and a card scan nobody has lands here with their school email filled in). **More:** import the school list (preview, then confirm), exports (new people for Square, everyone for Square, the customers spreadsheet), give points to many, merge duplicates, archive many, restore archived, make and delete test accounts |
| **Customer** | Can-they-use-a-tab answer; points, owing, overdue, spend; record repayment; tab limit (pick a band or set a score adjustment, turn the tab off or on); **Tab by hand** (add a tab charge by hand, give more time to pay, write off a debt, all already pointed at them); tab score with every line explained; adjust points; **Move points** to someone else; tab charges with due dates and status; repayments; points history; Square payments; tab history; activity log; edit (name, Square ID, archive) |
| **Claim sale** (button on a customer) | Gives this customer the Square sale rung up in the 30 seconds before the press, or waits for Square to send it (the bar under the header, with Undo) |
| **Pick a sale** (button on a customer) | The latest Square sales with no customer; click one to give it to this customer |
| **Sales** | Two sub-tabs. **Sales** (`/transactions`, below): every checkout, plus the **Takings report**. **Square** (`/square`): connection status, last sync, **Sync now**; how payments are recognised; recent payments with the **Counted as** override and raw data; filter to payments with no customer; **No account** on a sale with nobody on it (it still counts, nobody gets it), with **Undo**; under More, re-sync from a date, import sales history, find a Square payment, export Square payments. `/sales` goes to Sales |
| **Tabs** | Who owes (everyone owing, most overdue first), overdue, due in the next 2 days; **Who to chase**; **Tab rules** (tab limit, days to pay, minimum tab score); under More, all balances, exports (tab charges, repayments, tab decisions), pause all tabs, turn off all tabs |
| **Rewards** | Live rewards and how many were redeemed; **Start a reward**; **Schedule double points** (one day, a few, or every week); weekly double points; custom badges and events; under More, bonus points for a period, points leaderboard, random winner, export points history, reset all points |
| **Money** | Two sub-tabs: **Money** (restocks and payouts) and **Profit** (item costs, section 5) |
| **Settings** (More) | Set-once things: who can do what, updates, Fizz's Gemini key, term dates, closed days, PIN, maintenance mode, backups (back up now, download a copy), health check, sign everyone out, security keys, devices. Links to Tab rules (on Tabs) and the customer display message |
| **Customer display** (header Display menu, *Edit message*; `/display/settings`) | The message between customers, what's on the shelf, show what they owe |
| **Log** (More) | Every action by every staff member, newest first; export to CSV |
| **Tools** (More) | Every action, searchable, each linking to its home; and every tool form (section 8) |

**The sales numbers**, all worked out from what Square sync has stored:
- **Sold:** the value of goods sold, including what went on tabs.
- **Taken:** money actually received - Square sales and tab repayments, plus repayments
  recorded by hand, minus refunds. A tab sale adds nothing until it's paid off.
- **Orders:** checkouts with something sold (a repayment on its own isn't an order).
- **vs last week:** compared with the same weekday last week, up to the same time of day.

The desktop header also shows the time (and the date on wider screens).

---

### Transactions

**Sales** in the top bar (its **Sales** sub-tab, `/transactions`) lists every Square checkout with who it was, what they got, how
they paid, any tab or refund, and the points it earned, which Square's own list can't show.
Pick dates (today by default), filter to sales, tab sales, tab repayments or ones with no
customer, and search by customer or item. Checkouts with nobody on them have an "Assign to…"
box right there. Tab money recorded by hand in Fizzy (and write-offs) shows up too.

### What the site no longer spells out

The pages are kept to labels and buttons. What they used to explain:

- **Repayments.** In Square: attach the customer, add the *Tab repayment* item, type the amount and take cash or card. Fizzy records it by itself. The repayment form on a customer page is only for money that didn't go through Square.
- **Tab limits.** Each person's limit comes from their tab score: 80+ gets the highest limit, 65+ three quarters, 50+ half, below that a quarter. Paying on time raises the score and paying late lowers it. 1 point per cent; tab buys earn their points once paid off.
- **Account types** (owner only). Admin accounts are protected: by default only the owner can change their points, tab or details. Test accounts are for trying things out, and stay out of leaderboards, reports and streaks.
- **Student passcodes.** Anything 4+ characters. Students sign in on the student site with their name or student number.
- **Rewards.** Rewards show on every customer's page until ended. Make a matching discount in Square (Items, Discounts, e.g. "Points: Free Coke" at 100%), not the Fizzy Tab one. 1000 points is $10 spent.
- **Double points.** Extra points are added by themselves; tab buys get theirs once paid off. Leave the last day blank for one day.
- **Restocks and payouts.** Restocks take the cost of the whole lot ($18 for 24 Cokes); profit uses the cost of one. Payouts split evenly between whoever's ticked; change the amounts to what people actually got.
- **Profit.** Clear a cost to remove it; under 20% margin shows red. Renaming an item in Square keeps its cost. Revenue counts discounts such as rewards, counts tab sales at full price, and doesn't take refunds off.
- **Square profiles not linked.** Profiles with the same email as someone already in Fizzy are probably doubles: merge them in Square (Customers, select both, Merge), or assign their sales on the Square page.
- **Claiming sales.** Ring the sale up first, then press Claim sale: it takes the sale from the 30 seconds before the press (the newest, if there are two), as soon as Square sends it. A sale goes to the first press after it, so pressing for the next person never takes the last one's. Pick a sale lists the latest sales with nobody on them, newest first. Picked the wrong one? Fix it on the Square page. A teacher's sale, or anyone's without an account: mark it No account (it still counts in takings) before the next press, or it may go to the next customer. Tab sales can't be No account.
- **History import.** Safe to run again. Takes a minute or two for a lot of history; Fizzy keeps working meanwhile.
- **Directory import.** Needs a name column (`Cleaned Name` or `Name`) and `Email`; everything else is ignored. Adds new students and fixes names, never deletes anyone.
- **Backups.** `fizzy.db` has students' names and emails in it, so keep downloads private.
- **Who can do what.** "Owner only" means the PIN until there's an owner password; with no PIN either, everything's open to anyone signed in. Only the owner can change the table, the PIN and account types.
- **PIN.** Staff type it for anything set to "PIN". The owner password works anywhere the PIN does. It stays unlocked for 10 minutes, or until someone else is put on the counter. Forgotten: reset it on the home PC.
- **Owner password.** Set `FIZZY_ADMIN_PASSWORD` in `.env` to lock the dangerous stuff behind it.
- **Maintenance mode.** Everyone, staff and students, gets a "Back soon" page until it's turned off on that page with the owner password. Square sales still come in.
- **Updates.** "Update now" restarts Fizzy (down for about a minute) and rolls back by itself if the new version doesn't start. "Can't read the updater's status": update the updater script and restart it, or `chmod 644 update-status/*`. "Updater isn't running": update on the home PC (see Updating).
- **Customer display.** The message shows between customers. Ticked items show on its menu (up to 12). "Show what they owe" includes anything overdue.
- **Fizz** (Ask). Use a paid Gemini key from aistudio.google.com (on the free tier Google may train on the data). Stored encrypted, owner password only, never shown again. Model blank means the default. Nothing changes until you press *Do it*.
- **Term dates** are for badges and events that work by term. **Closed days** make cookedcoke.co show "Closed" and the dashboard show a recap.
- **Security keys.** A key or passkey signs in instead of the staff password, so one key can be shared. You still pick who's on the counter, and the PIN and owner password work the same. A key only works on the address it was added on.
- **Devices.** With `FIZZY_ACCESS_HOST` set, registered devices open the main address directly and everything else goes through Cloudflare Access; both still need the staff password. Device links work once.

## 5. Profit

**Profit** (dashboard button, or the top bar) needs no Square Retail Plus: type what one of
each item costs you in the table and press **Save costs**.

**Where the items come from.** Fizzy reads your item list from Square every 6 hours (or press
**Get items from Square**), so every item shows, even ones that haven't sold yet, with its
Square price and the margin you'd make. Each variation is its own row with its own cost, e.g.
*Coca-Cola (Can)* and *Coca-Cola (Mini)*. Sales are matched by Square's own item ids, so
renaming an item in Square keeps its cost. Anything not in the list (a custom amount) is
matched by name. Reading the item list needs the token's *ITEMS_READ* permission (a personal
access token has it); without it, items show up here once they've sold.

- **Revenue** is what each item sold for after discounts like rewards, but before the Fizzy
  Tab discount, since that money comes in later as repayments.
- **Profit** = revenue minus cost; **margin** = profit as a % of revenue (under 20% shows red).
- Items without a cost are listed but left out of the totals, and the page says how much
  revenue that is. Refunds aren't taken off.
- **Today / Last 7 days / Last 30 days** switch the range. The dashboard shows today's profit.

**Money** (dashboard button, or the top bar) is a record of what was bought and who got
paid. It doesn't try to track how much money the stall has.
- **Add a restock:** day, shop, each item with quantity and the cost for the whole line
  (24 Coke for $18), and optionally a receipt photo (phones open the camera). The cost of one
  ($0.75) becomes that item's cost on the Profit page. Use the item names from Square (the
  box suggests them).
- **Pay the partners:** type how much you're paying out (minus anything you keep for stock)
  and the calculator splits it evenly between whoever's ticked; **Use these amounts** fills
  them in. Change any amount before recording - record what was actually paid.
- The top shows what was spent on stock and paid to each partner over the dates you pick.

Receipt photos are kept in `data/receipts/` and copied into the backup folder with the daily
backup.

## 6. Rewards (points shop)

Run a reward whenever you like, e.g. **Free Coke for 1000 points**, or **$2 off for 500**. It
stays live until you end it.

**Starting one:** **Rewards** in the top bar → *Start a reward*. Give it:
- a name;
- the points it costs;
- what staff should do in Square, e.g. "apply the Points: Free Coke discount";
- optionally, a limit per customer.

**At the counter:** while a reward is live, it shows on every customer's page.
- **They have enough points:** it's green, with a **Redeem** button. Offer it;
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
many people) is kept. Redeeming doesn't count as spending, so it doesn't change the tab
score. 1 point = 1 cent spent, so 1000 points = $10 spent.

**Double points days.** On the Rewards page, *Schedule double points*: a name, a multiplier
(2 = double), and the first and last day (today or later). During those days every purchase
earns the extra points by itself, within a minute; something bought on the tab gets its bonus
when it's paid off, even weeks later, and a refund takes the bonus back. Students see it
coming on the student site, and the dashboard shows it while it's on. **Delete** removes one
that hasn't started; **End** stops one straight away (bonus already earned stays). For
past days, use *Bonus points for a period* (Rewards, More).

**Every week.** Tick *Repeat every week* when scheduling and it repeats on that weekday, e.g.
double points every Friday. Fizzy sets up the next two weeks ahead, so students and the website
see the next one coming. **Stop** (under *Every week*) ends it: ones coming up go, one running
today carries on.

**Discord.** Set `DISCORD_WEBHOOK_URL` (Discord: channel settings → Integrations → Webhooks →
New Webhook → Copy URL) and, if you want a ping, `DISCORD_MENTION` (e.g. `@everyone`, or a role
as `<@&ROLE_ID>`). Starting a reward or scheduling double points then shows an *Announce on
Discord* box, ticked by default. The post is sent by Fizzy on the home PC, never by the counter
device's browser, so it works even where the school wifi blocks Discord.

### Fizz (Gemini)

**Fizz** is a chat with Gemini that knows the stall: a box on the dashboard, or full screen at `/ask`
(the addresses, settings and permission names still say "ask"). Ask it anything about sales, customers
and tabs ("what sells best at lunch on Fridays?", "who used to come every week but stopped?"); it
looks things up itself (read-only: it can't change the database). It can also set things up:
custom badges and events, double points days, rewards and the display message. Those come back as a
card with **Do it**, which needs the owner password by default (Who can do what, Fizz) and is logged.

Answers are written in the background: leaving the page, closing the tab or losing the connection
doesn't lose one. Open the dashboard (or `/ask`) again and it joins the answer where it's up to (the app too); once done, it's
in the saved conversation. One question at a time per person.

**Custom badges and events** are how weird ideas happen without anyone writing code: describe it
("a badge for 20 Chi Forest and 10 milk teas in a term, 500 points", "every 5th noodles gets 100
bonus points", "triple points on anything before 11:05 at recess") and Gemini builds a rule out of
building blocks (items, counts, spend, days, weeks, streaks, time of day, weekdays, terms, first
buyers of the day, how they paid, double points days; all/any/at least N of; once, or again each
day/week/month/term). Fizzy checks the rule, shows who'd qualify right now, and runs it itself, so a
rule can't do anything the building blocks don't allow. Rules pay points like badges (as sales sync,
or when a page is opened), never take points back, and show on the student site. Pause or delete them
on the **Rewards** page. Terms come from **Settings, Term dates**. If an idea needs a building block
that doesn't exist, Gemini says so: ask for it to be added to `fizzy/rules.py`.

Set up: paste a key in **Settings, Fizz** (owner password; a paid key, since sales and student data are
sent to Google). It's stored encrypted and never shown again. `GEMINI_API_KEY` in `.env` works too and
wins if both are set. The model can be changed there as well (default `gemini-flash-latest`).

### Badges pay points

Each badge on the student site pays points once, the first time it's earned: First sip 50,
Regular 200, Legend 1,000, On a roll 200, Unstoppable 750, Explorer 150, Big spender 300,
Treat yourself 100, Good for it 200, Double trouble 100 (fizzy/badges.py, `BADGE_POINTS`). They're
paid when the sale that earns them syncs from Square, or when their page is next opened. Badges
people already had when this was switched on didn't pay out, so it didn't hand out a pile of free
rewards on day one. Badge points count on the leaderboard but not towards tab spend or the tab score.

## 7. Student site

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
- a **streak**: how many days in a row the stall was open and they bought something (weekends,
  holidays and closed lunches don't break it, and today isn't over until it's over);
- **badges**, e.g. *First sip*, *Regular* (10 days), *On a roll* (5-day streak), *Explorer*
  (5 different things), *Good for it* (a tab paid on time), *Double trouble* (bought on a
  double points day), with progress bars for the ones still to get;
- any double points day that's on now or coming up;
- recent activity in plain words (staff notes and names are never shown);
- **Leaderboard**, this month and all time, ranked by points *earned* (redeeming rewards
  doesn't drop you down). Only students with a passcode are on it - getting one is opting
  in. Names are shortened to first name + last initial, e.g. *Nora F.*, and it's only shown
  to signed-in students. Everyone also sees their own rank.

Before signing in, anyone can see the list of rewards that are on, and nothing else.

**The main website.** The student site's host also serves `/promos.json`: the live rewards and
any double points days on now or in the next two weeks (names, points and dates only, nothing
about any student). cookedcoke.co reads it and shows a *What's on* strip under its header, so
starting a reward or scheduling double points in Fizzy updates the website by itself. Add
`?promos=demo` to the website's address to preview the strip.

**Setting it up:**
1. Cloudflare → your tunnel → add a public hostname, e.g. `my.cookedcoke.co` →
   `http://fizzy:8000`. Don't put Cloudflare Access in front of it.
2. Set `FIZZY_PUBLIC_HOST=my.cookedcoke.co` in `.env`, then `docker compose up -d`.

The sign-in limits use the visitor address Cloudflare passes on (`CF-Connecting-IP`), so
keep the student site behind the tunnel. That host only serves the student pages; every staff page is "not found" there, and it
isn't affected by the device lock. Without `FIZZY_PUBLIC_HOST`, the student pages are at
`/me` on the staff site (handy for trying it out, but behind the device lock if that's on).

**Hunt posters' short links.** With `FIZZY_LINK_HOST=link.cookedcoke.co` (another public hostname on
the tunnel, also without Access), printed hunt codes are `https://link.cookedcoke.co/<code>`. On an
iPhone the camera opens the My CCC app there, or its App Clip if the app isn't installed. Any other
browser is sent on to `https://my.cookedcoke.co/c/<code>`, so the find counts on the student site:
on their account if they're signed in there, else kept in that browser until they sign in there.

### Security

**Security** (header, More) is for the fridge camera. The Blink cameras only record while they're
**armed**, so disarm them while you're selling and arm them when you close: the switch is at the top
of the page (it goes through Home Assistant, so it needs `FIZZY_HA_TOKEN`). Who can use it is the
"Arm or disarm the cameras" row in Settings, Who can do what.

Every fridge-camera clip is watched by an AI on the home server (Gemini, with the room camera's clips
from just before and after) and rated 0 to 10 for how likely it shows a theft. Clips rated high enough
come to Fizzy with their videos, newest first under **To watch**. Open one, watch the clips, then mark
it **OK** or **Theft** (Undo puts it back). The AI can be wrong: it only says what it saw.

The videos are kept on the Fizzy server (next to the database, not in the backups), up to
`FIZZY_INCIDENT_MAX_GB`; past that the oldest videos are deleted and their ratings stay. The analyser
sends them to `POST /security/ingest` and `PUT /security/ingest/<id>/<file>` with
`Authorization: Bearer <FIZZY_SECURITY_TOKEN>`.

## 8. Tools

Each tool lives on the page of what it changes (the **Home** column) and is also on **Tools** (header,
More), which starts with a searchable list of every action in Fizzy, linking to its home. Hover or tap
**i** to see what a tool does, and click its name to open it. Run from its home page, a tool comes back
to that page with its message; run from Tools, back to Tools. Tools in red change a lot at once or can't be undone,
and the biggest ones make you type a word (`RESET`, `REVOKE`, `SIGN OUT`) to confirm.
Everything is written to the activity log. Wherever a tool needs a customer, the box
suggests names as you type. Pick from that list (it fills in "Name <email>"), or type an exact
name, email or student number. Anything less exact never acts on its own: the tool stops and
says who you might mean, so a typo can't hit the wrong student.

| Section | Tool | Home | Use it for |
|---|---|---|---|
| Points | Give points to many | Customers | Event prizes: a list of names/emails/student numbers, or everyone |
| | Bonus points for a period | Rewards | Double-points day: tops up points on purchases between two dates. Tab purchases count by the date they were bought, for the part already repaid. Don't run the same dates twice |
| | Move points | Customer | Points went to the wrong person |
| | Reset all points | Rewards | End of year. Spend history and tab eligibility are kept |
| Tabs | Pause all tabs | Tabs | Holidays, end of term. Approvals come back when unpaused |
| | Turn off all tabs | Tabs | End of year |
| | Add tab charge by hand | Customer | Only if the sale isn't in Square at all; otherwise it'd be counted twice |
| | Write off a debt | Customer | Clear some or all of what someone owes. Earns no points |
| | Give more time to pay | Customer | Push back the due date on all of someone's unpaid charges |
| Customers | Add a customer | Customers | New student, teacher, anyone not in the school list |
| | Merge duplicates | Customers | Two records for one person: everything moves to the one you keep, including the duplicate's Square profile (its future sales land on the kept person) and its tab approval if the kept one had none |
| | Archive many | Customers | Left school or graduated: hidden from search, history kept |
| | Restore archived | Customers | Bring them back |
| Reports | Takings summary | Sales | Counting up: Square sales, what went on tabs, tab repayments, refunds, items returned from tabs (not money), and anything recorded without Square |
| | Who to chase | Tabs | Everyone owing, oldest first, with their emails ready to copy |
| | All balances | Tabs | Everyone who owes or is in credit |
| | Points leaderboard | Rewards | Printable top-N for events |
| Fun | Random winner | Rewards | Giveaway draw from buyers in a date range, anyone with points, or everyone. Can be weighted by points |
| Square | Sync now / Re-sync from a date / Import sales history / Find a payment | Sales, Square | After an outage, to backfill, or to look up one payment |
| Data | Export CSV / Download backup / Health check | Each table's page; Settings | Spreadsheets, a copy of the database, and a list of things to tidy up |
| Staff | Noticeboard | Dashboard | A message for the others at the top of the dashboard |
| | Sign everyone out | Settings | A device was lost. Also change `FIZZY_PASSWORD` if the password leaked |

---

### Admin PIN

Optional, set under **Settings → PIN** (4 to 8 digits). By default it's needed for anything
done by hand: giving or taking points, rewards, double points days, tabs, customers and
passcodes, restocks and payouts, costs and most tools. Claiming Square sales and syncing never
ask. The owner can change all of this under Who can do what (below). Five wrong
tries locks it for 10 minutes. Forgot it? On the home PC:

```
docker compose exec -u fizzy fizzy python -m fizzy clear-pin
```

## 9. Search

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
page and in the tools' customer boxes all use the same matching. The Python
(`fizzy/fuzzy.py`) and browser (`fizzy/static/fuzzy.js`) copies are tested to rank
identically.

There are no keyboard shortcuts: everything is a button. In the search box, the arrow keys and
`Enter` pick a result, and `Esc` clears it. A card scanner works on any page (see above).

---

## 10. Setting up

Do these once, in order.

### 10.1 Fizzy itself

See [Running it](#11-running-it). You'll need a `.env` file. Copy `.env.example` and fill in
at least `FIZZY_PASSWORD` and `FIZZY_SECRET_KEY`:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"   # makes a secret key
```

### 10.2 Import the school list

**Import** page (or `python -m fizzy import directory.csv`). The CSV needs a name column
(`Cleaned Name` or `Name`) and `Email`. Only those two are stored; every other column is
ignored. Emails must look like `rns<digits>@reddamnorthshore.nsw.edu.au`. Re-importing a
newer list adds new students and updates changed names, and never deletes anyone.

### 10.3 Put everyone into Square

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

### 10.4 Square: the discount, the item, and payment types

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

### 10.5 Square API access

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

### 10.6 Test before going live

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

## 11. Running it

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

**Updating.** The updater script runs as whichever user starts it, and that user must own `update-status/` and `updates/` (`sudo chown -R <user> update-status updates` if Docker created them as root). Fizzy only reads `update-status/`, so the files in it have to be world-readable (the script does this itself).

**Updating, continued.** The database lives in `./data` and survives rebuilds, and new tables and columns
are added automatically on start, so updating never needs a database step. The updater script
does it all: pulls, backs up, rebuilds, checks Fizzy came back, and goes back to the old version
by itself if the new one doesn't start.

```sh
deploy/update.sh              # Linux / macOS (or Git Bash on Windows)
.\deploy\update.ps1           # Windows PowerShell
```

(The very first time, to get the updater itself: `git pull && docker compose up -d --build`.)

**The Update button.** Settings → Updates (owner only) shows what's running, whether there's
anything new, and how the last update went, with an **Update now** button. Fizzy never updates
itself: the button leaves a note in `./updates`, and the updater, left running on the PC,
picks it up within a minute. Start it with the PC:

- **Windows:** Task Scheduler → Create Task. Trigger: *At log on*. Action: start `powershell`
  with arguments `-ExecutionPolicy Bypass -WindowStyle Hidden -File C:\path\to\fizzy\deploy\update.ps1 -Watch -Auto`.
  Docker Desktop needs to be set to start on log on too.
- **Linux:** edit and install `deploy/fizzy-updater.service` (instructions at its top).
- **macOS:** leave `deploy/update.sh --watch --auto` running in a Terminal tab.

`-Auto` / `--auto` also updates by itself overnight (2am to 5am) when there's something new.
Leave it off to only update when someone presses the button. A version that failed to start
isn't retried overnight; pressing Update still tries it. If files in the Fizzy folder were
edited by hand, it won't pull and says so.

### Cloudflare Tunnel

1. Cloudflare Zero Trust → Networks → Tunnels → **Create a tunnel** (cloudflared). Copy the
   token into `CLOUDFLARE_TUNNEL_TOKEN`.
2. Add a public hostname: `fizzy.cookedcoke.co` → service `http://fizzy:8000`. For the
   [student site](#7-student-site), add a second one (e.g. `my.cookedcoke.co`) to the same
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
- **Lost or replaced device:** Settings → Devices → **Remove**. Also use **Settings → Sign
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

- **Health:** the dashboard shows when the last backup ran, and turns red if backups are off,
  the last one failed (e.g. the SSD is unplugged or full; Fizzy retries hourly), the file is
  missing, or there hasn't been one for over a day.

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

## 12. Configuration reference

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
| `FIZZY_LINK_HOST` | *(off)* | Hunt posters' short links, e.g. `link.cookedcoke.co`: serves Apple's file for the app and App Clip; browsers go on to the student site |
| `FIZZY_ACCESS_HOST` | *(off)* | Turns on the device lock. Unregistered devices are sent here (behind Cloudflare Access) |
| `CF_ACCESS_TEAM_DOMAIN` | — | Your Access team domain, e.g. `cookedcoke.cloudflareaccess.com` |
| `CF_ACCESS_AUD` | — | The Access application's Audience (AUD) tag |
| `FIZZY_SECURITY_TOKEN` | *(off)* | The clip analyser's key for sending incidents (see Security). Without it, `/security/ingest` doesn't exist |
| `FIZZY_INCIDENT_MAX_GB` | `5` | Incident videos kept next to the database; past this the oldest are deleted (their ratings stay) |
| `FIZZY_HA_URL` | `https://home.w-l.xyz` | Home Assistant, which runs the Blink cameras |
| `FIZZY_HA_TOKEN` | *(off)* | A Home Assistant long-lived access token. Turns on the cameras' Arm / Disarm switch |
| `FIZZY_HA_ALARM_ENTITY` | `alarm_control_panel.blink_cooked_coke_co_security` | The Blink system in Home Assistant |
| `CLOUDFLARE_TUNNEL_TOKEN` | — | Used by the `cloudflared` container |

The tab limit, days to pay and spend threshold live in the database and are changed on the
**Settings** page, not in `.env`.

---

## 13. Data and privacy

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
- **Leaving school:** archive them (Customers → More → Archive many). Their history stays for the
  records, hidden from search.

---

### Admin and test accounts

On a customer's page, **Edit customer → Account type** (owner only):

- **Admin (one of us)**: for your and your partners' own accounts. By default only the owner
  password can change an admin account's points, tab, passcode or details, including through
  tools like "Give points to many". Claiming their own Square sales still works as normal.
- **Test account**: fake customers for trying things out. By default anyone signed in can do
  anything to them, with no passcode and no big-change limit (as long as no real customer is
  in the same action). They're left out of leaderboards, top customers, raffles, "Who to chase"
  and the Square import file.

**Customers → More → Make test accounts** makes up to 20 at a time, and **Delete test accounts** (owner)
removes them and everything done to them; Square sales put on them go back to "no customer".
Both rules are rows in **Who can do what**, so the owner can change them.

### When the server's down

There are two "back soon" pages: a friendly one for the student site (`fizzy/static/down.html`)
and a staff one for Fizzy (`fizzy/static/down-staff.html`, which reminds staff to keep selling in
Square). Each address gets its own, shown three ways:

1. **Maintenance mode**, while Fizzy is running but you're working on it. Everyone, staff and
   students, gets the page; Square sales still come in through the webhook. Turn it on under
   **Settings → Maintenance mode** (owner password, with an optional message), and off from the
   page itself: **Owner? Turn maintenance off**, then the owner password. Or on the home PC:
   ```
   docker compose exec -u fizzy fizzy python -m fizzy maintenance on "Upgrading the server, back by lunch."
   docker compose exec -u fizzy fizzy python -m fizzy maintenance off
   ```
   Leave the message out for the default ("We're doing some maintenance on our end...").
2. **Phones and browsers that have opened Fizzy before** show it by themselves when Cloudflare
   can't reach the home PC (and a "You're offline" page when their own internet's out).
3. **Everyone else, even with the PC off**: a free Cloudflare Worker. Once:
   - Cloudflare dashboard → **Workers & Pages → Create → Create Worker**, name it
     `fizzy-down`, **Deploy**, then **Edit code**: paste in all of `cloudflare/worker.js`,
     **Deploy**.
   - In the worker's **Settings → Domains & Routes → Add → Route**: `fizzy.cookedcoke.co/*`
     (zone cookedcoke.co). Add `my.cookedcoke.co/*` too, and the Access host if you use one.

   It passes everything straight through, and only steps in when Cloudflare can't reach the PC.
   The free plan covers 100,000 requests a day, far more than the stall needs. If you change
   either page, run `python cloudflare/make_worker.py` and paste the new `worker.js` in again.

## 14. Troubleshooting

| Problem | Check |
|---|---|
| "Square isn't connected" on the dashboard | `SQUARE_ACCESS_TOKEN` is set in `.env`, then `docker compose up -d` |
| "Last Square sync failed" | The error text is shown on the Square page. Usually an expired or wrong token, or the wrong `SQUARE_ENVIRONMENT` |
| A sale didn't give points | The customer probably wasn't attached on the Terminal. **Square** page → *Only unlinked* → **Assign to…** |
| A tab sale isn't on their tab | No customer attached: it's under **Needs attention**, so assign it there. No discount: see [Fixing a mistake](#fixing-a-mistake) |
| A repayment didn't clear the tab | No customer attached: assign it under **Needs attention**. Otherwise the item must be called exactly **Tab repayment** |
| A customer shows "Not in Square yet" | Import the Square CSV, *new only* (10.3), then sync. Or paste their Square customer ID into **Edit customer** |
| Webhooks not arriving | `SQUARE_WEBHOOK_URL` must match Square's subscription URL exactly; no Cloudflare Access on `/square/webhook`. Polling still catches everything within 5 minutes |
| Can't log in after an update | Check `FIZZY_SECURE_COOKIES=1` and that you're using `https://` |
| Counter device keeps getting sent to `fizzy-remote` | Its registration was lost (browser data cleared, different browser) or removed. Register it again |
| "Cloudflare Access required" on `fizzy-remote` | Check `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` match the Access application, and that Access is actually in front of that hostname |
| Everything slow at the counter | Fizzy pages render in ~5 ms; check the PC is awake and the tunnel is up (`docker compose logs cloudflared`) |
| Something looks wrong with the data | **Settings → Health check** |

---

## 15. Development

```sh
pip install -r requirements.txt pytest
python -m pytest                      # rules, Square processing, tools, search (needs node for the JS check)
FIZZY_PASSWORD=dev FIZZY_SECRET_KEY=dev FIZZY_SECURE_COOKIES=0 uvicorn fizzy.app:app --reload
```

**Stack:** Python 3.13, FastAPI, Jinja templates, SQLite (WAL mode). One process with a
background thread for Square polling and backups; server-rendered pages; two small
dependency-free scripts plus [Turbo](https://turbo.hotwired.dev) 8 (Drive only; vendored as
`fizzy/static/turbo.js`, MIT) so the staff site swaps pages in place instead of reloading them;
no build step. Runs comfortably on a Raspberry Pi.

| File | What's in it |
|---|---|
| `fizzy/logic.py` | Tab and points rules: repayment order, overdue, status, refunds |
| `fizzy/square.py` | Square API client, payment/refund processing (safe to repeat), webhook check |
| `fizzy/fuzzy.py` | Name matching (fzf scoring + typo tolerance) |
| `fizzy/static/fuzzy.js` | Browser copy of `fuzzy.py`. **Change both together**; a test compares them |
| `fizzy/static/app.js` | In-browser search, card scans, confirm and passcode boxes, the New sale pop-up |
| `fizzy/static/turbo.js` | Turbo 8.0.12, unmodified (MIT, © 37signals). Staff site only, not the student site or the customer display |
| `fizzy/tools.py` | The tools: each is a `TOOLS` entry plus an `h_<id>` handler, with its home in `HOMES`; `index()` is the list on Tools, `form_ctx()` what a page needs to draw tool forms |
| `fizzy/templates/_tools.html` | Tool forms, drawn the same on Tools and on each tool's own page |
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
- No keyboard shortcuts (removed: nobody used them). A button that needs a second look gets
  `data-confirm="Question?"`, and Fizzy asks in its own box first.

**Writing page scripts** (staff site). Turbo keeps the browser tab's `window`, `document` and the
scripts in `<head>` from page to page; only `<body>` is swapped. So:
- Per-page code goes in `init()` in `app.js`, which runs on the first load and after every Turbo
  page change. Anything there that outlives the page's own elements (a timer, a listener on `window`
  or `document`) must use `listen(target, type, fn)` / `every(fn, ms)` / `later(fn)`, which are
  undone before the next page is drawn. Plain `addEventListener`/`setInterval` there leaks: the
  handler keeps running on every later page.
- An inline `<script>` in a page runs each time that page is shown. It may bind to that page's own
  elements freely; anything on `window`/`document`, or a timer, needs removing on `turbo:before-render`.
- A form whose POST answers with a page (not a redirect) needs `data-turbo="false"` (Turbo only
  draws redirects and errors), as does any download that isn't a `.csv`. Redirect with `back()`:
  it keeps a `#section` anchor, which Turbo would otherwise lose.
- Turbo's page cache is off for every page (`base.html`): Back and Forward ask Fizzy again, as they did
  before Turbo, since any page can show points, tabs or who's on the counter that changed elsewhere.
- A control that sends its form as soon as it changes keeps the saved value in `data-was`; if the
  passcode box for that form is cancelled, `app.js` puts it back.

**Where things live** (README, section 4). A new action goes on the page of what it changes; rare or
dangerous ones under that page's More. A tool form on another page is `{{ tl.tool(id, back='/that/page') }}`
from `_tools.html` (the page passes `tool_ctx=tools.form_ctx(conn)`, which never goes to the app as JSON):
it posts to the same `/tools/<id>`, so its passcode level is the same, and its hidden `back` (a path on
this site only, `web.safe_back`) sends the answer back to that page at `#<id>`. Add new ones to `HOMES` or
`PAGE_ACTIONS` in `tools.py` so Tools lists them. The header's tabs are in `base.html` (`tabs`, `more_tabs`);
Settings, Log and Tools always sit in the More menu (`data-fixed`), and tabs that don't fit join them above.

**No account.** `POST /square/no-account` with `key` (a checkout key from Pick a sale) marks every row of
that checkout (`square_payments.no_account` = when, `no_account_by` = who); `POST /square/no-account/undo`
clears it. With `X-Fizzy-Format: json` they answer `{"ok": true, "key", "no_account", "message"}` or
`{"error": "..."}` (400). `kind` is left alone, so stats count it as before; `square.unclaimed`, `_link`
(so `link_pending` and syncs), claiming and assigning all skip marked rows, and a late part of the same
order is marked too. A checkout with a customer or any tab money can't be marked.

**Claim sale for the iOS app.** `POST /customers/{id}/claim/next` with `X-Fizzy-Format: json` answers
`{"claimed": {...}}` (the same fields as a sale in `GET /customers/{id}/claim`; with `"already": true` when
the customer already had a sale from then, given another way, and nothing new was claimed), `{"waiting":
{"id", "customer_id", "name", "pressed_at", "wait_until"}}` (with `"already": true` when their press was
already waiting), or `{"error": "..."}` (400). `POST /claim-waits/{id}/cancel` is Undo (`{"cancelled": id}`,
or 409 when it already finished), and `GET /api/claim-waits` is what the bar shows. Presses live in the
`claim_waits` table; `square.claim_waiting` matches waiting ones at the end of every sync, and a watcher
thread in `app.py` syncs every 5 seconds while any are waiting. Those syncs (and the one a press starts)
are light: only new payments, orders and refunds, no customer or item list unless a sale comes from a
Square profile Fizzy doesn't know, and no order is fetched for a payment Fizzy already has. The poller's
full sync goes by `square_full_sync`, so light ones don't put it off.

How presses and sales are matched (see the top of the claim section of `square.py`): a sale rung up at t
(`first_at`, its first payment) belongs to the first press with t <= `pressed_at` <= t + 30 s, and no
other press can have it. Presses are the `claim_waits` rows that aren't cancelled: one claimed at once
keeps its row (`done_by` "at once", left out of the bar) and an expired one still counts, so a later
press never takes their sales. So does a press whose request is still waiting on Square
(`square.pressing`, held by `app.py` until the row is committed), so a later tap whose request gets
written first can't take its sale. Times are compared to the millisecond, Square's own from the raw
JSON (`_rung_at`), since `created_at` is whole seconds. `claim_next` (given the tap's time by `app.py`, taken before it waits for
Square) adds the press and claims the newest sale in Fizzy that belongs to it, or leaves it waiting up to
`CLAIM_GRACE` (180 s); `claim_waiting` does the same for every waiting press after each sync. A checkout
paid in several goes isn't matched while its order lists a payment not yet in, or its payments (with tips)
come to less than the order's total (`_all_in`). Since ownership only depends on Square's times and the
press times, the owners come out the same whatever order or delay sales arrive with
(`test_every_arrival_order_gives_the_same_owners`), except for the stray sale in the counter section.

---

## License

[MIT](LICENSE) © 2026 Cooked Coke Co.

The MIT licence covers the code. The Cooked Coke Co. name and logo (`fizzy/static/logo.svg`,
the favicons) are the business's own branding and aren't included in that licence.

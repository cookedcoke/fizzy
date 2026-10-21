# Fizzy

Customer directory, points and pay-later tabs for Cooked Coke Co. Staff-only, served at
**fizzy.cookedcoke.co** through a Cloudflare Tunnel (no open ports at home).

- **Stack:** Python 3.13, FastAPI, Jinja templates, SQLite (WAL). One process, server-rendered HTML,
  no build step, so it runs fine on any PC or a Raspberry Pi.
- **Access:** one shared staff password. After signing in, pick who's on the counter from the
  header dropdown; every action is logged against that name. The session lasts 30 days on the
  desk machine. **Lock** signs out.

## Rules (as built)

| Rule | Default | Where to change |
|---|---|---|
| Max owing per customer | $10 | Settings |
| Charge due after | 7 days | Settings (applies to new charges) |
| Tab eligibility | $30 spent (completed sales + tab repayments, minus refunds) | Settings |
| Points | 1 per cent paid. Tab purchases earn points **when repaid**, not when charged | fixed |

- **Tab status:** *Locked* (under $30) → *Eligible* (staff must approve) → *Approved*.
  Staff can also *Reject* or *Revoke*, with an optional note. Every decision is kept in the
  customer's history.
- **Blocked (overdue):** an approved customer with any overdue charge is blocked
  automatically. It clears automatically once paid. Each late payment stays permanently on
  their page as a "late-payment record".
- **Repayments** go to the oldest charge first. Overpaying leaves the customer in credit, and
  credit earns no points.
- **Refunds and exchanges** in Square are reversed automatically: points come off a sale, a
  tab charge is reduced, and points are taken back if the customer had already repaid the
  refunded items.
- **Rule breaches:** Square can't stop a tab sale, so a tab charge for someone not approved,
  over the limit or overdue is still recorded as owing. It's flagged in red on the dashboard
  for staff to deal with.

## Counter speed and keyboard shortcuts

Search runs in the browser: the page loads the customer list once (a few tens of KB) and
filters it as you type, in about 1-5 ms per keystroke with no network wait. The highlighted
result is preloaded in Chrome/Edge, so Enter opens it straight away. Server pages render in
about 5 ms; on the counter, most of the wait is the Cloudflare round trip.

Press **?** on any page for the full list.

| Key | Does |
|---|---|
| `/` or `S` | Search (box in the top bar on every page) |
| `↑` `↓` `Enter` | Pick a result |
| `Esc` | Leave the field, then back to the dashboard |
| `R` | Customer page: repayment amount (pre-filled with what's owed; `Enter` saves) |
| `P` | Customer page: adjust points (`Tab` to reason, `Enter` saves) |
| `A` / `X` | Customer page: approve / reject or revoke the tab (`X` asks first) |
| `N` `H` `L` `E` | Customer page: tab note, decision history, activity log, edit |
| `W` | Switch who's on the counter |
| `U` | Square page: sync now |
| `1`-`8` | Tools page: jump to a section |
| `G` then `D` `C` `S` `T` `I` `L` `,` | Go to Dashboard, Customers, Square, Tools, Import, Log, Settings |
| `Ctrl`+`Enter` | Submit the form you're in |

A typical counter lookup: `Esc`, type a name, `Enter`, read the green/red answer, then `Esc`.

## Tools

**Tools** in the top bar holds the occasional jobs. Hover or tap ⓘ on a tool to see what it
does; tap the name to open it. Tools shown in red change a lot at once or can't be undone.
Some of these ask you to type a word (RESET, REVOKE, SIGN OUT) to confirm. Everything a tool
changes is written to the activity log.

| Section | Tools |
|---|---|
| Points | give points to many / everyone, bonus multiplier for a date range, move points, reset all |
| Tabs | pause all tabs, approve all eligible, revoke all, add a charge by hand, write off a debt, give more time to pay |
| Customers | add one, merge duplicates, archive many, restore archived |
| Reports | takings summary, who to chase (with emails to copy), all balances, points leaderboard (all printable) |
| Fun | random winner draw, optionally weighted by points |
| Square | sync now, re-sync from a date, find a payment |
| Data | CSV exports, download a database backup, health check |
| Staff | dashboard noticeboard, sign everyone out |

## Square setup

### 1. Customers

1. Import the school CSV into Fizzy: **Import** page, or `python -m fizzy import directory.csv`.
   Only name and school email are stored.
2. On **Customers**, click **Download all customers as a Square import CSV**. Import it in
   Square Dashboard → Customers → Import customers. Each row carries a *Reference ID*
   `fizzy-<id>`, so Fizzy links the profiles back automatically on the next sync. Profiles
   are also matched by email.
3. At the counter, **attach the customer profile on the Terminal** for every sale. Without
   it there are no points, and a tab sale lands in "Needs attention".

### 2. Tab sales: "Other Payment Types" is the tab button

Square AU can't rename the **Other Payment Types** tender, so **Other is reserved for tabs**.
Fizzy counts every Other payment as a tab charge, so don't use Other for anything else.
**House Account** payments count as tabs too, in case someone uses it.

House Account isn't used for the tab system itself. It's Square's own pay-later feature:
each customer needs an account set up in Square, Square collects the money by invoice or
card on file, and the balance has to be cleared within Square's time limits. That would
compete with Fizzy rather than feed it.

1. Square app → Settings → Checkout → Payment types: keep **Other Payment Types** enabled.
2. At the counter: attach the customer, then pay with **Other Payment Types**.
3. In the API these are `source_type: EXTERNAL` (House Account is `SQUARE_ACCOUNT`). The list
   is `SQUARE_TAB_TENDERS`.

**Verify before going live:** do one $0.50 tab sale and one cash sale with a customer
attached. Click **Sync now** on the **Square** page, and check the tab sale is listed as
*Tab charge*. Any payment can be re-marked from the dropdown on that page. Refund both test
sales afterwards and check they reverse.

### 3. Repayments: in Fizzy only

Record repayments on the customer's page in Fizzy and **don't put them through Square**. The
sale was already recorded in Square once (paid with Other). Ringing up the repayment as well
would count the same money as a second sale in Square's reports.

How the money adds up:

- **Square gross sales** are correct: every sale appears once.
- **Square's "Other" total** is what went on tabs that day. It's not cash in hand.
- **Money actually collected** = Square cash and card + Fizzy repayments. Cash repayments go
  in the till and PayID goes to the bank account. **Tools → Takings summary** shows both side
  by side for counting up.
- Take repayments as **cash or PayID**. A card repayment can only be taken through Square,
  which would double-count the sale. If it ever has to happen, record it in Fizzy as *card*
  and note it when reconciling.

(`SQUARE_REPAYMENT_ITEM` can make Fizzy detect repayments rung up in Square. It's off by
default for the reason above.)

### 4. API credentials

Square Developer Dashboard → create an application (production):

- `SQUARE_ACCESS_TOKEN`: the production access token. It needs read access to Payments,
  Orders and Customers.
- `SQUARE_LOCATION_ID`: Locations tab.
- Webhooks (optional, makes updates near-instant): add a subscription for `payment.created`,
  `payment.updated`, `refund.created`, `refund.updated` and `customer.created` with URL
  `https://fizzy.cookedcoke.co/square/webhook`. Copy the **signature key** to
  `SQUARE_WEBHOOK_SIGNATURE_KEY`.
- Polling every 5 minutes runs regardless, so a missed webhook is harmless.
- `SQUARE_SYNC_FROM=YYYY-MM-DD` sets how far back the first sync goes. Use it to award points
  for past sales; by default it goes back 1 day.

## Deploy (Docker, on the home PC)

Needs Docker Desktop (Windows/macOS) or Docker Engine (Linux).

```sh
git clone <repo> fizzy && cd fizzy
cp .env.example .env      # fill in password, secret key, Square + tunnel token
docker compose up -d --build
docker compose logs -f fizzy
```

### Cloudflare Tunnel

1. Cloudflare Zero Trust → Networks → Tunnels → Create tunnel (cloudflared). Copy the token
   into `CLOUDFLARE_TUNNEL_TOKEN`.
2. Public hostname: `fizzy.cookedcoke.co` → service `http://fizzy:8000`.
3. Nothing is published on the host's ports. For local testing without the tunnel, add
   `ports: ["127.0.0.1:8000:8000"]` to the `fizzy` service and set `FIZZY_SECURE_COOKIES=0`.
4. Optional hardening: a Cloudflare WAF rate-limit rule on `/login`. Fizzy also locks an IP
   out for 15 minutes after 10 wrong passwords.
5. Don't put Cloudflare Access in front of `/square/webhook`. Square has to reach it, and
   every request is checked against Square's signature.

### Backups

The app writes a consistent SQLite snapshot to `FIZZY_BACKUP_DIR` once a day and keeps the
newest 30 (`FIZZY_BACKUP_KEEP`). In Docker that's `./backups`. Point the volume at the
external SSD when it's ready, e.g. `- /mnt/ssd/fizzy-backups:/backups` or
`- E:/fizzy-backups:/backups`. There's also **Settings → Back up now**, and
`python -m fizzy backup DIR`.

To restore: stop the app, copy a backup over `data/fizzy.db` (delete `fizzy.db-wal` and
`fizzy.db-shm` if present), then start it again.

### Without Docker (Linux, systemd)

```sh
sudo useradd -r fizzy && sudo mkdir -p /opt/fizzy && sudo chown fizzy /opt/fizzy
# as fizzy, in /opt/fizzy:
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # edit
sudo cp deploy/fizzy.service /etc/systemd/system/ && sudo systemctl enable --now fizzy
```

Then run `cloudflared` as a service pointing `fizzy.cookedcoke.co` at `http://localhost:8000`.

## Development

```sh
pip install -r requirements.txt pytest
python -m pytest
FIZZY_PASSWORD=dev FIZZY_SECRET_KEY=dev FIZZY_SECURE_COOKIES=0 uvicorn fizzy.app:app --reload
```

## Data and privacy

Customers are mostly minors, so Fizzy stores only: name, school email, Square customer ID,
tab decisions, and money/points history. The import CSV and the database are git-ignored.
Never commit them. Customer login isn't built. Customers are keyed by school email, so a
later "my points / my tab" view can authenticate against the school's Microsoft accounts and
look the customer up by email.

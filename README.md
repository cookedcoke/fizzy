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

### 2. Tab sales: a custom "Other" payment type

Square has no house accounts in Australia (that feature is US only). What it does have is
**"Other" tender types**, which record a sale without moving money:

1. Square Dashboard → Settings → Checkout → Payment types. Turn on "Other" payment types and,
   if your account offers custom names, add one called **Fizzy Tab**.
2. On the Terminal, staff attach the customer, then pay with *Other → Fizzy Tab*. If custom
   names aren't available, use plain "Other" with the note **Fizzy Tab**.
3. Through the API these come through as `source_type: EXTERNAL` payments. Fizzy treats one
   as a tab charge when its external source, type or note contains `SQUARE_TAB_MATCH`
   (default `fizzy tab`).

**Verify before going live:** do one $0.50 test tab sale and one cash sale with a customer
attached. Click **Sync now** on the **Square** page, then open **raw** on both payments.
Check that the tab sale shows *tab_charge*. If Square stores the name in a different field,
change `SQUARE_TAB_MATCH`, or reclassify the payment with the dropdown on that page (this is
also how to fix any mistake). Refund both test sales afterwards and check they reverse.

### 3. Repayments

Record them on the customer's page in Fizzy (cash / PayID / card), **and** ring up a
custom-amount item called **Tab repayment** in Square for the same amount, taking cash, card
or Other (PayID). That keeps Square's takings matching the till.

- Fizzy matches the Square payment to the Fizzy entry (same customer and amount, within 24
  hours), so it's counted only once.
- A Tab repayment rung up in Square without a Fizzy entry is still recorded automatically.
- Create **Tab repayment** as a catalogue item (variable price) so the name is always exact.
  Repayment payments never earn points as sales; the repayment itself earns them.

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

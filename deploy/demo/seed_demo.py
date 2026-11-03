"""Fill a fresh demo database with believable, made-up data: for App Store screenshots and the
App Review demo server (see README.md). Run with FIZZY_DB pointing at an empty file and
FIZZY_BACKUP_DIR set:  python seed_demo.py settings.json"""
import json
import os
import random
import sys
from datetime import datetime, time, timedelta, timezone

sys.path.insert(0, os.environ.get("FIZZY_SRC") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from fizzy import config, db, events, logic, money, pin, portal, rewards, square, stats  # noqa: E402

random.seed(67)
conn = db.connect(config.DB_PATH)
db.init(conn)
T = stats.today()
NOW = datetime.now(timezone.utc)

# The demo passcode and settings.
for row in json.load(open(sys.argv[1])):   # permissions, from settings.json
    if row["key"] != "admin_pin":          # older copies of the file carried the passcode's hash
        db.set_setting(conn, row["key"], row["value"])
pin.set_pin(conn, os.environ.get("DEMO_PIN", "4821"), "Will")   # the passcode App Review is given
db.set_setting(conn, "tab_limit_cents", "1500")

MENU = [  # name, price, cost, how popular
    ("Coke No Sugar", 300, 145, 10), ("Coca-Cola", 300, 145, 8), ("Sprite", 300, 140, 4), ("Fanta", 300, 140, 3),
    ("Iced Tea Peach", 350, 170, 4), ("Mount Franklin", 200, 70, 3), ("Mi Goreng", 350, 120, 6),
    ("Smith's Chips", 250, 105, 6), ("Shapes", 250, 110, 3), ("Kit Kat", 250, 115, 4), ("Freddo", 150, 65, 5),
    ("Toastie", 450, 180, 5), ("Pocky", 300, 150, 3),
]
for i, (name, price, cost, _) in enumerate(MENU):
    conn.execute("INSERT INTO catalog_items VALUES (?, ?, ?, NULL, ?, ?, 0, ?)", (f"V{i}", f"I{i}", name, name, price, db.now_iso()))
weights = [w for *_, w in MENU]

FIRST = ("Olivia Liam Charlotte Noah Amelia Oliver Isla Jack Mia William Ava Henry Grace Leo Chloe Lucas Zoe Thomas Ruby "
         "Ethan Sienna Hudson Matilda Archie Ivy Oscar Harper Mason Willow Hugo Aria Kai Elsie Max Lily Felix Hannah Jasper "
         "Priya Arjun Mei Daniel Sofia").split()
LAST = ("Nguyen Smith Chen Williams Patel Brown Kim Taylor Wilson Singh Martin Lee Anderson Wong Thompson Walker Zhang "
        "Harris Clarke Ryan Lam Kelly Murphy Tran Hughes Mitchell Park Cooper Shah Evans").split()
names = set()
while len(names) < 44:
    names.add(f"{random.choice(FIRST)} {random.choice(LAST)}")
custs = []
for i, n in enumerate(sorted(names)):
    sq = f"SQ{i}"
    cur = conn.execute("INSERT INTO customers(name, email, square_customer_id, created_at) VALUES (?, ?, ?, ?)",
                       (n, f"rns{20230000 + random.randint(100, 9999):08d}@reddamnorthshore.nsw.edu.au", sq,
                        (NOW - timedelta(days=60)).isoformat(timespec="seconds")))
    custs.append(conn.execute("SELECT * FROM customers WHERE id = ?", (cur.lastrowid,)).fetchone())
regulars = custs[:14]          # buy most days
tabbers = custs[:7]            # have an approved tab
for c in tabbers:
    logic.set_tab_decision(conn, c["id"], "approved", "Regular, always pays back", random.choice(config.STAFF_NAMES))

# Busy at recess and lunch, a little before and after school.
SLOTS = [((8, 5), (8, 40), 4), ((10, 50), (11, 20), 12), ((12, 55), (13, 40), 14), ((15, 10), (15, 35), 5)]


def when(day):
    (h1, m1), (h2, m2), _ = random.choices(SLOTS, [w for *_, w in SLOTS])[0]
    a, b = h1 * 60 + m1, h2 * 60 + m2
    m = random.randint(a, b)
    local = datetime.combine(day, time(m // 60, m % 60, random.randint(0, 59)), config.TZ)
    return local.astimezone(timezone.utc)


def basket():
    n = random.choices([1, 2, 3], [60, 30, 10])[0]
    return [MENU[i] for i in random.choices(range(len(MENU)), weights, k=n)]


def lines(items):
    out = {}
    for it in items:   # two of the same is one line of 2, like Square
        out[it] = out.get(it, 0) + 1
    return [{"name": n, "quantity": str(q), "catalog_object_id": f"V{MENU.index((n, p, c, w))}", "total_money": {"amount": p * q}}
            for (n, p, c, w), q in out.items()]


days = [T - timedelta(days=d) for d in range(16, -1, -1) if (T - timedelta(days=d)).weekday() < 5]
seq = 0
owing = {c["id"]: 0 for c in tabbers}
for day in days:
    n_orders = random.randint(48, 66) if day != T else 61
    plan = sorted(when(day) for _ in range(n_orders))
    for ts in plan:
        seq += 1
        oid, pid, iso = f"o{seq}", f"p{seq}", ts.isoformat(timespec="seconds")
        r = random.random()
        c = random.choice(regulars) if r < 0.45 else random.choice(custs) if r < 0.8 or day == T else None  # today's all claimed
        items = basket()
        total = sum(p for _, p, _, _ in items)
        sq = c["square_customer_id"] if c else None
        days_left = (T - day).days
        order_lines = lines(items)
        src_type = random.choices(["CARD", "CASH"], [70, 30])[0]
        square.process_payment(conn, {"id": pid, "status": "COMPLETED", "amount_money": {"amount": total}, "source_type": src_type,
                                      "card_details": {"card": {"card_brand": random.choice(["VISA", "MASTERCARD"])}} if src_type == "CARD" else None,
                                      "customer_id": sq, "created_at": iso, "order_id": oid},
                               {"id": oid, "line_items": order_lines, "customer_id": sq})
    if day != T:
        conn.commit()

# Tab sales once there's history behind them: an older one paid back, and a few small ones from this week.
def tab_sale(c, ts, items):
    global seq
    seq += 1
    oid, pid, iso = f"o{seq}", f"p{seq}", ts.isoformat(timespec="seconds")
    total = sum(p for _, p, _, _ in items)
    order = {"id": oid, "line_items": lines(items), "state": "COMPLETED", "customer_id": c["square_customer_id"], "closed_at": iso,
             "created_at": iso, "discounts": [{"name": "Fizzy Tab", "percentage": "100", "applied_money": {"amount": total}}]}
    square.process_order(conn, order)
    square.process_payment(conn, {"id": pid, "status": "COMPLETED", "amount_money": {"amount": 0}, "source_type": "CASH",
                                  "customer_id": c["square_customer_id"], "created_at": iso, "order_id": oid}, order)
    return total


for c in tabbers:
    owed = tab_sale(c, when(days[-8]), [MENU[0], MENU[7]])
    seq += 1
    iso = when(days[-6]).isoformat(timespec="seconds")
    square.process_payment(conn, {"id": f"p{seq}", "status": "COMPLETED", "amount_money": {"amount": owed}, "source_type": "CASH",
                                  "customer_id": c["square_customer_id"], "created_at": iso, "order_id": f"o{seq}"},
                           {"id": f"o{seq}", "customer_id": c["square_customer_id"],
                            "line_items": [{"name": config.SQUARE_REPAYMENT_ITEM.title(), "quantity": "1", "total_money": {"amount": owed}}]})
tabbers = [conn.execute("SELECT * FROM customers WHERE id = ?", (c["id"],)).fetchone() for c in tabbers]  # now approved
for c in tabbers[:5]:
    for day in random.sample(days[-3:], 2):
        items = basket()[:2]
        if sum(p for _, p, _, _ in items) <= logic.tab_status(conn, c)["available_cents"]:
            owing[c["id"]] += tab_sale(c, when(day), items)

# Student site passcodes: most students have signed up. Amelia Williams' (the first) is the one App Review uses.
for c in custs:
    if c["id"] == custs[0]["id"]:
        portal.set_passcode(conn, c, "2468", "Will")
    elif random.random() < 0.75:
        portal.set_passcode(conn, c, f"{random.randint(1000, 9999)}", "Will")

# A reward, a weekly double points day, a message on the customer display.
rid = rewards.create(conn, "Free drink", 1500, "Apply the Points: Free drink discount", None, "Will")
for c in random.sample(regulars, 4):
    if logic.points_balance(conn, c["id"]) >= 1500:
        rewards.redeem(conn, rid, c, random.choice(config.STAFF_NAMES))
fri = T + timedelta(days=(4 - T.weekday()) % 7)
events.create_rule(conn, "Fizzy Friday", 2, fri.isoformat(), fri.isoformat(), "Will")
db.set_setting(conn, "display_message", "Double points every Friday!")

# Restocks and payouts for the Money page.
for d_ago, shop, buy in [(13, "Costco", [("Coke No Sugar", 48, 6960), ("Coca-Cola", 48, 6960), ("Mi Goreng", 30, 3600)]),
                         (9, "Woolworths", [("Smith's Chips", 24, 2520), ("Kit Kat", 24, 2760), ("Freddo", 30, 1950)]),
                         (6, "Costco", [("Sprite", 24, 3360), ("Fanta", 24, 3360), ("Iced Tea Peach", 24, 4080)]),
                         (3, "Aldi", [("Toastie", 20, 3600), ("Shapes", 12, 1320), ("Pocky", 12, 1800)]),
                         (0, "Costco", [("Coke No Sugar", 48, 6960), ("Mount Franklin", 24, 1680)])]:
    money.add_restock(conn, T - timedelta(days=d_ago), shop, "", buy, random.choice(config.STAFF_NAMES))
money.add_payout(conn, T - timedelta(days=7), "Mike", 4000, "Last week's share", "Will")
money.add_payout(conn, T - timedelta(days=7), "Frankie", 4000, "Last week's share", "Will")

# Nothing for the dashboard to warn about: synced just now, backed up just now.
db.set_setting(conn, "square_last_sync", db.now_iso())
db.set_setting(conn, "badge_points_baseline", db.now_iso())
conn.commit()
path = db.backup(conn, config.BACKUP_DIR, 3)
db.set_setting(conn, "last_backup", db.now_iso())
db.set_setting(conn, "last_backup_path", path)
conn.commit()
print("orders", seq, "customers", len(custs), "owing", {k: v for k, v in owing.items() if v})

"""SQLite schema and connection helpers.

Money is always integer cents. Times are UTC ISO-8601 strings.
"""
import os
import sqlite3
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    square_customer_id TEXT UNIQUE,
    -- Staff decision only: NULL (none yet), approved, rejected, revoked.
    -- locked / eligible / blocked_overdue are derived, never stored.
    tab_decision TEXT,
    tab_decision_note TEXT,
    tab_decision_by TEXT,
    tab_decision_at TEXT,
    created_at TEXT NOT NULL,
    archived_at TEXT,
    merged_into INTEGER REFERENCES customers(id)   -- set on the archived side of a merge
);

-- Extra Square profiles that belong to a customer (e.g. the duplicate's profile after a merge).
CREATE TABLE IF NOT EXISTS square_aliases (
    square_customer_id TEXT PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id)
);

-- Permanent history of every tab decision (approve / reject / revoke / reset).
CREATE TABLE IF NOT EXISTS tab_decisions (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    decision TEXT,
    note TEXT,
    staff TEXT,
    created_at TEXT NOT NULL
);

-- Every completed Square payment Fizzy has seen. Processing is idempotent on square_payment_id.
CREATE TABLE IF NOT EXISTS square_payments (
    square_payment_id TEXT PRIMARY KEY,
    square_order_id TEXT,
    square_customer_id TEXT,
    customer_id INTEGER REFERENCES customers(id),
    kind TEXT NOT NULL,            -- sale | tab_charge | tab_repayment
    amount_cents INTEGER NOT NULL,
    tender TEXT,
    items TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS square_refunds (
    square_refund_id TEXT PRIMARY KEY,
    square_payment_id TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    raw_json TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tab_charges (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount_cents INTEGER NOT NULL,
    refunded_cents INTEGER NOT NULL DEFAULT 0,
    items TEXT,
    charged_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    square_payment_id TEXT UNIQUE,
    staff TEXT,
    -- Set when the charge broke a rule (not approved, over limit, overdue). Still owed.
    breach TEXT,
    breach_ack_by TEXT
);

CREATE TABLE IF NOT EXISTS tab_repayments (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount_cents INTEGER NOT NULL,  -- negative for a refunded repayment
    method TEXT NOT NULL,           -- cash | card | other | writeoff
    note TEXT,
    staff TEXT,
    square_payment_id TEXT,
    created_at TEXT NOT NULL
);

-- Points ledger. Balance = SUM(delta). spend_cents feeds the tab unlock threshold.
CREATE TABLE IF NOT EXISTS points_ledger (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    delta INTEGER NOT NULL,
    spend_cents INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,           -- purchase | refund | tab_repaid | tab_refund | manual
    note TEXT,
    square_payment_id TEXT,
    repayment_id INTEGER,
    staff TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    staff TEXT,
    action TEXT NOT NULL,
    customer_id INTEGER,
    details TEXT,
    created_at TEXT NOT NULL
);

-- Devices allowed on the main host without Cloudflare Access (see devices.py).
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_by TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS device_links (
    token_hash TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

-- Points shop: reward events staff start and end (see rewards.py).
CREATE TABLE IF NOT EXISTS rewards (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    points_cost INTEGER NOT NULL,
    square_note TEXT,                 -- what to do in Square, e.g. "apply the Points: Free Coke discount"
    per_customer_limit INTEGER,       -- NULL = no limit
    created_by TEXT,
    created_at TEXT NOT NULL,
    ended_at TEXT,
    ended_by TEXT
);

CREATE TABLE IF NOT EXISTS redemptions (
    id INTEGER PRIMARY KEY,
    reward_id INTEGER NOT NULL REFERENCES rewards(id),
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    points INTEGER NOT NULL,
    staff TEXT,
    created_at TEXT NOT NULL,
    undone_at TEXT,
    undone_by TEXT
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS ix_charges_customer ON tab_charges(customer_id);
CREATE INDEX IF NOT EXISTS ix_repay_customer ON tab_repayments(customer_id);
CREATE INDEX IF NOT EXISTS ix_points_customer ON points_ledger(customer_id);
CREATE INDEX IF NOT EXISTS ix_sqpay_customer ON square_payments(customer_id);
CREATE INDEX IF NOT EXISTS ix_audit_customer ON audit_log(customer_id);
"""

DEFAULT_SETTINGS = {
    "tab_limit_cents": "1000",
    "tab_due_days": "7",
    "unlock_spend_cents": "3000",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None):
    path = path or config.DB_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# Columns added after the first release. CREATE TABLE IF NOT EXISTS never changes an existing
# table, so databases created before these existed get them added here on start-up.
MIGRATIONS = [
    ("customers", "merged_into", "INTEGER REFERENCES customers(id)"),
]


def init(conn):
    conn.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    conn.commit()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def audit(conn, staff, action, customer_id=None, details=""):
    conn.execute(
        "INSERT INTO audit_log(staff, action, customer_id, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (staff, action, customer_id, details, now_iso()),
    )


def backup(conn, dest_dir, keep=30):
    """Online backup to dest_dir/fizzy-YYYYmmdd-HHMMSS.db, pruning to the newest `keep` files."""
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest_path = os.path.join(dest_dir, f"fizzy-{stamp}.db")
    dest = sqlite3.connect(dest_path)
    with dest:
        conn.backup(dest)
    dest.close()
    files = sorted(f for f in os.listdir(dest_dir) if f.startswith("fizzy-") and f.endswith(".db"))
    for old in files[:-keep] if keep > 0 else []:
        os.remove(os.path.join(dest_dir, old))
    return dest_path

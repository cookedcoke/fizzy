"""Customer import (school CSV), search and Square export."""
import csv
import io
import re

from . import fuzzy
from .db import audit, now_iso

EMAIL_RE = re.compile(r"^rns\d+@reddamnorthshore\.nsw\.edu\.au$")


def _pick(row, *names):
    for n in names:
        for k, v in row.items():
            if k and k.strip().lower() == n and v and v.strip():
                return v.strip()
    return ""


def parse_directory_csv(text):
    """Return (rows, errors). Only name and school email are kept; other columns are ignored."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    rows, errors, seen = [], [], set()
    for i, row in enumerate(reader, start=2):
        name = _pick(row, "cleaned name", "name", "original name")
        email = _pick(row, "email", "school email").lower()
        if not name and not email:
            continue
        if not name:
            errors.append(f"Line {i}: {email} has no name - skipped")
            continue
        if not EMAIL_RE.match(email):
            errors.append(f"Line {i}: '{email}' isn't a school email - skipped")
            continue
        if email in seen:
            errors.append(f"Line {i}: duplicate {email} - skipped")
            continue
        seen.add(email)
        rows.append({"name": " ".join(name.split()), "email": email})
    return rows, errors


def import_rows(conn, rows, staff, apply=True):
    """Upsert by email. Returns counts; with apply=False nothing is written (dry run)."""
    added = updated = unchanged = 0
    for r in rows:
        existing = conn.execute("SELECT id, name FROM customers WHERE email = ?", (r["email"],)).fetchone()
        if existing is None:
            added += 1
            if apply:
                conn.execute("INSERT INTO customers(name, email, created_at) VALUES (?, ?, ?)",
                             (r["name"], r["email"], now_iso()))
        elif existing["name"] != r["name"]:
            updated += 1
            if apply:
                conn.execute("UPDATE customers SET name = ? WHERE id = ?", (r["name"], existing["id"]))
        else:
            unchanged += 1
    if apply:
        audit(conn, staff, "import", None, f"added {added}, updated {updated}, unchanged {unchanged}")
    return {"added": added, "updated": updated, "unchanged": unchanged}


def search(conn, q, limit=20):
    """Fuzzy name search (fzf-style, typo tolerant) or student-number match. See fuzzy.py."""
    customers = conn.execute("SELECT * FROM customers WHERE archived_at IS NULL").fetchall()
    hits = fuzzy.rank(q, customers, limit, key=lambda c: (c["name"], c["email"].split("@")[0]))
    return [c for c, _ in hits]


def square_import_csv(conn, only_new=False):
    """CSV for Square Dashboard > Customers > Import. Reference ID lets Fizzy link profiles back.
    only_new: just the people not linked to a Square profile yet, so a second import doesn't
    make Square duplicates of everyone already there."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["First Name", "Last Name", "Email Address", "Reference ID"])
    new = (" AND square_customer_id IS NULL" if only_new else "") + " AND kind IS NOT 'test'"
    for c in conn.execute(f"SELECT * FROM customers WHERE archived_at IS NULL{new} ORDER BY name"):
        parts = c["name"].split(" ", 1)
        w.writerow([parts[0], parts[1] if len(parts) > 1 else "", c["email"], f"fizzy-{c['id']}"])
    return out.getvalue()

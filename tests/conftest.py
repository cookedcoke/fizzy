import os
import sys

os.environ.setdefault("FIZZY_PASSWORD", "test-pw")
os.environ.setdefault("FIZZY_SECRET_KEY", "test-secret")
os.environ.setdefault("FIZZY_SECURE_COOKIES", "0")
os.environ["SQUARE_ACCESS_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402

from fizzy import db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    db.init(c)
    yield c
    c.close()


@pytest.fixture
def cust(conn):
    def make(name="Test Kid", email="rns00000001@reddamnorthshore.nsw.edu.au", sq="SQ1"):
        cur = conn.execute("INSERT INTO customers(name, email, square_customer_id, created_at) VALUES (?, ?, ?, ?)",
                           (name, email, sq, db.now_iso()))
        return conn.execute("SELECT * FROM customers WHERE id = ?", (cur.lastrowid,)).fetchone()
    return make

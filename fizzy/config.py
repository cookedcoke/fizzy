"""Configuration from environment variables. Secrets never live in the repo."""
import os
from zoneinfo import ZoneInfo


def _env(name, default=""):
    return os.environ.get(name, default).strip()


DB_PATH = _env("FIZZY_DB", "data/fizzy.db")
PASSWORD = _env("FIZZY_PASSWORD")
SECRET_KEY = _env("FIZZY_SECRET_KEY")
STAFF_NAMES = [s.strip() for s in _env("FIZZY_STAFF", "Bru,Mike,Frankie").split(",") if s.strip()]
TZ = ZoneInfo(_env("FIZZY_TZ", "Australia/Sydney"))
BACKUP_DIR = _env("FIZZY_BACKUP_DIR")
BACKUP_KEEP = int(_env("FIZZY_BACKUP_KEEP", "30"))
SECURE_COOKIES = _env("FIZZY_SECURE_COOKIES", "1") == "1"

SQUARE_TOKEN = _env("SQUARE_ACCESS_TOKEN")
SQUARE_ENV = _env("SQUARE_ENVIRONMENT", "production")
SQUARE_LOCATION_ID = _env("SQUARE_LOCATION_ID")
SQUARE_WEBHOOK_KEY = _env("SQUARE_WEBHOOK_SIGNATURE_KEY")
SQUARE_WEBHOOK_URL = _env("SQUARE_WEBHOOK_URL", "https://fizzy.cookedcoke.co/square/webhook")
SQUARE_POLL_SECONDS = int(_env("SQUARE_POLL_SECONDS", "300"))
# Square payment source types that mean "put it on the tab". Square AU can't rename the
# "Other Payment Types" tender, so Other is reserved for tabs (EXTERNAL in the API);
# House Account (SQUARE_ACCOUNT) is counted too in case someone uses it. See README.
SQUARE_TAB_TENDERS = {t.strip().upper() for t in _env("SQUARE_TAB_TENDERS", "EXTERNAL,SQUARE_ACCOUNT").split(",") if t.strip()}
# Repayments are recorded in Fizzy only. Set this to an item name only if you ever ring
# repayments through Square (that counts the money as a second sale in Square's reports).
SQUARE_REPAYMENT_ITEM = _env("SQUARE_REPAYMENT_ITEM", "")

SQUARE_BASE = (
    "https://connect.squareupsandbox.com" if SQUARE_ENV == "sandbox" else "https://connect.squareup.com"
)

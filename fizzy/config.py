"""Configuration from environment variables. Secrets never live in the repo."""
import os
from zoneinfo import ZoneInfo


def _env(name, default=""):
    """A setting from the environment. Blank counts as unset (a `KEY=` line in .env), so it
    falls back to the default instead of turning a feature off or crashing on int("")."""
    return os.environ.get(name, "").strip() or default


DB_PATH = _env("FIZZY_DB", "data/fizzy.db")
PASSWORD = _env("FIZZY_PASSWORD")
SECRET_KEY = _env("FIZZY_SECRET_KEY")
STAFF_NAMES = [s.strip() for s in _env("FIZZY_STAFF", "Bru,Mike,Frankie").split(",") if s.strip()]
TZ = ZoneInfo(_env("FIZZY_TZ", "Australia/Sydney"))
BACKUP_DIR = _env("FIZZY_BACKUP_DIR")
BACKUP_KEEP = int(_env("FIZZY_BACKUP_KEEP", "30"))
SECURE_COOKIES = _env("FIZZY_SECURE_COOKIES", "1") == "1"

# Registered devices + Cloudflare Access (see devices.py). Off unless FIZZY_ACCESS_HOST is set.
HOST = _env("FIZZY_HOST", "fizzy.cookedcoke.co")                 # counter: registered devices only
ACCESS_HOST = _env("FIZZY_ACCESS_HOST")                          # anyone else, behind Cloudflare Access
CF_ACCESS_TEAM = _env("CF_ACCESS_TEAM_DOMAIN")                   # e.g. cookedcoke.cloudflareaccess.com
CF_ACCESS_AUD = _env("CF_ACCESS_AUD")                            # the Access application's audience tag

SQUARE_TOKEN = _env("SQUARE_ACCESS_TOKEN")
SQUARE_ENV = _env("SQUARE_ENVIRONMENT", "production")
SQUARE_LOCATION_ID = _env("SQUARE_LOCATION_ID")
SQUARE_WEBHOOK_KEY = _env("SQUARE_WEBHOOK_SIGNATURE_KEY")
SQUARE_WEBHOOK_URL = _env("SQUARE_WEBHOOK_URL", "https://fizzy.cookedcoke.co/square/webhook")
SQUARE_POLL_SECONDS = int(_env("SQUARE_POLL_SECONDS", "300"))
# How Fizzy recognises tab activity in Square. See README "How Square and Fizzy fit together".
# Tab sale: an order with this discount (a 100% discount called "Fizzy Tab"). The discounted
# amount goes on the customer's tab; Square records a $0 (or reduced) sale.
SQUARE_TAB_DISCOUNT = _env("SQUARE_TAB_DISCOUNT", "fizzy tab")
# Tab repayment: an order containing this item (a catalogue item with a blank, variable price).
SQUARE_REPAYMENT_ITEM = _env("SQUARE_REPAYMENT_ITEM", "tab repayment")
# Optional: payment types that also mean "on the tab" (e.g. EXTERNAL for Other Payment Types,
# SQUARE_ACCOUNT for House Account). Off by default - the discount is the tab button.
SQUARE_TAB_TENDERS = {t.strip().upper() for t in _env("SQUARE_TAB_TENDERS", "").split(",") if t.strip()}

SQUARE_BASE = (
    "https://connect.squareupsandbox.com" if SQUARE_ENV == "sandbox" else "https://connect.squareup.com"
)

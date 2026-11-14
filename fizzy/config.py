"""Configuration from environment variables. Secrets never live in the repo."""
import os
from zoneinfo import ZoneInfo


def _env(name, default=""):
    """A setting from the environment. Blank counts as unset (a `KEY=` line in .env), so it
    falls back to the default instead of turning a feature off or crashing on int("")."""
    return os.environ.get(name, "").strip() or default


DB_PATH = _env("FIZZY_DB", "data/fizzy.db")
PASSWORD = _env("FIZZY_PASSWORD")
ADMIN_PASSWORD = _env("FIZZY_ADMIN_PASSWORD")  # owner password for the dangerous stuff (optional)
SECRET_KEY = _env("FIZZY_SECRET_KEY")
STAFF_NAMES = [s.strip() for s in _env("FIZZY_STAFF", "Will,Mike,Frankie").split(",") if s.strip()]
TZ = ZoneInfo(_env("FIZZY_TZ", "Australia/Sydney"))
BACKUP_DIR = _env("FIZZY_BACKUP_DIR")
BACKUP_KEEP = int(_env("FIZZY_BACKUP_KEEP", "30"))
SECURE_COOKIES = _env("FIZZY_SECURE_COOKIES", "1") == "1"
# The Ask page (ask.py). Use a paid key: on the free tier Google may use what's sent to train its models.
GEMINI_API_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-flash-latest")

# Registered devices + Cloudflare Access (see devices.py). Off unless FIZZY_ACCESS_HOST is set.
HOST = _env("FIZZY_HOST", "fizzy.cookedcoke.co")                 # counter: registered devices only
ACCESS_HOST = _env("FIZZY_ACCESS_HOST")                          # anyone else, behind Cloudflare Access
CF_ACCESS_TEAM = _env("CF_ACCESS_TEAM_DOMAIN")                   # e.g. cookedcoke.cloudflareaccess.com
CF_ACCESS_AUD = _env("CF_ACCESS_AUD")                            # the Access application's audience tag
# Student site (see portal.py): students check their own points and tab. Only the student
# pages are served on this host. Unset: the student pages are at /me on the staff site.
PUBLIC_HOST = _env("FIZZY_PUBLIC_HOST").lower()                  # e.g. my.cookedcoke.co
# Short links printed on hunt posters (challenges.py): link.cookedcoke.co/<code>. Fizzy answers on this
# host too, with the file Apple needs so the My CCC App Clip (and app) open these links. Off unless set.
LINK_HOST = _env("FIZZY_LINK_HOST").lower()                      # e.g. link.cookedcoke.co
APPLE_TEAM_ID = _env("FIZZY_APPLE_TEAM_ID", "9MQ6DTJYXF")
STUDENT_APP_ID = _env("FIZZY_STUDENT_APP_ID", "co.cookedcoke.student")
# My CCC on the App Store, as a QR code on the customer display. Set it blank to leave the code off.
STUDENT_APP_STORE_URL = _env("FIZZY_STUDENT_APP_STORE_URL", "https://apps.apple.com/au/app/id6829730460")
# Discord announcements for new rewards and double points days (see discord.py). Off unless set.
DISCORD_WEBHOOK_URL = _env("DISCORD_WEBHOOK_URL")
DISCORD_MENTION = _env("DISCORD_MENTION")                        # e.g. @everyone, or <@&ROLE_ID>

# Security (see security.py): fridge-camera incidents sent by the clip analyser on the home server, and
# the Blink cameras' arm/disarm switch through Home Assistant. Each part is off until its settings are set.
SECURITY_TOKEN = _env("FIZZY_SECURITY_TOKEN")                    # the analyser's key for /security/ingest
INCIDENT_MAX_GB = float(_env("FIZZY_INCIDENT_MAX_GB", "5"))      # clip storage; oldest clips go first
HA_URL = _env("FIZZY_HA_URL", "https://home.w-l.xyz").rstrip("/")
HA_TOKEN = _env("FIZZY_HA_TOKEN")                                # a Home Assistant long-lived access token
HA_ALARM_ENTITY = _env("FIZZY_HA_ALARM_ENTITY", "alarm_control_panel.blink_cooked_coke_co_security")

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

# Paying off a tab in My CCC with Apple Pay: the app gets a one-time card nonce from Square's SDK and
# Fizzy charges it here. Off until the Apple Pay merchant ID and Square certificate are set up.
APPLE_PAY = _env("FIZZY_APPLE_PAY", "0") == "1"
# The student website points students who owe money at the app to pay. Off until the app version with
# Apple Pay is on the App Store.
APP_PAY_PROMO = _env("FIZZY_APP_PAY_PROMO", "0") == "1"
SQUARE_APP_ID = _env("SQUARE_APPLICATION_ID")                     # sq0idp-... (sandbox-sq0idb-... in sandbox): not secret
SQUARE_CURRENCY = _env("SQUARE_CURRENCY", "AUD")
APPLE_PAY_MERCHANT_ID = _env("FIZZY_APPLE_PAY_MERCHANT_ID", "merchant.co.cookedcoke.student")

SQUARE_BASE = (
    "https://connect.squareupsandbox.com" if SQUARE_ENV == "sandbox" else "https://connect.squareup.com"
)

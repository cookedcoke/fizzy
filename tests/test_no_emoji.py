"""The staff site has no emojis, anywhere (the student site's badges are fine)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fizzy"
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿️]")
STUDENT = {"badges.py"}  # the student site's badges


def test_no_emojis_on_the_staff_site():
    files = [f for f in (ROOT / "templates").glob("*.html") if not f.name.startswith(("me", "_me"))]
    files += [ROOT / "static" / n for n in ("app.js", "style.css", "down-staff.html", "offline.html")]
    files += [f for f in ROOT.glob("*.py") if f.name not in STUDENT]
    found = [f"{f.name}:{i}" for f in files for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
             if EMOJI.search(line)]
    assert not found, found

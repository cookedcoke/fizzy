import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from fizzy import fuzzy

# Made-up names with the shapes that matter: shared first names, shared surnames,
# multi-word surnames, hyphens and apostrophes.
NAMES = [
    "Nora Zhang", "Nora Combe", "Nora Le Fevre", "Norah Sun", "Ivy Zhang", "Leo Zhang",
    "Charlotte Ng", "Cora Zhao", "Adeline Yates", "Elina Fong", "Jayden Lim", "Jayden Teo",
    "Jayden Kirk", "William Ruse", "William Bates", "Hugo Williams", "Jemima O'Donnell",
    "Ethan Ashby-Nolan", "Sebastian Holloway", "Seb Tran", "Milo Lund", "Mimi Kerr", "Will Hartley Brown",
]
PEOPLE = [{"name": n, "sid": f"rns0{8000000 + i * 37}"} for i, n in enumerate(NAMES)]


def names(q, n=5):
    return [p["name"] for p, _ in fuzzy.rank(q, PEOPLE, n)]


def test_prefix_and_word_starts():
    assert names("nora z")[0] == "Nora Zhang"
    assert names("zhang nora") == ["Nora Zhang"]          # any word order
    assert names("le fev")[0] == "Nora Le Fevre"


def test_fzf_subsequence_and_initials():
    assert names("nozh")[0] == "Nora Zhang"
    assert names("ashnol") == ["Ethan Ashby-Nolan"]
    assert names("odonnell") == ["Jemima O'Donnell"]


def test_word_start_beats_middle_of_word():
    # "li" at the start of a surname should beat "li" buried inside a word.
    assert names("li")[0] == "Jayden Lim"


def test_misspellings():
    assert set(names("zhnag", 10)) >= {"Nora Zhang", "Ivy Zhang", "Leo Zhang"}   # swapped letters
    assert {"William Bates", "William Ruse"} <= set(names("wiliam")[:3])          # missing letter
    assert names("jaiden")[0].startswith("Jayden")                                 # wrong letter
    assert names("adelin yaets") == ["Adeline Yates"]


def test_student_number():
    sid = PEOPLE[3]["sid"]
    assert names(sid) == ["Norah Sun"]
    assert names(sid[3:]) == ["Norah Sun"]


def test_highlight_positions():
    (_, pos), = fuzzy.rank("nozh", [{"name": "Nora Zhang", "sid": ""}])
    assert pos == [0, 1, 5, 6]


def test_no_match():
    assert names("xyzzy") == []


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_browser_copy_ranks_identically():
    rng = random.Random(7)
    queries = ["nor", "nozh", "zhnag", "zhang nora", "li", "wiliam", "jaiden", "seb", "o'd", "rns080",
               "ashby-n", "mike", "lu", "w", "adelin yaets", "e", "xyz"]
    for _ in range(80):  # plus random fragments and mangled names
        n = rng.choice(NAMES).lower()
        i = rng.randrange(len(n))
        q = n[i:i + rng.randint(2, 7)]
        if rng.random() < 0.4 and len(q) > 3:
            j = rng.randrange(len(q) - 1)
            q = q[:j] + q[j + 1] + q[j] + q[j + 2:]
        queries.append(q.strip() or "a")
    expected = {q: [[p["name"], pos] for p, pos in fuzzy.rank(q, PEOPLE, 8)] for q in queries}
    js = Path(__file__).parent.parent / "fizzy" / "static" / "fuzzy.js"
    script = (f"const f = require({json.dumps(str(js))}); const people = {json.dumps(PEOPLE)};"
              f"const out = {{}}; for (const q of {json.dumps(queries)}) out[q] = f.rank(q, people, 8).map(h => [h[0].name, h[1]]);"
              "console.log(JSON.stringify(out));")
    got = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    for q in queries:
        assert got[q] == expected[q], q

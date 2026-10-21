"""Customer name matching: fzf-style fuzzy scoring plus typo tolerance.

fzf scores a query as an in-order subsequence of the name ("chzh" -> NOra ZHang), rewarding
matches at word starts and consecutive runs, and penalising gaps. That handles skipped
letters and abbreviations but not misspellings, so any query word that doesn't match as a
subsequence falls back to an edit distance (with swaps) against the name's words.

static/app.js has a line-for-line JavaScript copy so search can run in the browser;
tests/test_fuzzy.py checks the two give identical rankings.
"""
SCORE_MATCH = 16
GAP_START = -3
GAP_EXT = -1
BONUS_BOUNDARY = 8          # match right after a space/hyphen/apostrophe, or at the start
BONUS_CONSECUTIVE = 4       # each extra letter in an unbroken run
FIRST_CHAR_MULTIPLIER = 2   # the query's first letter landing on a word start counts double
NEG = float("-inf")
SEPARATORS = " -'."


def _bonus(text, j):
    return BONUS_BOUNDARY if j == 0 or text[j - 1] in SEPARATORS else 0


def fzf(pat, text):
    """Best fzf-style score for pat inside text, and the matched positions. None if no match."""
    n, m = len(pat), len(text)
    if n == 0 or n > m:
        return None
    # Quick reject: must be an in-order subsequence.
    k = 0
    for ch in text:
        if ch == pat[k]:
            k += 1
            if k == n:
                break
    if k < n:
        return None
    H = [[NEG] * m for _ in range(n)]   # best score with pat[i] matched at text[j]
    B = [[0] * m for _ in range(n)]     # bonus carried along a consecutive run
    P = [[-1] * m for _ in range(n)]    # where pat[i-1] was matched (for highlighting)
    for i in range(n):
        gap_best, gap_j = NEG, -1       # best H[i-1][k] + gap penalty for k <= j-2
        for j in range(m):
            if i > 0 and j >= 2:
                if gap_best > NEG:
                    gap_best += GAP_EXT
                cand = H[i - 1][j - 2] + GAP_START
                if cand > gap_best:
                    gap_best, gap_j = cand, j - 2
            if text[j] != pat[i]:
                continue
            b = _bonus(text, j)
            if i == 0:
                H[i][j], B[i][j] = SCORE_MATCH + b * FIRST_CHAR_MULTIPLIER, b
                continue
            consec = NEG
            if j >= 1 and H[i - 1][j - 1] > NEG:
                cb = max(B[i - 1][j - 1], BONUS_CONSECUTIVE, b)
                consec = H[i - 1][j - 1] + SCORE_MATCH + cb
            gapped = gap_best + SCORE_MATCH + b if gap_best > NEG else NEG
            if consec >= gapped and consec > NEG:
                H[i][j], B[i][j], P[i][j] = consec, cb, j - 1
            elif gapped > NEG:
                H[i][j], B[i][j], P[i][j] = gapped, b, gap_j
    last = n - 1
    best_j = max(range(m), key=lambda j: (H[last][j], -j))
    if H[last][best_j] == NEG:
        return None
    pos, j = [], best_j
    for i in range(last, -1, -1):
        pos.append(j)
        j = P[i][j]
    return H[last][best_j], pos[::-1]


def typo_distance(a, b, limit):
    """Damerau-Levenshtein distance (adjacent swaps count as 1), stopping early past limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[len(b)]


def _typo(term, name):
    """Score a misspelt term against the name's words (whole word or same-length prefix)."""
    if len(term) < 3:
        return None
    limit = 1 if len(term) < 7 else 2
    best, best_start, start = limit + 1, 0, 0
    for w in name.split(" "):
        for cand in (w, w[:len(term)]):
            d = typo_distance(term, cand, limit)
            if d < best:
                best, best_start = d, start
        start += len(w) + 1
    if best > limit:
        return None
    return len(term) * 6 - best * 12, list(range(best_start, best_start + min(len(term), len(name) - best_start)))


def match(query, name, sid=""):
    """Score a query against one customer. Returns (score, positions) or None.
    Every space-separated word must match somewhere (any order), like fzf."""
    q = " ".join(query.lower().split())
    if not q:
        return None
    if q.isdigit() or (q.startswith("rns") and q[3:].isdigit()):
        return (1000 - len(sid), []) if q in sid else None
    lname = name.lower()
    total, positions = 0, set()
    for term in q.split(" "):
        hit = fzf(term, lname) or _typo(term, lname)
        if hit is None:
            return None
        total += hit[0]
        positions.update(hit[1])
    return total, sorted(positions)


def rank(query, people, limit=8, key=lambda p: (p["name"], p.get("sid", ""))):
    """Best matches first; ties go to the shorter, then alphabetically first, name."""
    scored = []
    for p in people:
        name, sid = key(p)
        m = match(query, name, sid)
        if m:
            scored.append((-m[0], len(name), name, m[1], p))
    scored.sort(key=lambda s: s[:3])
    return [(s[4], s[3]) for s in scored[:limit]]

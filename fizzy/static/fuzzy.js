// Customer name matching for the in-browser search: a line-for-line copy of fizzy/fuzzy.py
// (fzf-style scoring + typo fallback). tests/test_fuzzy.py runs both under the same queries
// and checks they rank identically, so change them together.
(function (root) {
  "use strict";
  var SCORE_MATCH = 16, GAP_START = -3, GAP_EXT = -1, BONUS_BOUNDARY = 8, BONUS_CONSECUTIVE = 4,
      FIRST_CHAR_MULTIPLIER = 2, NEG = -Infinity, SEPARATORS = " -'.";

  function bonusAt(text, j) { return j === 0 || SEPARATORS.indexOf(text[j - 1]) >= 0 ? BONUS_BOUNDARY : 0; }

  function fzf(pat, text) {
    var n = pat.length, m = text.length, i, j, k = 0;
    if (n === 0 || n > m) return null;
    for (j = 0; j < m; j++) { if (text[j] === pat[k] && ++k === n) break; }
    if (k < n) return null;
    var H = [], B = [], P = [];
    for (i = 0; i < n; i++) { H.push(new Array(m).fill(NEG)); B.push(new Array(m).fill(0)); P.push(new Array(m).fill(-1)); }
    for (i = 0; i < n; i++) {
      var gapBest = NEG, gapJ = -1;
      for (j = 0; j < m; j++) {
        if (i > 0 && j >= 2) {
          if (gapBest > NEG) gapBest += GAP_EXT;
          var cand = H[i - 1][j - 2] + GAP_START;
          if (cand > gapBest) { gapBest = cand; gapJ = j - 2; }
        }
        if (text[j] !== pat[i]) continue;
        var b = bonusAt(text, j);
        if (i === 0) { H[i][j] = SCORE_MATCH + b * FIRST_CHAR_MULTIPLIER; B[i][j] = b; continue; }
        var consec = NEG, cb = 0;
        if (j >= 1 && H[i - 1][j - 1] > NEG) {
          cb = Math.max(B[i - 1][j - 1], BONUS_CONSECUTIVE, b);
          consec = H[i - 1][j - 1] + SCORE_MATCH + cb;
        }
        var gapped = gapBest > NEG ? gapBest + SCORE_MATCH + b : NEG;
        if (consec >= gapped && consec > NEG) { H[i][j] = consec; B[i][j] = cb; P[i][j] = j - 1; }
        else if (gapped > NEG) { H[i][j] = gapped; B[i][j] = b; P[i][j] = gapJ; }
      }
    }
    var last = n - 1, bestJ = 0;
    for (j = 1; j < m; j++) { if (H[last][j] > H[last][bestJ]) bestJ = j; }
    if (H[last][bestJ] === NEG) return null;
    var pos = [];
    for (i = last, j = bestJ; i >= 0; i--) { pos.push(j); j = P[i][j]; }
    return [H[last][bestJ], pos.reverse()];
  }

  function typoDistance(a, b, limit) {
    if (Math.abs(a.length - b.length) > limit) return limit + 1;
    var prev2 = null, prev = [], i, j;
    for (j = 0; j <= b.length; j++) prev.push(j);
    for (i = 1; i <= a.length; i++) {
      var cur = [i];
      for (j = 1; j <= b.length; j++) {
        var cost = a[i - 1] === b[j - 1] ? 0 : 1;
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
        if (prev2 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) cur[j] = Math.min(cur[j], prev2[j - 2] + 1);
      }
      if (Math.min.apply(null, cur) > limit) return limit + 1;
      prev2 = prev; prev = cur;
    }
    return prev[b.length];
  }

  function typo(term, name) {
    if (term.length < 3) return null;
    var limit = term.length < 7 ? 1 : 2, best = limit + 1, bestStart = 0, start = 0, words = name.split(" ");
    for (var w = 0; w < words.length; w++) {
      var cands = [words[w], words[w].slice(0, term.length)];
      for (var c = 0; c < 2; c++) {
        var d = typoDistance(term, cands[c], limit);
        if (d < best) { best = d; bestStart = start; }
      }
      start += words[w].length + 1;
    }
    if (best > limit) return null;
    var pos = [];
    for (var p = bestStart; p < bestStart + Math.min(term.length, name.length - bestStart); p++) pos.push(p);
    return [term.length * 6 - best * 12, pos];
  }

  function match(query, name, sid) {
    var q = query.toLowerCase().split(/\s+/).filter(Boolean).join(" ");
    if (!q) return null;
    if (/^\d+$/.test(q) || /^rns\d+$/.test(q)) return sid.indexOf(q) >= 0 ? [1000 - sid.length, []] : null;
    var lname = name.toLowerCase(), total = 0, positions = {}, terms = q.split(" ");
    for (var t = 0; t < terms.length; t++) {
      var hit = fzf(terms[t], lname) || typo(terms[t], lname);
      if (!hit) return null;
      total += hit[0];
      hit[1].forEach(function (x) { positions[x] = 1; });
    }
    return [total, Object.keys(positions).map(Number).sort(function (a, b) { return a - b; })];
  }

  function rank(query, people, limit) {
    var scored = [];
    for (var k = 0; k < people.length; k++) {
      var m = match(query, people[k].name, people[k].sid || "");
      if (m) scored.push([-m[0], people[k].name.length, people[k].name, m[1], people[k]]);
    }
    scored.sort(function (a, b) { return a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0); });
    return scored.slice(0, limit || 8).map(function (s) { return [s[4], s[3]]; });
  }

  var api = { rank: rank, match: match, fzf: fzf, typoDistance: typoDistance };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.FizzyFuzzy = api;
})(this);

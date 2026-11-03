// Fizzy counter UI: instant search, card scans, confirm and passcode boxes. No dependencies
// besides Turbo (turbo.js), which swaps pages in place instead of loading each one from scratch.
//
// This file runs ONCE per browser tab, not once per page: Turbo keeps it (and `document`, `window`)
// from page to page. So it's in two parts:
//  - things set up once, at the top level of this file: card scans, the confirm box, passcodes;
//  - init(), run for every page as it's drawn. Anything it starts that outlives the page's own
//    elements (a timer, a listener on window or document) goes through listen() / every() /
//    later(), and is undone by teardown() before the next page is drawn.
//
// Search runs in the browser against /api/index (every active customer; fetched again when it's
// over a minute old or after a form was sent), so typing never waits on the network. The highlighted
// result is prerendered so Enter opens it instantly. (There are no keyboard shortcuts: everything is
// a button.)
(function () {
  "use strict";
  var $ = function (s, el) { return (el || document).querySelector(s); };
  var $$ = function (s, el) { return Array.prototype.slice.call((el || document).querySelectorAll(s)); };
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function money(c) { return (c < 0 ? "-" : "") + "$" + (Math.abs(c) / 100).toFixed(2); }
  function typing(el) {
    el = el || document.activeElement;
    return el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) && el.type !== "checkbox" && el.type !== "submit";
  }
  function json(sel) { var el = $(sel); return el ? JSON.parse(el.textContent) : null; }

  // ---- a page's lifetime ------------------------------------------------------------------------
  var cleanups = [];
  function later(fn) { cleanups.push(fn); }  // run fn when this page goes
  function listen(target, type, fn, opts) {
    target.addEventListener(type, fn, opts);
    later(function () { target.removeEventListener(type, fn, opts); });
  }
  function every(fn, ms) { var id = setInterval(fn, ms); later(function () { clearInterval(id); }); return id; }
  function teardown() {
    var c = cleanups; cleanups = [];
    c.forEach(function (f) { try { f(); } catch (e) { /* the next one still runs */ } });
  }

  // ---- search -------------------------------------------------------------------------------
  var STALE = 60 * 1000;  // points and balances change at the counter all day
  var index = null, indexAt = 0, indexReq = null, indexError = null;
  function loadIndex() {
    if (!indexReq && (!index || Date.now() - indexAt > STALE)) {
      indexReq = fetch("/api/index", { credentials: "same-origin", cache: "no-store" }).then(function (r) {
        if (r.redirected && r.url.indexOf("/login") >= 0) throw new Error("signed-out");
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      }).then(function (rows) {
        index = rows; indexAt = Date.now(); indexError = null; indexReq = null;
        return rows;
      }).catch(function (err) { indexReq = null; indexError = String(err && err.message || err); });
    }
    return indexReq || Promise.resolve(index);
  }

  var rank = window.FizzyFuzzy.rank;

  function highlight(name, pos) {
    var out = "", set = {};
    pos.forEach(function (p) { set[p] = 1; });
    for (var i = 0; i < name.length; i++) out += set[i] ? "<mark>" + esc(name[i]) + "</mark>" : esc(name[i]);
    return out.replace(/<\/mark><mark>/g, "");
  }

  var spec = null;
  // Forget the prerendered customer: after a change (a form sent) or on another page it may be out of
  // date. Turbo leaves added <script>s in the head, so without this it could outlive many pages.
  function unprerender() { if (spec) { spec.remove(); spec = null; } }
  function prerender(href) {
    // Chrome/Edge: prerender the highlighted customer so Enter is instant. Others: prefetch.
    if (spec && spec.dataset.href === href && spec.isConnected) return;
    if (spec) spec.remove();
    if (HTMLScriptElement.supports && HTMLScriptElement.supports("speculationrules")) {
      spec = document.createElement("script");
      spec.type = "speculationrules";
      spec.textContent = JSON.stringify({ prerender: [{ source: "list", urls: [href] }] });
    } else {
      spec = document.createElement("link");
      spec.rel = "prefetch"; spec.href = href;
    }
    spec.dataset.href = href;
    document.head.appendChild(spec);
  }

  function bindSearch(box) {
    var out = box.parentNode.querySelector(".results"), sel = 0, rows = [];
    function mark() {
      $$("a.hit", out).forEach(function (a, i) { a.classList.toggle("sel", i === sel); });
      if (rows[sel]) prerender("/customers/" + rows[sel].id);
    }
    function render() {
      var q = box.value.trim();
      if (!q) { out.innerHTML = ""; rows = []; return; }
      if (!index) {
        if (indexError) {
          out.innerHTML = indexError === "signed-out"
            ? '<div class="none">Signed out. <a href="/login" data-turbo="false">Sign in again</a>.</div>'
            : '<div class="none">Couldn\'t load the customer list. Press Enter to search on the server.</div>';
          indexError = null;  // try again on the next keystroke
          return;
        }
        loadIndex().then(function () { if (box.isConnected) render(); }); return;
      }
      var hits = rank(q, index, 8); rows = hits.map(function (h) { return h[0]; }); sel = 0;
      out.innerHTML = rows.length ? hits.map(function (h, i) {
        var r = h[0];
        return '<a class="hit" href="/customers/' + r.id + '"><span><span class="nm">' + highlight(r.name, h[1]) + '</span><span class="em">' + esc(r.sid) + "</span></span>" +
          '<span class="facts"><span>' + r.points.toLocaleString() + " pts</span><span>owes " + money(r.owing) + "</span>" + (r.reward ? '<span class="tag reward" title="Can redeem a reward">Reward</span>' : "") + '<span class="tag ' + esc(r.status) + '">' + esc(r.label) + "</span></span></a>";
      }).join("") : '<div class="none">No one matches “' + esc(q) + "”.</div>";
      mark();
    }
    listen(box, "input", render);
    listen(box, "focus", function () { loadIndex(); if (box.value.trim() && !out.innerHTML) render(); });
    listen(box, "keydown", function (e) {
      if (e.key === "ArrowDown") { sel = Math.min(sel + 1, rows.length - 1); mark(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); mark(); e.preventDefault(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (window.fizzyScan && fizzyScan(box.value.trim())) return;  // card scanned into the search box
        // A real page load on purpose: it picks up Chrome's prerender of the highlighted row.
        if (!index) { location.href = "/customers?q=" + encodeURIComponent(box.value.trim()); }  // server search fallback
        else if (rows[sel]) location.href = "/customers/" + rows[sel].id;
      }
      else if (e.key === "Escape") { e.stopPropagation(); if (box.value) { box.value = ""; render(); } else box.blur(); }
    });
    listen(out, "mousemove", function (e) {
      var a = e.target.closest("a.hit"); if (!a) return;
      var i = $$("a.hit", out).indexOf(a); if (i !== sel) { sel = i; mark(); }
    });
    listen(document, "click", function (e) { if (!out.contains(e.target) && e.target !== box) out.innerHTML = ""; });
  }

  // ---- student card scans (once) -------------------------------------------------------------
  // A USB barcode scanner "types" the card's RNS number fast and presses Enter: catch it (when no
  // box has focus) and open that student. Keys held back that turn out to be a person typing are
  // passed on as normal.
  var SCAN = /^(r|rn|rns\d{0,8})$/i, scanBuf = "", scanHeld = [], scanTimer = 0;
  function scanFlush() {
    var held = scanHeld; scanBuf = ""; scanHeld = []; clearTimeout(scanTimer);
    held.forEach(function (k) { document.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true })); });
  }
  window.fizzyScan = function (code) {
    if (!/^rns\d{8}$/i.test(code)) return false;
    location.href = "/scan?code=" + encodeURIComponent(code.toLowerCase());
    return true;
  };
  document.addEventListener("keydown", function (e) {
    if (!e.isTrusted || e.ctrlKey || e.metaKey || e.altKey || typing() || document.querySelector("dialog[open]")) return;
    if (e.key === "Enter" && scanBuf) {
      e.preventDefault(); e.stopImmediatePropagation();
      if (fizzyScan(scanBuf)) { scanBuf = ""; scanHeld = []; clearTimeout(scanTimer); } else scanFlush();
      return;
    }
    if (e.key.length !== 1 || !SCAN.test(scanBuf + e.key)) { if (scanBuf) scanFlush(); return; }
    e.preventDefault(); e.stopImmediatePropagation();
    scanBuf += e.key; scanHeld.push(e.key);
    clearTimeout(scanTimer); scanTimer = setTimeout(scanFlush, 80);  // scanners send keys a few ms apart
  }, true);

  // ---- confirm box (once) -----------------------------------------------------------------------
  // Any button with data-confirm asks first, however it's pressed (click or Enter).
  // Asked in Fizzy's own box rather than the browser's. The box moves into each new page's <body>.
  var ask = document.createElement("dialog"), onYes = null, passed = null;
  ask.className = "ask";
  ask.innerHTML = '<p></p><div class="actions"><button type="button" class="primary" value="ok">Yes, do it</button>' +
                  '<button type="button" value="no">Cancel</button></div>';
  ask.addEventListener("click", function (e) {
    var v = e.target.value;
    if (e.target === ask || v === "no") ask.close();
    if (v === "ok") { var f = onYes; ask.close(); f(); }
  });
  window.fizzyAsk = function (text, yes) {
    onYes = yes; ask.querySelector("p").textContent = text; ask.showModal();
    ask.querySelector("[value=ok]").focus();
  };
  // ---- clicks during the cross-fade (once) ------------------------------------------------------
  // Turbo cross-fades a page change (a view transition, about 0.16s). While one runs, the browser sends
  // every click to <html>, so a quick second click (a tab, then at once a row) would be lost: finish
  // the fade at once and click whatever is now under the pointer.
  var fade = null;
  if (document.startViewTransition) {
    var startFade = document.startViewTransition;
    document.startViewTransition = function () {
      var t = startFade.apply(document, arguments), done = function () { if (fade === t) fade = null; };
      fade = t; t.finished.then(done, done);
      return t;
    };
  }
  document.addEventListener("click", function (e) {
    var t = fade || document.activeViewTransition;
    if (!t || e.target !== document.documentElement) return;
    e.preventDefault(); e.stopImmediatePropagation();
    var x = e.clientX, y = e.clientY, go = function () {
      var el = document.elementFromPoint(x, y);
      if (el && el !== document.documentElement) { if (typing(el)) el.focus(); else el.click(); }
    };
    try { t.skipTransition(); } catch (err) { /* already over */ }
    t.finished.then(go, go);
  }, true);

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-confirm]");
    if (!b || b.tagName === "DETAILS") return;
    if (passed === b) { passed = null; return; }  // the second, confirmed click
    e.preventDefault(); e.stopImmediatePropagation();
    fizzyAsk(b.dataset.confirm, function () { passed = b; b.click(); });
  }, true);

  // ---- passcodes (once) ---------------------------------------------------------------------------
  // Anything above "anyone signed in" asks in a box first, then carries on. The rules come from the
  // owner's "Who can do what" settings (each page carries them; init() reads them); the server
  // checks them again either way.
  var lockRules = null, lockCust = null;
  function num(v) {  // same as the server: blank is 0, anything that isn't plainly a number is huge
    v = String(v || "").replace(/[$,\s]/g, "");
    if (!v) return 0;
    var n = Number(v); return isNaN(n) ? Infinity : Math.abs(n);
  }
  function have() {
    var d = document.body.dataset;
    return Number(d.unlockedUntil || 0) > Date.now() / 1000 + 5 ? Number(d.unlocked || 0) : 0;
  }
  function need(method, url, data) {
    if (!lockRules) return 0;
    var u = new URL(url, location.href), path = u.pathname;
    if (u.origin !== location.origin) return 0;
    var R = lockRules, test = function (re) { return new RegExp(re).test(path); };
    if (R.free.some(test)) return 0;
    var locked = R.locked.filter(function (l) { return test(l[0]); })[0];
    if (locked) return locked[1];
    var act = R.actions.filter(function (a) { return a[0] === method && test(a[1]); })[0];
    var level = act ? act[2] : method === "POST" ? R.other : 0;
    var people = data ? Math.max(1, String(data.get("people") || "").split(/[\n;]+/).filter(function (x) { return x.trim(); }).length) : 1;
    if (data && test(R.big.paths) && (data.get("everyone") || num(data.get("delta")) * people > R.big.points ||
        num(data.get("points")) > R.big.points || num(data.get("amount")) > R.big.dollars)) {
      level = Math.max(level, R.big.level);
    }
    if (data && path === "/tools/write_off" && !String(data.get("amount") || "").trim()) level = Math.max(level, R.big.level);
    if (data && path === "/tools/extend_due" && num(data.get("days") || 1) > 30) level = Math.max(level, R.big.level);
    var run = R.big.running;  // lots of small hand changes in the last hour add up
    if (data && run && test(R.big.paths) && path !== "/events") {
      var p = num(data.get("delta")) * people + num(data.get("points")), d = num(data.get("amount"));
      if ((p || d) && (run.points + p > R.big.points * run.times || run.dollars + d > R.big.dollars * run.times)) level = Math.max(level, R.big.level);
    }
    if (data && path === "/events") {
      var s0 = data.get("start"), e0 = data.get("end") || s0, days = (Date.parse(e0) - Date.parse(s0)) / 864e5;
      var big = !!data.get("weekly") || num(data.get("multiplier") || 2) > 2 || !(days <= 6);
      level = big ? Math.max(level, R.big.level) : level;
    }
    return level;
  }
  // On a customer's page: admin and test accounts change the level (the server works this out for names typed into tools).
  function needFor(method, url, data) {
    var n = need(method, url, data), path = new URL(url, location.href).pathname;
    if (n === undefined || !lockCust || !lockCust.kind || path.indexOf("/customers/" + lockCust.id + "/") !== 0 || /\/(claim|claim\/next|kind)$/.test(path)) return n;
    if (lockCust.kind === "test") return lockCust.levels[0];
    return Math.max(n, lockCust.levels[n]);
  }
  function needsPin(method, url, data) { var n = needFor(method, url, data); return n > have() ? n : 0; }
  var pinBox = document.createElement("dialog"), pinThen = null, pinNo = null, pinNeed = 1;
  pinBox.className = "ask";
  pinBox.innerHTML = '<form method="dialog"><p><b>Enter passcode</b></p>' +
    '<input type="password" autocomplete="off" aria-label="Passcode">' +
    '<div class="err" role="alert" hidden></div>' +
    '<div class="actions"><button class="primary" value="ok">Unlock</button><button type="button" value="no">Cancel</button></div></form>';
  var pinIn = $("input", pinBox), pinErr = $(".err", pinBox);
  $("[value=no]", pinBox).addEventListener("click", function () { pinBox.close(); });
  $("form", pinBox).addEventListener("submit", function (e) {
    e.preventDefault();
    var body = new URLSearchParams({ pin: pinIn.value, need: pinNeed });
    fetch("/settings/pin/unlock", { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        if (!r.ok) { pinErr.hidden = false; pinErr.textContent = r.error; pinIn.select(); return; }
        document.body.dataset.unlocked = r.rank; document.body.dataset.unlockedUntil = r.until;
        var again = pinThen; pinThen = null; pinNo = null; pinBox.close(); again();
      })
      .catch(function () { pinErr.hidden = false; pinErr.textContent = "Couldn't reach Fizzy. Try again."; });
  });
  // Closed any other way than unlocking (Cancel, Esc): no() hears about it.
  pinBox.addEventListener("close", function () {
    if (pinBox.open) return;  // already asking again
    var no = pinNo; pinThen = null; pinNo = null; if (no) no();
  });
  function askPin(level, then, no) {
    pinNeed = level; pinThen = then; pinNo = no || null; pinIn.value = ""; pinErr.hidden = true; pinBox.showModal(); pinIn.focus();
  }
  // A form that wasn't sent after all (the passcode box was cancelled): a control that sends its form
  // as soon as it's changed (Square's "Counted as") shows the saved value again, its data-was.
  function unsent(f) { $$("[data-was]", f).forEach(function (c) { c.value = c.dataset.was; }); }
  window.fizzyPin = function (method, url, then) { var n = needsPin(method, url); if (n) askPin(n, then); else then(); };
  // A question or passcode box belongs to the page it was asked on: leaving the page (a link, Back)
  // answers "no". Left open, it would follow into the next page's <body> as a stray, non-modal box.
  function closeBoxes() {
    $$("dialog[open]").forEach(function (d) { d.close(); });
    onYes = null; pinThen = null; pinNo = null;
  }
  // Runs before Turbo sends the form (Turbo leaves alone a submit that's been stopped).
  document.addEventListener("submit", function (e) {
    var f = e.target, method = (f.getAttribute("method") || "get").toUpperCase();
    if (e.defaultPrevented || f.closest("dialog.ask")) return;
    var by = e.submitter;  // a button with its own formaction (the pop-up's No account) is checked as that
    var n = needsPin(method, by && by.hasAttribute("formaction") ? by.formAction : f.action, new FormData(f));
    if (!n) return;
    e.preventDefault();
    askPin(n, function () { f.requestSubmit(by && by.form === f ? by : undefined); }, function () { unsent(f); });
  });
  // The server has the last word: when it wants a higher passcode than this page worked out (it knows
  // things the page doesn't, like whether a Square payment is tab money), a form Turbo sent comes back
  // as 423 with the level it needs. Ask for that, then send the same form again.
  var sentWith = null;  // the button that sent the form now in flight
  document.addEventListener("turbo:submit-start", function (e) { sentWith = e.detail.formSubmission.submitter || null; });
  document.addEventListener("turbo:before-fetch-response", function (e) {
    var r = e.detail.fetchResponse, f = e.target, by = sentWith;
    if (r.statusCode !== 423 || r.header("X-Fizzy") !== "pin" || !f || f.tagName !== "FORM") return;
    e.preventDefault();
    r.responseText.then(function (t) {
      var n = Number(JSON.parse(t).need) || 1;
      if (f.isConnected) askPin(n, function () { f.requestSubmit(by && by.form === f ? by : undefined); }, function () { unsent(f); });
    });
  });
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    var n = a && needsPin("GET", a.href);
    if (!n) return;
    e.preventDefault();
    // Unlocked now: this time it goes through (with Turbo, or as a download), without asking its
    // "are you sure?" question a second time.
    askPin(n, function () { passed = a.closest("[data-confirm]"); a.click(); });
  });

  // Every Turbo request says which version of Fizzy's scripts this tab is running. After an update the
  // server then knows Turbo will throw its answer away and load the page again for real
  // (data-turbo-track), and keeps the "Saved." message and the section to land on for that load.
  // The version is the one this file was loaded as, read once: an error page Turbo draws brings its own
  // <head>, and with it a newer ?v= after an update, while this old code keeps running.
  function version() { var s = $('script[src*="/static/app.js"]'), m = s && /[?&]v=([^&#]+)/.exec(s.getAttribute("src")); return m ? m[1] : null; }
  var V = version();
  document.addEventListener("turbo:before-fetch-request", function (e) {
    if (V) e.detail.fetchOptions.headers["X-Fizzy-V"] = V;
  });

  // Turbo 8 leaves two kinds of failed request as an "Uncaught (in promise)" error although it has
  // dealt with them: a page download cut off by Back or another click (AbortError), and a request
  // that couldn't reach Fizzy at all (it reports that as turbo:fetch-request-error first, and app.js
  // loads the page for real so the offline page shows). Neither is a problem: keep them out of the console.
  // (A form sent while Fizzy can't be reached still logs one "Failed to fetch": Turbo writes that one
  // to the console itself, and the offline page shows all the same.)
  var turboErrors = [];
  document.addEventListener("turbo:fetch-request-error", function (e) { turboErrors = [e.detail.error].concat(turboErrors.slice(0, 4)); });
  addEventListener("unhandledrejection", function (e) {
    var r = e.reason;
    if (r && (r.name === "AbortError" || turboErrors.indexOf(r) >= 0)) e.preventDefault();
  });

  function sendDisplay(m) {
    try { window.webkit.messageHandlers.fizzyDisplay.postMessage(m); } catch (e) {}
    try { new BroadcastChannel("fizzy-display").postMessage(m); } catch (e) {}
  }

  // ---- what the customer display shows (header, "Display") ----------------------------------
  // Chosen per device (the counter computer driving the display), so it's kept in localStorage.
  // "auto": each page says what it shows (a customer's page: them; any other page: the idle screens).
  // Anything else holds the display there whatever page is open: "rotate" (the idle screens taking
  // turns), one idle slide held still (the display shows Welcome while that slide has nothing on it),
  // or "hide" (the logo). Every page sends the choice again as it opens, so the display agrees.
  // displayMem: the pick, when this browser wouldn't save it (private mode, storage full); then it's
  // kept for this tab, and wins over whatever older value storage still gives back.
  var DISPLAY_MODES = ["auto", "rotate", "hide", "hello", "menu", "popular", "leaders", "soon"], DISPLAY_KEY = "fizzy-display-mode", displayMem = null;
  function displayMode() {
    var m = displayMem;
    if (m === null) { try { m = localStorage.getItem(DISPLAY_KEY); } catch (e) { m = null; } }
    return DISPLAY_MODES.indexOf(m) >= 0 ? m : "auto";
  }
  // again: sent as a page opens, not picked (the display ignores it while hidden from the Mac app's menu).
  function tellDisplay(again) {
    var m = displayMode(), msg = { type: "mode", mode: m };
    if (again) msg.auto = true;
    sendDisplay(msg);
    if (m === "auto" && $("header nav")) {
      var dm = json("#display-msg") || { type: "idle" };
      dm.auto = true;
      sendDisplay(dm);
    }
  }
  function setDisplayMode(m) {
    if (DISPLAY_MODES.indexOf(m) < 0) m = "auto";
    try { localStorage.setItem(DISPLAY_KEY, m); displayMem = null; } catch (e) { displayMem = m; }
    showDisplayMode($("[data-display-mode]"));
    tellDisplay(false);
  }
  // The header's menu shows this device's choice (amber when it's holding the display; the name in
  // its tooltip, for when it's folded down to an icon).
  function showDisplayMode(sel) {
    if (!sel) return;
    var m = displayMode(), box = sel.closest(".dmode");
    sel.value = m; sel.classList.add("ready");
    if (box) {
      box.classList.toggle("held", m !== "auto");
      box.title = "Customer display: " + (sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : "Auto");
    }
  }
  // The Mac app's Display menu: Hide customer display is Hidden, Show customer display is Auto.
  window.fizzyDisplayMode = setDisplayMode;

  // ==== each page ====================================================================================

  // ---- "new sale - claim it?" (customer page) ------------------------------------------------
  // While a customer's page is open, a sale rung up in Square with no customer pops up here
  // within a few seconds, with Claim, Not theirs and No account buttons. Nothing is claimed without a click.
  function claimPrompt(cp) {
    var seenKey = "fizzy-dismissed-" + cp.dataset.cid, shownKey = null;
    var dismissed = function () { try { return JSON.parse(sessionStorage.getItem(seenKey) || "[]"); } catch (e) { return []; } };
    var hide = function () { cp.hidden = true; shownKey = null; document.body.classList.remove("has-claim-prompt"); };
    var dismiss = function (key) {
      try { var d = dismissed(); d.push(key); sessionStorage.setItem(seenKey, JSON.stringify(d.slice(-50))); } catch (e) { /* private mode */ }
      hide();
    };
    var showSale = function (sales) {
      var skip = dismissed(), fresh = sales.filter(function (s) { return skip.indexOf(s.key) < 0; });
      if (!sales.length && !cp.hidden) document.dispatchEvent(new CustomEvent("fizzy:claim-check"));  // maybe a press is waiting: the bar says so
      if (!fresh.length) { hide(); return; }
      var s = fresh[0];
      if (s.key === shownKey) return;
      shownKey = s.key;
      cp.elements.key.value = s.key;
      $(".cp-at", cp).textContent = s.at;
      $(".cp-more", cp).textContent = fresh.length > 1 ? " · " + (fresh.length - 1) + " more on the claim page" : "";
      $(".cp-items", cp).textContent = s.items;
      $(".cp-amts", cp).innerHTML = [s.paid && esc(s.paid) + " <small>paid</small>", s.tab && '<span class="tab">' + esc(s.tab) + " <small>on tab</small></span>",
                                     s.repaid && '<span class="repay">' + esc(s.repaid) + " <small>repaid</small></span>"].filter(Boolean).join(" · ");
      $(".cp-warn", cp).hidden = !s.tab_warning;
      $(".cp-noacct", cp).hidden = !!(s.tab || s.repaid);  // tab money always needs a person
      cp.hidden = false;
      document.body.classList.add("has-claim-prompt");  // room at the bottom so it doesn't cover anything
      cp.classList.remove("pop"); void cp.offsetWidth; cp.classList.add("pop");
    };
    var gone = false;
    later(function () { gone = true; });
    var poll = function () {
      if (document.visibilityState !== "visible") return;
      fetch(cp.dataset.watch + "?since=" + encodeURIComponent(cp.dataset.since), { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; }).then(function (s) { if (s && !gone) showSale(s); }).catch(function () {});
    };
    listen($(".cp-no", cp), "click", function () { dismiss(cp.elements.key.value); });
    // No account (a teacher's sale, someone not recording it): the form goes to /square/no-account, and the
    // page comes back with its message. Hidden now, so it doesn't sit there while that's on its way.
    listen(cp, "turbo:submit-start", function (e) {
      if (e.detail.formSubmission.submitter && e.detail.formSubmission.submitter.classList.contains("cp-noacct")) dismiss(cp.elements.key.value);
    });
    poll();
    every(poll, 3000);
  }

  // ---- a button that mustn't go twice (Claim sale): a double tap sends it once ----------------------
  // Turbo doesn't stop a second submit while the first is on its way, and Claim sale takes a moment (it
  // asks Square first). Once the request has gone the button is disabled; it comes back only if the
  // answer wasn't a success (e.g. a passcode was needed), since a success replaces the page.
  function onceForm(f) {
    var busy = false, by = null;
    listen(f, "submit", function (e) { if (busy) e.preventDefault(); });
    listen(f, "turbo:submit-start", function (e) {
      busy = true; by = e.detail.formSubmission.submitter || $("button", f);
      if (by) { by.disabled = true; by.setAttribute("aria-busy", "true"); }
    });
    listen(f, "turbo:submit-end", function (e) {
      if (e.detail.success) return;
      busy = false;
      if (by) { by.disabled = false; by.removeAttribute("aria-busy"); }
    });
  }

  // ---- passcode reveal (customer page): fetched only when asked for; the server logs each look ----
  function revealButton(b) {
    listen(b, "click", function () { fizzyPin("POST", b.dataset.reveal, reveal); });
    function reveal() {
      fetch(b.dataset.reveal, { method: "POST", credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
        var out = document.createElement("span");
        out.className = "mono revealed";
        out.textContent = d.passcode || "can't be read - set a new one";
        b.replaceWith(out);
      }).catch(function () { b.textContent = "Couldn't load - try again"; });
    }
  }

  // ---- sign-in code on the customer display: made fresh each press, works once, for a minute ----
  function qrButton(b) {
    var label = b.innerHTML, timer = null;
    later(function () { clearInterval(timer); });
    listen(b, "click", function () { fizzyPin("POST", b.dataset.qrLogin, make); });
    function make() {
      fetch(b.dataset.qrLogin, { method: "POST", credentials: "same-origin" }).then(function (r) { if (!r.ok) throw r; return r.json(); }).then(function (d) {
        var dm = json("#display-msg");
        if (!dm || !b.isConnected) return;
        sendDisplay({ type: "show", customer: dm.customer, login: d });
        var left = d.seconds; clearInterval(timer);
        b.textContent = "On the display (" + left + "s)";
        timer = setInterval(function () {
          left -= 1;
          if (left <= 0) { clearInterval(timer); b.innerHTML = label; return; }
          b.textContent = "On the display (" + left + "s)";
        }, 1000);
      }).catch(function () { b.textContent = "Couldn't make one - try again"; setTimeout(function () { b.innerHTML = label; }, 3000); });
    }
  }

  // ---- long tables: latest few rows, the rest one click away ------------------------------
  function clipTable(t) {
    var n = Number(t.dataset.clip), rows = $$("tr", t).filter(function (r) { return !r.querySelector("th"); });
    if (rows.length <= n + 2) return;
    rows.slice(n).forEach(function (r) { r.hidden = true; });
    var more = document.createElement("button");
    more.type = "button"; more.className = "sm clip-more"; more.textContent = "Show all " + rows.length;
    more.addEventListener("click", function () { rows.forEach(function (r) { r.hidden = false; }); more.remove(); });
    (t.closest(".scroll") || t).after(more);
  }
  // Clipped afresh each time the page is drawn (the dashboard's refresh keeps the page and its last clip).
  function reclip() {
    $$(".clip-more").forEach(function (b) { b.remove(); });
    $$("table[data-clip] tr[hidden]").forEach(function (r) { r.hidden = false; });
    $$("table[data-clip]").forEach(clipTable);
  }

  // ---- clock (desktop header) -------------------------------------------------------------
  function clock(el) {
    var fmtTime = new Intl.DateTimeFormat("en-AU", { timeZone: el.dataset.tz, hour: "numeric", minute: "2-digit" });
    var fmtDay = new Intl.DateTimeFormat("en-AU", { timeZone: el.dataset.tz, weekday: "short", day: "numeric", month: "short" });
    var tick = function () {
      var now = new Date();
      el.innerHTML = '<span class="d">' + esc(fmtDay.format(now)) + "</span>" + esc(fmtTime.format(now).replace(" ", "").toLowerCase());
    };
    tick();
    every(tick, 1000);
  }

  // ---- pages that refresh themselves (dashboard) -----------------------------------------
  // Redrawn in place after the given seconds with nobody touching anything, so the numbers stay
  // current on a screen left open - but never while someone is typing or has a dialog open. Turbo
  // "morphs" the page: only what changed is touched, and the scroll position stays.
  //
  // Turbo looks for these twice: in the page it's leaving (to morph), then after the new page's head is
  // in (to keep the scroll). So they go in before the refresh, back in on turbo:render, and out on turbo:load.
  var refreshing = false;
  function refreshMetas() {
    ["turbo-refresh-method:morph", "turbo-refresh-scroll:preserve"].forEach(function (p) {
      var kv = p.split(":");
      if ($('meta[name="' + kv[0] + '"]')) return;
      var m = document.createElement("meta"); m.name = kv[0]; m.content = kv[1]; m.dataset.refresh = "";
      document.head.appendChild(m);
    });
  }
  function refreshOver() { refreshing = false; $$("meta[data-refresh]").forEach(function (m) { m.remove(); }); }
  function autoRefresh(el) {
    var idle = Number(el.dataset.autorefresh) * 1000, last = Date.now();
    // An empty search box that's only focused (it's autofocused on load) doesn't count as typing.
    var busy = function () { var a = document.activeElement; return typing() && !(a.matches("[data-search]") && !a.value); };
    ["keydown", "pointerdown", "pointermove", "input", "scroll", "wheel"].forEach(function (ev) {
      listen(window, ev, function () { last = Date.now(); }, { passive: true, capture: true });
    });
    every(function () {
      if (Date.now() - last < idle || busy() || document.visibilityState !== "visible" || $("dialog[open]")) return;
      if (!window.Turbo) { location.reload(); return; }
      var vt = $('meta[name="view-transition"]');
      if (vt) vt.remove();  // no cross-fade for a refresh (the new page's head brings it back)
      // Morph and keep the scroll for this refresh only (see refreshMetas), so a form here that redirects
      // back to / still loads the page from the top with its message on screen.
      refreshMetas();
      refreshing = "starting";
      last = Date.now();
      Turbo.visit(location.href, { action: "replace" });
    }, 5000);
  }

  // ---- background jobs (Square sync, re-sync, history import): a bar while one runs, and how it went ----
  function jobBar(jb) {
    var seenKey = "fizzy-job-seen", timer = null, gone = false;
    later(function () { gone = true; clearTimeout(timer); });
    function seen() { try { return sessionStorage.getItem(seenKey); } catch (e) { return null; } }
    function check() {
      fetch("/api/job", { credentials: "same-origin", cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
        if (gone) return;
        if (!j || !j.name) { jb.hidden = true; return; }
        var t = $(".t", jb), ok = $("button", jb);
        if (j.running) {
          jb.hidden = false; jb.className = "jobbar"; ok.hidden = true;
          t.textContent = j.name + "... " + (j.seconds > 5 ? j.seconds + "s" : "");
          timer = setTimeout(check, 2000);
        } else if (seen() !== String(j.finished)) {
          jb.hidden = false; jb.className = "jobbar " + (j.ok ? "ok" : "bad"); ok.hidden = false; t.textContent = j.message;
          ok.onclick = function () { try { sessionStorage.setItem(seenKey, String(j.finished)); } catch (e) {} jb.hidden = true; };
        }
      }).catch(function () { if (!gone) timer = setTimeout(check, 5000); });
    }
    check();
  }

  // ---- Claim sale presses (every page): waiting for a sale, or just finished ----------------------
  // The bar under the header (_claimbar.html). The server draws it with the page; while there's anything
  // in it, this asks /api/claim-waits every 2 seconds, then stops once it's empty. A finished line goes
  // by itself after a minute, or at once with its x.
  function claimBar(bar) {
    var hideKey = "fizzy-claim-hidden", gone = false, sig = "", poller = null;
    later(function () { gone = true; });
    function hidden() { try { return JSON.parse(sessionStorage.getItem(hideKey) || "[]"); } catch (e) { return []; } }
    function hide(id) {
      try { var h = hidden(); h.push(id); sessionStorage.setItem(hideKey, JSON.stringify(h.slice(-50))); } catch (e) { /* private mode */ }
    }
    function line(w) {
      var name = "<b>" + esc(w.name) + "</b>", x = '<button type="button" class="sm x" aria-label="Hide this">&times;</button>', inner;
      if (w.status === "waiting") {
        inner = '<span class="spin" aria-hidden="true"></span><span class="t">Waiting for Square to send ' + name + "'s sale</span>" +
          '<form class="inline" method="post" action="/claim-waits/' + Number(w.id) + '/cancel"><button class="sm" title="Stop waiting: the sale won\'t go to ' +
          esc(w.first) + '">Undo</button></form>';
      } else if (w.status === "claimed") {
        inner = '<span class="t">' + name + "'s sale is in: " + esc(w.amounts) + '.</span><a class="btn sm" href="/customers/' + Number(w.customer_id) + '">View</a>' + x;
      } else {
        inner = '<span class="t">No sale came in for ' + name + '.</span><a class="btn sm" href="/customers/' + Number(w.customer_id) + '/claim">Pick a sale</a>' + x;
      }
      return '<div class="cw ' + esc(w.status) + '" data-id="' + Number(w.id) + '" data-status="' + esc(w.status) + '">' + inner + "</div>";
    }
    function lines() { return $$(".cw", bar); }
    function signature() { return lines().map(function (l) { return l.dataset.id + ":" + l.dataset.status; }).join(","); }
    function show(d) {
      var skip = hidden(), waits = (d.waits || []).filter(function (w) { return w.status === "waiting" || skip.indexOf(w.id) < 0; });
      var now = waits.map(function (w) { return w.id + ":" + w.status; }).join(",");
      if (now !== sig) {  // only redrawn when something changed, so an Undo being pressed isn't swept away
        sig = now; bar.innerHTML = waits.map(line).join("");
      }
      bar.hidden = !waits.length;
      if (waits.length) start(); else stop();
    }
    function poll() {
      if (document.visibilityState !== "visible") return;
      fetch("/api/claim-waits", { credentials: "same-origin", cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d && !gone) show(d); }).catch(function () {});
    }
    function stop() { if (poller !== null) { clearInterval(poller); poller = null; } }
    function start() { if (poller === null) poller = every(poll, 2000); }
    // Nothing in the bar: look now and then anyway, so a press made on another till shows up here
    // (and at once when this page's "New sale" box goes away because of one).
    every(function () { if (poller === null) poll(); }, 10000);
    listen(document, "fizzy:claim-check", function () { if (poller === null) poll(); });
    listen(bar, "click", function (e) {
      var b = e.target.closest(".x"), el = b && b.closest(".cw");
      if (!el) return;
      hide(Number(el.dataset.id)); el.remove();
      sig = signature();
      if (!lines().length) { bar.hidden = true; stop(); }
    });
    // As drawn by the server: leave out lines already hidden here.
    var skip = hidden();
    lines().forEach(function (el) { if (el.dataset.status !== "waiting" && skip.indexOf(Number(el.dataset.id)) >= 0) el.remove(); });
    sig = signature();
    bar.hidden = !lines().length;
    if (lines().length) start();
  }

  // ---- landing on a section -------------------------------------------------------------------
  // A redirect to /settings#pin (or /tools#merge...) followed by Turbo loses the # part, so Fizzy
  // sends it as <body data-jump="pin"> instead (see back() in web.py). A <details> there is opened:
  // on Tools, the tool you just used. The message about what was just done ("Saved.") moves into that
  // section: left at the top of the page, scrolling to the section would hide it under the header.
  function section(id) {
    try { id = decodeURIComponent(id || ""); } catch (e) { return null; }
    var el = id && document.getElementById(id);
    if (el && el.tagName === "DETAILS") el.open = true;
    return el;
  }
  function jump() {
    var id = document.body.dataset.jump;
    if (!id) return;
    delete document.body.dataset.jump;  // once
    var el = section(id), msg = $("main > .flash[role=status]");
    if (!el) return;
    if (msg && !el.contains(msg) && !/^(TABLE|TBODY|TR|INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) {
      var head = el.tagName === "DETAILS" ? $("summary", el) : el.firstElementChild;
      if (head && /^(SUMMARY|H[1-6])$/.test(head.tagName)) head.after(msg); else el.prepend(msg);
    }
    el.scrollIntoView({ block: "start" });
  }

  function init() {
    teardown();
    document.body.appendChild(ask); document.body.appendChild(pinBox);  // each page has a new <body>
    lockRules = json("#lock-rules");
    lockCust = json("#lock-customer");
    if (lockRules && lockRules.numeric) pinIn.inputMode = "numeric"; else pinIn.removeAttribute("inputmode");

    $$("input[data-search]").forEach(bindSearch);
    var cp = $("#claim-prompt"); if (cp) claimPrompt(cp);
    $$("form[data-once]").forEach(onceForm);
    $$("[data-reveal]").forEach(revealButton);
    $$("[data-qr-login]").forEach(qrButton);
    // "i" tips: open towards whichever side has room
    $$(".tip").forEach(function (t) {
      var side = function () { t.classList.toggle("tip-right", t.getBoundingClientRect().left < 280); };
      listen(t, "pointerenter", side); listen(t, "focus", side);
    });
    reclip();
    var ck = $("[data-clock]"); if (ck) clock(ck);
    var auto = $("[data-autorefresh]"); if (auto) autoRefresh(auto);
    later(unprerender);
    var jb = $("#jobbar"); if (jb) jobBar(jb);
    var cb = $("#claimbar"); if (cb) claimBar(cb);
    // Customer display: tell the screen facing the queue what to show (on Auto, who's being served).
    // Straight to it, no server in between: the Mac app's bridge, or a BroadcastChannel when it's a
    // second browser window. The header's Display menu shows this device's choice.
    if ($("header nav")) {
      var dsel = $("[data-display-mode]");
      if (dsel) {
        showDisplayMode(dsel);
        listen(dsel, "change", function () { setDisplayMode(dsel.value); });
        // Picked in another tab on this device: show it here too (that tab has told the display already).
        listen(window, "storage", function (e) {
          if (e.key !== DISPLAY_KEY && e.key !== null) return;
          displayMem = null; showDisplayMode(dsel);
        });
      }
      tellDisplay(true);
    }
    section(location.hash.slice(1));
    // Warm the index as soon as the page is idle so the first keystroke is instant.
    (window.requestIdleCallback || setTimeout)(function () { if ($("input[data-search]")) loadIndex(); });
  }

  // Fizzy can't be reached or is in maintenance mode (Cloudflare's error page has no X-Fizzy header):
  // load the page for real instead, so the service worker shows its "Fizzy is down" page, or the
  // maintenance page shows with its own look. The same for a form when the network is gone (offline page).
  // Always a GET (never "resend the form?"): assigning the address without its # part loads it again.
  function realLoad(url) { location.assign((url || location.href).split("#")[0]); }
  function outage(e) {
    var r = e.detail.fetchResponse, from = r.header("X-Fizzy");
    if (r.statusCode < 500 || (from && from !== "maintenance")) return;
    e.preventDefault();
    realLoad(e.target && e.target.tagName === "FORM" ? null : r.location.href);
  }

  // A button with autofocus (the claim page's first "Claim") shows its focus ring on a full page load.
  // Turbo focuses it with a plain focus(), which after a mouse click draws no ring: focus it first,
  // with the ring (Turbo's own focus() then changes nothing).
  function autofocusRing() {
    var el = $$("[autofocus]").filter(function (x) {
      return !x.closest("[inert], :disabled, [hidden], details:not([open]), dialog:not([open])");
    })[0];
    if (el && !typing(el)) try { el.focus({ focusVisible: true }); } catch (e) { /* old browser: Turbo focuses it */ }
  }

  var morphed = false;
  // Turbo's loading bar: it shows only once a page has taken half a second (style.css), whatever kind of
  // visit it is. Turbo itself would show it at once for Back and Forward.
  if (window.Turbo) Turbo.session.progressBarDelay = 0;
  init();
  jump();
  if (window.Turbo) {
    document.addEventListener("turbo:before-render", function (e) {
      teardown();
      closeBoxes();
      // The page-in fade in style.css is for full page loads. Not when the view transition already
      // cross-fades it, and not for a morph (the page stays).
      var html = document.documentElement;
      html.classList.toggle("still", !!document.startViewTransition || e.detail.renderMethod === "morph");
      // The dashboard's refresh: nothing replays its opening animation (charts growing, the message
      // sliding in), even where the redraw had to replace an element rather than update it (a banner
      // appearing above them shifts everything down). Until the next real page change.
      html.classList.toggle("morphed", e.detail.renderMethod === "morph");
      morphed = e.detail.renderMethod === "morph";
    });
    // turbo:render, not turbo:load: a form answered with an error status is drawn without a turbo:load.
    document.addEventListener("turbo:render", function () {
      // An error page brings its own head: after an update, a newer app.js than the one running. Load it for real.
      if (V && version() !== V) { realLoad(); return; }
      if (refreshing) refreshMetas();  // for Turbo's scroll, which comes next
      init();
      if (!morphed) autofocusRing();
      setTimeout(jump, 0);  // after Turbo's own scroll to the top
    });
    document.addEventListener("turbo:load", function () { refreshOver(); jump(); });
    // Any other visit (a click while the refresh was on its way) isn't a refresh: no morphing that one.
    document.addEventListener("turbo:visit", function () { if (refreshing === "starting") refreshing = true; else if (refreshing) refreshOver(); });
    document.addEventListener("turbo:submit-end", function () { indexAt = 0; unprerender(); });  // something changed: fresh search results
    // The dashboard's refresh landed somewhere else (signed out: the sign-in page). Don't morph the
    // dashboard into it: load that page for real.
    // A form sent while the refresh is on its way cancels it: its answer is the form's, not a refresh.
    document.addEventListener("turbo:submit-start", function () { if (refreshing) refreshOver(); });
    document.addEventListener("turbo:before-fetch-response", function (e) {
      var r = e.detail.fetchResponse;
      if (!refreshing || !r.redirected || e.target !== document.documentElement) return;
      e.preventDefault(); refreshOver();
      realLoad(r.location.href);
    });
    document.addEventListener("turbo:before-fetch-response", outage);
    document.addEventListener("turbo:fetch-request-error", function (e) { if (e.target && e.target.tagName === "FORM") realLoad(null); });
    // The dashboard's refresh redraws only what changed. The header and the job bar are this page's
    // own (the clock, the "More" menu, a job in progress): leave them as they are.
    document.addEventListener("turbo:before-morph-element", function (e) {
      // Only keep them as they are: if the new page has none (the sign-in page), they go.
      if (e.detail.newElement && e.target.matches && e.target.matches("header, #jobbar, dialog.ask")) e.preventDefault();
    });
  }
})();

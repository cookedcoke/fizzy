// Fizzy counter UI: instant search + keyboard shortcuts. No dependencies.
//
// Search runs in the browser against /api/index (every active customer, fetched once per
// page load), so typing never waits on the network. The highlighted result is prerendered
// so Enter opens it instantly.
//
// Shortcuts: any element with data-key="x" is triggered by pressing x (inputs get focus,
// buttons/links get clicked; data-confirm asks first). The ? panel lists them.
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

  // ---- search -------------------------------------------------------------------------------
  var index = null, indexReq = null, indexError = null;
  function loadIndex() {
    if (!indexReq) {
      indexReq = fetch("/api/index", { credentials: "same-origin" }).then(function (r) {
        if (r.redirected && r.url.indexOf("/login") >= 0) throw new Error("signed-out");
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      }).then(function (rows) {
        index = rows; indexError = null;
        return rows;
      }).catch(function (err) { indexReq = null; indexError = String(err && err.message || err); });
    }
    return indexReq;
  }

  var rank = window.FizzyFuzzy.rank;

  function highlight(name, pos) {
    var out = "", set = {};
    pos.forEach(function (p) { set[p] = 1; });
    for (var i = 0; i < name.length; i++) out += set[i] ? "<mark>" + esc(name[i]) + "</mark>" : esc(name[i]);
    return out.replace(/<\/mark><mark>/g, "");
  }

  var spec = null;
  function prerender(href) {
    // Chrome/Edge: prerender the highlighted customer so Enter is instant. Others: prefetch.
    if (spec && spec.dataset.href === href) return;
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
    // Digits normally jump to a result, but while searching by student number they're part of
    // the query - then Alt+digit jumps instead. Names never contain digits, so no clash.
    function numbersNeedAlt() { var q = box.value.trim(); return !/[a-z]/i.test(q) || /^rns\d*$/i.test(q); }
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
            ? '<div class="none">Signed out. <a href="/login">Sign in again</a>.</div>'
            : '<div class="none">Couldn\'t load the customer list. Press Enter to search on the server.</div>';
          indexError = null;  // try again on the next keystroke
          return;
        }
        loadIndex().then(render); return;
      }
      var hits = rank(q, index, 8); rows = hits.map(function (h) { return h[0]; }); sel = 0;
      out.innerHTML = rows.length ? hits.map(function (h, i) {
        var r = h[0];
        return '<a class="hit" href="/customers/' + r.id + '"><span><kbd class="num">' + (numbersNeedAlt() ? "Alt " : "") + (i + 1) + '</kbd><span class="nm">' + highlight(r.name, h[1]) + '</span><span class="em">' + esc(r.sid) + "</span></span>" +
          '<span class="facts"><span>' + r.points.toLocaleString() + " pts</span><span>owes " + money(r.owing) + "</span>" + (r.reward ? '<span class="tag reward" title="Can redeem a reward">Reward</span>' : "") + '<span class="tag ' + esc(r.status) + '">' + esc(r.label) + "</span></span></a>";
      }).join("") : '<div class="none">No one matches “' + esc(q) + "”.</div>";
      mark();
    }
    box.addEventListener("input", render);
    box.addEventListener("focus", function () { loadIndex(); if (box.value.trim() && !out.innerHTML) render(); });
    box.addEventListener("keydown", function (e) {
      var digit = /^(Digit|Numpad)([1-9])$/.exec(e.code);
      if (digit && !e.ctrlKey && !e.metaKey && rows.length && (e.altKey || !numbersNeedAlt())) {
        var r = rows[+digit[2] - 1];
        if (r) { e.preventDefault(); location.href = "/customers/" + r.id; }
        return;
      }
      if (e.key === "ArrowDown") { sel = Math.min(sel + 1, rows.length - 1); mark(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); mark(); e.preventDefault(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (window.fizzyScan && fizzyScan(box.value.trim())) return;  // card scanned into the search box
        if (!index) { location.href = "/customers?q=" + encodeURIComponent(box.value.trim()); }  // server search fallback
        else if (rows[sel]) location.href = "/customers/" + rows[sel].id;
      }
      else if (e.key === "Escape") { e.stopPropagation(); if (box.value) { box.value = ""; render(); } else box.blur(); }
    });
    out.addEventListener("mousemove", function (e) {
      var a = e.target.closest("a.hit"); if (!a) return;
      var i = $$("a.hit", out).indexOf(a); if (i !== sel) { sel = i; mark(); }
    });
    document.addEventListener("click", function (e) { if (!out.contains(e.target) && e.target !== box) out.innerHTML = ""; });
  }

  // ---- shortcuts ----------------------------------------------------------------------------
  var GO = { d: ["/", "Dashboard"], c: ["/customers", "Customers"], s: ["/square", "Square"], x: ["/transactions", "Transactions"], t: ["/tools", "Tools"], r: ["/rewards", "Rewards"],
             i: ["/import", "Import"], l: ["/audit", "Log"], ",": ["/settings", "Settings"], p: ["/profit", "Profit"], m: ["/money", "Money"] };
  var pendingG = 0;

  function trigger(el) {
    if (el.tagName === "DETAILS") { el.open = !el.open; var f = el.open && $("input,textarea,select", el); if (f) f.focus(); return; }
    if (typing(el) || el.tagName === "SELECT") { el.focus(); if (el.select) el.select(); return; }
    el.click();
  }

  function help() {
    var dlg = $("#keys");
    if (dlg.open) { dlg.close(); return; }
    var page = $$("[data-key]").filter(function (el) { return el.offsetParent !== null || el.tagName === "DETAILS"; });
    $("#keys-page").innerHTML = page.length ? page.map(function (el) {
      return "<tr><td><kbd>" + esc(el.dataset.key.toUpperCase()) + "</kbd></td><td>" + esc(el.dataset.keyLabel || el.textContent.trim()) + "</td></tr>";
    }).join("") : '<tr><td colspan="2" class="empty">None on this page.</td></tr>';
    dlg.showModal();
  }

  // ---- student card scans ------------------------------------------------------------------
  // A USB barcode scanner "types" the card's RNS number fast and presses Enter. Catch it before
  // the shortcuts see it (R would open the repayment box), and open that student. Keys held back
  // that turn out to be a person typing are passed on to the shortcuts as normal.
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

  document.addEventListener("keydown", function (e) {
    if (e.defaultPrevented || e.altKey) return;
    // Ctrl/Cmd+Enter submits the form you're in - only when it has one plain submit button.
    // Forms with a choice of buttons (e.g. approve / reject) need the button picked.
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      var form = document.activeElement && document.activeElement.form;
      var btns = form ? $$("button:not([type=button]):not([disabled]), input[type=submit]", form) : [];
      if (btns.length === 1 && !btns[0].name) { e.preventDefault(); form.requestSubmit(btns[0]); }
      return;
    }
    if (e.ctrlKey || e.metaKey) return;
    if (e.key === "Escape") {
      if (typing()) { document.activeElement.blur(); return; }
      if ($("#keys").open) return;
      if (location.pathname !== "/") location.href = "/";
      return;
    }
    if (typing()) return;
    var k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (pendingG && Date.now() - pendingG < 1200) {
      pendingG = 0;
      if (GO[k]) { e.preventDefault(); location.href = GO[k][0]; }
      return;
    }
    if (k === "g") { pendingG = Date.now(); return; }
    if (k === "?" ) { e.preventDefault(); help(); return; }
    if (k === "/" || k === "s" || k === "f") {
      var box = $("input[data-search]");
      if (box) { e.preventDefault(); box.focus(); box.select(); return; }
    }
    var el = $('[data-key="' + (k === '"' ? '\\"' : k) + '"]');
    if (el) { e.preventDefault(); trigger(el); }
  });

  // ---- "new sale - claim it?" (customer page) ------------------------------------------------
  // While a customer's page is open, a sale rung up in Square with no customer pops up here
  // within a few seconds. Enter claims it for this customer (the button has focus, unless
  // you're typing somewhere); Esc dismisses it. Nothing is claimed without a keypress.
  var cp = $("#claim-prompt");
  if (cp) {
    var seenKey = "fizzy-dismissed-" + cp.dataset.cid, shownKey = null;
    var dismissed = function () { try { return JSON.parse(sessionStorage.getItem(seenKey) || "[]"); } catch (e) { return []; } };
    var dismiss = function (key) {
      try { var d = dismissed(); d.push(key); sessionStorage.setItem(seenKey, JSON.stringify(d.slice(-50))); } catch (e) { /* private mode */ }
      cp.hidden = true; shownKey = null;
      document.body.classList.remove("has-claim-prompt");
    };
    var showSale = function (sales) {
      var skip = dismissed(), fresh = sales.filter(function (s) { return skip.indexOf(s.key) < 0; });
      if (!fresh.length) { cp.hidden = true; shownKey = null; document.body.classList.remove("has-claim-prompt"); return; }
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
      cp.hidden = false;
      document.body.classList.add("has-claim-prompt");  // room at the bottom so it doesn't cover anything
      cp.classList.remove("pop"); void cp.offsetWidth; cp.classList.add("pop");
      // Enter is armed only for a sale taken after this page was opened (the one being rung up now);
      // an older one still shows, but needs a click or Tab first.
      if (!typing() && s.at_iso > cp.dataset.opened) $("button[type=submit]", cp).focus();
    };
    var poll = function () {
      if (document.visibilityState !== "visible") return;
      fetch(cp.dataset.watch + "?since=" + encodeURIComponent(cp.dataset.since), { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; }).then(function (s) { if (s) showSale(s); }).catch(function () {});
    };
    $(".cp-no", cp).addEventListener("click", function () { dismiss(cp.elements.key.value); });
    addEventListener("keydown", function (e) {
      if (cp.hidden || e.key !== "Escape" || typing()) return;  // in a field, Esc just leaves the field
      e.preventDefault(); e.stopImmediatePropagation();
      dismiss(cp.elements.key.value);
    }, true);
    poll();
    setInterval(poll, 3000);
  }

  // ---- passcode reveal (customer page): fetched only when asked for; the server logs each look ----
  $$("[data-reveal]").forEach(function (b) {
    b.addEventListener("click", function () { fizzyPin("POST", b.dataset.reveal, reveal); });
    function reveal() {
      fetch(b.dataset.reveal, { method: "POST", credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
        var out = document.createElement("span");
        out.className = "mono revealed";
        out.textContent = d.passcode || "can't be read - set a new one";
        b.replaceWith(out);
      }).catch(function () { b.textContent = "Couldn't load - try again"; });
    }
  });

  // ---- sign-in code on the customer display: made fresh each press, works once, for a minute ----
  $$("[data-qr-login]").forEach(function (b) {
    var label = b.innerHTML, timer = null;
    b.addEventListener("click", function () { fizzyPin("POST", b.dataset.qrLogin, make); });
    function make() {
      fetch(b.dataset.qrLogin, { method: "POST", credentials: "same-origin" }).then(function (r) { if (!r.ok) throw r; return r.json(); }).then(function (d) {
        var dm = $("#display-msg") ? JSON.parse($("#display-msg").textContent) : null;
        if (!dm) return;
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
  });

  // ---- "i" tips: open towards whichever side has room ------------------------------------------
  $$(".tip").forEach(function (t) {
    t.addEventListener("pointerenter", function () { t.classList.toggle("tip-right", t.getBoundingClientRect().left < 280); });
    t.addEventListener("focus", function () { t.classList.toggle("tip-right", t.getBoundingClientRect().left < 280); });
  });

  // ---- long tables: latest few rows, the rest one click away ------------------------------
  $$("table[data-clip]").forEach(function (t) {
    var n = Number(t.dataset.clip), rows = $$("tr", t).filter(function (r) { return !r.querySelector("th"); });
    if (rows.length <= n + 2) return;
    rows.slice(n).forEach(function (r) { r.hidden = true; });
    var more = document.createElement("button");
    more.type = "button"; more.className = "sm clip-more"; more.textContent = "Show all " + rows.length;
    more.addEventListener("click", function () { rows.forEach(function (r) { r.hidden = false; }); more.remove(); });
    (t.closest(".scroll") || t).after(more);
  });

  // ---- clock (desktop header) -------------------------------------------------------------
  var clock = $("[data-clock]");
  if (clock) {
    var fmtTime = new Intl.DateTimeFormat("en-AU", { timeZone: clock.dataset.tz, hour: "numeric", minute: "2-digit" });
    var fmtDay = new Intl.DateTimeFormat("en-AU", { timeZone: clock.dataset.tz, weekday: "short", day: "numeric", month: "short" });
    var tick = function () {
      var now = new Date();
      clock.innerHTML = '<span class="d">' + esc(fmtDay.format(now)) + "</span>" + esc(fmtTime.format(now).replace(" ", "").toLowerCase());
    };
    tick();
    setInterval(tick, 1000);
  }

  // ---- pages that refresh themselves (dashboard) -----------------------------------------
  // Reloads after the given seconds with nobody touching anything, so the numbers stay current
  // on a screen left open - but never while someone is typing or has a dialog open.
  var auto = $("[data-autorefresh]");
  if (auto) {
    var idle = Number(auto.dataset.autorefresh) * 1000, last = Date.now();
    // An empty search box that's only focused (it's autofocused on load) doesn't count as typing.
    var busy = function () { var el = document.activeElement; return typing() && !(el.matches("[data-search]") && !el.value); };
    try {  // keep the scroll position across the reload
      var y = sessionStorage.getItem("fizzy-autorefresh-y");
      if (y) { sessionStorage.removeItem("fizzy-autorefresh-y"); scrollTo(0, Number(y)); }
    } catch (e) { /* private mode */ }
    ["keydown", "pointerdown", "pointermove", "input", "scroll", "wheel"].forEach(function (ev) {
      addEventListener(ev, function () { last = Date.now(); }, { passive: true, capture: true });
    });
    setInterval(function () {
      if (Date.now() - last < idle || busy() || document.visibilityState !== "visible" || $("dialog[open]")) return;
      try { sessionStorage.setItem("fizzy-autorefresh-y", String(scrollY)); } catch (e) { /* private mode */ }
      location.reload();
    }, 5000);
  }

  // Hover tips naming the shortcut for anything that has one (visible chips are in the templates).
  $$("[data-key]").forEach(function (el) {
    var key = el.dataset.key.toUpperCase(), what = el.dataset.keyLabel || el.textContent.trim();
    var target = el.tagName === "DETAILS" ? $("summary", el) : el;
    if (target && !target.title) target.title = what + " - press " + key;
  });
  $$("form button:not([type=button]):not([title])").forEach(function (b) {
    if (b.form && b.form.method === "post") b.title = "Ctrl+Enter submits from anywhere in this form";
  });
  // Any button with data-confirm asks first, however it's pressed (click, shortcut, Enter).
  // Asked in Fizzy's own box rather than the browser's.
  var ask = document.createElement("dialog"), onYes = null, passed = null;
  ask.className = "ask";
  ask.innerHTML = '<p></p><div class="actions"><button type="button" class="primary" value="ok">Yes, do it</button>' +
                  '<button type="button" value="no">Cancel</button></div>';
  document.body.appendChild(ask);
  ask.addEventListener("click", function (e) {
    var v = e.target.value;
    if (e.target === ask || v === "no") ask.close();
    if (v === "ok") { var f = onYes; ask.close(); f(); }
  });
  window.fizzyAsk = function (text, yes) {
    onYes = yes; ask.querySelector("p").textContent = text; ask.showModal();
    ask.querySelector("[value=ok]").focus();
  };
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-confirm]");
    if (!b || b.tagName === "DETAILS") return;
    if (passed === b) { passed = null; return; }  // the second, confirmed click
    e.preventDefault(); e.stopImmediatePropagation();
    fizzyAsk(b.dataset.confirm, function () { passed = b; b.click(); });
  }, true);
  // Passcodes: anything above "anyone signed in" asks in a box first, then carries on. The rules
  // come from the owner's "Who can do what" settings; the server checks them again either way.
  var lockRules = $("#lock-rules") ? JSON.parse($("#lock-rules").textContent) : null;
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
  var lockCust = $("#lock-customer") ? JSON.parse($("#lock-customer").textContent) : null;
  function needFor(method, url, data) {
    var n = need(method, url, data), path = new URL(url, location.href).pathname;
    if (n === undefined || !lockCust || !lockCust.kind || path.indexOf("/customers/" + lockCust.id + "/") !== 0 || /\/(claim|kind)$/.test(path)) return n;
    if (lockCust.kind === "test") return lockCust.levels[0];
    return Math.max(n, lockCust.levels[n]);
  }
  function needsPin(method, url, data) { var n = needFor(method, url, data); return n > have() ? n : 0; }
  var pinBox = document.createElement("dialog"), pinThen = null, pinNeed = 1;
  pinBox.className = "ask";
  pinBox.innerHTML = '<form method="dialog"><p><b>Enter passcode</b></p>' +
    '<input type="password" autocomplete="off" aria-label="Passcode">' +
    '<div class="err" role="alert" hidden></div>' +
    '<div class="actions"><button class="primary" value="ok">Unlock</button><button type="button" value="no">Cancel</button></div></form>';
  document.body.appendChild(pinBox);
  var pinIn = $("input", pinBox), pinErr = $(".err", pinBox);
  if (lockRules && lockRules.numeric) pinIn.inputMode = "numeric";
  $("[value=no]", pinBox).addEventListener("click", function () { pinBox.close(); });
  $("form", pinBox).addEventListener("submit", function (e) {
    e.preventDefault();
    var body = new URLSearchParams({ pin: pinIn.value, need: pinNeed });
    fetch("/settings/pin/unlock", { method: "POST", body: body, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        if (!r.ok) { pinErr.hidden = false; pinErr.textContent = r.error; pinIn.select(); return; }
        document.body.dataset.unlocked = r.rank; document.body.dataset.unlockedUntil = r.until;
        pinBox.close(); var again = pinThen; pinThen = null; again();
      })
      .catch(function () { pinErr.hidden = false; pinErr.textContent = "Couldn't reach Fizzy. Try again."; });
  });
  function askPin(level, then) {
    pinNeed = level; pinThen = then; pinIn.value = ""; pinErr.hidden = true; pinBox.showModal(); pinIn.focus();
  }
  window.fizzyPin = function (method, url, then) { var n = needsPin(method, url); if (n) askPin(n, then); else then(); };
  document.addEventListener("submit", function (e) {
    var f = e.target, method = (f.getAttribute("method") || "get").toUpperCase();
    if (e.defaultPrevented || f.closest("dialog.ask")) return;
    var n = needsPin(method, f.action, new FormData(f));
    if (!n) return;
    e.preventDefault();
    var by = e.submitter;
    askPin(n, function () { f.requestSubmit(by && by.form === f ? by : undefined); });
  });
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[href]");
    var n = a && needsPin("GET", a.href);
    if (!n) return;
    e.preventDefault();
    askPin(n, function () { location.href = a.href; });
  });
  $$("input[data-search]").forEach(bindSearch);
  var hb = $("#keys-btn"); if (hb) hb.addEventListener("click", help);
  function sendDisplay(m) {
    try { window.webkit.messageHandlers.fizzyDisplay.postMessage(m); } catch (e) {}
    try { new BroadcastChannel("fizzy-display").postMessage(m); } catch (e) {}
  }
  // Customer display: tell the screen facing the queue who's being served. Straight to it, no
  // server in between: the Mac app's bridge, or a BroadcastChannel when it's a second browser window.
  if ($("header nav")) {
    var dm = $("#display-msg") ? JSON.parse($("#display-msg").textContent) : { type: "idle" };
    dm.auto = true;
    sendDisplay(dm);
  }
  // Warm the index as soon as the page is idle so the first keystroke is instant.
  (window.requestIdleCallback || setTimeout)(function () { if ($("input[data-search]")) loadIndex(); });
})();

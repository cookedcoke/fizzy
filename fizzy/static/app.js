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
  var GO = { d: ["/", "Dashboard"], c: ["/customers", "Customers"], s: ["/square", "Square"], t: ["/tools", "Tools"], r: ["/rewards", "Rewards"],
             i: ["/import", "Import"], l: ["/audit", "Log"], ",": ["/settings", "Settings"] };
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
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-confirm]");
    if (b && b.tagName !== "DETAILS" && !confirm(b.dataset.confirm)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  $$("input[data-search]").forEach(bindSearch);
  var hb = $("#keys-btn"); if (hb) hb.addEventListener("click", help);
  // Warm the index as soon as the page is idle so the first keystroke is instant.
  (window.requestIdleCallback || setTimeout)(function () { if ($("input[data-search]")) loadIndex(); });
})();

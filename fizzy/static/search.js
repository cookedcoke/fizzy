// Search-as-you-type. Enter opens the highlighted customer; arrows move.
(function () {
  var box = document.getElementById("q"), out = document.getElementById("results");
  if (!box || !out) return;
  var sel = 0, timer, seq = 0;
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }
  function mark() { var a = out.querySelectorAll("a.hit"); a.forEach(function (x, i) { x.classList.toggle("sel", i === sel); }); }
  function run() {
    var q = box.value.trim(), my = ++seq;
    if (!q) { out.innerHTML = ""; return; }
    fetch("/api/search?q=" + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (rows) {
      if (my !== seq) return;
      sel = 0;
      out.innerHTML = rows.length ? rows.map(function (r) {
        return '<a class="hit" href="/customers/' + r.id + '"><span><span class="nm">' + esc(r.name) + '</span><span class="em">' + esc(r.email.split("@")[0]) + '</span></span>' +
          '<span class="facts"><span>' + r.points + ' pts</span><span>owes ' + esc(r.owing) + '</span><span class="tag ' + esc(r.status_key) + '">' + esc(r.status) + '</span></span></a>';
      }).join("") : '<div class="none">No one matches “' + esc(q) + '”.</div>';
      mark();
    });
  }
  box.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(run, 120); });
  box.addEventListener("keydown", function (e) {
    var a = out.querySelectorAll("a.hit");
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, a.length - 1); mark(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); mark(); e.preventDefault(); }
    else if (e.key === "Enter" && a.length) { location.href = a[sel].href; e.preventDefault(); }
    else if (e.key === "Escape") { box.value = ""; out.innerHTML = ""; }
  });
  document.addEventListener("click", function (e) { if (!out.contains(e.target) && e.target !== box) out.innerHTML = ""; });
  box.addEventListener("focus", function () { if (box.value.trim() && !out.innerHTML) run(); 
  });
})();

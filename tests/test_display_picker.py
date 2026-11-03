"""The header's Display menu: what the customer display shows (Auto, one idle screen held still, all
of them taking turns, or hidden). The staff side keeps the choice per device (app.js, localStorage) and
sends it as {type: "mode"}; display.html acts on it and remembers it. The display's script is run here
in node against a small stand-in for the page, so the screens it ends up on are checked, not just its text."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_app import client, login  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
MODES = ["auto", "rotate", "hide", "hello", "menu", "popular", "leaders", "soon"]
CSV = b"Cleaned Name,Email\nAdeline Yates,rns00000001@reddamnorthshore.nsw.edu.au\n"


def _picker(page):
    m = re.search(r'<label class="dmode"[^>]*>.*?</label>', page, re.S)
    assert m, "no Display menu in the header"
    return m.group(0)


def _js_list(src, name):
    m = re.search(name + r'\s*=\s*\[([^\]]*)\]', src)
    assert m, name
    return re.findall(r'"([a-z]+)"', m.group(1))


def test_every_staff_page_has_the_display_menu(client):
    login(client)
    client.post("/import", files={"file": ("d.csv", CSV, "text/csv")}, data={"confirm": "1"})
    cid = client.get("/api/search?q=adel").json()[0]["id"]
    for path in ("/", "/customers", f"/customers/{cid}", "/tools", "/settings", "/money"):
        page = client.get(path).text
        header = page.split("<header>", 1)[1].split("</header>", 1)[0]
        box = _picker(header)
        sel = re.search(r"<select[^>]*>", box).group(0)
        assert "data-display-mode" in sel and 'aria-label="' in sel and 'autocomplete="off"' in sel, path
        assert "name=" not in sel, path                       # not part of any form: nothing is sent to Fizzy
        assert re.findall(r'<option value="([a-z]+)"', box) == ["auto", "hello", "menu", "popular", "leaders", "soon", "rotate", "hide", "edit"]
        assert "selected" not in box, path                    # the device's own choice is set by app.js, never guessed here
        assert "Display" in box and "Hidden" in box and "Auto" in box
        assert header.index("data-clock") < header.index('class="dmode"') < header.index('action="/switch"')   # by the clock


def test_not_on_the_sign_in_page_or_the_display(client):
    assert "data-display-mode" not in client.get("/login").text
    login(client)
    assert "data-display-mode" not in client.get("/display").text
    assert "data-display-mode" not in client.get("/me").text


def test_the_same_choices_everywhere():
    app = (ROOT / "fizzy/static/app.js").read_text()
    disp = (ROOT / "fizzy/templates/display.html").read_text()
    base = (ROOT / "fizzy/templates/base.html").read_text()
    assert _js_list(app, "DISPLAY_MODES") == MODES
    assert _js_list(disp, "MODES") == MODES
    # ...and last, Edit message, which isn't a mode: it opens the display's settings
    assert sorted(re.findall(r'<option value="([a-z]+)"', _picker(base))) == sorted(MODES + ["edit"])
    assert 'dsel.value === "edit"' in app and "/display/settings" in app
    # slide names: each held slide is one of the display's own
    slides = re.findall(r'class="slide" data-s="([a-z]+)"', disp)
    assert slides == MODES[3:]


def test_app_js_sends_the_mode_on_every_page():
    app = (ROOT / "fizzy/static/app.js").read_text()
    init = app.split("function init() {", 1)[1].split("\n  }\n", 1)[0]
    assert 'listen(dsel, "change"' in init                     # undone before the next page: no doubled listeners
    assert "tellDisplay(true)" in init and "showDisplayMode(dsel)" in init
    assert 'type: "mode"' in app and "localStorage.getItem(DISPLAY_KEY)" in app and "localStorage.setItem(DISPLAY_KEY" in app
    tell = app.split("function tellDisplay(again) {", 1)[1].split("\n  }\n", 1)[0]
    assert 'm === "auto"' in tell                              # the page's own message only on Auto
    assert "window.fizzyDisplayMode = setDisplayMode" in app   # for the Mac app's menu
    css = (ROOT / "fizzy/static/style.css").read_text()
    assert "header .dmode select:not(.ready) { visibility: hidden }" in css


def test_the_header_makes_room_for_it():
    """base.html's fit(): the menu's word goes first, then the tagline, labels and date, then the header
    search folds to its magnifier, then the clock, then the menu folds to its icon, and only then do tabs
    move to More. Run again after app.js draws a Turbo page (the clock fills in then)."""
    base = (ROOT / "fizzy/templates/base.html").read_text()
    fit = base.split("function fit() {", 1)[1].split("\n    }\n", 1)[0]
    steps = re.search(r"var steps = \[(.*?)\];", fit).group(1)
    assert re.findall(r'\[(\w+), "(\w+)"\]', steps) == [("dm", "tight"), ("h", "tight"), ("h", "fold"), ("clock", "squeezed"), ("dm", "squeezed")]
    assert fit.index("var steps") < fit.index("menu.insertBefore")
    assert 'h.classList.remove("tight", "fold")' in fit       # undone each time: wider again shows it all again
    assert 'document.addEventListener("turbo:render", fit)' in base and 'document.removeEventListener("turbo:render", fit)' in base
    css = (ROOT / "fizzy/static/style.css").read_text()
    assert "header .dmode.squeezed select" in css and "header .dmode.held" in css


def test_a_wider_window_never_hides_more():
    """What fit() hides above 1600px is exactly what style.css hides at 1600px and below (labels, date,
    tagline; the header search folded below 1600px), so a wider window is never shown less of the header
    than a narrower one."""
    css = (ROOT / "fizzy/static/style.css").read_text()
    narrow = re.search(r"@media \(max-width: 1600px\) \{ ([^{]+) \{ display: none \} \}", css).group(1)
    tight = re.search(r"\n(header\.tight [^{]+) \{ display: none \}", css).group(1)
    parts = lambda sel, pre: {re.sub("^" + re.escape(pre), "", x.strip()) for x in sel.split(",")}  # noqa: E731
    assert parts(tight, "header.tight ") == parts(narrow, "header ") == {".brand small", ".who .label", ".clock .d"}
    assert "header.tight .dmode svg { display: block }" in css
    fold_media = css.split("@media (min-width: 721px) and (max-width: 1599px) {", 1)[1].split("\n}\n", 1)[0]
    fold_class = css.split("@media (min-width: 721px) {\n  header.fold", 1)[1].split("\n}\n", 1)[0]
    rules = lambda block: [ln.strip().split("{", 1)[1] if "{" in ln else ln.strip() for ln in block.strip().splitlines()]  # noqa: E731
    assert rules(fold_media) == rules("  header.fold" + fold_class)             # the same fold, kept in step


def test_mac_menu_sets_hidden_and_auto():
    main = (ROOT / "mac/src/main.js").read_text()
    assert "displayMode('hide', '{\"type\":\"hide\"}')" in main
    assert "displayMode('auto', '{\"type\":\"idle\"}')" in main
    assert "window.fizzyDisplayMode" in main


# ---- display.html, run in node ------------------------------------------------------------------

HARNESS = r"""
const vm = require("vm"), fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const src = html.slice(html.lastIndexOf("<script>") + 8, html.lastIndexOf("</script>"));
const store = new Map();
let now = 0, timers = [], seq = 0, qrState = "waiting";
const idleData = { events: [], soon: [], rewards: [], leaders: [{ rank: 1, name: "Adeline", points: 900 }], leaders_more: 0,
                   popular: [], menu: [{ name: "Coke", price: "$3.00" }], message: "" };
const customer = { id: 7, first: "Adeline", points: 120 };
function El(id) {
  this.id = id; this.cls = new Set(); this.kids = []; this.hidden = false; this.dataset = {}; this.innerHTML = "";
  this.style = { setProperty() {} }; this._t = "";
  const self = this;
  this.classList = { add: c => self.cls.add(c), remove: c => self.cls.delete(c), contains: c => self.cls.has(c),
                     toggle: (c, on) => { on = on === undefined ? !self.cls.has(c) : on; on ? self.cls.add(c) : self.cls.delete(c); return on; } };
}
Object.defineProperty(El.prototype, "className", { get() { return [...this.cls].join(" "); }, set(v) { this.cls = new Set(String(v).split(/\s+/).filter(Boolean)); } });
Object.defineProperty(El.prototype, "textContent", { get() { return this._t; }, set(v) { this._t = String(v); this.kids = []; } });
El.prototype.appendChild = function (k) { this.kids.push(k); return k; };
El.prototype.append = function (...k) { this.kids.push(...k); };
El.prototype.querySelector = function () { return this._q || (this._q = new El()); };
El.prototype.getBoundingClientRect = () => ({ left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 });
El.prototype.remove = function () {};
class FakeDate extends Date { constructor(...a) { if (a.length) super(...a); else super(now); } static now() { return now; } }
function page() {
  const els = {}, slides = ["hello", "menu", "popular", "leaders", "soon"].map(s => { const e = new El(); e.dataset.s = s; return e; });
  const later = (fn, ms, every) => { const id = ++seq; timers.push({ id, at: now + (ms || 0), fn, every: every ? ms : 0 }); return id; };
  const clear = id => { timers = timers.filter(t => t.id !== id); };
  const ctx = {
    document: { getElementById: id => els[id] || (els[id] = new El(id)), querySelectorAll: () => slides, createElement: () => new El(),
                createTextNode: () => ({}), addEventListener() {}, hidden: false },
    localStorage: { getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)) },
    matchMedia: () => ({ matches: false }), performance: { now: () => now }, requestAnimationFrame() {}, navigator: {},
    innerHeight: 900, setTimeout: (f, ms) => later(f, ms), setInterval: (f, ms) => later(f, ms, true), clearTimeout: clear, clearInterval: clear,
    fetch: url => Promise.resolve({ ok: true, status: 200, redirected: false,
      json: () => Promise.resolve(url.includes("/idle") ? idleData : url.includes("/qr/") ? { state: qrState } : customer) }),
    Date: FakeDate, JSON, Math, Number, String, Array, Promise, console,
  };
  ctx.window = ctx;
  vm.createContext(ctx); vm.runInContext(src, ctx);
  return { ctx, els, slides };
}
const flush = () => new Promise(r => setImmediate(r));
async function advance(ms) {
  const end = now + ms;
  for (;;) {
    timers.sort((a, b) => a.at - b.at);
    const t = timers[0];
    if (!t || t.at > end) break;
    now = t.at;
    if (t.every) t.at += t.every; else timers.shift();
    t.fn(); await flush();
  }
  now = end; await flush();
}
let p;
function state() {
  const on = ["idle", "cust", "blank"].filter(s => p.els[s].cls.has("on"));
  const slide = p.slides.filter(s => s.cls.has("on")).map(s => s.dataset.s);
  return { screen: on.join(","), slide: on[0] === "idle" ? slide.join(",") : "", signin: !p.els["c-signin"].hidden,
           dots: p.els["dots"].kids.length, countdown: !p.els["countdown"].hidden };
}
const out = {};
async function step(name, msg, wait) {
  if (msg) p.ctx.fizzyDisplay(msg);
  await flush(); await advance(wait || 0);
  out[name] = state();
}
(async () => {
  const login = { id: "abc", seconds: 60, svg: "<svg></svg>" };
  p = page();
  await step("start", null, 10);
  await step("auto_customer", { type: "show", customer, auto: true });
  await step("auto_other_page", { type: "idle", auto: true });
  await step("held_leaders", { type: "mode", mode: "leaders" });
  await step("held_customer_page", { type: "show", customer, auto: true });
  await step("held_page_again", { type: "mode", mode: "leaders", auto: true });
  await step("held_signin", { type: "show", customer, login });
  await step("held_signin_page_change", { type: "mode", mode: "leaders", auto: true }, 1000);
  await step("held_signin_expired", null, 61000);
  qrState = "used";
  await step("held_signin_again", { type: "show", customer, login }, 2000);
  await step("held_signin_used", null, 8000);
  qrState = "waiting";
  await step("empty_slide", { type: "mode", mode: "popular" });
  await step("rotate", { type: "mode", mode: "rotate" });
  await step("rotate_customer_page", { type: "show", customer, auto: true });
  await step("hidden", { type: "mode", mode: "hide" });
  await step("hidden_customer_page", { type: "show", customer, auto: true });
  await step("hidden_signin", { type: "show", customer, login });
  await step("hidden_signin_expired", null, 61000);
  await step("back_to_auto", { type: "mode", mode: "auto" }, 1000);
  await step("auto_after", { type: "show", customer, auto: true });
  await step("mac_hide", { type: "hide" });
  await step("mac_hide_page", { type: "mode", mode: "auto", auto: true });
  await step("mac_hide_page_customer", { type: "show", customer, auto: true });
  await step("mac_show", { type: "idle" });
  await step("mac_show_customer", { type: "show", customer, auto: true });
  await step("leaders_again", { type: "mode", mode: "leaders" });
  out.saved = store.get("fizzy-display-showing");
  timers = []; p = page();                                         // the display reloads
  await step("reloaded", null, 10);
  await step("reloaded_customer_page", { type: "show", customer, auto: true });
  await step("auto_customer_back", { type: "mode", mode: "auto" });
  await step("auto_customer_back2", { type: "show", customer, auto: true }, 1000);
  process.stdout.write(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_display_follows_the_chosen_mode(tmp_path):
    h = tmp_path / "harness.js"
    h.write_text(HARNESS)
    r = subprocess.run(["node", str(h), str(ROOT / "fizzy/templates/display.html")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)
    idle = lambda slide: {"screen": "idle", "slide": slide}  # noqa: E731
    sub = lambda k: {x: s[k][x] for x in ("screen", "slide")}  # noqa: E731

    assert s["start"]["screen"] == "idle" and s["start"]["dots"] == 3          # Auto: hello, menu, leaders take turns
    assert s["auto_customer"]["screen"] == "cust"
    assert s["auto_other_page"]["screen"] == "idle"
    # Held on one slide: no rotation, and pages opening (a customer's too) don't move it
    assert sub("held_leaders") == idle("leaders") and s["held_leaders"]["dots"] == 0 and not s["held_leaders"]["countdown"]
    assert sub("held_customer_page") == idle("leaders")
    assert sub("held_page_again") == idle("leaders")
    # The Sign-in code button still reaches it: the customer and the code, then back once it runs out or is used
    assert s["held_signin"]["screen"] == "cust" and s["held_signin"]["signin"]
    assert s["held_signin_page_change"]["screen"] == "cust" and s["held_signin_page_change"]["signin"]
    assert sub("held_signin_expired") == idle("leaders")
    assert s["held_signin_again"]["screen"] == "cust"                           # "Signed in on a phone." for a moment
    assert sub("held_signin_used") == idle("leaders")
    # A slide with nothing on it shows Welcome
    assert sub("empty_slide") == idle("hello")
    # All of them taking turns, whatever page is open
    assert s["rotate"]["screen"] == "idle" and s["rotate"]["dots"] == 3
    assert s["rotate_customer_page"]["screen"] == "idle"
    # Hidden
    assert s["hidden"]["screen"] == "blank" and s["hidden_customer_page"]["screen"] == "blank"
    assert s["hidden_signin"]["screen"] == "cust" and s["hidden_signin"]["signin"]
    assert s["hidden_signin_expired"]["screen"] == "blank"
    # Back to Auto: idle until the page says otherwise, then pages are followed again
    assert s["back_to_auto"]["screen"] == "idle" and s["auto_after"]["screen"] == "cust"
    # The Mac menu: Hide stays hidden while pages open; Show is Auto
    assert s["mac_hide"]["screen"] == "blank"
    assert s["mac_hide_page"]["screen"] == "blank" and s["mac_hide_page_customer"]["screen"] == "blank"
    assert s["mac_show"]["screen"] == "idle" and s["mac_show_customer"]["screen"] == "cust"
    # A reload comes back on the same screen
    assert json.loads(s["saved"]) == {"mode": "leaders", "menu": False}
    assert sub("reloaded") == idle("leaders") and sub("reloaded_customer_page") == idle("leaders")
    assert s["auto_customer_back2"]["screen"] == "cust"


# ---- app.js's Display menu, run in node ---------------------------------------------------------
# The menu's code from app.js (and init()'s part that hooks it up), run against a stand-in for the page
# and for localStorage, with tabs that share one store the way tabs on one device do.

APP_HARNESS = r"""
const vm = require("vm"), fs = require("fs");
const app = fs.readFileSync(process.argv[2], "utf8");
const cut = (a, b) => { const i = app.indexOf(a), j = app.indexOf(b, i); if (i < 0 || j < 0) throw new Error("not found: " + a); return app.slice(i, j); };
const menu = cut("  var DISPLAY_MODES", "  window.fizzyDisplayMode = setDisplayMode;");
const hook = cut('    if ($("header nav")) {\n      var dsel', "    section(location.hash.slice(1));");
function tab(store, how, page) {
  const sent = [], listeners = [];
  const box = { cls: new Set(), title: "", classList: { toggle(c, on) { on ? box.cls.add(c) : box.cls.delete(c); } } };
  const sel = { value: "", ready: false, classList: { add() { sel.ready = true; } }, closest: () => box,
                get selectedOptions() { return [{ textContent: sel.value }]; } };
  const ls = {
    getItem: k => { if (how === "noread") throw new Error("SecurityError"); return store.has(k) ? store.get(k) : null; },
    setItem: (k, v) => { if (how !== "ok") throw new Error("QuotaExceededError"); store.set(k, String(v)); },
  };
  const ctx = { localStorage: ls, console, JSON };
  ctx.window = ctx;
  vm.createContext(ctx);
  const t = vm.runInContext("(function (sel, sent, listeners, msg) {" +
    "var $ = function (s) { return s === '[data-display-mode]' ? sel : s === 'header nav' ? {} : null; };" +
    "var json = function (s) { return s === '#display-msg' && msg ? JSON.parse(JSON.stringify(msg)) : null; };" +
    "var sendDisplay = function (m) { sent.push(JSON.parse(JSON.stringify(m))); };" +
    "var listen = function (target, type, fn) { listeners.push({ target: target, type: type, fn: fn }); };" +
    "var tab = { open: function () { listeners.length = 0; " + hook + "} };" +
    menu + "\ntab.mode = displayMode; return tab; })", ctx)(sel, sent, listeners, page);
  const fire = (type, ev) => listeners.filter(l => l.type === type).forEach(l => l.fn(ev || {}));
  return { sent, sel, box, open: () => { sent.length = 0; t.open(); }, mode: t.mode, fire,
           pick: v => { sent.length = 0; sel.value = v; fire("change"); },
           storage: key => { sent.length = 0; fire("storage", { key }); },
           state: () => ({ value: sel.value, held: box.cls.has("held"), sent: sent.slice(), storageHooks: listeners.filter(l => l.type === "storage" && l.target && l.target.localStorage === ls).length }) };
}
const out = {};
const cust = { type: "show", customer: { id: 1, first: "Adeline" } };
// Storage can be read but not written (full, or old Safari private mode): the pick is kept for this tab
for (const how of ["nowrite", "noread"]) {
  const a = tab(new Map(), how, null);
  a.open(); out[how + "_open"] = a.state();
  a.pick("popular"); out[how + "_pick"] = a.state();
  a.open(); out[how + "_next_page"] = a.state();
  a.pick("auto"); out[how + "_auto"] = a.state();
}
// Two tabs on one device: a pick in one shows in the other's menu straight away
const store = new Map();
const A = tab(store, "ok", cust), B = tab(store, "ok", null);
A.open(); B.open();
A.pick("popular"); B.storage("fizzy-display-mode"); out.b_sees_pick = B.state();
B.pick("auto"); A.storage("fizzy-display-mode"); out.a_sees_auto = A.state();
A.pick("popular"); out.a_picks_again = A.state();
B.storage("something-else"); out.b_other_key = B.state();
B.sel.value = "hide"; B.storage(null); out.b_cleared = B.state();
store.set("fizzy-display-mode", "leaders"); B.storage("fizzy-display-mode"); out.b_after_clear = B.state();
B.open(); out.b_next_page_hooks = B.state().storageHooks;
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_app_js_display_menu(tmp_path):
    h = tmp_path / "app_harness.js"
    h.write_text(APP_HARNESS)
    r = subprocess.run(["node", str(h), str(ROOT / "fizzy/static/app.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)
    mode = lambda m, again=False: dict({"type": "mode", "mode": m}, **({"auto": True} if again else {}))  # noqa: E731

    for how in ("nowrite", "noread"):
        assert s[how + "_open"]["value"] == "auto" and s[how + "_open"]["sent"] == [mode("auto", True), {"type": "idle", "auto": True}]
        # the pick sticks (menu and display) though it couldn't be saved
        assert s[how + "_pick"]["value"] == "popular" and s[how + "_pick"]["held"], how
        assert s[how + "_pick"]["sent"] == [mode("popular")], how
        assert s[how + "_next_page"]["value"] == "popular" and s[how + "_next_page"]["sent"] == [mode("popular", True)], how
        assert s[how + "_auto"]["value"] == "auto" and not s[how + "_auto"]["held"]
        assert s[how + "_auto"]["sent"] == [mode("auto"), {"type": "idle", "auto": True}]

    # the other tab's menu follows, without telling the display again (the tab that picked has)
    assert s["b_sees_pick"]["value"] == "popular" and s["b_sees_pick"]["held"] and s["b_sees_pick"]["sent"] == []
    assert s["a_sees_auto"]["value"] == "auto" and not s["a_sees_auto"]["held"] and s["a_sees_auto"]["sent"] == []
    # so the first tab can pick that same option again
    assert s["a_picks_again"]["sent"] == [mode("popular")]
    assert s["b_other_key"]["sent"] == [] and s["b_other_key"]["value"] == "auto"   # untouched by other keys
    assert s["b_cleared"]["value"] == "popular"                                     # storage cleared: re-read
    assert s["b_after_clear"]["value"] == "leaders"
    assert s["b_next_page_hooks"] == 1                                              # one listener a page, not one more each page

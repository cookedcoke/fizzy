import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { GlassIconReveal } from "@/components/ui/liquid-glass-logo-reveal";
import { api, base, href, type GuestCard, type Home as HomeData, type ScanResult } from "@/lib/api";
import { Login } from "@/pages/Login";
import { Home } from "@/pages/Home";
import { GuestHunt } from "@/pages/Hunt";

type View =
  | { kind: "loading" }
  | { kind: "home"; data: HomeData }
  | { kind: "hunt"; cards: GuestCard[]; result: ScanResult | null }
  | { kind: "error"; message: string };

/** The page's own path, without the /me prefix used off the student host. */
const route = () => { const p = location.pathname; return (p.startsWith(base) ? p.slice(base.length) : p) || "/"; };

// The glass reveal plays in full the first time the site opens in a browser session (about 2.5 s, until the
// glass lets go of the logo); after that the splash only shows while the page is actually loading.
const firstOpen = (() => {
  try { if (sessionStorage.getItem("cc-revealed")) return false; sessionStorage.setItem("cc-revealed", "1"); } catch { /* private mode */ }
  return !matchMedia("(prefers-reduced-motion: reduce)").matches;
})();

export default function App() {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [revealed, setRevealed] = useState(!firstOpen);
  useEffect(() => { if (!revealed) { const t = setTimeout(() => setRevealed(true), 2600); return () => clearTimeout(t); } }, [revealed]);

  const load = useCallback(async () => {
    const path = route();
    try {
      const code = path.match(/^\/c\/([^/]+)$/)?.[1];
      if (code) {
        // A poster's code, opened with the phone's camera: scan it, then show where it landed.
        const { body } = await api.scan(decodeURIComponent(code));
        if (body.signed_in) {
          history.replaceState(null, "", href("/"));
          const home = await api.home();
          if (home.body.signed_in) home.body.note = { message: body.message, ok: body.ok, result: body.result };
          setView({ kind: "home", data: home.body });
        } else {
          history.replaceState(null, "", href("/hunt"));
          setView({ kind: "hunt", cards: body.cards ?? [], result: body.result });
        }
        return;
      }
      if (path === "/hunt") {
        const { body } = await api.hunt();
        setView(v => ({ kind: "hunt", cards: body.cards ?? [], result: v.kind === "hunt" ? v.result : null }));
        return;
      }
      if (path !== "/") history.replaceState(null, "", href("/"));
      const { body } = await api.home();
      setView({ kind: "home", data: body });
    } catch {
      setView({ kind: "error", message: "Couldn't reach the stall's server. Check your connection and try again." });
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  // Installed as an app there's no reload button: after a minute away, come back to fresh numbers.
  useEffect(() => {
    let hid = 0;
    const onVis = () => { if (document.hidden) hid = Date.now(); else if (hid && Date.now() - hid > 60000) load(); };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [load]);

  const toHome = () => { history.replaceState(null, "", href("/")); setView({ kind: "loading" }); load(); };

  return (
    <MotionConfig reducedMotion="user">
      <AnimatePresence>{(view.kind === "loading" || !revealed) && <Splash key="splash" />}</AnimatePresence>
      {view.kind === "error" && <ErrorPage message={view.message} retry={load} />}
      {view.kind === "hunt" && <GuestHunt cards={view.cards} result={view.result} reload={load} />}
      {view.kind === "home" && (view.data.signed_in
        ? <Home me={view.data} reload={load} onOut={toHome} />
        : <Login w={view.data} onIn={toHome} />)}
    </MotionConfig>
  );
}

/** The loading screen: our logo lands as a drop of glass, the glass bends it at the rim, then lets go. */
function Splash() {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  return (
    <motion.div className="fixed inset-0 z-50" initial={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: .35 } }}>
      <GlassIconReveal logo="/static/logo.svg" label="My Cooked Coke" loop={false} className="size-full cursor-default"
        look={dark ? "dark" : "light"} background={dark ? "#17120f" : "#FFF7EE"} logoColor={dark ? "#FF2A1A" : "#E3110B"}
        plateColor="#221b17" logoScale={0.62} finalScale={0.85} tileSize={220} />
    </motion.div>
  );
}

function ErrorPage({ message, retry }: { message: string; retry: () => void }) {
  return (
    <main className="mx-auto max-w-[420px] px-[18px] pt-16 text-center">
      <img src="/static/logo.svg" alt="" className="mx-auto size-16" />
      <h1 className="mt-3 text-[26px] font-extrabold">Can't load right now</h1>
      <p className="mt-1 text-muted">{message}</p>
      <button onClick={retry} className="mt-5 rounded-full bg-red px-6 py-3 font-display font-bold text-white">Try again</button>
    </main>
  );
}

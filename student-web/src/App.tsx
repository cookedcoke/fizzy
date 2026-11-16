import { useCallback, useEffect, useState } from "react";
import { MotionConfig, motion } from "motion/react";
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

export default function App() {
  const [view, setView] = useState<View>({ kind: "loading" });

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
      {view.kind === "loading" && <Splash />}
      {view.kind === "error" && <ErrorPage message={view.message} retry={load} />}
      {view.kind === "hunt" && <GuestHunt cards={view.cards} result={view.result} reload={load} />}
      {view.kind === "home" && (view.data.signed_in
        ? <Home me={view.data} reload={load} onOut={toHome} />
        : <Login w={view.data} onIn={toHome} />)}
    </MotionConfig>
  );
}

function Splash() {
  return (
    <div className="grid min-h-dvh place-items-center">
      <motion.img src="/static/logo.svg" alt="Loading" className="size-14" animate={{ scale: [1, 1.08, 1], opacity: [.7, 1, .7] }}
        transition={{ duration: 1.2, repeat: Infinity }} />
    </div>
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

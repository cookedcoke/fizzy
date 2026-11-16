import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import { api, type Run, type ScanResult, type Step } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { KindIcon } from "@/components/Icon";

const tone: Record<ScanResult["kind"], string> = {
  found: "bg-ok-wash [&_.ico]:bg-ok [&_.ico]:text-white dark:[&_.ico]:text-[#0B2A1A]",
  done: "bg-sun-wash [&_.ico]:bg-sun [&_.ico]:text-[#3D2800]",
  already: "bg-card [&_.ico]:bg-sun-wash [&_.ico]:text-[#8A6200] dark:[&_.ico]:text-sun",
  soon: "bg-card [&_.ico]:bg-sun-wash [&_.ico]:text-[#8A6200] dark:[&_.ico]:text-sun",
  over: "bg-card [&_.ico]:bg-line [&_.ico]:text-muted",
  bad: "bg-red-wash [&_.ico]:bg-red [&_.ico]:text-white",
  info: "bg-card [&_.ico]:bg-line [&_.ico]:text-muted",
};

/** What a scan just did: the first thing on the page after opening a poster's link. */
export function ScanResultCard({ r }: { r: ScanResult }) {
  return (
    <motion.section role="status" initial={{ opacity: 0, y: 10, scale: .98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 380, damping: 26 }}
      className={cn("mt-3.5 flex items-center gap-3.5 rounded-[18px] px-[18px] py-4 shadow-card", tone[r.kind])}>
      <motion.span className="ico grid size-12 flex-none place-items-center rounded-full"
        initial={{ scale: .4, rotate: -20 }} animate={{ scale: 1, rotate: 0 }} transition={{ type: "spring", stiffness: 500, damping: 18, delay: .08 }}>
        <KindIcon kind={r.kind} className="size-6" />
      </motion.span>
      <div className="min-w-0 [overflow-wrap:anywhere]">
        {r.title && <h2 className="text-[21px] font-extrabold leading-tight">{r.title}</h2>}
        <p className="mt-0.5 text-[15px] leading-snug text-muted">{r.line}</p>
      </div>
    </motion.section>
  );
}

export function Progress({ done, total }: { done: number; total: number }) {
  const pct = total ? Math.round((100 * done) / total) : 0;
  return (
    <div className="mt-3">
      <div className="flex justify-between text-[14px]"><span className="font-semibold">{done} of {total} found</span>
        {done < total && <span className="text-muted">{total - done} to go</span>}</div>
      <div className="mt-1.5 h-3 overflow-hidden rounded-full bg-line" role="progressbar" aria-label="Stops found"
        aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
        <motion.div className="h-full rounded-full bg-gradient-to-r from-sun to-[#FF9A2E]"
          initial={{ width: 0 }} animate={{ width: `${pct}%` }} transition={{ duration: .9, ease: [.2, .8, .2, 1] }} />
      </div>
    </div>
  );
}

/** Every stop: found ones filled in; the rest show their hint (never the code). */
export function Stops({ steps, just }: { steps: Step[]; just?: string | null }) {
  if (!steps.length) return null;
  return (
    <ol className="mt-3.5 grid list-none grid-cols-[repeat(auto-fill,minmax(128px,1fr))] gap-2 p-0 sm:grid-cols-3">
      {steps.map((s, i) => {
        const isJust = !!just && s.done && s.label === just;
        return (
          <motion.li key={i} initial={isJust ? { scale: .9 } : false} animate={isJust ? { scale: [1.06, 1] } : undefined}
            className={cn("flex min-h-12 items-start gap-2.5 rounded-xl px-2.5 py-2.5",
              s.done ? "bg-sun-wash" : "bg-card-2", isJust && "ring-2 ring-sun")}>
            <span className={cn("mt-px grid size-6 flex-none place-items-center rounded-full border-2",
              s.done ? "border-sun bg-sun text-[#3D2800]" : "border-line")}>
              {s.done && <KindIcon kind="got" className="size-[15px]" />}
            </span>
            <span className="flex min-w-0 flex-col leading-tight [overflow-wrap:anywhere]">
              <b className="text-[14px] font-[650]">{s.label}</b>
              {s.detail && !s.done && <small className="text-[12.5px] text-muted">{s.detail}</small>}
              <span className="sr-only">{s.done ? " (found)" : " (not found yet)"}</span>
            </span>
          </motion.li>
        );
      })}
    </ol>
  );
}

export function NextPoster() {
  return (
    <p className="mt-3.5 flex items-center gap-3 rounded-[14px] bg-sun-wash px-3.5 py-3 text-[14px] text-sun-ink">
      <span className="grid size-10 flex-none place-items-center rounded-full bg-sun text-[#3D2800]"><KindIcon kind="camera" className="size-5" /></span>
      <span><b>Scan the next poster.</b> Open your camera and point it at the code. You can scan in the My CCC app too.</span>
    </p>
  );
}

const clock = (s: number) => {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + ":" + String(x).padStart(2, "0");
};

function useNow(ms = 500) {
  const [now, set] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => set(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}

/** A timed hunt: the clock while it runs, the finish time and place after, and the nickname form until given. */
export function RunPanel({ run, onSaved }: { run?: Run | null; onSaved: () => void }) {
  const now = useNow();
  const [nick, setNick] = useState(""), [klass, setKlass] = useState("");
  const [busy, setBusy] = useState(false), [err, setErr] = useState<string | null>(null);
  if (!run) return null;
  const save = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    const { body } = await api.huntName(nick, klass);
    setBusy(false);
    if (body.ok) onSaved(); else setErr(body.message);
  };
  return (
    <div className="mt-3.5 mb-1 rounded-[18px] bg-card-2 px-4 py-3.5 text-center">
      <div className="text-[13px] font-bold uppercase tracking-[.06em] text-muted">{run.finished ? "Your time" : "Your clock"}</div>
      <div className="font-display text-[clamp(44px,16vw,68px)] font-extrabold leading-[1.05] tabular-nums">
        {run.finished ? run.elapsed : clock((now - run.started_ms) / 1000)}
      </div>
      {run.finished && run.rank != null && (
        <div className="font-display text-[18px] font-bold">#{run.rank} so far
          {run.top != null && <small className="ml-1 font-sans text-[13px] font-normal text-muted">Fastest {run.top} decided at close</small>}</div>
      )}
      {run.finished && <div className="mt-1 text-muted tabular-nums">{new Date(now).toLocaleTimeString([], { hour12: false })}</div>}
      {!run.finished && run.clue && (
        <div className="mt-2.5 rounded-xl bg-sun-wash px-3 py-2 text-left text-sun-ink"><small className="block text-[12px] font-bold uppercase tracking-wide">Your next clue</small>{run.clue}</div>
      )}
      {run.needs_name ? (
        <form onSubmit={save} className="mt-3 grid gap-2 text-left">
          <p className="text-[14px]"><b>{run.finished ? "Add your nickname so you are on the board" : "Add your nickname"}</b> (only the nickname is shown) and your class or year, so staff can find you if you win.</p>
          {err && <p role="alert" className="rounded-lg bg-red-wash px-3 py-2 text-[14px] text-red-deep">{err}</p>}
          <input className="rounded-xl border border-line bg-card px-3.5 py-3 text-[16px] outline-none focus:border-red" value={nick} onChange={e => setNick(e.target.value)} maxLength={20} placeholder="Nickname" autoComplete="off" required />
          <input className="rounded-xl border border-line bg-card px-3.5 py-3 text-[16px] outline-none focus:border-red" value={klass} onChange={e => setKlass(e.target.value)} maxLength={12} placeholder="Class / year (e.g. 8B)" autoComplete="off" required />
          <Button disabled={busy}>{busy ? "Saving..." : "Save"}</Button>
        </form>
      ) : run.nickname ? (
        <p className="mt-2 text-[13px] text-muted">On the board as <b className="text-ink">{run.nickname}</b> ({run.klass})</p>
      ) : null}
    </div>
  );
}

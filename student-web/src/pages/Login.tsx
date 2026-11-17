import { CupSoda } from "lucide-react";
import { useState } from "react";
import { motion } from "motion/react";
import { api, type WhatsOn } from "@/lib/api";
import { num } from "@/lib/format";
import { Events } from "@/components/Events";
import { Marquee } from "@/components/ui/marquee";
import { ShimmerButton } from "@/components/ui/shimmer-button";

const field = "w-full rounded-[14px] border-[1.5px] border-line bg-[#FFFDFB] px-4 py-3.5 text-[17px] outline-none transition-colors focus:border-red focus:bg-card dark:bg-[#1d1714] dark:focus:bg-card";

export function Login({ w, onIn }: { w: WhatsOn; onIn: () => void }) {
  const [who, setWho] = useState(""), [pc, setPc] = useState(""), [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false), [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;   // one tap, one try: a second tap would count as another wrong guess
    setBusy(true); setErr(null);
    const { body } = await api.login(who, pc);
    setBusy(false);
    if (body.ok) onIn(); else setErr(body.error ?? "Something went wrong. Try again.");
  };
  return (
    <div className="relative isolate min-h-dvh overflow-hidden">
    <div aria-hidden className="absolute -top-48 left-1/2 -z-10 size-[460px] -translate-x-1/2 rounded-full bg-sun/20 blur-3xl dark:bg-red/15" />
    <main className="mx-auto max-w-[420px] px-[18px] pt-[max(40px,env(safe-area-inset-top))] pb-12 text-center">
      <motion.img src="/static/logo.svg" alt="" className="mx-auto size-[72px]"
        initial={{ scale: .6, rotate: -12, opacity: 0 }} animate={{ scale: 1, rotate: 0, opacity: 1 }} transition={{ type: "spring", stiffness: 260, damping: 16 }} />
      <h1 className="mt-3 text-[32px] font-extrabold">My Cooked Coke</h1>
      <p className="mb-6 text-muted">Your points, rewards and tab.</p>
      <motion.form onSubmit={submit} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: .1 }}
        className="rounded-[20px] border border-line/70 bg-card p-5 text-left shadow-card">
        {err && <motion.div role="alert" initial={{ x: -8 }} animate={{ x: [8, -6, 4, 0] }} transition={{ duration: .35 }}
          className="mb-3 rounded-xl bg-red-wash px-3.5 py-2.5 text-[14px] text-red-deep">{err}</motion.div>}
        <label htmlFor="who" className="mb-1.5 block text-[14px] font-semibold">Your name or student number</label>
        <input id="who" className={field} value={who} onChange={e => setWho(e.target.value)} autoComplete="username" autoCapitalize="words"
          autoCorrect="off" spellCheck={false} enterKeyHint="next" required autoFocus />
        <label htmlFor="pc" className="mt-4 mb-1.5 block text-[14px] font-semibold">Passcode</label>
        <div className="relative">
          <input id="pc" className={field + " pr-20"} type={show ? "text" : "password"} value={pc} onChange={e => setPc(e.target.value)}
            autoComplete="current-password" enterKeyHint="go" required />
          <button type="button" onClick={() => setShow(!show)} aria-controls="pc"
            className="absolute top-1/2 right-2.5 -translate-y-1/2 rounded-full px-3 py-1 text-[13.5px] font-semibold text-muted hover:text-ink">{show ? "Hide" : "Show"}</button>
        </div>
        <ShimmerButton type="submit" disabled={busy} aria-busy={busy} background="var(--red)" shimmerColor="#FFE7A3" shimmerDuration="2.6s"
          className="mt-5 w-full py-3.5 font-display text-[16px] font-bold shadow-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red disabled:opacity-60">
          {busy ? "Checking..." : "Let me in"}</ShimmerButton>
        <p className="mt-3.5 text-center text-[13px] text-muted">No passcode yet, or forgot it? Ask at the stall.</p>
      </motion.form>
      {/* what's on, all in one place under the form: double points days, then the rewards */}
      {(w.rewards.length > 0 || w.events_on.length > 0 || w.events_soon.length > 0) && (
        <section className="mt-9 text-left">
          <h2 className="mb-3 text-[19px] font-[750]">What's on</h2>
          <Events w={w} />
          {w.rewards.length > 0 && (w.rewards.length > 2 ? (
            <Marquee pauseOnHover repeat={3} className="[--duration:28s] [--gap:.5rem] [mask-image:linear-gradient(90deg,transparent,white_8%,white_92%,transparent)]">
              {w.rewards.map(r => <Chip key={r.name} name={r.name} cost={r.points_cost} />)}
            </Marquee>
          ) : (
            <div className="flex flex-wrap gap-2">{w.rewards.map(r => <Chip key={r.name} name={r.name} cost={r.points_cost} />)}</div>
          ))}
        </section>
      )}
    </main>
    </div>
  );
}

function Chip({ name, cost }: { name: string; cost: number }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-line/70 bg-card px-3.5 py-1.5 text-[14px] shadow-card">
      <CupSoda aria-hidden className="size-4 text-red" strokeWidth={2.2} /><b className="font-display">{name}</b><span className="text-muted">· {num(cost)} pts</span></span>
  );
}

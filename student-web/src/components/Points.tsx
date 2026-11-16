import { useEffect, useState } from "react";
import { animate, motion, useMotionValue, useTransform } from "motion/react";
import { num } from "@/lib/format";
import { AnimatedShinyText } from "@/components/ui/animated-shiny-text";
import { DotPattern } from "@/components/ui/dot-pattern";
import { NumberTicker } from "@/components/ui/number-ticker";

export interface Stat { label: string; value: number | null; suffix?: string; prefix?: string }

/** The fizzy can: the balance, counting up from what this phone saw last visit ("+120 since last time"),
 *  with the streak, badges and this month's place underneath. */
export function PointsCard({ points, who, stats }: { points: number; who: string; stats: Stat[] }) {
  const [gain, setGain] = useState(0);
  const mv = useMotionValue(points);
  const shown = useTransform(mv, v => num(Math.round(v)));
  useEffect(() => {
    const key = "cc-pts-" + who;
    let before: number | null = null;
    try { const v = localStorage.getItem(key); before = v === null ? null : Number(v); localStorage.setItem(key, String(points)); } catch { /* private mode */ }
    if (before !== null && points > before) {
      setGain(points - before);
      mv.set(before);
      const a = animate(mv, points, { duration: .9, ease: [.2, .8, .2, 1] });
      return () => a.stop();
    }
    mv.set(points);
  }, [points, who, mv]);
  const bubbles = [[86, 18, 8], [78, 40, 5], [92, 52, 12], [70, 66, 7], [96, 76, 5]];
  return (
    <div className="relative isolate overflow-hidden rounded-[26px] bg-gradient-to-br from-[#FF2A1A] via-red to-[#B30000] text-white shadow-[0_18px_40px_-18px_rgba(179,0,0,.7)]">
      <DotPattern width={14} height={14} cr={1.1} className="-z-10 text-white/15 [mask-image:radial-gradient(260px_circle_at_85%_10%,white,transparent)]" />
      {bubbles.map(([x, y, r], i) => (
        <motion.span key={i} aria-hidden className="absolute -z-10 rounded-full bg-white/20"
          style={{ left: `${x}%`, top: `${y}%`, width: r * 2, height: r * 2, marginLeft: -r, marginTop: -r }}
          animate={{ y: [0, -6, 0], opacity: [.7, 1, .7] }} transition={{ duration: 3 + i * .6, repeat: Infinity, ease: "easeInOut" }} />
      ))}
      <div className="px-[22px] pt-5 pb-4">
        <div className="flex flex-wrap items-center gap-2 text-[13px] font-semibold opacity-90">Your points
          {gain > 0 && <motion.span initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: .3 }}
            className="rounded-full bg-white/20 px-2 py-0.5 text-[12px]">
            <AnimatedShinyText className="mx-0 text-white/85 via-white dark:text-white/85 dark:via-white">+{num(gain)} since last time</AnimatedShinyText></motion.span>}</div>
        <motion.div className="my-1.5 font-display text-[60px] font-extrabold leading-none tracking-[-.03em]">{shown}</motion.div>
        <div className="text-[13px] opacity-85">1 point for every cent you spend. Tab buys earn theirs when you pay them off.</div>
      </div>
      <dl className="grid grid-cols-3 border-t border-white/15 bg-black/10">
        {stats.map((s, i) => (
          <div key={s.label} className={i ? "border-l border-white/15 px-3 py-3" : "px-3 py-3 pl-[22px]"}>
            <dt className="text-[11.5px] font-semibold uppercase tracking-wide opacity-75">{s.label}</dt>
            <dd className="font-display text-[22px] font-extrabold leading-tight">
              {s.value == null ? <span className="opacity-60">-</span>
                : <>{s.prefix}<NumberTicker value={s.value} className="tracking-normal text-white dark:text-white" />{s.suffix}</>}</dd>
          </div>))}
      </dl>
    </div>
  );
}

export function Streak({ days, best, today }: { days: number; best: number; today: boolean }) {
  return (
    <div className="mt-3 flex items-center gap-3 rounded-[20px] border border-line/70 bg-card px-4 py-3 shadow-card">
      <motion.div aria-hidden className="grid size-11 flex-none place-items-center rounded-full bg-sun-wash text-[24px]"
        animate={days ? { scale: [1, 1.12, 1], rotate: [0, -4, 4, 0] } : undefined}
        transition={{ duration: 1.6, repeat: Infinity, repeatDelay: 2 }}>{days ? "🔥" : "🧊"}</motion.div>
      <div className="flex flex-col leading-snug">
        <b className="font-display">{days ? `${days}-day streak` : "No streak yet"}</b>
        <span className="text-[13.5px] text-muted">
          {today ? "Bought today - nice." : days ? "Grab something today to keep it going." : "Buy something on days the stall's open to start one."}
          {best > days && ` Best: ${best}.`}</span>
      </div>
    </div>
  );
}

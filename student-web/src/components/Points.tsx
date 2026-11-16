import { useEffect, useState } from "react";
import { animate, motion, useMotionValue, useTransform } from "motion/react";
import { num } from "@/lib/format";
import { AnimatedShinyText } from "@/components/ui/animated-shiny-text";

/** The fizzy can: the balance, counting up from what this phone saw last visit ("+120 since last time"). */
export function PointsCard({ points, who }: { points: number; who: string }) {
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
  const bubbles = [[86, 22, 8], [78, 48, 5], [92, 62, 12], [70, 80, 7], [96, 92, 5]];
  return (
    <div className="relative overflow-hidden rounded-[24px] bg-gradient-to-br from-red via-[#E00010] to-[#B30000] px-[22px] pt-5 pb-[22px] text-white shadow-[0_14px_30px_-14px_rgba(179,0,0,.6)]">
      {bubbles.map(([x, y, r], i) => (
        <motion.span key={i} aria-hidden className="absolute rounded-full bg-white/20"
          style={{ left: `${x}%`, top: `${y}%`, width: r * 2, height: r * 2, marginLeft: -r, marginTop: -r }}
          animate={{ y: [0, -6, 0], opacity: [.7, 1, .7] }} transition={{ duration: 3 + i * .6, repeat: Infinity, ease: "easeInOut" }} />
      ))}
      <div className="text-[13px] font-semibold opacity-85">Your points
        {gain > 0 && <motion.span initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: .3 }}
          className="ml-2 rounded-full bg-white/20 px-2 py-0.5 text-[12px]">
          <AnimatedShinyText className="mx-0 text-white/85 via-white dark:text-white/85 dark:via-white">+{num(gain)} since last time</AnimatedShinyText></motion.span>}</div>
      <motion.div className="my-1.5 font-display text-[56px] font-extrabold leading-none tracking-[-.03em]">{shown}</motion.div>
      <div className="text-[13px] opacity-85">1 point for every cent you spend. Tab buys earn theirs when you pay them off.</div>
    </div>
  );
}

export function Streak({ days, best, today }: { days: number; best: number; today: boolean }) {
  return (
    <div className="mt-2.5 flex items-center gap-3 rounded-[18px] bg-card px-4 py-3 shadow-card">
      <motion.div aria-hidden className="text-[30px]" animate={days ? { scale: [1, 1.12, 1], rotate: [0, -4, 4, 0] } : undefined}
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

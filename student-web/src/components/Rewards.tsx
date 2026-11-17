import { Check, CupSoda, PartyPopper } from "lucide-react";
import { useEffect, useRef } from "react";
import { motion } from "motion/react";
import type { Offer } from "@/lib/api";
import { Confetti, type ConfettiRef } from "@/components/ui/confetti";
import { num } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Card, Section } from "@/components/ui/card";

/** Confetti once for a reward that's newly ready: the first time this phone sees it ready, not every visit. */
function useCheer(offers: Offer[], confetti: React.RefObject<ConfettiRef | null>) {
  const ready = offers.filter(o => o.can_redeem).map(o => o.name).sort().join("|");
  useEffect(() => {
    if (!ready || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let seen = "";
    try { seen = localStorage.getItem("cc-ready") ?? ""; localStorage.setItem("cc-ready", ready); } catch { return; }
    const before = new Set(seen.split("|"));
    if (!ready.split("|").some(n => !before.has(n))) return;
    const t = setTimeout(() => confetti.current?.fire({ particleCount: 90, spread: 70, origin: { y: .35 },
      colors: ["#FE0001", "#FFC23D", "#FFF7EE", "#B30000"] }), 500);
    return () => clearTimeout(t);
  }, [ready, confetti]);
}

/** Rewards as a timeline: one line up through them in order of cost, filled to where the student's points reach,
 *  each reward a stop on it (gold and ticked when it's theirs). Much shorter than a card each. */
export function Rewards({ offers, points }: { offers: Offer[]; points: number }) {
  const confetti = useRef<ConfettiRef>(null);
  useCheer(offers, confetti);
  if (!offers.length) return null;
  const stops = [...offers].sort((a, b) => a.points_cost - b.points_cost);
  // the fill reaches the stop they last passed, plus their way to the next
  const pts = points;
  const reached = stops.filter(o => pts >= o.points_cost).length;
  const next = stops[reached];
  const prevCost = reached ? stops[reached - 1].points_cost : 0;
  const part = next ? Math.max(0, Math.min(1, (pts - prevCost) / (next.points_cost - prevCost))) : 0;
  const fill = stops.length > 1 ? Math.min(1, (Math.max(0, reached - 1) + (reached ? part : 0)) / (stops.length - 1)) : reached ? 1 : 0;
  return (
    <Section title="Rewards" small={`${num(pts)} points`}>
      <Confetti ref={confetti} manualstart aria-hidden className="pointer-events-none fixed inset-0 z-50 size-full" />
      <Card className="relative px-4 py-2">
        <ol className="relative">
          {/* the line, and how far up it the student has got */}
          {stops.length > 1 && <div aria-hidden className="absolute top-[26px] bottom-[26px] left-[15px] w-[3px] rounded-full bg-line">
            <motion.div className="w-full rounded-full bg-gradient-to-b from-sun to-[var(--bar-b)]"
              initial={{ height: 0 }} animate={{ height: `${fill * 100}%` }} transition={{ duration: 1, ease: "easeOut", delay: .2 }} />
          </div>}
          {stops.map((o, i) => (
            <motion.li key={o.name} initial={{ opacity: 0, x: -6 }} whileInView={{ opacity: 1, x: 0 }} viewport={{ once: true }} transition={{ delay: .1 + i * .06 }}
              className="relative flex items-center gap-3 py-2.5">
              <span aria-hidden className={cn("relative z-10 grid size-[33px] flex-none place-items-center rounded-full ring-4 ring-card",
                o.can_redeem ? "bg-sun text-[#3D2800] shadow-[0_6px_14px_-8px_rgba(179,110,0,.9)]" : o.limit_hit ? "bg-card-2 text-muted" : "bg-sun-wash text-[#8A6200] dark:text-sun")}>
                {o.can_redeem ? <PartyPopper className="size-4" strokeWidth={2.3} /> : o.limit_hit ? <Check className="size-4" strokeWidth={2.6} /> : <CupSoda className="size-4" strokeWidth={2.3} />}
              </span>
              <span className={cn("min-w-0 flex-1 leading-tight", o.limit_hit && "opacity-60")}>
                <b className="font-display">{o.name}</b>
                <span className="block text-[12.5px] text-muted">{num(o.points_cost)} pts{o.limit_hit && (o.per_customer_limit ?? 0) > 1 ? ` · used ${o.used} times` : ""}</span>
              </span>
              {o.can_redeem ? <span className="rounded-full bg-sun px-2.5 py-0.5 text-[12px] font-bold text-[#3D2800]">Ready</span>
                : o.limit_hit ? <span className="text-[12.5px] text-muted">Used</span>
                : <span className="text-[13px] font-semibold tabular-nums text-muted">{num(o.short)} to go</span>}
            </motion.li>
          ))}
        </ol>
        {stops.some(o => o.can_redeem) && <p className="border-t border-line/70 py-2.5 text-[13px] text-muted">Ready ones are yours - just ask at the stall.</p>}
      </Card>
    </Section>
  );
}

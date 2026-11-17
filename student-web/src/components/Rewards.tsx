import { CupSoda, PartyPopper } from "lucide-react";
import { useEffect, useRef } from "react";
import { motion } from "motion/react";
import type { Offer } from "@/lib/api";
import { Confetti, type ConfettiRef } from "@/components/ui/confetti";
import { ShineBorder } from "@/components/ui/shine-border";
import { num } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Bar, Card, Section } from "@/components/ui/card";

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

export function Rewards({ offers }: { offers: Offer[] }) {
  const confetti = useRef<ConfettiRef>(null);
  useCheer(offers, confetti);
  if (!offers.length) return null;
  const sorted = [...offers].sort((a, b) => Number(b.can_redeem) - Number(a.can_redeem));
  return (
    <Section title="Rewards">
      <Confetti ref={confetti} manualstart aria-hidden className="pointer-events-none fixed inset-0 z-50 size-full" />
      <div className="grid gap-2.5">
        {sorted.map((o, i) => (
          <motion.div key={o.name} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * .05 }}>
            <Card className={cn("relative flex items-center gap-3.5", o.can_redeem && "bg-gradient-to-br from-card from-55% to-sun-wash ring-1 ring-sun/60")}>
              {o.can_redeem && <ShineBorder borderWidth={2} duration={8} shineColor={["#FFC23D", "#FE0001", "#FFC23D"]} />}
              <div aria-hidden className={cn("grid size-11 flex-none place-items-center rounded-full", o.can_redeem ? "bg-sun text-[#3D2800]" : "bg-sun-wash text-[#8A6200] dark:text-sun")}>
                {o.can_redeem ? <PartyPopper className="size-[22px]" strokeWidth={2.2} /> : <CupSoda className="size-[22px]" strokeWidth={2.2} />}</div>
              <div className="min-w-0 flex-1">
                <b className="font-display">{o.name}</b><span className="ml-2 text-[13px] text-muted">{num(o.points_cost)} pts</span>
                {o.can_redeem && <span className="ml-2 rounded-full bg-sun px-2 py-0.5 text-[11.5px] font-bold text-[#3D2800]">Ready</span>}
                <div className="text-[13.5px] text-muted">{o.can_redeem ? "It's yours - just ask at the stall."
                  : o.limit_hit ? `You've used this one${(o.per_customer_limit ?? 0) > 1 ? ` ${o.used} times` : ""} - that's the limit.`
                  : `${num(o.short)} more points to go.`}</div>
                {!o.can_redeem && !o.limit_hit && <Bar pct={(100 * (o.points_cost - o.short)) / o.points_cost} />}
              </div>
            </Card>
          </motion.div>
        ))}
      </div>
    </Section>
  );
}

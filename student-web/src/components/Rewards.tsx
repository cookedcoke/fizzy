import { motion } from "motion/react";
import type { Offer } from "@/lib/api";
import { num } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Bar, Card, Section } from "@/components/ui/card";

export function Rewards({ offers }: { offers: Offer[] }) {
  if (!offers.length) return null;
  const sorted = [...offers].sort((a, b) => Number(b.can_redeem) - Number(a.can_redeem));
  return (
    <Section title="Rewards">
      <div className="grid gap-2.5">
        {sorted.map((o, i) => (
          <motion.div key={o.name} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * .05 }}>
            <Card className={cn("flex items-center gap-3.5", o.can_redeem && "bg-gradient-to-br from-card from-55% to-sun-wash ring-2 ring-sun")}>
              <div aria-hidden className="grid size-11 flex-none place-items-center rounded-full bg-sun-wash text-[22px]">{o.can_redeem ? "🎉" : "🥤"}</div>
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

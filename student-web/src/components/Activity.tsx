import { useState } from "react";
import type { Activity as A } from "@/lib/api";
import { money, num, when } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Card, Section } from "@/components/ui/card";
import { AnimatedList } from "@/components/ui/animated-list";
import { Gift, Medal, Receipt, CupSoda, type LucideIcon } from "lucide-react";

function Row({ a }: { a: A }) {
  const up = a.points != null && a.points > 0;
  const down = a.points != null && a.points < 0;
  const [Icon, tone]: [LucideIcon, string] = /^badge/i.test(a.what) ? [Medal, "sun"] : /reward|redeem/i.test(a.what) ? [Gift, "sun"]
    : /tab|repay|paid/i.test(a.what) ? [Receipt, "red"] : up ? [CupSoda, "ok"] : down ? [Gift, "sun"] : [Receipt, "red"];
  return (
    <div className="flex w-full items-center gap-3 rounded-[14px] bg-card-2/70 px-3 py-2.5">
      <span aria-hidden className={cn("grid size-9 flex-none place-items-center rounded-full",
        tone === "sun" ? "bg-sun-wash text-[#8A6200] dark:text-sun" : tone === "ok" ? "bg-ok-wash text-ok" : "bg-red-wash text-red-deep")}>
        <Icon className="size-[18px]" strokeWidth={2.2} /></span>
      <span className="min-w-0 flex-1 leading-tight">
        <span className="block truncate text-[14px] font-medium">{a.what}</span>
        <span className="text-[12.5px] text-muted">{when(a.at)}</span></span>
      <span className={cn("font-display font-bold tabular-nums", up && "text-ok")}>
        {a.points != null ? `${a.points > 0 ? "+" : ""}${num(a.points)}` : money(a.money ?? 0)}</span>
    </div>
  );
}

/** What happened lately, dropping in one after another (newest on top). */
export function ActivityFeed({ items }: { items: A[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, 5);
  return (
    <Section title="Recent activity">
      <Card className="px-3 py-3">
        {items.length ? (<>
          {/* AnimatedList shows its children last-first, so they go in oldest first. */}
          <AnimatedList key={all ? "all" : "some"} delay={all ? 40 : 140} className="gap-2">
            {[...shown].reverse().map((a, i) => <Row key={`${a.at}-${shown.length - 1 - i}`} a={a} />)}
          </AnimatedList>
          {items.length > 5 && <button onClick={() => setAll(!all)} className="mt-2.5 ml-1 text-[13.5px] font-semibold text-red-deep">{all ? "Show less" : "Show more"}</button>}
        </>) : <p className="px-1 text-[14px] text-muted">Nothing yet - grab something at the stall to start earning points.</p>}
      </Card>
    </Section>
  );
}

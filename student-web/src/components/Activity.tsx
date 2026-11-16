import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import type { Activity as A } from "@/lib/api";
import { money, num, when } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Card, Section } from "@/components/ui/card";

function Row({ a }: { a: A }) {
  const up = a.points != null && a.points > 0;
  return (
    <li className="flex items-baseline gap-3 border-b border-line py-2 last:border-0">
      <span className="w-12 flex-none text-[12.5px] text-muted">{when(a.at)}</span>
      <span className="min-w-0 flex-1 text-[14px]">{a.what}</span>
      <span className={cn("font-semibold tabular-nums", up && "text-ok")}>
        {a.points != null ? `${a.points > 0 ? "+" : ""}${num(a.points)} pts` : money(a.money ?? 0)}</span>
    </li>
  );
}

export function ActivityFeed({ items }: { items: A[] }) {
  const [all, setAll] = useState(false);
  return (
    <Section title="Recent activity">
      <Card>
        {items.length ? (<>
          <ul>{items.slice(0, 5).map((a, i) => <Row key={i} a={a} />)}</ul>
          <AnimatePresence initial={false}>
            {all && (
              <motion.ul initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                {items.slice(5).map((a, i) => <Row key={i} a={a} />)}
              </motion.ul>)}
          </AnimatePresence>
          {items.length > 5 && <button onClick={() => setAll(!all)} className="mt-2 text-[13.5px] font-semibold text-red-deep">{all ? "Show less" : "Show more"}</button>}
        </>) : <p className="text-[14px] text-muted">Nothing yet - grab something at the stall to start earning points.</p>}
      </Card>
    </Section>
  );
}

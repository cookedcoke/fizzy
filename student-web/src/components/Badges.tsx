import { useState } from "react";
import { motion } from "motion/react";
import type { Badge } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Bar, Section } from "@/components/ui/card";

function One({ b, i }: { b: Badge; i: number }) {
  return (
    <motion.div title={b.how} initial={{ opacity: 0, scale: .94 }} animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: (i % 6) * .04 }} whileHover={{ y: -2 }}
      className={cn("flex flex-col rounded-[16px] bg-card px-3 py-3 shadow-card", b.earned ? "bg-gradient-to-br from-card from-55% to-sun-wash" : "opacity-80")}>
      <div aria-hidden className={cn("mb-1 text-[28px]", !b.earned && "grayscale")}>{b.icon}</div>
      <b className="font-display text-[14.5px] leading-tight">{b.name}</b>
      <span className="text-[12.5px] leading-snug text-muted">{b.how}</span>
      {b.points > 0 && <em className="mt-1 text-[12px] font-semibold not-italic text-[#8A6200] dark:text-sun">+{b.points} points</em>}
      {!b.earned && b.count && (<><Bar pct={b.progress} /><small className="mt-1 text-[11.5px] text-muted">{b.count}</small></>)}
    </motion.div>
  );
}

export function Badges({ badges, earned }: { badges: Badge[]; earned: number }) {
  const [all, setAll] = useState(false);
  const got = badges.filter(b => b.earned), todo = badges.filter(b => !b.earned);
  const shown = all ? [...got, ...todo] : [...got, ...todo.slice(0, 2)];
  return (
    <Section title="Badges" small={`${earned} of ${badges.length}`}>
      <div className="grid grid-cols-2 gap-2.5">{shown.map((b, i) => <One key={b.name} b={b} i={i} />)}</div>
      {todo.length > 2 && <button onClick={() => setAll(!all)} className="mt-2.5 text-[13.5px] font-semibold text-red-deep">{all ? "Show fewer" : `See all ${badges.length} badges`}</button>}
    </Section>
  );
}

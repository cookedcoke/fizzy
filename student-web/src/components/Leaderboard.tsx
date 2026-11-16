import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import type { Board } from "@/lib/api";
import { num } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Card, Section } from "@/components/ui/card";

const medal = (r: number) => r === 1 ? "bg-sun text-[#3D2800]" : r === 2 ? "bg-[#D9DCE1] text-ink dark:bg-[#4a4f57] dark:text-[#f7efe6]"
  : r === 3 ? "bg-[#F2C29B] text-[#5A2E0B] dark:bg-[#7a4a26] dark:text-[#fff3e6]" : "bg-card-2 text-muted";

export function Leaderboard({ boards }: { boards: Board[] }) {
  const [i, setI] = useState(0);
  const b = boards[i];
  if (!b) return null;
  return (
    <Section title="Leaderboard" small="points earned">
      <div role="tablist" className="mb-2.5 inline-flex rounded-full bg-card p-1 shadow-card">
        {boards.map((x, k) => (
          <button key={x.title} role="tab" aria-selected={k === i} onClick={() => setI(k)}
            className={cn("relative rounded-full px-3.5 py-1 text-[13.5px] font-semibold", k === i ? "text-cream" : "text-muted")}>
            {k === i && <motion.span layoutId="board-pick" className="absolute inset-0 rounded-full bg-ink" transition={{ type: "spring", stiffness: 500, damping: 36 }} />}
            <span className="relative">{x.title}</span>
          </button>))}
      </div>
      <Card>
        <AnimatePresence mode="wait">
          <motion.div key={b.title} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }} transition={{ duration: .18 }}>
            {b.rows.length ? (<>
              <ol className="grid gap-1">
                {b.rows.map((r, k) => (
                  <li key={k} className={cn("flex items-center gap-3 rounded-xl px-1.5 py-1.5", r.me && "bg-sun-wash")}>
                    <span className={cn("grid size-7 flex-none place-items-center rounded-full font-display text-[13px] font-extrabold", medal(r.rank))}>{r.rank}</span>
                    <span className="min-w-0 flex-1 truncate">{r.name}{r.me && <span className="text-muted"> (you)</span>}</span>
                    <span className="font-display font-bold tabular-nums">{num(r.points)}</span>
                  </li>))}
              </ol>
              {b.mine && b.mine.rank > b.rows.length && <p className="mt-2 text-[13.5px] text-muted">You're #{b.mine.rank} of {b.mine.of} with {num(b.mine.points)}.</p>}
            </>) : <p className="text-[14px] text-muted">No points yet{b.title === "This month" ? " this month" : ""}.</p>}
          </motion.div>
        </AnimatePresence>
      </Card>
    </Section>
  );
}

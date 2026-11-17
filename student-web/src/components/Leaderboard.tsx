import { Crown } from "lucide-react";
import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import type { Board, BoardRow } from "@/lib/api";
import { num } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Card, Section } from "@/components/ui/card";
import { NumberTicker } from "@/components/ui/number-ticker";

const medal = (r: number) => r === 1 ? "bg-sun text-[#3D2800]" : r === 2 ? "bg-[#D9DCE1] text-ink dark:bg-[#4a4f57] dark:text-[#f7efe6]"
  : r === 3 ? "bg-[#F2C29B] text-[#5A2E0B] dark:bg-[#7a4a26] dark:text-[#fff3e6]" : "bg-card-2 text-muted";
const initials = (n: string) => n.split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase();

/** The top three on steps: first in the middle and tallest. */
function Podium({ rows }: { rows: BoardRow[] }) {
  const order = [rows[1], rows[0], rows[2]].filter(Boolean);
  const height = (r: number) => r === 1 ? "h-[74px]" : r === 2 ? "h-[54px]" : "h-[40px]";
  return (
    <div className="mb-3 grid grid-cols-3 items-end gap-2">
      {order.map(r => (
        <motion.div key={r.rank} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 260, damping: 22, delay: r.rank === 1 ? .15 : .05 }}
          className="flex min-w-0 flex-col items-center text-center">
          <span className={cn("relative grid size-11 place-items-center rounded-full font-display text-[14px] font-extrabold ring-2 ring-card",
            medal(r.rank), r.me && "ring-red")}>
            {r.rank === 1 && <Crown aria-hidden className="absolute -top-4 size-[18px] fill-sun text-[#B37A00]" strokeWidth={2} />}
            {initials(r.name)}</span>
          <span className="mt-1 w-full truncate text-[13px] font-semibold">{r.me ? "You" : r.name.split(" ")[0]}</span>
          <span className="font-display text-[13px] font-bold text-muted tabular-nums">{num(r.points)}</span>
          <div className={cn("mt-1.5 grid w-full place-items-center rounded-t-[12px] font-display text-[18px] font-extrabold", height(r.rank),
            r.rank === 1 ? "bg-gradient-to-b from-sun to-sun/60 text-[#3D2800]" : "bg-card-2 text-muted")}>{r.rank}</div>
        </motion.div>))}
    </div>
  );
}

export function Leaderboard({ boards }: { boards: Board[] }) {
  const [i, setI] = useState(0);
  const b = boards[i];
  if (!b) return null;
  const rest = b.rows.length >= 3 ? b.rows.slice(3) : b.rows;
  return (
    <Section title="Leaderboard" small="points earned">
      <div role="tablist" className="mb-3 inline-flex rounded-full border border-line/70 bg-card p-1 shadow-card">
        {boards.map((x, k) => (
          <button key={x.title} role="tab" aria-selected={k === i} onClick={() => setI(k)}
            className={cn("relative rounded-full px-3.5 py-1 text-[13.5px] font-semibold focus-visible:outline-2 focus-visible:outline-red", k === i ? "text-cream" : "text-muted")}>
            {k === i && <motion.span layoutId="board-pick" className="absolute inset-0 rounded-full bg-ink" transition={{ type: "spring", stiffness: 500, damping: 36 }} />}
            <span className="relative">{x.title}</span>
          </button>))}
      </div>
      <Card>
        <AnimatePresence mode="wait">
          <motion.div key={b.title} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }} transition={{ duration: .18 }}>
            {b.rows.length ? (<>
              {b.rows.length >= 3 && <Podium rows={b.rows} />}
              <ol className="grid gap-1">
                {rest.map(r => (
                  <li key={r.rank + r.name} className={cn("flex items-center gap-3 rounded-xl px-1.5 py-1.5", r.me && "bg-sun-wash")}>
                    <span className={cn("grid size-7 flex-none place-items-center rounded-full font-display text-[13px] font-extrabold", medal(r.rank))}>{r.rank}</span>
                    <span className="min-w-0 flex-1 truncate">{r.name}{r.me && <span className="text-muted"> (you)</span>}</span>
                    <NumberTicker value={r.points} className="font-display font-bold tracking-normal text-ink dark:text-ink" />
                  </li>))}
              </ol>
              {b.mine && b.mine.rank > b.rows.length && <p className="mt-2 rounded-xl bg-sun-wash px-3 py-2 text-[13.5px]">You're <b>#{b.mine.rank}</b> of {b.mine.of} with {num(b.mine.points)}.</p>}
            </>) : <p className="text-[14px] text-muted">No points yet{b.title === "This month" ? " this month" : ""}.</p>}
          </motion.div>
        </AnimatePresence>
      </Card>
    </Section>
  );
}

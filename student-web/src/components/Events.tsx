import type { CustomEvent, WhatsOn } from "@/lib/api";
import { day, times } from "@/lib/format";
import { cn } from "@/lib/utils";

const box = "mb-3 rounded-[14px] px-3.5 py-2.5 text-[14px] [&_b]:font-display [&_b]:font-[750]";
const on = "bg-red-solid text-white font-medium";
const soon = "bg-sun-wash text-sun-ink";

export function Events({ w, custom = [] }: { w: WhatsOn; custom?: CustomEvent[] }) {
  return (
    <>
      {w.events_on.length > 0
        ? w.events_on.map((e, i) => (
          <div key={i} className={cn(box, on)}>⚡ <b>{e.name}</b> is on{e.start_date === e.end_date ? " today" : " now"} - {times(e.multiplier)} points on everything
            {e.end_date !== e.start_date && <> until {day(e.end_date)}</>}.</div>))
        : w.events_soon[0] && (() => { const e = w.events_soon[0]; return (
          <div className={cn(box, soon)}>📅 Coming up: <b>{e.name}</b> on {day(e.start_date, { weekday: "long", day: "numeric", month: "short" })}
            {e.end_date !== e.start_date && <> to {day(e.end_date)}</>} - {times(e.multiplier)} points.</div>); })()}
      {custom.map((e, i) => (
        <div key={"c" + i} className={cn(box, e.soon ? soon : on)}>
          {e.soon && e.start ? `📅 From ${day(e.start)}: ` : "⚡ "}<b>{e.name}</b> - {e.summary}</div>
      ))}
    </>
  );
}

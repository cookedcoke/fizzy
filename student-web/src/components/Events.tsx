import type { CustomEvent, WhatsOn } from "@/lib/api";
import { day, times } from "@/lib/format";
import { cn } from "@/lib/utils";
import { BorderBeam } from "@/components/ui/border-beam";
import { CalendarDays, Zap } from "lucide-react";

const ic = "mr-1.5 inline size-[15px] -translate-y-px align-middle";

const box = "relative mb-3 overflow-hidden rounded-[16px] px-3.5 py-2.5 text-[14px] [&_b]:font-display [&_b]:font-[750]";
const on = "bg-gradient-to-r from-red-solid to-red text-white font-medium shadow-[0_10px_22px_-14px_rgba(179,0,0,.8)]";
const soon = "border border-sun/40 bg-sun-wash text-sun-ink";

export function Events({ w, custom = [] }: { w: WhatsOn; custom?: CustomEvent[] }) {
  return (
    <>
      {w.events_on.length > 0
        ? w.events_on.map((e, i) => (
          <div key={i} className={cn(box, on)}><BorderBeam size={70} duration={6} colorFrom="#FFE7A3" colorTo="#FFC23D" /><Zap aria-hidden className={cn(ic, "fill-current")} strokeWidth={2.2} /><b>{e.name}</b> is on{e.start_date === e.end_date ? " today" : " now"} - {times(e.multiplier)} points on everything
            {e.end_date !== e.start_date && <> until {day(e.end_date)}</>}.</div>))
        : w.events_soon[0] && (() => { const e = w.events_soon[0]; return (
          <div className={cn(box, soon)}><CalendarDays aria-hidden className={ic} strokeWidth={2.2} />Coming up: <b>{e.name}</b> on {day(e.start_date, { weekday: "long", day: "numeric", month: "short" })}
            {e.end_date !== e.start_date && <> to {day(e.end_date)}</>} - {times(e.multiplier)} points.</div>); })()}
      {custom.map((e, i) => (
        <div key={"c" + i} className={cn(box, e.soon ? soon : on)}>
          {e.soon && e.start ? <><CalendarDays aria-hidden className={ic} strokeWidth={2.2} />From {day(e.start)}: </>
            : <Zap aria-hidden className={cn(ic, "fill-current")} strokeWidth={2.2} />}<b>{e.name}</b> - {e.summary}</div>
      ))}
    </>
  );
}

import { useState } from "react";
import type { Card as C, ScanResult } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Card, Section } from "@/components/ui/card";
import { NextPoster, Progress, RunPanel, Stops } from "@/components/Hunt";

/** A challenge (scavenger hunt) on the signed-in home page. */
export function ChallengeCard({ k, name, sid, scanned, onChange }: { k: C; name: string; sid: string; scanned: ScanResult | null; onChange: () => void }) {
  const soon = k.when.startsWith("Starts");
  const waiting = k.done && k.status !== "Prize collected.";
  const [showStops, setShowStops] = useState(false);
  return (
    <Section title={k.kicker} small={k.when || undefined}>
      <Card className={cn(k.done && "ring-2 ring-sun", soon && "opacity-90")}>
        <div className="flex items-start gap-3">
          <div aria-hidden className="grid size-11 flex-none place-items-center rounded-full bg-sun-wash text-[22px]">{k.icon}</div>
          <div className="min-w-0">
            <b className="font-display text-[17px]">{k.title}</b><span className="ml-2 text-[13px] text-muted">{k.prize}</span>
            {!k.done && <div className="text-[14px] text-muted">{k.text}</div>}
          </div>
        </div>
        {waiting ? (<>
          {scanned?.kind !== "done" && <p className="mt-3 font-display text-[22px] font-extrabold text-ok">Finished!</p>}
          <div className="mt-3 rounded-[16px] border-2 border-dashed border-sun bg-sun-wash px-4 py-3.5 text-center">
            <div className="text-[13px] font-semibold text-sun-ink">{k.status.replace("Finished! ", "")}</div>
            <div className="font-display text-[22px] font-extrabold">{name}</div>
            {sid && <div className="font-mono text-[13px] text-muted">{sid}</div>}
          </div>
        </>) : k.done ? <p className="mt-3 font-display text-[17px] font-bold text-ok">{k.status || "Done!"}</p>
          : soon ? <p className="mt-3 text-[14px] text-muted">{k.when}. The stops are below - come back then to scan them.</p>
          : k.progress.total > 1 ? <Progress done={k.progress.done} total={k.progress.total} /> : null}
        <RunPanel run={k.run} onSaved={onChange} />
        {k.done ? (<>
          <button onClick={() => setShowStops(!showStops)} className="mt-3 text-[13.5px] font-semibold text-red-deep">
            {showStops ? "Hide the stops" : `See all ${k.steps.length} stops`}</button>
          {showStops && <Stops steps={k.steps} />}
        </>) : (<>
          <Stops steps={k.steps} just={scanned?.label} />
          {k.actions.length > 0 && <NextPoster />}
        </>)}
      </Card>
    </Section>
  );
}

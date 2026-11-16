import { useState } from "react";
import { href, type GuestCard, type ScanResult } from "@/lib/api";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import { Card, Section } from "@/components/ui/card";
import { TopBar } from "@/components/TopBar";
import { KindIcon } from "@/components/Icon";
import { NextPoster, Progress, RunPanel, ScanResultCard, Stops } from "@/components/Hunt";

/** A hunt played without signing in: what this phone has found, and the claim code once finished. */
export function GuestHunt({ cards, result, reload }: { cards: GuestCard[]; result: ScanResult | null; reload: () => void }) {
  const home = href("/");
  return (
    <>
      <TopBar right={<a className={buttonVariants({ variant: "pill" })} href={home}>Sign in</a>} />
      <main className="mx-auto max-w-[560px] px-[18px] pb-12">
        {result && <ScanResultCard r={result} />}
        {cards.length ? cards.map((k, i) => <GuestCardView key={i} k={k} just={result?.label} home={home} reload={reload} />) : (
          <Card className="mt-6 flex items-center gap-3.5">
            <span className="grid size-11 flex-none place-items-center rounded-full bg-sun text-[#3D2800]"><KindIcon kind="camera" className="size-5" /></span>
            <p className="text-[14px]"><b>Find a hunt poster to start.</b> Open your phone's camera and point it at the code on a hunt poster.</p>
          </Card>)}
        <p className="mx-auto mt-7 max-w-[380px] text-center text-[13px] text-muted">This is kept in this browser, so scan every code with the same phone.{" "}
          <a href={home} className="font-semibold text-red-deep">Sign in here</a> and what you've found moves to your account.</p>
      </main>
    </>
  );
}

function GuestCardView({ k, just, home, reload }: { k: GuestCard; just?: string | null; home: string; reload: () => void }) {
  const [showStops, setShowStops] = useState(false);
  return (
    <Section title={k.title} small={`Prize: ${k.prize}`}>
      <Card className={cn(k.claim && "ring-2 ring-sun")}>
        {k.claimed ? (<>
          <p className="font-display text-[17px] font-bold text-ok">Prize claimed. Nice one!</p>
          <p className="mt-1 text-[13.5px] text-muted">You found all {k.progress.total} and the stall has given you {k.prize.toLowerCase()}.</p>
        </>) : k.claim ? (<>
          <p className="font-display text-[22px] font-extrabold text-ok">Finished! Show this at the stall:</p>
          <div className="mt-3 rounded-[16px] border-2 border-dashed border-sun bg-sun-wash px-4 py-4 text-center">
            <div className="text-[13px] font-semibold text-sun-ink">Your claim code</div>
            <div aria-label={k.claim.split("").join(" ")} className="font-display text-[44px] font-extrabold tracking-[.18em]">{k.claim}</div>
            <div className="text-[13px] text-muted">Staff type it in to give you {k.prize.toLowerCase()}.</div>
          </div>
          <a className={buttonVariants() + " mt-4"} href={home}>Sign in to put it on your account</a>
          <p className="mt-2 text-center text-[13.5px] text-muted">No passcode yet? Just show the code - it works without one.</p>
        </>) : (<>
          <p className="text-[14px] text-muted">{k.text}</p>
          <Progress done={k.progress.done} total={k.progress.total} />
        </>)}
        <RunPanel run={k.run} onSaved={reload} />
        {k.claim ? (<>
          <button onClick={() => setShowStops(!showStops)} className="mt-3 text-[13.5px] font-semibold text-red-deep">{showStops ? "Hide the stops" : `See all ${k.steps.length} stops`}</button>
          {showStops && <Stops steps={k.steps} />}
        </>) : (<><Stops steps={k.steps} just={just} /><NextPoster /></>)}
      </Card>
    </Section>
  );
}

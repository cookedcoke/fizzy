import type { Tab } from "@/lib/api";
import { appPayUrl } from "@/lib/api";
import { money, when } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Card, Section } from "@/components/ui/card";
import { AnimatedCircularProgressBar } from "@/components/ui/animated-circular-progress-bar";
import { BorderBeam } from "@/components/ui/border-beam";
import { NumberTicker } from "@/components/ui/number-ticker";

/** Dollars that count up into place. */
function Dollars({ cents, className }: { cents: number; className?: string }) {
  return <span className={className}>{cents < 0 && "-"}$<NumberTicker value={Math.abs(cents) / 100} decimalPlaces={2} className="tracking-normal text-inherit dark:text-inherit" /></span>;
}

const msg = "font-display text-[17px] font-bold leading-snug";
const sub = "mt-1 text-[13.5px] text-muted";

export function TabCard({ t }: { t: Tab }) {
  const sc = t.score;
  return (
    <Section title={<>Your tab{t.has_overdue && <small className="font-sans text-[13px] font-semibold text-red-deep">needs paying</small>}</>}>
      <Card className={cn("relative", t.has_overdue && "border-red/40")}>
        {t.has_overdue && <BorderBeam size={90} duration={5} borderWidth={2} colorFrom="#FE0001" colorTo="#FFC23D" />}
        {t.has_overdue && t.status !== "blocked_overdue" ? (<><p className={cn(msg, "text-red-deep")}>{money(t.overdue_cents)} is overdue.</p><p className={sub}>Pay it at the stall.</p></>)
          : t.status === "approved" ? (t.can_charge ? <p className={cn(msg, "text-ok")}>You can put up to {money(t.available_cents)} on your tab.</p>
            : <><p className={msg}>Your tab is full.</p><p className={sub}>Pay some off to use it again.</p></>)
          : t.status === "blocked_overdue" ? (<><p className={cn(msg, "text-red-deep")}>Your tab is on hold - {money(t.overdue_cents)} is overdue.</p><p className={sub}>Pay it at the stall and it unlocks straight away.</p></>)
          : t.status === "paused" ? <p className={msg}>Tabs are paused for everyone right now.</p>
          : t.status === "low_score" ? (<><p className={msg}>Get your tab score to {t.min_score} to unlock a tab.</p><p className={sub}>You're on {sc.score}. See below for how it works.</p></>)
          : (<><p className={msg}>Tabs aren't available on your account.</p><p className={sub}>Ask at the stall if you have questions.</p></>)}

        {(t.owing_cents > 0 || t.has_charges) && (<>
          <div className="mt-3.5 grid grid-cols-2 gap-2.5">
            <div className={cn("rounded-[14px] bg-card-2 px-3.5 py-3", t.has_overdue && "bg-red-wash")}>
              <div className="text-[12.5px] font-semibold text-muted">You owe</div>
              <div className={cn("font-display text-[26px] font-extrabold leading-tight", t.has_overdue && "text-red-deep")}><Dollars cents={t.owing_cents} /></div>
              {t.next_due && <div className="text-[12.5px] text-muted">{t.has_overdue ? "Was due" : "Pay by"} {when(t.next_due, { weekday: "short", day: "numeric", month: "short" })}</div>}
            </div>
            {t.status !== "blocked_overdue" && (
              <div className="rounded-[14px] bg-card-2 px-3.5 py-3">
                <div className="text-[12.5px] font-semibold text-muted">Tab limit</div>
                <div className="font-display text-[26px] font-extrabold leading-tight"><Dollars cents={t.limit_cents} /></div>
                <div className="text-[12.5px] text-muted">{t.due_days} days to pay</div>
              </div>)}
          </div>
          {t.balance_cents < 0 && <p className={sub}>You have {money(-t.balance_cents)} in credit - it's used next time you put something on your tab.</p>}
          {t.late_count > 0 && <p className={sub}>{t.late_count} late payment{t.late_count === 1 ? "" : "s"} on record.</p>}
        </>)}
        {appPayUrl && t.owing_cents > 0 && <p className={sub}>Pay it off with Apple Pay in the <a className="font-semibold text-red-deep underline" href={appPayUrl}>My CCC app</a>.</p>}

        {t.status !== "rejected" && t.status !== "revoked" && (
          <details className="group mt-3.5 border-t border-line pt-3" open={t.status === "low_score"}>
            <summary className="flex cursor-pointer rounded-xl focus-visible:outline-2 focus-visible:outline-red list-none items-center gap-3 [&::-webkit-details-marker]:hidden">
              <AnimatedCircularProgressBar value={sc.score} gaugePrimaryColor={sc.score >= 80 ? "var(--ok)" : sc.score >= t.min_score ? "var(--sun)" : "var(--red)"}
                gaugeSecondaryColor="var(--line)" className={cn("size-14 flex-none font-display text-[17px] font-extrabold",
                  sc.score >= 80 ? "text-ok" : sc.score >= t.min_score ? "text-[#8A6200] dark:text-sun" : "text-red-deep")} />
              <span><b>Your tab score</b><br /><span className="text-[13.5px] text-muted">
                {t.status === "approved" ? <>Gives you a {money(sc.limit_cents)} limit{sc.next && <> · {sc.next.score}+ gets {money(sc.next.limit_cents)}</>}</> : "Out of 100 · tap to see why"}</span></span>
            </summary>
            <ul className="mt-3 grid gap-1.5">
              {sc.factors.map((f, i) => (
                <li key={i} className="flex justify-between text-[14px]"><span>{f.label}</span>
                  <b className={cn(f.points < 0 && "text-red-deep")}>{i === 0 ? f.points : (f.points >= 0 ? "+" : "") + f.points}</b></li>))}
            </ul>
            {sc.tips.length > 0 && <p className={sub}>To raise it: {sc.tips.join(" ")}</p>}
          </details>
        )}
      </Card>
    </Section>
  );
}

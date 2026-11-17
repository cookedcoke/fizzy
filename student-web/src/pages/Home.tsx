import { api, type Me } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { BlurFade } from "@/components/ui/blur-fade";
import { TopBar } from "@/components/TopBar";
import { Events } from "@/components/Events";
import { ScanResultCard } from "@/components/Hunt";
import { ChallengeCard } from "@/components/Challenge";
import { StatRing, useGain, useWide } from "@/components/StatRing";
import { MemberCard } from "@/components/MemberCard";
import { Rewards } from "@/components/Rewards";
import { TabCard } from "@/components/TabCard";
import { ActivityFeed } from "@/components/Activity";
import { Leaderboard } from "@/components/Leaderboard";
import { Badges } from "@/components/Badges";

const greeting = () => { const h = new Date().getHours(); return h < 12 ? "Morning" : h < 17 ? "Hey" : "Evening"; };

export function Home({ me, reload, onOut }: { me: Me; reload: () => void; onOut: () => void }) {
  const t = me.tab;
  const urgent = t.has_overdue || (t.status === "approved" && !t.can_charge);
  const r = me.note?.result ?? null;
  const scanned = r && r.kind !== "info" ? r : null;
  const showTab = me.tab.has_charges || (t.status !== "rejected" && t.status !== "revoked");
  const cards = me.cards.map(k => <ChallengeCard key={k.id} k={k} name={me.name} sid={me.sid} scanned={scanned} onChange={reload} />);
  const wide = useWide();
  const gain = useGain(me.points, me.sid || me.name);
  const card = <MemberCard member={me.member} holder={me.name} points={me.points} gain={gain} />;
  const out = async () => { await api.logout(); onOut(); };
  return (
    <>
      <TopBar right={<Button variant="pill" onClick={out}>Sign out</Button>} />
      <main className="mx-auto max-w-[960px] px-[18px] pb-14">
        <BlurFade direction="up" offset={10}>
          <h1 className="mt-2 mb-4 text-[32px] leading-tight font-extrabold">{greeting()} <span className="text-red">{me.first}</span></h1>
        </BlurFade>
        {/* what needs them now comes first: a poster just scanned, a tab to pay, a message, what's on */}
        <div className="md:max-w-[calc(100%-388px)]">
          {scanned && <><ScanResultCard r={scanned} />{cards}</>}
          {urgent && <TabCard t={t} />}
          {me.note && !scanned && <div role="status" className="mb-3 rounded-[14px] bg-red-solid px-3.5 py-2.5 text-[14px] font-medium text-white">{me.note.message}</div>}
          <Events w={me} custom={me.custom_events} />
        </div>
        {!wide && <BlurFade delay={.05} direction="up" offset={10}>{card}<StatRing me={me} wide={false} /></BlurFade>}
        <div className="md:grid md:grid-cols-[minmax(0,1fr)_minmax(0,360px)] md:gap-7">
          <div>
            {wide && <BlurFade delay={.05} direction="up" offset={10}>{card}<StatRing me={me} wide /></BlurFade>}
            {!scanned && cards}
            <Rewards offers={me.offers} />
            {!urgent && showTab && <TabCard t={t} />}
            <ActivityFeed items={me.activity} />
          </div>
          <div>
            <Leaderboard boards={me.boards} />
            <Badges badges={me.badges} earned={me.badges_earned} />
          </div>
        </div>
        <p className="mx-auto mt-9 max-w-[380px] text-center text-[13px] text-muted">Something look off? Ask us at the stall.</p>
      </main>
    </>
  );
}

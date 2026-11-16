import { useEffect, useState } from "react";
import type { Me } from "@/lib/api";
import { num } from "@/lib/format";
import { WarpCarousel, type WarpCard } from "@/components/ui/warp-carousel";

type RGB = [number, number, number];
const hex = (h: string): RGB => { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
const toHex = (c: RGB) => "#" + c.map(v => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("");
const mix = (a: RGB, b: RGB, t: number): RGB => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

/** A card's mesh gradient in our colours: the 6 by 7 grid the carousel wants, blended between four corners,
 *  with a hot spot of light (where the card catches the glow) and a darker pool at the bottom for the footer. */
function mesh(tl: string, tr: string, bl: string, br: string, hot: string, hx = 0.75, hy = 0.6, size = 0.42): string[] {
  const [a, b, c, d, h] = [tl, tr, bl, br, hot].map(hex);
  const out: string[] = [];
  for (let j = 0; j < 7; j++) for (let i = 0; i < 6; i++) {
    const x = i / 5, y = j / 6;
    let col = mix(mix(a, b, x), mix(c, d, x), y);
    const r = Math.hypot((x - hx) / size, (y - hy) / (size * 1.1));
    col = mix(col, h, Math.max(0, 1 - r) ** 1.6);
    out.push(toHex(col));
  }
  return out;
}

const ordinal = (n: number) => { const s = n % 100; return s >= 11 && s <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] ?? "th"; };

/** The page's own background, so the stage the ring turns on blends into it in light and dark. */
function usePageColour() {
  const read = () => getComputedStyle(document.documentElement).getPropertyValue("--cream").trim() || "#FFF7EE";
  const [c, setC] = useState(read);
  useEffect(() => {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const on = () => setC(read());
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return c;
}

/** "+120 since last time": what this phone saw on its last visit, remembered per student. */
function useGain(points: number, who: string) {
  const [gain, setGain] = useState(0);
  useEffect(() => {
    const key = "cc-pts-" + who;
    try {
      const v = localStorage.getItem(key);
      if (v !== null && points > Number(v)) setGain(points - Number(v));
      localStorage.setItem(key, String(points));
    } catch { /* private mode */ }
  }, [points, who]);
  return gain;
}

const FONT = '"Bricolage", ui-rounded, system-ui, -apple-system, sans-serif';

/** The top of the home page: points, streak, this month's place, badges and the next reward, on a ring of
 *  cards that turns in 3D through a wide lens (Warp Carousel, WebGL). */
export function StatRing({ me }: { me: Me }) {
  const page = usePageColour();
  const gain = useGain(me.points, me.sid || me.name);
  const s = me.streak;
  const month = me.boards[0];
  const mine = month?.rows.find(x => x.me);
  const rank = mine?.rank ?? month?.mine?.rank ?? null;
  const monthPts = mine?.points ?? month?.mine?.points ?? 0;
  const next = [...me.offers].filter(o => !o.limit_hit).sort((a, b) => Number(b.can_redeem) - Number(a.can_redeem) || a.short - b.short)[0];
  const todo = me.badges.find(b => !b.earned && b.count);

  const cards: WarpCard[] = [
    // no unit here: a five-digit balance plus "pts" is wider than the card
    // ("16,200" just fits; from 100,000 it's shortened to "102k")
    { title: "Your\npoints", value: me.points >= 100000 ? `${Math.floor(me.points / 1000)}k` : num(me.points), unit: me.points < 1000 ? "pts" : undefined, detail: "wave",
      footer: gain > 0 ? `+${num(gain)} since last time` : "1 point for every cent",
      numeral: ["#FFF7EE", "#FFD27A"], accent: "#FFC23D", glow: "#FE0001",
      mesh: mesh("#7A0804", "#E3110B", "#3A0503", "#B30000", "#FF6A3C", 0.78, 0.35) },
    { title: s.days ? "Day\nstreak" : "No streak\nyet", value: String(s.days), unit: s.days === 1 ? "day" : "days", detail: "ring",
      footer: s.today ? "Bought today, nice" : s.days ? "Buy today to keep it" : `Best so far: ${s.best}`,
      numeral: ["#FFFFFF", "#FFF1C4"], accent: "#FFF3D1", glow: "#FFC23D",
      mesh: mesh("#B35A00", "#FFB21A", "#6B2A00", "#E07A00", "#FFE07A", 0.5, 0.45, 0.5) },
    { title: `${month?.title ?? "This month"}\non the board`, value: rank ? String(rank) : "-", unit: rank ? ordinal(rank) : undefined, detail: "cross",
      labels: ["Leaderboard"], footer: rank ? `${num(monthPts)} points earned` : "Earn points to get on it",
      numeral: ["#FFC23D", "#FFF7EE"], accent: "#FFC23D", glow: "#B30000",
      mesh: mesh("#2A1712", "#4A1A12", "#120A08", "#3A0A06", "#A3150C", 0.8, 0.75, 0.5) },
    { title: "Badges\nearned", value: String(me.badges_earned), unit: `/${me.badges.length}`, detail: "none",
      footer: todo ? `Next: ${todo.name}` : "You've got them all",
      numeral: ["#FFF7EE", "#FFC23D"], accent: "#FFC23D", glow: "#FFB000",
      mesh: mesh("#5A3B00", "#8A6200", "#2A1A00", "#5A3600", "#FFC23D", 0.25, 0.3, 0.5) },
  ];
  if (next) {
    const pct = Math.round((100 * (next.points_cost - next.short)) / next.points_cost);
    cards.push({ title: `${next.name}\n${num(next.points_cost)} points`, value: String(next.can_redeem ? 100 : pct), unit: "%", detail: "slider",
      labels: ["0", num(next.points_cost)], footer: next.can_redeem ? "Ready, ask at the stall" : `${num(next.short)} more to go`,
      numeral: ["#FFFFFF", "#FFD27A"], accent: "#FFC23D", glow: next.can_redeem ? "#FFC23D" : "#E3110B",
      mesh: mesh("#D10000", "#7A0804", "#4A0604", "#2A1712", "#FF8A3C", 0.3, 0.3, 0.45) });
  }

  // The ring is drawn on a 720 by 440 unit stage, its cards sized by whichever side is tighter. It's laid out
  // between 460 and 760px wide and centred: wider and the lens flare just smears across a big screen; on a phone
  // narrower than 460 it runs a little past the screen so the cards stay readable and the flare starts at each edge.
  // Its left and right ends fade into the page, so it never looks boxed in.
  return (
    <div className="relative left-1/2 flex w-screen -translate-x-1/2 justify-center overflow-x-clip">
      <WarpCarousel cards={cards} background={page} font={FONT} autoplay interval={4.5} step={1} ariaLabel="Your stats"
        className="shrink-0 focus-visible:outline-2 focus-visible:outline-red"
        style={{ width: "clamp(460px, 100%, 760px)", height: "clamp(290px, 62vw, 342px)", minHeight: 0, aspectRatio: "auto",
          maskImage: "linear-gradient(90deg, transparent, #000 13%, #000 87%, transparent)",
          WebkitMaskImage: "linear-gradient(90deg, transparent, #000 13%, #000 87%, transparent)" }} />
    </div>
  );
}

import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { Member } from "@/lib/api";
import { num } from "@/lib/format";
import black from "@/assets/member/MemberBlack.jpg";
import blackGloss from "@/assets/member/MemberBlackGloss.jpg";
import red from "@/assets/member/MemberRed.jpg";
import redGloss from "@/assets/member/MemberRedGloss.jpg";
import white from "@/assets/member/MemberWhite.jpg";
import whiteGloss from "@/assets/member/MemberWhiteGloss.jpg";

// The student's card, as in My CCC: Black Coke or Red Coke for members, white for everyone else, with their
// points in the middle. The art is brand/member-cards; everything that moves is drawn here from where the
// light is (the mouse, or a finger dragging the card): it leans, a band of light shows the gloss pattern as it
// passes, a glare follows, Black Coke's silver type catches it and the edge shines on the lit side. Tap or
// click to turn it over. All of it stays still for anyone who asks for less motion.

const ART = { black: [black, blackGloss], red: [red, redGloss], white: [white, whiteGloss] } as const;
const INK = "#16130F";
// Sizes are those of the 1536-wide artwork: u(104) is 104 of its units, as a share of the card's width.
const u = (n: number) => `${(n / 1536) * 100}cqw`;
const SANS = '"Avenir Next", Avenir, "Segoe UI", system-ui, sans-serif';
const MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';

function useStill() {
  const q = "(prefers-reduced-motion: reduce)";
  const [still, set] = useState(() => matchMedia(q).matches);
  useEffect(() => {
    const mq = matchMedia(q), on = () => set(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return still;
}

/** Counts up to `to` from `from` once, easing out (straight to it for anyone who asks for less motion). */
function useCountUp(from: number, to: number, still: boolean) {
  const [n, set] = useState(still ? to : from);
  useEffect(() => {
    if (still || from >= to) { set(to); return; }
    let raf = 0;
    const t0 = performance.now() + 300, ms = 1200;
    const tick = (t: number) => {
      const p = Math.max(0, Math.min(1, (t - t0) / ms));
      set(Math.round(from + (to - from) * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [from, to, still]);
  return n;
}

/** Where the light is, moving towards `target` on a soft spring every frame: it follows the mouse with a little
 *  lag and glides back to the middle when the mouse leaves, instead of jumping. */
function useLight(target: { x: number; y: number }, still: boolean) {
  const [pos, set] = useState({ x: 0, y: 0 });
  const st = useRef({ x: 0, y: 0, vx: 0, vy: 0, raf: 0, last: 0 });
  const goal = useRef(target);
  goal.current = target;
  useEffect(() => {
    if (still) { set({ x: 0, y: 0 }); return; }
    const s = st.current;
    const tick = (t: number) => {
      const dt = Math.min(0.05, s.last ? (t - s.last) / 1000 : 1 / 60);
      s.last = t;
      // a gently damped spring: about 0.7 s to settle, no wobble to speak of
      const k = 38, c = 11;
      s.vx += (k * (goal.current.x - s.x) - c * s.vx) * dt;
      s.vy += (k * (goal.current.y - s.y) - c * s.vy) * dt;
      s.x += s.vx * dt; s.y += s.vy * dt;
      const settled = Math.abs(goal.current.x - s.x) + Math.abs(goal.current.y - s.y) + Math.abs(s.vx) + Math.abs(s.vy) < 0.002;
      if (settled) { s.x = goal.current.x; s.y = goal.current.y; s.vx = s.vy = 0; }
      set({ x: s.x, y: s.y });
      s.raf = settled ? 0 : requestAnimationFrame(tick);
    };
    // (re)started on every new target; the motion carries on from where it is, since it lives in the ref
    cancelAnimationFrame(s.raf);
    s.last = 0;
    s.raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(s.raf); s.raf = 0; };
  }, [target.x, target.y, still]);
  return pos;
}

export function MemberCard({ member, holder, points, gain }: { member: Member | null; holder: string; points: number; gain: number }) {
  const tier = member?.tier ?? "white";
  const isBlack = tier === "black", isWhite = tier === "white";
  const [base, gloss] = ART[tier];
  const still = useStill();
  const [flipped, setFlipped] = useState(false);
  const [aim, setAim] = useState({ x: 0, y: 0 });
  const shown = useCountUp(Math.max(0, points - gain), points, still);
  const ref = useRef<HTMLButtonElement>(null);

  const move = (e: React.PointerEvent) => {
    if (still || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const x = Math.max(-1, Math.min(1, ((e.clientX - r.left) / r.width) * 2 - 1));
    const y = Math.max(-1, Math.min(1, ((e.clientY - r.top) / r.height) * 2 - 1));
    setAim({ x, y });
  };
  const rest = () => setAim({ x: 0, y: 0 });
  const { x: lx, y: ly } = useLight(aim, still);

  const pct = member ? `${member.pct}%` : "";
  const until = member ? new Date(member.ends_on + "T00:00").toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric" }).toUpperCase() : "";
  const line = member ? `MEMBER  ·  ${pct} OFF EVERYTHING${member.term ? `  ·  ${member.term.toUpperCase()}` : ""}` : "";

  // Black Coke's type is polished silver whose bright streak follows the light; Red Coke's plain white; the white card's ink.
  const metal = (soft = false): CSSProperties => isBlack ? {
    backgroundImage: `linear-gradient(180deg, ${soft ? "#dcdcdc" : "#fafafa"} 0%, #949494 ${42 + lx * 25}%, #f7f7f7 ${55 + lx * 25}%, #7f7f7f 100%)`,
    WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent",
    filter: "drop-shadow(0 1.5px 0.5px rgba(0,0,0,.7))",
  } : isWhite ? { color: INK, textShadow: "0 1.5px 0 rgba(255,255,255,.8)" } : { color: "#fff", textShadow: "0 3px 6px rgba(0,0,0,.18)" };

  const face: CSSProperties = { position: "absolute", inset: 0, borderRadius: u(58), overflow: "hidden",
    backfaceVisibility: "hidden", WebkitBackfaceVisibility: "hidden", containerType: "inline-size" };
  const edgeAngle = Math.atan2(-0.4 - ly, -0.6 - lx) * 180 / Math.PI + 90;
  // a metal rim, as in My CCC: a band of chrome (polished red on Red Coke) whose bright side turns to the light,
  // a dark groove where it meets the face and a bright hairline outside
  const [hi, mid, lo] = tier === "red" ? ["#FFB8B8", "#B80F1A", "#520008"] : isWhite ? ["#FFFFFF", "#C7C7C7", "#8C8C8C"] : ["#F5F5F5", "#808080", "#292929"];
  const rim = `max(3px, ${(18 / 1536) * 100}cqw)`;
  const ring: CSSProperties = { WebkitMask: "linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0)", WebkitMaskComposite: "xor",
    mask: "linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0)" };
  const edge = (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      <div style={{ position: "absolute", inset: 0, borderRadius: u(58), padding: rim, ...ring,
        background: `conic-gradient(from ${edgeAngle}deg, ${hi}, ${mid} 12%, ${lo} 30%, ${mid} 45%, ${hi} 55%, ${lo} 72%, ${mid} 88%, ${hi})`,
        boxShadow: `inset 0 0 0 max(.5px, .08cqw) rgba(255,255,255,${isWhite ? .9 : .55})` }} />
      {isBlack && <div style={{ position: "absolute", inset: 0, borderRadius: u(58), padding: rim, ...ring, mixBlendMode: "plus-lighter", opacity: .09,
        background: `conic-gradient(from ${edgeAngle * 2}deg, red, orange, yellow, lime, cyan, blue, purple, red)` }} />}
      <div style={{ position: "absolute", inset: rim, borderRadius: u(40), border: `max(.6px, .13cqw) solid rgba(0,0,0,${isWhite ? .18 : .45})` }} />
    </div>
  );

  return (
    <div className="member-card mb-8 sm:mb-5" style={{ perspective: 1100 }}>
      <button ref={ref} type="button" onClick={() => setFlipped(f => !f)} onPointerMove={move} onPointerLeave={rest} onPointerUp={e => e.pointerType !== "mouse" && rest()}
        aria-label={isWhite ? `Your card. ${num(points)} points. Turn it over.` : `${member!.name} member card. ${num(points)} points. ${pct} off everything until ${until}. Turn it over.`}
        className="relative block w-full cursor-pointer text-left rounded-[3.8cqw] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-red"
        style={{ aspectRatio: "1536 / 969", containerType: "inline-size", touchAction: "pan-y",
          transform: `rotateY(${lx * 9}deg) rotateX(${-ly * 7}deg)`,
          filter: `drop-shadow(${-lx * 6}px ${9 - ly * 4}px 9px ${isWhite ? "rgba(0,0,0,.16)" : isBlack ? "rgba(0,0,0,.32)" : "rgba(140,0,10,.28)"})`,
          animation: still ? undefined : "member-land .9s cubic-bezier(.2,1.25,.4,1) both" }}>
        <div style={{ position: "absolute", inset: 0, transformStyle: "preserve-3d",
          transform: `rotateY(${flipped ? 180 : 0}deg)`, transition: still ? undefined : "transform .7s cubic-bezier(.3,1.25,.45,1)" }}>

          {/* front */}
          <div style={face}>
            <img src={base} alt="" draggable={false} className="absolute inset-0 h-full w-full select-none" />
            <img src={gloss} alt="" draggable={false} className="absolute inset-0 h-full w-full select-none"
              style={{ opacity: isBlack ? .9 : isWhite ? .7 : .45, mixBlendMode: isBlack ? "screen" : "soft-light",
                maskImage: `linear-gradient(${118 + lx * 10}deg, transparent ${8 + lx * 30}%, rgba(0,0,0,.85) ${40 + lx * 30}%, #000 ${44 + lx * 30}%, rgba(0,0,0,.4) ${50 + lx * 30}%, transparent ${64 + lx * 30}%)`,
                WebkitMaskImage: `linear-gradient(${118 + lx * 10}deg, transparent ${8 + lx * 30}%, rgba(0,0,0,.85) ${40 + lx * 30}%, #000 ${44 + lx * 30}%, rgba(0,0,0,.4) ${50 + lx * 30}%, transparent ${64 + lx * 30}%)` }} />
            <div aria-hidden className="absolute inset-0" style={{ mixBlendMode: "plus-lighter",
              background: `radial-gradient(circle at ${30 + lx * 50}% ${20 + ly * 50}%, rgba(255,255,255,${isBlack ? .16 : isWhite ? .2 : .22}), transparent 58%)` }} />
            {!isWhite && <div className="absolute" style={{ top: u(70), right: u(96), fontFamily: SANS, fontWeight: 500, fontSize: u(104), lineHeight: 1.1, ...metal() }}>{pct}</div>}
            <div className="absolute inset-x-0 text-center" style={{ top: "33%" }}>
              <div style={{ fontFamily: SANS, fontWeight: 500, fontSize: u(170), lineHeight: 1, fontVariantNumeric: "tabular-nums", ...metal() }}>
                {num(shown)}
              </div>
              <div className="flex items-center justify-center" style={{ gap: u(14), marginTop: u(14) }}>
                <span style={{ fontFamily: MONO, fontSize: u(26), letterSpacing: u(8), color: isWhite ? "rgba(22,19,15,.55)" : "rgba(255,255,255,.65)" }}>POINTS</span>
                {gain > 0 && <span style={{ fontFamily: MONO, fontWeight: 600, fontSize: u(24), padding: `${u(4)} ${u(12)}`, borderRadius: 999,
                  background: isWhite ? "rgba(22,19,15,.08)" : "rgba(255,255,255,.16)", color: isWhite ? "rgba(22,19,15,.75)" : "rgba(255,255,255,.9)" }}>+{num(gain)}</span>}
              </div>
            </div>
            <div className="absolute" style={{ left: u(92), right: u(96), bottom: u(isBlack ? 96 : 110) }}>
              <div style={{ fontFamily: SANS, fontWeight: 500, fontSize: u(90), letterSpacing: u(20), lineHeight: 1, whiteSpace: "nowrap", ...metal() }}>
                {isWhite ? "MY CCC" : member!.name.toUpperCase()}
              </div>
              {isBlack && <div style={{ width: u(118), height: u(4), marginTop: u(10), background: "#D6171F" }} />}
            </div>
            {edge}
          </div>

          {/* back */}
          <div style={{ ...face, transform: "rotateY(180deg)" }}>
            <img src={base} alt="" draggable={false} className="absolute inset-0 h-full w-full -scale-x-100 select-none" />
            <div className="absolute inset-0" style={{ background: `rgba(0,0,0,${isBlack ? .12 : isWhite ? .03 : .25})` }} />
            {/* the magnetic stripe: glossy, with fine edges and a sheen that follows the light */}
            <div className="absolute inset-x-0" style={{ top: u(90), height: u(150), background: "linear-gradient(180deg, #050505, #171717, #030303)",
              boxShadow: `inset 0 max(1px, 0.13cqw) rgba(255,255,255,${isBlack ? .22 : .1}), inset 0 calc(-1 * max(1px, 0.13cqw)) rgba(255,255,255,${isBlack ? .12 : .06}), 0 ${u(4)} ${u(12)} rgba(0,0,0,.5)` }}>
              <div className="absolute inset-0" style={{ mixBlendMode: "plus-lighter",
                background: `linear-gradient(100deg, transparent ${15 - lx * 30}%, rgba(255,255,255,.16) ${35 - lx * 30}%, transparent ${55 - lx * 30}%)` }} />
            </div>
            <div className="absolute text-left" style={{ left: u(92), right: u(92), bottom: u(70), display: "flex", flexDirection: "column", gap: u(18) }}>
              <div style={{ fontFamily: SANS, fontWeight: 500, fontSize: u(48), lineHeight: 1.2, color: isWhite ? INK : "#fff" }}>
                {isWhite ? "Get 10% or 20% off everything." : `${pct} off everything at the stall, all term.`}
              </div>
              <div style={{ fontFamily: MONO, fontSize: u(28), lineHeight: 1.35, color: isWhite ? "rgba(22,19,15,.7)" : "rgba(255,255,255,.75)" }}>
                {isWhite ? "Red Coke is $5 for 10% off, Black Coke $12 for 20% off, all term. Ask at the stall and this card turns red or black."
                  : `Say you're ${member!.name} when you pay, and the discount comes off before anything goes on your tab. Free rewards stay free.`}
              </div>
              {!isWhite && <div style={{ fontFamily: MONO, fontSize: u(26), letterSpacing: u(4), color: "rgba(255,255,255,.6)", whiteSpace: "nowrap", marginTop: u(12) }}>{line}</div>}
              <div className="flex items-baseline justify-between" style={{ gap: u(20), marginTop: isWhite ? u(12) : 0 }}>
                <span style={{ fontFamily: MONO, fontSize: u(36), letterSpacing: u(5), whiteSpace: "nowrap", ...(isWhite ? { color: INK } : metal(true)) }}>{holder.toUpperCase()}</span>
                <span style={{ fontFamily: MONO, fontSize: u(26), letterSpacing: u(3), whiteSpace: "nowrap", color: isWhite ? "rgba(22,19,15,.55)" : "rgba(255,255,255,.6)" }}>
                  {isWhite ? "COOKED COKE CO" : `VALID TO  ${until}`}
                </span>
              </div>
            </div>
            {edge}
          </div>
        </div>
      </button>
    </div>
  );
}

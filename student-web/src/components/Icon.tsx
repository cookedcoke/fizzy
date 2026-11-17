import { Award, BadgeCheck, Banknote, Calendar, Camera, Check, Compass, Crown, CupSoda, Flag, Flame, Gift, Heart, Repeat,
  Rocket, RotateCcw, Snowflake, Sparkles, Star, Target, Trophy, X, Zap, type LucideIcon } from "lucide-react";
import type { ScanResult } from "@/lib/api";

export function KindIcon({ kind, className }: { kind: ScanResult["kind"] | "got" | "camera"; className?: string }) {
  const p = { className, strokeWidth: 2.6, "aria-hidden": true } as const;
  switch (kind) {
    case "found": case "got": return <Check {...p} />;
    case "already": return <RotateCcw {...p} />;
    case "done": return <Trophy {...p} />;
    case "soon": return <Calendar {...p} />;
    case "over": return <Flag {...p} />;
    case "camera": return <Camera {...p} />;
    default: return <X {...p} />;
  }
}

/** Badges come from Fizzy with an emoji (My CCC still draws those); the site draws the matching icon instead,
 *  so every badge, the built-in ones and any made on the Ask page, is in one consistent style. */
const BADGE: Record<string, LucideIcon> = {
  "🥤": CupSoda, "🔁": Repeat, "👑": Crown, "🔥": Flame, "☄": Rocket, "🧭": Compass, "💸": Banknote, "💰": Banknote,
  "🎁": Gift, "✅": BadgeCheck, "⚡": Zap, "⭐": Star, "🌟": Star, "✨": Sparkles, "🏆": Trophy, "🎯": Target,
  "❤": Heart, "🧊": Snowflake, "🚀": Rocket,
};

export function BadgeIcon({ emoji, className }: { emoji: string; className?: string }) {
  const Icon = BADGE[emoji.replace(/\uFE0F/g, "")] ?? Award;
  return <Icon className={className} strokeWidth={2.2} aria-hidden />;
}

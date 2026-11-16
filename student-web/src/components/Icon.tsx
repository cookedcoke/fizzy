import { Calendar, Camera, Check, Flag, RotateCcw, Trophy, X } from "lucide-react";
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

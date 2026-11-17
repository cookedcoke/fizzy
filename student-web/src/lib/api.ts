/** Talks to Fizzy through the same JSON the My CCC iOS app uses (the X-Fizzy-Format header). */
export type Money = number; // cents

export interface Event { name: string; multiplier: number; start_date: string; end_date: string }
export interface Reward { name: string; points_cost: number }
export interface WhatsOn { rewards: Reward[]; events_on: Event[]; events_soon: Event[] }

export interface ScanResult { kind: "found" | "already" | "done" | "soon" | "over" | "bad" | "info"; title: string | null; line: string; label: string | null }
export interface Step { label: string; detail?: string; done: boolean; kind?: string }
export interface Run {
  nickname: string | null; klass: string | null; needs_name: boolean; started_ms: number; finished: boolean;
  elapsed: string | null; rank?: number | null; top?: number | null; clue?: string | null;
}
export interface Card {
  id: string; kicker: string; title: string; text: string; icon: string; when: string; steps: Step[];
  progress: { done: number; total: number }; prize: string; done: boolean; status: string;
  actions: { kind: string; label: string }[]; run?: Run | null;
}
export interface GuestCard {
  title: string; text: string; prize: string; steps: Step[]; progress: { done: number; total: number };
  run?: Run | null; claim: string | null; claimed: boolean;
}
export interface Badge { icon: string; name: string; how: string; earned: boolean; points: number; progress: number; count: string }
export interface Offer { name: string; points_cost: number; per_customer_limit: number | null; used: number; limit_hit: boolean; short: number; can_redeem: boolean }
export interface Activity { at: string; what: string; points: number | null; money: number | null }
export interface BoardRow { rank: number; name: string; points: number; me: boolean }
export interface Board { title: string; rows: BoardRow[]; mine: { rank: number; points: number; of: number } | null }
export interface Tab {
  status: string; can_charge: boolean; available_cents: Money; limit_cents: Money; min_score: number; due_days: number;
  owing_cents: Money; overdue_cents: Money; has_overdue: boolean; balance_cents: Money; late_count: number;
  has_charges: boolean; next_due: string | null;
  score: { score: number; limit_cents: Money; next: { score: number; limit_cents: Money } | null; factors: { label: string; points: number }[]; tips: string[] };
}
export interface CustomEvent { name: string; summary: string; soon: boolean; start: string | null }
/** Red Coke / Black Coke: a discount on everything until the end of term. */
export interface Member { tier: "red" | "black"; name: string; pct: number; ends_on: string; term: string | null }
export interface Me extends WhatsOn {
  signed_in: true; name: string; first: string; sid: string; points: number; tab: Tab;
  streak: { days: number; best: number; today: boolean };
  offers: Offer[]; activity: Activity[]; boards: Board[]; badges: Badge[]; badges_earned: number;
  cards: Card[]; custom_events: CustomEvent[]; tz: string; member: Member | null;
  note?: { message: string; ok: boolean; result: ScanResult | null };
}
export type Home = Me | (WhatsOn & { signed_in: false });

declare global { interface Window { __FIZZY__?: { base: string; appPayUrl: string | null } } }
const cfg = window.__FIZZY__ ?? { base: "/me", appPayUrl: null };
export const base = cfg.base;
export const appPayUrl = cfg.appPayUrl;
/** A path on the student site ("/hunt") as the browser sees it: no /me on the student host. */
export const href = (p: string) => (base + p).replace(/\/$/, "") || "/";

async function call<T>(path: string, form?: Record<string, string>): Promise<{ status: number; body: T }> {
  const init: RequestInit = { credentials: "same-origin", headers: { "X-Fizzy-Format": "json", Accept: "application/json" } };
  if (form) { init.method = "POST"; init.body = new URLSearchParams(form); }
  const r = await fetch(base + path, init);
  const body = await r.json().catch(() => ({ error: "Couldn't reach the stall's server. Check your connection and try again." }));
  return { status: r.status, body: body as T };
}

export const api = {
  home: () => call<Home>("/"),
  login: (who: string, passcode: string) => call<{ ok?: boolean; error?: string }>("/login", { who, passcode }),
  logout: () => call<{ ok: boolean }>("/logout", {}),
  scan: (code: string) => call<{ ok: boolean; message: string; signed_in: boolean; result: ScanResult | null; cards?: GuestCard[] }>(
    "/c/" + encodeURIComponent(code)),
  hunt: () => call<{ ok: boolean; cards: GuestCard[] }>("/hunt"),
  huntName: (nickname: string, klass: string) => call<{ ok: boolean; message: string }>("/hunt/name", { nickname, klass }),
};

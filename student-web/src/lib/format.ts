const tz = () => "Australia/Sydney";
export const money = (cents: number) =>
  (cents < 0 ? "-" : "") + "$" + (Math.abs(cents) / 100).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const num = (n: number) => n.toLocaleString("en-AU");
/** A timestamp (UTC, from Fizzy) as a Sydney date, e.g. "Wed 12 Nov". */
export const when = (iso: string, opts: Intl.DateTimeFormatOptions = { day: "2-digit", month: "short" }) =>
  new Date(iso).toLocaleDateString("en-AU", { timeZone: tz(), ...opts });
/** A plain date ("2026-11-12") as words. */
export const day = (ymd: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" }) =>
  new Date(ymd + "T12:00:00").toLocaleDateString("en-AU", opts);
export const times = (m: number) => `${Number.isInteger(m) ? m : m.toFixed(1)}×`;

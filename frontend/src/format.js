export const mw = (n) => (n == null ? "n/a" : `${Math.round(n).toLocaleString("en-GB")} MW`);
export const gwh = (mwh) => (mwh == null ? "n/a" : `${(mwh / 1000).toFixed(1)} GWh`);
export const pct = (n) => (n == null ? "n/a" : `${n.toFixed(1)}%`);

const utc = { timeZone: "UTC" };
export const dayLabel = (isoDate) =>
  new Date(`${isoDate}T12:00:00Z`).toLocaleDateString("en-GB", { ...utc, weekday: "short", day: "numeric", month: "short" });
export const timeLabel = (iso) =>
  new Date(iso).toLocaleString("en-GB", { ...utc, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) + " UTC";
export const hhmm = (iso) =>
  new Date(iso).toLocaleTimeString("en-GB", { ...utc, hour: "2-digit", minute: "2-digit" });

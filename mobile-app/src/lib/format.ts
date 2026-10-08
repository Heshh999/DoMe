import { parseRfc3339 } from "@dome/protocol";

export function relativeTime(ts: string | null | undefined, now: Date = new Date()): string {
  if (!ts) return "never";
  let d: Date;
  try {
    d = parseRfc3339(ts);
  } catch {
    return "unknown";
  }
  const diff = Math.max(0, now.getTime() - d.getTime());
  const s = Math.round(diff / 1000);
  if (s < 10) return "just now";
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  const days = Math.round(h / 24);
  return `${days} days ago`;
}

export function absoluteTime(ts: string | null | undefined): string {
  if (!ts) return "—";
  try {
    return parseRfc3339(ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return "unknown";
  }
}

export function mmss(seconds: number | undefined | null): string {
  if (seconds === undefined || seconds === null || !Number.isFinite(seconds)) return "–:––";
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

export function money(cents: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}

export function secondsUntil(ts: string, now: Date = new Date()): number {
  try {
    return Math.max(0, Math.round((parseRfc3339(ts).getTime() - now.getTime()) / 1000));
  } catch {
    return 0;
  }
}

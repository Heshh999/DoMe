/**
 * Billing and AI-allowance helpers for the Billing page, Settings and the pricing page (docs/BILLING.md; protocol 1.3
 * rules.ai_allowance): the refresh wording the shared fixture pins ("Refreshes today at 2 pm"), the 80 % warning level,
 * the one-line allowance summary, which limit stopped a turn and why, and the plan-state copy. Pure functions: the page
 * passes the server's bodies in and renders what comes out. Nothing here decides a plan — every state shown comes from
 * GET /v1/account/billing and GET /v1/session, never from a URL or a return from Stripe.
 */
import { ERRORS, type rest } from "@dome/protocol";

import { planById, usd, WARN_AT_PERCENT, WINDOW_HOURS } from "../content/plans.ts";
import { errorMessage } from "./labels.ts";

export type AssistantAllowance = rest.AssistantAllowance;
export type AllowanceBucket = rest.AllowanceBucket;
export type BillingStatusResponse = rest.BillingStatusResponse;
export type ExhaustedReason = NonNullable<rest.AssistantAllowance["exhausted"]>;

/** Leaving the app for Stripe's hosted page: an object so tests can replace it (jsdom cannot navigate). */
export const browserNavigation = {
  assign(url: string): void {
    window.location.assign(url);
  },
};

/** Only an https URL the server handed us is ever navigated to (the contract pins `^https://`). */
export function isCheckoutUrl(url: string): boolean {
  return /^https:\/\/[^\s]+$/.test(url) && url.length <= 2048;
}

// ---- time wording -----------------------------------------------------------------------------------

interface ZonedParts {
  day: number;
  weekday: string;
  date: string;
  time: string;
}

function zonedParts(d: Date, timeZone: string | undefined): ZonedParts {
  const tz = timeZone ? { timeZone } : {};
  const ymd = new Intl.DateTimeFormat("en-US", { ...tz, year: "numeric", month: "numeric", day: "numeric" }).formatToParts(d);
  const n = (type: string) => Number(ymd.find((p) => p.type === type)?.value ?? "0");
  const day = Math.floor(Date.UTC(n("year"), n("month") - 1, n("day")) / 86_400_000);
  const weekday = new Intl.DateTimeFormat("en-US", { ...tz, weekday: "long" }).format(d);
  const date = new Intl.DateTimeFormat("en-GB", { ...tz, day: "numeric", month: "long" }).format(d);
  const clock = new Intl.DateTimeFormat("en-US", { ...tz, hour: "numeric", minute: "2-digit", hour12: true }).formatToParts(d);
  const part = (type: string) => clock.find((p) => p.type === type)?.value ?? "";
  const minutes = part("minute");
  const period = part("dayPeriod").toLowerCase().replace(/\./g, "");
  const time = `${part("hour")}${minutes === "00" ? "" : `:${minutes}`} ${period}`;
  return { day, weekday, date, time };
}

function parseTs(ts: string): Date | null {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * When an allowance comes back, in the viewer's zone (fixtures/ai-window-cases.json pins the wording): "Refreshes today
 * at 2 pm", "tomorrow at 9 am", a weekday within six days, else "27 October at 2 pm"; minutes only when non-zero.
 * `null` is Free's taste: "Does not renew".
 */
export function refreshText(resetsAt: string | null, now: Date = new Date(), timeZone?: string): string {
  if (resetsAt === null) return "Does not renew";
  const at = parseTs(resetsAt);
  if (!at) return "Refreshes later";
  const target = zonedParts(at, timeZone);
  const today = zonedParts(now, timeZone);
  const days = target.day - today.day;
  const when = days <= 0 ? "today" : days === 1 ? "tomorrow" : days <= 6 ? target.weekday : target.date;
  return `Refreshes ${when} at ${target.time}`;
}

/** An absolute date for renewals, endings and deadlines: "20 November 2026". */
export function longDate(ts: string | null | undefined, timeZone?: string): string {
  const d = ts ? parseTs(ts) : null;
  if (!d) return "—";
  return new Intl.DateTimeFormat("en-GB", { ...(timeZone ? { timeZone } : {}), day: "numeric", month: "long", year: "numeric" }).format(d);
}

// ---- allowance ---------------------------------------------------------------------------------------

export type BucketLevel = "ok" | "warn" | "exhausted";

/** Warn from `warn_at_percent` (80) of the bucket; exhausted at the limit (a 0 limit is simply exhausted). */
export function bucketLevel(used: number, limit: number): BucketLevel {
  if (used >= limit) return "exhausted";
  return (used * 100) / limit >= WARN_AT_PERCENT ? "warn" : "ok";
}

/** True when any open window is at the warning level (the Settings line turns amber, the Ask page shows a banner). */
export function allowanceWarns(a: AssistantAllowance): boolean {
  if (a.kind !== "windows") return false;
  return [a.window, a.week].some((b) => !!b && b.limit > 0 && bucketLevel(b.used, b.limit) !== "ok");
}

/** "Refreshes today at 2 pm" → "refreshes today at 2 pm", for the middle of a sentence. */
export const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/**
 * When the next request works again. While a bucket is full that is the latest end among the full buckets (the window
 * and the week both full on Pro after four full windows: the week, never the window's earlier end, which would promise
 * a request that is still refused); otherwise the earliest end of the open windows; null while none is open (the next
 * request opens them). The server's own resets_at wins when it names one.
 */
export function nextRefresh(a: AssistantAllowance): string | null {
  if (a.exhausted && a.resets_at) return a.resets_at;
  const open = [a.window, a.week].filter((b): b is AllowanceBucket & { ends_at: string } => !!b && typeof b.ends_at === "string");
  if (open.length === 0) return null;
  const full = open.filter((b) => b.limit > 0 && b.used >= b.limit);
  const ends = (full.length > 0 ? full : open).map((b) => b.ends_at).sort((x, y) => Date.parse(x) - Date.parse(y));
  return (full.length > 0 ? ends[ends.length - 1] : ends[0]) ?? null;
}

/**
 * One line for Settings and Billing. Free: "3 of 10 AI requests to try used" (+ " · none left"). Paid: "2 of 30 used in
 * this 5-hour window · 7 of 120 this week · refreshes Saturday at 2 pm" (or "starts with your next request" while no
 * window is open), then " · 100 top-up requests available" when a balance exists.
 */
export function allowanceSummary(a: AssistantAllowance, now: Date = new Date(), timeZone?: string): string {
  const topup = a.topup.balance > 0 ? ` · ${a.topup.balance} top-up requests available` : "";
  if (a.kind === "taste" && a.taste) {
    const none = a.exhausted === "taste" || a.taste.used >= a.taste.limit ? " · none left" : "";
    return `${a.taste.used} of ${a.taste.limit} AI requests to try used${none}${topup}`;
  }
  if (a.kind === "windows" && a.window && a.week) {
    const refresh = nextRefresh(a);
    const tail = refresh ? lowerFirst(refreshText(refresh, now, timeZone)) : "starts with your next request";
    return `${a.window.used} of ${a.window.limit} used in this ${WINDOW_HOURS}-hour window · ${a.week.used} of ${a.week.limit} this week · ${tail}${topup}`;
  }
  return topup ? topup.slice(3) : "";
}

/**
 * Which limit stopped AI turns, in one sentence, then when it refreshes (rules.ai_allowance; docs/design/assistant.md
 * §1.4). `resetsAt` is the error's own detail when the server sent one; otherwise the allowance's `resets_at`.
 */
export function limitExplanation(reason: string | null | undefined, allowance: AssistantAllowance | null, options: { resetsAt?: string | null; now?: Date; timeZone?: string } = {}): string {
  const resets = options.resetsAt ?? allowance?.resets_at ?? null;
  const refresh = resets ? ` ${refreshText(resets, options.now ?? new Date(), options.timeZone)}.` : "";
  const n = (limit: number | undefined) => (limit === undefined ? "" : `${limit} `);
  switch (reason) {
    case "taste":
      return `You've used your one-time taste of ${n(allowance?.taste?.limit)}AI requests. It does not renew.`;
    case "window":
      return `You've used this ${WINDOW_HOURS}-hour window's ${n(allowance?.window?.limit)}AI requests.${refresh}`;
    case "week":
      return `You've used this week's ${n(allowance?.week?.limit)}AI requests.${refresh}`;
    case "cost_cap":
      return `This week's AI requests needed more processing than your plan includes.${refresh}`;
    case "failures":
      return `Too many AI requests failed this week, so the assistant is paused to protect your allowance.${refresh}`;
    default:
      return "";
  }
}

/**
 * The Ask page's chip: how many requests a new turn could still use — the smaller room of the two windows (plus the
 * top-up balance while it is spendable), Free's remaining taste, or null when the plan has no AI at all.
 */
export function requestsLeft(a: AssistantAllowance): number | null {
  const topup = a.topup.allowed ? a.topup.balance : 0;
  if (a.kind === "taste" && a.taste) return Math.max(0, a.taste.limit - a.taste.used) + topup;
  if (a.kind === "windows" && a.window && a.week) return Math.min(Math.max(0, a.window.limit - a.window.used), Math.max(0, a.week.limit - a.week.used)) + topup;
  return a.kind === "none" ? null : topup;
}

/** The 80 % banner on the Ask page: "You've used 24 of 30 AI requests in this 5-hour window. Refreshes today at 6:30 pm." */
export function warningText(a: AssistantAllowance, now: Date = new Date(), timeZone?: string): string | null {
  if (a.kind !== "windows" || !a.window || !a.week) return null;
  const windowWarn = a.window.limit > 0 && bucketLevel(a.window.used, a.window.limit) === "warn";
  const weekWarn = a.week.limit > 0 && bucketLevel(a.week.used, a.week.limit) === "warn";
  if (!windowWarn && !weekWarn) return null;
  const b = windowWarn ? a.window : a.week;
  const where = windowWarn ? `in this ${WINDOW_HOURS}-hour window` : "this week";
  const refresh = b.ends_at ? ` ${refreshText(b.ends_at, now, timeZone)}.` : "";
  return `You've used ${b.used} of ${b.limit} AI requests ${where}.${refresh}`;
}

// ---- plan state copy ---------------------------------------------------------------------------------

export type StateTone = "neutral" | "success" | "warning" | "danger" | "info";

export interface StateCopy {
  label: string;
  tone: StateTone;
  text: string;
}

const FREE_PLAN = planById("free");
const RETURNS_TO_FREE = `Then your account returns to Free: ${FREE_PLAN.pcs} PC and ${FREE_PLAN.phones} phones stay enabled, your layouts and routines are kept but cannot run, and you can re-subscribe at any time.`;
const KEPT = "You are on DoMe Free; your PCs, phones and settings were kept.";
const PENDING = "Pending activation — we are confirming your payment. Nothing is unlocked until it is verified.";

function planName(plan: rest.PlanId): string {
  return planById(plan).displayName;
}

/**
 * Label, tone and one explanation per state. The subscription row's state is the more precise source when there is
 * one; `entitlement_state` alone (an older server, or no row) falls back to the six coarse states.
 */
export function billingStateCopy(status: Pick<BillingStatusResponse, "plan" | "entitlement_state" | "subscription">, timeZone?: string): StateCopy {
  const sub = status.subscription;
  if (sub) {
    const name = planName(sub.plan);
    switch (sub.state) {
      case "checkout_pending":
      case "incomplete":
        return { label: "Pending activation", tone: "warning", text: PENDING };
      case "trialing":
      case "active":
        return sub.cancel_at_period_end
          ? { label: "Ends at period end", tone: "warning", text: `${name} stays on until ${longDate(sub.current_period_end, timeZone)}. ${RETURNS_TO_FREE}` }
          : { label: "Active", tone: "success", text: `Your ${name} subscription is active.` };
      case "cancel_scheduled":
        return { label: "Ends at period end", tone: "warning", text: `${name} stays on until ${longDate(sub.current_period_end, timeZone)}. ${RETURNS_TO_FREE}` };
      case "past_due":
        return { label: "Payment problem", tone: "danger", text: `Your last payment did not go through. Update your card by ${longDate(sub.grace_until, timeZone)} to keep ${name}. Free features keep working.` };
      case "unpaid":
        return { label: "Expired", tone: "neutral", text: `Your ${name} subscription ended because the payment could not be collected. ${KEPT}` };
      case "canceled":
        return { label: "Expired", tone: "neutral", text: `Your paid subscription ended. ${KEPT}` };
    }
  }
  switch (status.entitlement_state) {
    case "active":
      return { label: "Active", tone: "success", text: `Your ${planName(status.plan)} subscription is active.` };
    case "pending":
      return { label: "Pending activation", tone: "warning", text: PENDING };
    case "past_due":
      return { label: "Payment problem", tone: "danger", text: "Your last payment did not go through. Free features keep working." };
    case "canceling":
      return { label: "Ends at period end", tone: "warning", text: `Your subscription ends at the close of the current period. ${RETURNS_TO_FREE}` };
    case "expired":
      return { label: "Expired", tone: "neutral", text: `Your paid subscription ended. ${KEPT}` };
    default:
      return { label: "Free", tone: "neutral", text: "You are on DoMe Free. Everything you use today stays free; no card is needed." };
  }
}

// ---- errors ------------------------------------------------------------------------------------------

/** What to show for a failed billing request: the contract's message for its code, never the server's free text. */
export function billingErrorText(e: unknown): string {
  const code = e && typeof e === "object" && "code" in e ? String((e as { code: unknown }).code) : "INTERNAL";
  return ERRORS[code]?.user_message ?? errorMessage({ code });
}

/** "Add 100 AI requests for $2.99", from the server's figures (the contract's defaults until it answers). */
export function topupLabel(topup: { requests: number; price_cents: number }): string {
  return `Add ${topup.requests} AI requests for ${usd(topup.price_cents)}`;
}

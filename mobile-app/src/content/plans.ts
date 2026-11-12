/**
 * Plan content for the website: Free, Pro and Pro Plus with their prices, device counts, Screen view and
 * AI butler allowances, and the plain-words rules of the allowance, for the pricing page, the FAQ and
 * the Billing page. Device counts, prices and AI figures are read from the contract
 * (shared/protocol/plans.json → `PLANS`) so the website, the app and the server never disagree; the
 * taglines, minutes and rules are content.
 *
 * Honesty: only Free exists today. Pro and Pro Plus are shown with the status "Coming at paid launch",
 * Screen view, custom layouts and routines are "Coming" wherever they appear, and every AI allowance carries
 * "Not switched on yet" while the server says the assistant is off (GET /v1/plans `ai_enabled`). Nothing here is a claim that it can be bought: the
 * pricing page and the Billing page ask the server (GET /v1/plans, GET /v1/account/billing) whether
 * checkout exists before they offer one.
 *
 * Prices, the top-up and the AI figures are read from the contract (protocol 1.3): plans.json's
 * `pricing_defaults` and each plan's `ai_taste_requests` / `ai_window` / `ai_week`, so the website, the
 * Billing page and GET /v1/plans show one set of figures (test/public/plans.test.ts pins them). Prices
 * are tax-inclusive by the founder's decision (docs/PROGRESS.md 2026-10-20): the amount shown is the
 * amount charged.
 *
 * Before payment (docs/legal): every subscribe button and the paid plans on the pricing page carry the renewal,
 * cancellation and Terms lines below (renewalLine, HOW_TO_CANCEL, AGREE_TO_TERMS; drawn by SubscriptionTerms.tsx),
 * and the top-up button carries topUpTerms. Paid Screen view is never called "unlimited" (PAID_SCREEN_VIEW).
 */
import { PLANS } from "@dome/protocol";

const PRICING = PLANS.pricing_defaults;

/** A paid plan's display price from the contract's `pricing_defaults`. */
function contractPrice(id: "pro" | "pro_plus"): PlanPrice {
  const p = PRICING.plans[id];
  return { monthlyCents: p.monthly_cents, annualCents: p.annual_cents };
}

export type PlanId = "free" | "pro" | "pro_plus";

/** A plan's state on the website: a chip for Free, a non-button status line for the paid plans. */
export type PlanStatus = { kind: "available"; label: typeof AVAILABLE_NOW } | { kind: "coming"; label: typeof COMING_AT_PAID_LAUNCH };

export const AVAILABLE_NOW = "Available now";
export const COMING_AT_PAID_LAUNCH = "Coming at paid launch";
/** The label for Screen view everywhere it appears: the feature is in development. */
export const SCREEN_VIEW_STATUS = "Coming";
/** Point-and-talk / click by name is planned, not built. */
export const POINT_AND_TALK_STATUS = "Planned";
/**
 * Custom remote layouts and saved routines: in the plan contract, not built yet (docs/PRODUCT_AND_PLANS.md). "Coming",
 * never "at paid launch": paid plans can go on sale before they exist.
 */
export const LAYOUTS_STATUS = "Coming";
/** Beside every AI allowance while the server says the assistant cannot be used (GET /v1/plans `ai_enabled`). */
export const AI_OFF_STATUS = "Not switched on yet";

export interface PlanPrice {
  monthlyCents: number;
  annualCents: number;
}

export interface AiAllowance {
  /** Free: a one-time taste of this many requests (it does not renew). */
  tasteRequests?: number;
  /** Paid plans: requests per 5-hour window and per week, with the rough minutes they amount to. */
  perWindow?: { requests: number; minutes: number };
  perWeek?: { requests: number; minutes: number };
}

export interface PlanContent {
  id: PlanId;
  /** Short name: "Free", "Pro", "Pro Plus". */
  name: string;
  /** Full name: "DoMe Free", "DoMe Pro", "DoMe Pro Plus". */
  displayName: string;
  tagline: string;
  status: PlanStatus;
  /** `null` is Free: shown as "$0", never "$0.00". */
  price: PlanPrice | null;
  pcs: number;
  phones: number;
  /** Screen view allowance (the feature itself is coming). */
  screenView: string;
  ai: AiAllowance;
  topUp: boolean;
  /** The card's bullet list, in order; a status such as "Coming" is drawn as a chip after the text. */
  highlights: Highlight[];
}

export interface Highlight {
  text: string;
  status?: string;
  /** An AI allowance: the page adds AI_OFF_STATUS while the assistant is switched off. */
  ai?: boolean;
}

/** The 5-hour window in hours and the weekly window in days. */
export const WINDOW_HOURS = 5;
export const WEEK_DAYS = 7;
/** The app warns when this share of a window's allowance is used. */
export const WARN_AT_PERCENT = 80;
/** The optional top-up on Pro and Pro Plus: never automatic. */
export const TOP_UP: { readonly requests: number; readonly priceCents: number } = { requests: PRICING.topup.requests, priceCents: PRICING.topup.price_cents };
/** Free's one-time taste of the AI butler. */
export const FREE_TASTE_REQUESTS: number = PLANS.plans.free.ai_taste_requests;
/** Free's Screen view allowance, in minutes per 5-hour window. */
export const FREE_SCREEN_VIEW_MINUTES = 10;
/**
 * Pro and Pro Plus Screen view: no minute allowance (plans.json `screen_view_seconds_per_window: null`), but never
 * "unlimited": the relay keeps per-account limits on every plan (cloud-api screen/turn.py: relay connections per
 * account per calendar month; screen/service.py: starts per phone per minute).
 */
export const PAID_SCREEN_VIEW = "Without the Free plan’s time limit (fair-use limits apply)";
/** The same, where a line has no room (the comparison on phones, the demo film). */
export const PAID_SCREEN_VIEW_SHORT = "Fair use";
/** What "fair-use limits" means for Screen view, in one sentence (the same example the Terms give). */
export const SCREEN_VIEW_FAIR_USE = "Pro and Pro Plus will not have Free’s time limit, but fair-use limits apply, for example a monthly limit on relay connections per account.";
/**
 * Everyday commands DoMe understands by itself: free and never counted against an AI allowance, on every plan. Never
 * called "unlimited": a fair-use limit applies to them like every command (plans.json `manual_command_rate_limit`, 120
 * per minute per phone, enforced by cloud-api relay/router.py; the pricing comparison shows it).
 */
export const EVERYDAY_EXAMPLES = ["pause", "volume 40", "open Spotify"] as const;

const free = PLANS.plans.free;
const pro = PLANS.plans.pro;
const proPlus = PLANS.plans.pro_plus;

/** A paid plan's AI allowance: the contract's window and week; minutes are content (requests ÷ 2). */
function paidAi(id: "pro" | "pro_plus"): AiAllowance {
  const plan = PLANS.plans[id];
  const window = plan.ai_window.requests;
  const week = plan.ai_week.requests;
  return { perWindow: { requests: window, minutes: window / 2 }, perWeek: { requests: week, minutes: week / 2 } };
}
const PRO_AI = paidAi("pro");
const PRO_PLUS_AI = paidAi("pro_plus");

/** US dollars from cents, the way the website shows them: "$0", "$9.99", "$99.99" (never "$0.00"). */
export function usd(cents: number): string {
  const whole = Math.trunc(cents / 100);
  const rest = Math.abs(cents % 100);
  return rest === 0 ? `$${whole}` : `$${whole}.${String(rest).padStart(2, "0")}`;
}

/** "about 15 minutes", "about 1 hour", "about 2.5 hours". */
export function aboutTime(minutes: number): string {
  if (minutes < 60) return `about ${minutes} minutes`;
  if (minutes === 60) return "about 1 hour";
  const hours = minutes / 60;
  return `about ${Number.isInteger(hours) ? hours : hours.toFixed(1)} hours`;
}

/** "30 requests (about 15 minutes) per 5-hour window, up to 120 (about 1 hour) a week". */
export function aiSummary(ai: AiAllowance): string {
  if (ai.tasteRequests !== undefined) return `A one-time taste of ${ai.tasteRequests} requests, to try the butler`;
  if (ai.perWindow && ai.perWeek) return `${ai.perWindow.requests} requests (${aboutTime(ai.perWindow.minutes)}) per ${WINDOW_HOURS}-hour window, up to ${ai.perWeek.requests} (${aboutTime(ai.perWeek.minutes)}) a week`;
  return "";
}

/** How often a paid plan renews: each interval alone (the Billing page's choice), or both (the pricing page). */
export type RenewalInterval = "monthly" | "annual" | "both";

/** How the renewal sentence starts; SubscriptionTerms.tsx sets it in bold (it must stand out, not hide in small print). */
export const RENEWS_AUTOMATICALLY = "Renews automatically";
/**
 * Beside every button that starts a subscription, and under the paid plans on the pricing page: the plan renews
 * automatically at the price shown until it is cancelled. The amounts come from the caller's plan data (the server's
 * pricing, or plans.json's `pricing_defaults`), never from this file.
 */
export function renewalLine(price: PlanPrice, interval: RenewalInterval): string {
  const month = `every month at ${usd(price.monthlyCents)}`;
  const year = `every year at ${usd(price.annualCents)}`;
  const when = interval === "monthly" ? month : interval === "annual" ? year : `${month}, or ${year} if you choose yearly billing,`;
  return `${RENEWS_AUTOMATICALLY} ${when} until you cancel.`;
}
/** The Stripe prices are tax-inclusive (docs/BILLING.md §2, founder decision 2026-10-20). */
export const TAX_INCLUDED = "Price includes any sales tax.";
/** How to cancel, as the Billing page and the Terms describe it: the Manage subscription page, effective at period end. */
export const HOW_TO_CANCEL = "To cancel, open Billing and choose Manage subscription. Paid features stay until the end of the period you paid for.";
/** The text of the agreement line; SubscriptionTerms.tsx draws "Terms" and "Privacy notice" as links to /terms and /privacy. */
export const AGREE_TO_TERMS = "By subscribing you agree to the Terms and the Privacy notice.";

/**
 * Beside the top-up button: a one-time purchase, what it adds, and what happens to unused requests exactly as cloud-api
 * does today (docs/BILLING.md §10): the balance does not expire, is spent only once the 5-hour window or the week is used
 * up, and is kept on Free but can be spent only on a plan with top-ups.
 */
export function topUpTerms(requests: number): string {
  return `A one-time purchase, not a subscription: it adds ${requests} AI requests to your account. Unused top-up requests do not expire. They are used after your ${WINDOW_HOURS}-hour window or week runs out, and they are kept if you move to Free, to use again on Pro or Pro Plus.`;
}

const topUpLine: Highlight = { text: `Optional top-up: ${TOP_UP.requests} extra AI requests for ${usd(TOP_UP.priceCents)}, only when you choose`, ai: true };
const layoutsLine: Highlight = { text: `Custom remote layouts and saved routines of up to ${pro.routine_max_steps} steps`, status: LAYOUTS_STATUS };
const paidScreenView: Highlight = { text: `Screen view: ${PAID_SCREEN_VIEW.charAt(0).toLowerCase()}${PAID_SCREEN_VIEW.slice(1)}`, status: SCREEN_VIEW_STATUS };

export const PLAN_CONTENT: readonly PlanContent[] = [
  {
    id: "free",
    name: "Free",
    displayName: free.display_name,
    tagline: "Everything you need to run your PC from the couch, at home or away.",
    status: { kind: "available", label: AVAILABLE_NOW },
    price: null,
    pcs: free.max_enabled_pcs,
    phones: free.max_controllers,
    screenView: `${FREE_SCREEN_VIEW_MINUTES} minutes per ${WINDOW_HOURS}-hour window`,
    ai: { tasteRequests: FREE_TASTE_REQUESTS },
    topUp: false,
    highlights: [
      { text: "Full remote control: YouTube, media, volume, approved apps, lock and power, touchpad, keyboard and typed commands" },
      { text: `${free.max_enabled_pcs} PC and up to ${free.max_controllers} phones` },
      { text: "Everyday commands without AI: free, never counted" },
      { text: `AI butler: a one-time taste of ${FREE_TASTE_REQUESTS} requests`, ai: true },
      { text: `Screen view: ${FREE_SCREEN_VIEW_MINUTES} minutes per ${WINDOW_HOURS}-hour window`, status: SCREEN_VIEW_STATUS },
    ],
  },
  {
    id: "pro",
    name: "Pro",
    displayName: pro.display_name,
    tagline: "More PCs, Screen view without the Free plan’s time limit once it ships, and a real allowance for the AI butler.",
    status: { kind: "coming", label: COMING_AT_PAID_LAUNCH },
    price: contractPrice("pro"),
    pcs: pro.max_enabled_pcs,
    phones: pro.max_controllers,
    screenView: PAID_SCREEN_VIEW,
    ai: PRO_AI,
    topUp: true,
    highlights: [{ text: "Everything in Free" }, { text: `Up to ${pro.max_enabled_pcs} PCs and ${pro.max_controllers} phones` }, { text: `AI butler: ${aiSummary(PRO_AI)}`, ai: true }, paidScreenView, layoutsLine, topUpLine],
  },
  {
    id: "pro_plus",
    name: "Pro Plus",
    displayName: proPlus.display_name,
    tagline: `For people who talk to their PC all day: three times Pro’s AI requests per ${WINDOW_HOURS}-hour window.`,
    status: { kind: "coming", label: COMING_AT_PAID_LAUNCH },
    price: contractPrice("pro_plus"),
    pcs: proPlus.max_enabled_pcs,
    phones: proPlus.max_controllers,
    screenView: PAID_SCREEN_VIEW,
    ai: PRO_PLUS_AI,
    topUp: true,
    highlights: [{ text: "Everything in Pro" }, { text: `Up to ${proPlus.max_enabled_pcs} PCs and ${proPlus.max_controllers} phones` }, { text: `AI butler: ${aiSummary(PRO_PLUS_AI)}`, ai: true }, paidScreenView, layoutsLine, topUpLine],
  },
];

export function planById(id: PlanId): PlanContent {
  const plan = PLAN_CONTENT.find((p) => p.id === id);
  if (!plan) throw new Error(`unknown plan ${id}`);
  return plan;
}

/** The plain-words rules of the AI butler's allowance, shared by the pricing page and the FAQ. */
export const AI_RULES = {
  request: "One request is one thing you ask that DoMe cannot work out on its own, so the AI interprets it and picks safe PC actions from DoMe’s fixed list. One request is roughly 30 seconds of use.",
  /** What happens to the actions the AI picks: exactly what the app does today (lib/assistant.ts, the action registry's risk levels). */
  actions: "Simple actions such as pause or volume run right away and show what happened; opening apps, full screen and locking wait for your tap on Run; power actions and closing apps ask you to confirm. Turn on “Ask before every action” in Settings to approve everything first.",
  everyday: `Everyday commands DoMe understands by itself (“${EVERYDAY_EXAMPLES[0]}”, “${EVERYDAY_EXAMPLES[1]}”, “${EVERYDAY_EXAMPLES[2]}”) are free and never count against your AI allowance, on every plan, even after you reach an AI limit.`,
  windows: `The ${WINDOW_HOURS}-hour window starts with your first AI request; the weekly window refreshes ${WEEK_DAYS} days after the week’s first use. When paid plans launch, the app will show the exact refresh time (“Refreshes Saturday at 2 pm”) and warn you at ${WARN_AT_PERCENT}%.`,
  taste: `Free includes a one-time taste of ${FREE_TASTE_REQUESTS} AI requests, so you can try the butler before deciding. It does not renew.`,
  topUp: `On Pro and Pro Plus you can add ${TOP_UP.requests} AI requests for ${usd(TOP_UP.priceCents)} when you run out. You choose to buy a top-up; nothing is ever added or charged automatically.`,
  privacy: "The AI never sees your screen: no screenshots, no pictures. Nothing is recorded, and your speech and search words are not stored.",
  billing: "Prices are in US dollars and are the full amount you pay (any tax is included). Billing runs through the DoMe web app (Stripe) when paid plans launch.",
} as const;

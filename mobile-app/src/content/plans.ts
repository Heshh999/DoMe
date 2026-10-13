/**
 * Plan content for the website: Free, Pro and Pro Plus with their prices, device counts, Screen view and
 * AI butler allowances, and the plain-words rules of the allowance. These are the owner's decisions for
 * the pricing page and FAQ (content only): the live metering, the Stripe prices and the contract's limits
 * (shared/protocol/plans.json → `PLANS`) are separate work. Device counts for Free and Pro are read from
 * the contract so the website and the app never disagree about them.
 *
 * Honesty: only Free exists today. Pro and Pro Plus are shown with the status "Coming at paid launch"
 * and Screen view is "Coming" wherever it appears. Nothing here is a claim that it can be bought.
 */
import { PLANS } from "@dome/protocol";

export type PlanId = "free" | "pro" | "pro_plus";

/** A plan's state on the website: a chip for Free, a non-button status line for the paid plans. */
export type PlanStatus = { kind: "available"; label: typeof AVAILABLE_NOW } | { kind: "coming"; label: typeof COMING_AT_PAID_LAUNCH };

export const AVAILABLE_NOW = "Available now";
export const COMING_AT_PAID_LAUNCH = "Coming at paid launch";
/** The label for Screen view everywhere it appears: the feature is in development. */
export const SCREEN_VIEW_STATUS = "Coming";
/** Point-and-talk / click by name is planned, not built. */
export const POINT_AND_TALK_STATUS = "Planned";

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
}

/** The 5-hour window in hours and the weekly window in days. */
export const WINDOW_HOURS = 5;
export const WEEK_DAYS = 7;
/** The app warns when this share of a window's allowance is used. */
export const WARN_AT_PERCENT = 80;
/** The optional top-up on Pro and Pro Plus: never automatic. */
export const TOP_UP = { requests: 100, priceCents: 299 } as const;
/** Free's one-time taste of the AI butler. */
export const FREE_TASTE_REQUESTS = 10;
/** Free's Screen view allowance, in minutes per 5-hour window. */
export const FREE_SCREEN_VIEW_MINUTES = 10;
/** Everyday commands DoMe understands by itself: free, unlimited, never counted, on every plan. */
export const EVERYDAY_EXAMPLES = ["pause", "volume 40", "open Spotify"] as const;

const free = PLANS.plans.free;
const pro = PLANS.plans.pro;

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

const topUpLine: Highlight = { text: `Optional top-up: ${TOP_UP.requests} extra AI requests for ${usd(TOP_UP.priceCents)}, only when you choose` };
const layoutsLine: Highlight = { text: `Custom remote layouts and saved routines of up to ${pro.routine_max_steps} steps, at paid launch` };
const screenViewUnlimited: Highlight = { text: "Screen view: unlimited", status: SCREEN_VIEW_STATUS };

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
      { text: "Everyday commands without AI: unlimited, always" },
      { text: `AI butler: a one-time taste of ${FREE_TASTE_REQUESTS} requests` },
      { text: `Screen view: ${FREE_SCREEN_VIEW_MINUTES} minutes per ${WINDOW_HOURS}-hour window`, status: SCREEN_VIEW_STATUS },
    ],
  },
  {
    id: "pro",
    name: "Pro",
    displayName: pro.display_name,
    tagline: "More PCs, unlimited Screen view and a real allowance for the AI butler.",
    status: { kind: "coming", label: COMING_AT_PAID_LAUNCH },
    price: { monthlyCents: 999, annualCents: 9999 },
    pcs: pro.max_enabled_pcs,
    phones: pro.max_controllers,
    screenView: "Unlimited",
    ai: { perWindow: { requests: 30, minutes: 15 }, perWeek: { requests: 120, minutes: 60 } },
    topUp: true,
    highlights: [{ text: "Everything in Free" }, { text: `Up to ${pro.max_enabled_pcs} PCs and ${pro.max_controllers} phones` }, { text: `AI butler: ${aiSummary({ perWindow: { requests: 30, minutes: 15 }, perWeek: { requests: 120, minutes: 60 } })}` }, screenViewUnlimited, layoutsLine, topUpLine],
  },
  {
    id: "pro_plus",
    name: "Pro Plus",
    displayName: "DoMe Pro Plus",
    tagline: "For people who talk to their PC all day: three times the AI butler allowance.",
    status: { kind: "coming", label: COMING_AT_PAID_LAUNCH },
    price: { monthlyCents: 2499, annualCents: 24999 },
    pcs: pro.max_enabled_pcs,
    phones: pro.max_controllers,
    screenView: "Unlimited",
    ai: { perWindow: { requests: 90, minutes: 45 }, perWeek: { requests: 300, minutes: 150 } },
    topUp: true,
    highlights: [{ text: "Everything in Pro" }, { text: `Up to ${pro.max_enabled_pcs} PCs and ${pro.max_controllers} phones` }, { text: `AI butler: ${aiSummary({ perWindow: { requests: 90, minutes: 45 }, perWeek: { requests: 300, minutes: 150 } })}` }, screenViewUnlimited, layoutsLine, topUpLine],
  },
];

export function planById(id: PlanId): PlanContent {
  const plan = PLAN_CONTENT.find((p) => p.id === id);
  if (!plan) throw new Error(`unknown plan ${id}`);
  return plan;
}

/** The plain-words rules of the AI butler's allowance, shared by the pricing page and the FAQ. */
export const AI_RULES = {
  request: "One request is one thing you ask that DoMe cannot work out on its own, so the AI interprets it and picks safe PC actions, always shown to you before anything runs. One request is roughly 30 seconds of use.",
  everyday: `Everyday commands DoMe understands by itself (“${EVERYDAY_EXAMPLES[0]}”, “${EVERYDAY_EXAMPLES[1]}”, “${EVERYDAY_EXAMPLES[2]}”) are free, unlimited and never count, on every plan, even after you reach an AI limit.`,
  windows: `The ${WINDOW_HOURS}-hour window starts with your first AI request; the weekly window refreshes ${WEEK_DAYS} days after the week’s first use. The app shows the exact refresh time (“Refreshes Saturday at 2 pm”) and warns you at ${WARN_AT_PERCENT} %.`,
  taste: `Free includes a one-time taste of ${FREE_TASTE_REQUESTS} AI requests, so you can try the butler before deciding. It does not renew.`,
  topUp: `On Pro and Pro Plus you can add ${TOP_UP.requests} AI requests for ${usd(TOP_UP.priceCents)} when you run out. You choose to buy a top-up; nothing is ever added or charged automatically.`,
  privacy: "The AI never sees your screen: no screenshots, no pictures. Nothing is recorded, and your speech and search words are not stored.",
  billing: "Prices are in US dollars, before tax. Billing runs through the DoMe web app (Stripe) when paid plans launch.",
} as const;

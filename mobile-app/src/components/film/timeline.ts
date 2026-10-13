/**
 * The demo film's timeline: six scenes on one clock (milliseconds from 0 to TOTAL_MS), each with a caption, a
 * status chip and a representative still. Everything on the stage is drawn from the current time alone, so a
 * frame at time t always looks the same: on the website, in the reduced-motion storyboard (one still per
 * scene), in QA stills (`/demo?scene=N&paused=1`) and in the exported video (`/demo?export=1`).
 *
 * Honesty (rollout brief §4): Screen view is "Coming", point-and-talk is "Planned", Pro and Pro Plus "arrive at
 * paid launch". The AI butler exists today. Nothing here claims recordings, screenshots or AI vision.
 */

import { AVAILABLE_NOW, COMING_AT_PAID_LAUNCH, FREE_SCREEN_VIEW_MINUTES, FREE_TASTE_REQUESTS, planById, POINT_AND_TALK_STATUS, SCREEN_VIEW_STATUS, TOP_UP, usd, WINDOW_HOURS } from "../../content/plans.ts";

export type StatusTone = "now" | "coming" | "planned" | "info";

/**
 * One beat inside a scene: a short line under the caption that says what is happening right now, so the film works
 * without sound and at phone size (the phone mock-up's own text is too small to read there). `speech` lines are
 * words someone says, drawn in quotes.
 */
export interface Beat {
  at: number;
  text: string;
  speech?: boolean;
}

export interface Scene {
  /** 1-based number, as the scene chips and `?scene=N` use it. */
  n: number;
  id: "room" | "ask" | "screen" | "point" | "plans" | "end";
  /** Short name on the scene chip. */
  title: string;
  /** Start and end on the film clock (ms). Scenes are contiguous. */
  start: number;
  end: number;
  /** The subtitle line, read out through the aria-live region. */
  caption: string;
  /** The small status chip under the caption. */
  status: { label: string; tone: StatusTone };
  /** A representative moment for stills (ms on the film clock). */
  still: number;
  /** What happens beat by beat (optional), in time order. */
  beats?: readonly Beat[];
}

/** The film clock's last millisecond: the end card holds until here, then the film stops. */
export const TOTAL_MS = 66_000;

export const SCENES: readonly Scene[] = [
  {
    n: 1,
    id: "room",
    title: "Across the room",
    start: 0,
    end: 8_000,
    caption: "Your PC, in your hand.",
    status: { label: "Remote control · Available now, free", tone: "now" },
    still: 7_100,
    beats: [{ at: 4_300, text: "Tap Pause on the phone: the PC pauses, and the phone says it’s done" }],
  },
  {
    n: 2,
    id: "ask",
    title: "Just ask",
    start: 8_000,
    end: 22_000,
    caption: "Just ask. The AI butler turns requests in your own words into safe, approved actions, and shows you each step.",
    status: { label: `AI butler · Available now · Free: ${FREE_TASTE_REQUESTS} to try · more with Pro and Pro Plus`, tone: "now" },
    still: 17_600,
    beats: [
      { at: 9_400, text: "Put on the next episode and make it full screen", speech: true },
      { at: 13_600, text: "DoMe: “Very good. Playing the next episode, full screen.”" },
      { at: 15_000, text: "Tap Run: the next episode plays, full screen" },
    ],
  },
  {
    n: 3,
    id: "screen",
    title: "Screen view",
    start: 22_000,
    end: 40_000,
    caption: "See your PC. Tap what you see.",
    status: { label: `Screen view · ${SCREEN_VIEW_STATUS} · Free: ${FREE_SCREEN_VIEW_MINUTES} min per ${WINDOW_HOURS} h · Pro: unlimited`, tone: "coming" },
    still: 36_500,
    beats: [
      { at: 22_600, text: "Your PC’s screen, live on the phone" },
      { at: 24_200, text: "Tap a button on your PC’s screen" },
      { at: 26_400, text: "Two fingers to scroll" },
      { at: 29_000, text: "Swipe away: it keeps playing in a corner" },
      { at: 30_800, text: "TV apps get arrow keys" },
      { at: 36_000, text: "Hear your PC on your phone, or mute it" },
    ],
  },
  {
    n: 4,
    id: "point",
    title: "Point-and-talk",
    start: 40_000,
    end: 50_000,
    caption: "Point-and-talk. No screenshots, ever\u00a0— the AI never sees your screen.",
    status: { label: `Point-and-talk · ${POINT_AND_TALK_STATUS}`, tone: "planned" },
    still: 46_400,
    beats: [
      { at: 41_100, text: "Rest a finger on a button" },
      { at: 42_600, text: "click that", speech: true },
      { at: 43_400, text: "Windows names the button: “Subscribe”" },
      { at: 44_200, text: "DoMe: “Clicking Subscribe.”" },
    ],
  },
  {
    n: 5,
    id: "plans",
    title: "Pick your plan",
    start: 50_000,
    end: 60_000,
    caption: "Free to start. Upgrade when it’s worth it.",
    status: { label: "Pro and Pro Plus arrive at paid launch", tone: "info" },
    still: 56_000,
  },
  {
    n: 6,
    id: "end",
    title: "DoMe Remote",
    start: 60_000,
    end: TOTAL_MS,
    caption: "Your PC, in your hand.",
    status: { label: "Free today · Pro and Pro Plus at paid launch", tone: "info" },
    still: 64_000,
  },
];

/** The scene showing at time t (the last one at and after TOTAL_MS). */
export function sceneAt(t: number): Scene {
  const ms = clamp(t, 0, TOTAL_MS);
  return SCENES.find((s) => ms < s.end) ?? SCENES[SCENES.length - 1]!;
}

/** The scene numbered n (1-based), or the first one for anything else. */
export function sceneByNumber(n: number): Scene {
  return SCENES.find((s) => s.n === n) ?? SCENES[0]!;
}

/** The beat showing at time t within its scene (the last one started), or null. */
export function beatAt(t: number): Beat | null {
  const scene = sceneAt(t);
  let found: Beat | null = null;
  for (const b of scene.beats ?? []) if (t >= b.at) found = b;
  return found;
}

/** One line on a plan card; `status` ("Coming") is drawn as a chip after it; `key` lines are the two the vertical card keeps. */
export interface PlanLine {
  text: string;
  status?: string;
  key?: boolean;
}

export interface PlanFact {
  id: "free" | "pro" | "proplus";
  name: string;
  /** "$0", "$9.99": the headline price. */
  price: string;
  /** After the price ("/ month"), empty on Free. */
  per: string;
  /** The line under the price: the annual price, or Free's "No credit card". */
  sub: string;
  status: string;
  tone: StatusTone;
  lines: PlanLine[];
}

const freePlan = planById("free");
const proPlan = planById("pro");
const plusPlan = planById("pro_plus");
/** "AI: 30 requests per 5 h, up to 120 a week" (the weekly part never breaks across lines). */
const aiLine = (perWindow: number | undefined, perWeek: number | undefined) => `AI: ${perWindow} requests per ${WINDOW_HOURS} h, up\u00a0to\u00a0${perWeek}\u00a0a\u00a0week`;
const topUpLine = `Top-up: ${TOP_UP.requests} requests for ${usd(TOP_UP.priceCents)}`;

/**
 * The plan facts shown in scene 5 and on /demo, read from the website's plan content (src/content/plans.ts) so the
 * film, the demo page and the pricing page never disagree. Screen view carries its "Coming" status on every card.
 */
export const PLAN_FACTS: readonly PlanFact[] = [
  {
    id: "free",
    name: freePlan.name,
    price: usd(0),
    per: "",
    sub: "No credit card",
    status: AVAILABLE_NOW,
    tone: "now",
    lines: [
      { text: `${freePlan.pcs} PC · ${freePlan.phones} phones` },
      { text: "Remote control: all of it", key: true },
      { text: `Screen view: ${FREE_SCREEN_VIEW_MINUTES} min per ${WINDOW_HOURS} h`, status: SCREEN_VIEW_STATUS },
      { text: `AI: ${FREE_TASTE_REQUESTS} requests to try, once`, key: true },
    ],
  },
  {
    id: "pro",
    name: proPlan.name,
    price: usd(proPlan.price!.monthlyCents),
    per: "/ month",
    sub: `or ${usd(proPlan.price!.annualCents)} / year`,
    status: COMING_AT_PAID_LAUNCH,
    tone: "info",
    lines: [
      { text: `${proPlan.pcs} PCs · ${proPlan.phones} phones` },
      { text: "Remote control: all of it" },
      { text: "Screen view: unlimited", status: SCREEN_VIEW_STATUS, key: true },
      { text: aiLine(proPlan.ai.perWindow?.requests, proPlan.ai.perWeek?.requests), key: true },
      { text: topUpLine },
    ],
  },
  {
    id: "proplus",
    name: plusPlan.name,
    price: usd(plusPlan.price!.monthlyCents),
    per: "/ month",
    sub: `or ${usd(plusPlan.price!.annualCents)} / year`,
    status: COMING_AT_PAID_LAUNCH,
    tone: "info",
    lines: [
      { text: `${plusPlan.pcs} PCs · ${plusPlan.phones} phones` },
      { text: "Remote control: all of it" },
      { text: "Screen view: unlimited", status: SCREEN_VIEW_STATUS, key: true },
      { text: aiLine(plusPlan.ai.perWindow?.requests, plusPlan.ai.perWeek?.requests), key: true },
      { text: topUpLine },
    ],
  },
];

/** The honest line under the film on the website. */
export const CONCEPT_NOTE = "Concept preview. Screen view and point-and-talk are in development.";

/* ── motion helpers (pure; the brand's easing, cubic-bezier(0.22, 1, 0.36, 1)) ───────────────────────────── */

export function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

export function lerp(a: number, b: number, p: number): number {
  return a + (b - a) * p;
}

/** A CSS cubic-bezier(x1, y1, x2, y2) timing function as a number function (0..1 → 0..1). */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): (p: number) => number {
  const ax = 1 - 3 * x2 + 3 * x1;
  const bx = 3 * x2 - 6 * x1;
  const cx = 3 * x1;
  const ay = 1 - 3 * y2 + 3 * y1;
  const by = 3 * y2 - 6 * y1;
  const cy = 3 * y1;
  const sampleX = (u: number) => ((ax * u + bx) * u + cx) * u;
  const sampleY = (u: number) => ((ay * u + by) * u + cy) * u;
  const slopeX = (u: number) => (3 * ax * u + 2 * bx) * u + cx;
  const solve = (x: number): number => {
    let u = x;
    for (let i = 0; i < 8; i++) {
      const err = sampleX(u) - x;
      if (Math.abs(err) < 1e-5) return u;
      const d = slopeX(u);
      if (Math.abs(d) < 1e-6) break;
      u -= err / d;
    }
    let lo = 0;
    let hi = 1;
    u = x;
    while (hi - lo > 1e-5) {
      u = (lo + hi) / 2;
      if (sampleX(u) < x) lo = u;
      else hi = u;
    }
    return u;
  };
  return (p: number) => (p <= 0 ? 0 : p >= 1 ? 1 : sampleY(solve(p)));
}

/** The design system's easing (styles.css `--dome-ease`). */
export const EASE = cubicBezier(0.22, 1, 0.36, 1);
/** A gentle symmetric ease for things that come and go. */
export const EASE_IN_OUT = cubicBezier(0.65, 0, 0.35, 1);
export const LINEAR = (p: number) => clamp(p, 0, 1);

/** Progress 0..1 of a movement starting at `start` and lasting `dur` ms, eased; 0 before, 1 after. */
export function seg(t: number, start: number, dur: number, ease: (p: number) => number = EASE): number {
  if (dur <= 0) return t >= start ? 1 : 0;
  return ease(clamp((t - start) / dur, 0, 1));
}

/** 1 while t is inside [start, end), fading in over `fade` ms at the start and out over `fade` ms before the end. */
export function window(t: number, start: number, end: number, fade = 320): number {
  if (t < start || t >= end) return 0;
  const inP = seg(t, start, fade, EASE_IN_OUT);
  const outP = 1 - seg(t, end - fade, fade, EASE_IN_OUT);
  return Math.min(inP, outP);
}

/** A press: 1 → 0.9 → 1 over `dur` ms from `start` (for buttons and fingertips). */
export function press(t: number, start: number, dur = 360): number {
  const p = clamp((t - start) / dur, 0, 1);
  return p === 0 || p === 1 ? 1 : 1 - 0.1 * Math.sin(p * Math.PI);
}

/** The part of `text` typed by time t, word by word from `start`, at `wps` words per second. */
export function typedWords(text: string, t: number, start: number, wps = 4): string {
  if (t < start) return "";
  const words = text.split(" ");
  const n = clamp(Math.floor(((t - start) / 1000) * wps) + 1, 0, words.length);
  return words.slice(0, n).join(" ");
}

/** Which of several stops is active at time t: the index of the last stop at or before t, or -1. */
export function stopIndex(t: number, stops: readonly number[]): number {
  let i = -1;
  for (let k = 0; k < stops.length; k++) if (t >= stops[k]!) i = k;
  return i;
}

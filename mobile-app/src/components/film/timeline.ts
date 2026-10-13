/**
 * The demo film's timeline: six scenes on one clock (milliseconds from 0 to TOTAL_MS), each with a caption, a
 * status chip and a representative still. Everything on the stage is drawn from the current time alone, so a
 * frame at time t always looks the same: on the website, in the reduced-motion storyboard (one still per
 * scene), in QA stills (`/demo?scene=N&paused=1`) and in the exported video (`/demo?export=1`).
 *
 * Honesty (rollout brief §4): Screen view is "Coming", point-and-talk is "Planned", Pro and Pro Plus "arrive at
 * paid launch". The AI butler exists today. Nothing here claims recordings, screenshots or AI vision.
 */

export type StatusTone = "now" | "coming" | "planned" | "info";

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
  },
  {
    n: 2,
    id: "ask",
    title: "Just ask",
    start: 8_000,
    end: 22_000,
    caption: "Just ask. The AI butler turns everyday requests into safe, approved actions — and always shows you first.",
    status: { label: "AI butler · Pro and Pro Plus (Free: 10 to try)", tone: "now" },
    still: 17_600,
  },
  {
    n: 3,
    id: "screen",
    title: "Screen view",
    start: 22_000,
    end: 40_000,
    caption: "See your PC. Tap what you see.",
    status: { label: "Screen view · Coming · Free: 10 min per 5 h · Pro: unlimited", tone: "coming" },
    still: 36_500,
  },
  {
    n: 4,
    id: "point",
    title: "Point and talk",
    start: 40_000,
    end: 50_000,
    caption: "Point and talk. No screenshots, ever — the AI never sees your screen.",
    status: { label: "Point and talk · Planned", tone: "planned" },
    still: 46_400,
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

/** The plan facts shown in scene 5 and on the end card (rollout brief §5; content only, not live metering). */
export const PLAN_FACTS = [
  {
    id: "free",
    name: "Free",
    price: "$0",
    per: "forever",
    status: "Available now",
    tone: "now" as StatusTone,
    lines: ["1 PC · 2 phones", "Remote control: all of it", "Screen view: 10 min per 5 h", "AI: 10 requests to try, once"],
  },
  {
    id: "pro",
    name: "Pro",
    price: "$9.99",
    per: "/ month · $99.99 / year",
    status: "Coming at paid launch",
    tone: "info" as StatusTone,
    lines: ["5 PCs · 5 phones", "Remote control: all of it", "Screen view: unlimited", "AI: 30 requests per 5 h, 120 a week", "Top-up: 100 requests for $2.99"],
  },
  {
    id: "proplus",
    name: "Pro Plus",
    price: "$24.99",
    per: "/ month · $249.99 / year",
    status: "Coming at paid launch",
    tone: "info" as StatusTone,
    lines: ["5 PCs · 5 phones", "Remote control: all of it", "Screen view: unlimited", "AI: 90 requests per 5 h, 300 a week", "Top-up: 100 requests for $2.99"],
  },
] as const;

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

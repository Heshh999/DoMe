/**
 * The film's script: what every prop looks like at time t (ms on the film clock), derived purely from t with the
 * helpers in timeline.ts. Scene components read these and draw; nothing keeps state between frames, which is what
 * makes stills, the storyboard and the frame-by-frame export exact.
 *
 * Beats (see timeline.ts for the scenes):
 *  1 Across the room   0–8 s     the lamp comes on, the PC wakes, the phone rises; a tap on Pause reaches the PC.
 *  2 Just ask          8–22 s    Ask → "Put on the next episode and make it full screen" → plan shown first → Run.
 *  3 Screen view       22–40 s   the PC's screen inside the phone: tap, two-finger scroll, pinch to zoom (on the
 *                                phone only), tap an episode, 1× back to the whole screen. Only what Screen view's
 *                                first version has: no mini view, no PC sound, no TV-style remote (none is built).
 *  4 Point and talk    40–50 s   a finger rests on Subscribe, "click that", the element's name lights up.
 *  5 Pick your plan    50–60 s   Free / Pro / Pro Plus.
 *  6 End card          60–66 s   the lockup.
 */
import { PC_SCREEN } from "./layout.ts";
import { clamp, EASE_IN_OUT, lerp, press, seg, stopIndex, typedWords, window as win } from "./timeline.ts";

/* ── what the PC shows ──────────────────────────────────────────────────────────────────────────────── */

/** The (fictional) series playing on the PC. */
export const EPISODES = {
  3: { title: "Night Drive — Episode 3: Harbor Lights", short: "Episode 3", length: "31:40" },
  4: { title: "Night Drive — Episode 4: First Light", short: "Episode 4", length: "29:12" },
  5: { title: "Night Drive — Episode 5: Tunnel", short: "Episode 5", length: "27:05" },
} as const;

/** Scene 3: the tap on Episode 5's poster (in the zoomed picture) reaches the PC, which plays it. */
const EPISODE_5_AT = 32_500;

export interface PcView {
  /** Screen brightness 0..1 (the monitor lights up in scene 1). */
  on: number;
  /** The browser's video player fills the screen. */
  fullscreen: number;
  /** Which episode plays (3 → 4 after "the next episode", 4 → 5 after the tap on its poster in Screen view). */
  episode: 3 | 4 | 5;
  /** Playback position 0..1 of the current episode. */
  position: number;
  paused: boolean;
  /** The pause glyph that flashes when playback stops (0..1). */
  pauseFlash: number;
  /** The full-screen player's title and control bar (shown while paused). */
  controls: number;
  /** The "More episodes" shelf raised by the two-finger scroll (0..1). */
  shelf: number;
  /** Point-and-talk: the Subscribe button's highlight ring (0..1) and whether it has been clicked. */
  highlight: number;
  subscribed: boolean;
}

/** Position 0..1 within the episode: 38 % in when the film starts, advancing while playing. */
function videoPosition(t: number): number {
  const RATE = 1 / 1_860_000; // one 31-minute episode
  if (t < 5_900) return 0.38 + t * RATE; // episode 3 plays, paused by the tap at 5.9 s
  if (t < 15_800) return 0.38 + 5_900 * RATE;
  if (t < 25_300) return (t - 15_800) * RATE * 40; // episode 4 from the start (sped up so the bar visibly moves)
  if (t < EPISODE_5_AT) return 9_500 * RATE * 40; // paused by the tap in Screen view
  return (t - EPISODE_5_AT) * RATE * 40; // episode 5 from the start
}

export function pcAt(t: number): PcView {
  return {
    on: seg(t, 900, 1_100),
    fullscreen: t >= 40_000 ? 0 : seg(t, 16_600, 700) * (1 - seg(t, 39_800, 300)),
    episode: t >= EPISODE_5_AT ? 5 : t >= 15_800 ? 4 : 3,
    position: videoPosition(t),
    paused: (t >= 5_900 && t < 15_800) || (t >= 25_300 && t < EPISODE_5_AT),
    pauseFlash: win(t, 5_900, 7_200, 260) || win(t, 25_300, 26_800, 260),
    controls: t >= 25_300 && t < EPISODE_5_AT ? seg(t, 25_300, 400) : 0,
    shelf: t < EPISODE_5_AT ? seg(t, 27_200, 900) : 0,
    highlight: t >= 43_400 && t < 50_000 ? seg(t, 43_400, 500) : 0,
    subscribed: t >= 45_200,
  };
}

/* ── what the phone shows ───────────────────────────────────────────────────────────────────────────── */

export type PhonePage = "home" | "ask" | "screen";

export interface PhoneView {
  /** Entrance 0..1 (rises in during scene 1). */
  entrance: number;
  page: PhonePage;
  /** Cross-fade progress into `page` (1 = settled). */
  pageIn: number;
  /** Home: the Pause button pressed (scale), the "Done" toast. */
  pausePress: number;
  toast: number;
  /** Ask: the talk button's state and the conversation. */
  listening: boolean;
  thinking: boolean;
  micPress: number;
  ownerText: string;
  butlerText: string;
  plan: null | { steps: Array<{ label: string; done: boolean }>; runPress: number; running: boolean };
  /**
   * Screen view: the mirror's arrival (0..1); the pinch zoom, on the phone only (1 = the whole screen, up to
   * MIRROR.zoomMax); the 1× button's press, which brings back the whole screen.
   */
  mirror: number;
  zoom: number;
  oneXPress: number;
  /** Point and talk: the hold ring under the resting finger, the spoken words, the name tag. */
  hold: number;
  spoken: string;
  nameTag: number;
  clickingText: string;
}

const ASK_TEXT = "Put on the next episode and make it full screen";
const BUTLER_TEXT = "Very good. Playing the next episode, full screen.";

/** Scene 3: the pinch zooms in from ZOOM_IN_AT; the tap on 1× (ONE_X_AT) zooms back out to the whole screen. */
const ZOOM_IN_AT = 29_500;
const ONE_X_AT = 34_800;

export function phoneAt(t: number): PhoneView {
  const page: PhonePage = t < 8_000 ? "home" : t < 22_000 ? "ask" : "screen";
  const pageStart = t < 8_000 ? 0 : t < 22_000 ? 8_000 : 22_000;
  const listening = t >= 9_400 && t < 12_800;
  const planShown = t >= 13_900 && t < 22_000;
  return {
    entrance: seg(t, 1_800, 1_500),
    page,
    pageIn: seg(t, pageStart, 520),
    pausePress: press(t, 5_000),
    toast: t < 8_000 ? seg(t, 6_300, 500) : 0,
    listening,
    thinking: t >= 12_800 && t < 13_600,
    micPress: press(t, 9_400),
    ownerText: t < 22_000 ? typedWords(ASK_TEXT, t, 9_700, 3.4) : "",
    butlerText: t < 22_000 ? typedWords(BUTLER_TEXT, t, 13_600, 8) : "",
    plan: planShown
      ? {
          steps: [
            { label: "Next video", done: t >= 15_800 },
            { label: "Full screen", done: t >= 17_000 },
          ],
          runPress: press(t, 15_000),
          running: t >= 15_000,
        }
      : null,
    mirror: t >= 22_000 ? seg(t, 22_600, 1_000) : 0,
    zoom: 1 + (MIRROR.zoomMax - 1) * seg(t, ZOOM_IN_AT, 1_100, EASE_IN_OUT) * (1 - seg(t, ONE_X_AT + 100, 700, EASE_IN_OUT)),
    oneXPress: press(t, ONE_X_AT),
    hold: t >= 41_800 && t < 46_000 ? seg(t, 41_800, 800) : 0,
    spoken: t >= 40_000 && t < 50_000 ? typedWords("click that", t, 42_600, 4) : "",
    nameTag: t >= 43_400 && t < 50_000 ? seg(t, 43_400, 500) : 0,
    clickingText: t >= 40_000 && t < 50_000 ? typedWords("Clicking Subscribe.", t, 44_200, 6) : "",
  };
}

/* ── the hand (a translucent fingertip), in phone-screen coordinates (390 × 844) ───────────────────────── */

export interface HandView {
  /** 0 = hidden. */
  visible: number;
  x: number;
  y: number;
  /** The tap ripple (0..1). */
  ripple: number;
  /** Two fingertips this far apart, centred on (x, y), for the two-finger scroll and the pinch; 0 = one fingertip. */
  pair: number;
}

interface Stop {
  at: number;
  x: number;
  y: number;
}

/** Where the fingertip is on a path of stops (it glides between them with the brand easing). */
function along(t: number, stops: readonly Stop[], travel = 600): { x: number; y: number } {
  const i = stopIndex(t, stops.map((s) => s.at));
  if (i < 0) return { x: stops[0]!.x, y: stops[0]!.y };
  const from = stops[i]!;
  const to = stops[i + 1];
  if (!to) return { x: from.x, y: from.y };
  const p = seg(t, to.at - travel, travel);
  return { x: lerp(from.x, to.x, p), y: lerp(from.y, to.y, p) };
}

/**
 * The mirror (the PC's screen inside the phone) in phone-screen px. Screen view's pinch zooms the picture on the phone
 * only (nothing is sent to the PC), here up to `zoomMax` around `zoomOrigin` (mirror px, near the shelf's first
 * poster); while zoomed, the 1× button (`oneX`, mirror px, bottom left as in the app) brings back the whole screen.
 */
export const MIRROR = {
  full: { x: 16, y: 120, w: 358, h: 201 },
  zoomOrigin: { x: 20, y: 190 },
  zoomMax: 2,
  oneX: { x: 10, y: 161, size: 30 },
} as const;

/** A point of the PC's screen (1280 × 720 px) where the phone shows it inside the mirror at zoom `z`, phone-screen px. */
function inMirror(pc: { x: number; y: number }, z = 1): { x: number; y: number } {
  const s = MIRROR.full.w / PC_SCREEN.w;
  const o = MIRROR.zoomOrigin;
  return { x: MIRROR.full.x + o.x + z * (pc.x * s - o.x), y: MIRROR.full.y + o.y + z * (pc.y * s - o.y) };
}

/** The centre of the "More episodes" shelf's first poster, Episode 5 (Pc.tsx: 280 × 158 from x 40, y 530), PC px. */
const EPISODE_5_POSTER = { x: 180, y: 609 } as const;

/** Phone-screen coordinates of the things the finger touches (keep in step with Phone.tsx). */
export const TOUCH = {
  pause: { x: 195, y: 400 },
  mic: { x: 195, y: 688 },
  run: { x: 140, y: 448 },
  mirrorCentre: { x: 195, y: 220 },
  mirrorScroll: { x: 170, y: 240 },
  pinch: { x: 110, y: 280 },
  poster: inMirror(EPISODE_5_POSTER, MIRROR.zoomMax),
  oneX: { x: MIRROR.full.x + MIRROR.oneX.x + MIRROR.oneX.size / 2, y: MIRROR.full.y + MIRROR.oneX.y + MIRROR.oneX.size / 2 },
  subscribe: { x: 104, y: 297 },
} as const;

export function handAt(t: number): HandView {
  const hidden: HandView = { visible: 0, x: 0, y: 0, ripple: 0, pair: 0 };
  // scene 1: Pause
  if (t >= 4_300 && t < 6_000) {
    const p = along(t, [{ at: 4_300, x: 240, y: 520 }, { at: 5_000, ...TOUCH.pause }], 700);
    return { visible: win(t, 4_300, 6_000, 250), ...p, ripple: seg(t, 5_000, 600), pair: 0 };
  }
  // scene 2: the mic, then Run
  if (t >= 8_800 && t < 10_400) {
    const p = along(t, [{ at: 8_800, x: 300, y: 790 }, { at: 9_400, ...TOUCH.mic }], 600);
    return { visible: win(t, 8_800, 10_400, 250), ...p, ripple: seg(t, 9_400, 600), pair: 0 };
  }
  if (t >= 14_300 && t < 15_900) {
    const p = along(t, [{ at: 14_300, x: 300, y: 700 }, { at: 15_000, ...TOUCH.run }], 700);
    return { visible: win(t, 14_300, 15_900, 250), ...p, ripple: seg(t, 15_000, 600), pair: 0 };
  }
  // scene 3: tap the mirror, two-finger scroll, pinch to zoom, tap Episode 5's poster, tap 1×
  if (t >= 24_200 && t < 25_900) {
    const p = along(t, [{ at: 24_200, x: 300, y: 420 }, { at: 24_800, ...TOUCH.mirrorCentre }], 600);
    return { visible: win(t, 24_200, 25_900, 250), ...p, ripple: seg(t, 24_800, 600), pair: 0 };
  }
  if (t >= 26_400 && t < 28_600) {
    const drag = seg(t, 26_900, 1_300, EASE_IN_OUT);
    return { visible: win(t, 26_400, 28_600, 300), x: TOUCH.mirrorScroll.x + 26, y: TOUCH.mirrorScroll.y - drag * 70, ripple: 0, pair: 52 };
  }
  if (t >= 29_000 && t < 31_000) {
    // two fingertips spread apart while the picture zooms in under them
    const spread = seg(t, ZOOM_IN_AT, 1_100, EASE_IN_OUT);
    return { visible: win(t, 29_000, 31_000, 300), ...TOUCH.pinch, ripple: 0, pair: lerp(60, 150, spread) };
  }
  if (t >= 31_400 && t < 33_000) {
    const p = along(t, [{ at: 31_400, x: 300, y: 420 }, { at: 32_000, ...TOUCH.poster }], 600);
    return { visible: win(t, 31_400, 33_000, 250), ...p, ripple: seg(t, 32_000, 600), pair: 0 };
  }
  if (t >= 34_200 && t < 35_700) {
    const p = along(t, [{ at: 34_200, x: 260, y: 440 }, { at: ONE_X_AT, ...TOUCH.oneX }], 600);
    return { visible: win(t, 34_200, 35_700, 250), ...p, ripple: seg(t, ONE_X_AT, 600), pair: 0 };
  }
  // scene 4: rest on Subscribe
  if (t >= 41_100 && t < 46_200) {
    const p = along(t, [{ at: 41_100, x: 300, y: 460 }, { at: 41_800, ...TOUCH.subscribe }], 700);
    return { visible: win(t, 41_100, 46_200, 300), ...p, ripple: 0, pair: 0 };
  }
  return hidden;
}

/* ── the link between phone and PC: a pulse travels when a command is sent ──────────────────────────────── */

export interface LinkView {
  /** Visibility of the link path 0..1. */
  visible: number;
  /** The pulse's progress along the path 0..1 (phone → PC). */
  pulse: number;
}

const PULSES: ReadonlyArray<[number, number]> = [
  [5_100, 5_900],
  [15_100, 15_800],
  [24_850, 25_300],
  // the tap on Episode 5's poster; the pinch and 1× stay on the phone, so nothing travels for them
  [32_050, EPISODE_5_AT],
  [44_600, 45_200],
];

export function linkAt(t: number): LinkView {
  for (const [a, b] of PULSES) {
    if (t >= a - 150 && t < b + 250) return { visible: win(t, a - 150, b + 250, 150), pulse: clamp((t - a) / (b - a), 0, 1) };
  }
  return { visible: 0, pulse: 0 };
}

/* ── stage-level overlays: the dusk room, the plan cards and the end card ──────────────────────────────── */

export interface StageView {
  /** The room is on stage from the first frame (an export must never start blank); this only dims it for the veil. */
  room: number;
  /** The reading lamp switching on (0..1), the first thing that happens. */
  lamp: number;
  /** The dimming veil behind the plan cards and the end card (0..1). */
  veil: number;
  /** Scene 5: card entrances (one per plan), the Pro glow and the footnote. */
  cards: [number, number, number];
  proGlow: number;
  footnote: number;
  /** Scene 6: the lockup, tagline, buttons and the concept line. */
  lockup: number;
  tagline: number;
  buttons: number;
  concept: number;
  /** The devices' presence (they recede behind the plan cards). */
  devices: number;
}

export function stageAt(t: number): StageView {
  const inPlans = t >= 50_000 && t < 60_000;
  const fadeCards = 1 - seg(t, 59_700, 300);
  return {
    room: 1,
    lamp: seg(t, 250, 800),
    // fully opaque once in: the room and devices fade out under it, so nothing shows through the plan cards
    veil: t < 50_000 ? 0 : seg(t, 50_000, 800),
    cards: inPlans ? [seg(t, 50_400, 800) * fadeCards, seg(t, 50_750, 800) * fadeCards, seg(t, 51_100, 800) * fadeCards] : [0, 0, 0],
    proGlow: inPlans ? seg(t, 51_800, 900) * fadeCards : 0,
    footnote: inPlans ? seg(t, 53_000, 600) * fadeCards : 0,
    lockup: t >= 60_000 ? seg(t, 60_400, 1_200) : 0,
    tagline: t >= 60_000 ? seg(t, 61_400, 700) : 0,
    buttons: t >= 60_000 ? seg(t, 62_200, 700) : 0,
    concept: t >= 60_000 ? seg(t, 63_000, 600) : 0,
    devices: t < 50_000 ? 1 : 1 - seg(t, 50_000, 900),
  };
}

/**
 * The film's script: what every prop looks like at time t (ms on the film clock), derived purely from t with the
 * helpers in timeline.ts. Scene components read these and draw; nothing keeps state between frames, which is what
 * makes stills, the storyboard and the frame-by-frame export exact.
 *
 * Beats (see timeline.ts for the scenes):
 *  1 Across the room   0–8 s     the lamp comes on, the PC wakes, the phone rises; a tap on Pause reaches the PC.
 *  2 Just ask          8–22 s    Ask → "Put on the next episode and make it full screen" → plan shown first → Run.
 *  3 Screen view       22–40 s   the PC's screen inside the phone: tap, two-finger scroll, swipe away (mini-player),
 *                                open a TV app with a D-pad, PC audio on the phone with mute.
 *  4 Point and talk    40–50 s   a finger rests on Subscribe, "click that", the element's name lights up.
 *  5 Pick your plan    50–60 s   Free / Pro / Pro Plus.
 *  6 End card          60–66 s   the lockup.
 */
import { clamp, EASE_IN_OUT, lerp, press, seg, stopIndex, typedWords, window as win } from "./timeline.ts";

/* ── what the PC shows ──────────────────────────────────────────────────────────────────────────────── */

/** The (fictional) series playing on the PC. */
export const EPISODES = {
  3: { title: "Night Drive — Episode 3: Harbor Lights", short: "Episode 3", length: "31:40" },
  4: { title: "Night Drive — Episode 4: First Light", short: "Episode 4", length: "29:12" },
} as const;

export type PcApp = "browser" | "tv";

export interface PcView {
  /** Screen brightness 0..1 (the monitor lights up in scene 1). */
  on: number;
  app: PcApp;
  /** The browser's video player fills the screen. */
  fullscreen: number;
  /** Which episode plays (3 → 4 after "the next episode"). */
  episode: 3 | 4;
  /** Playback position 0..1 of the current episode. */
  position: number;
  paused: boolean;
  /** The pause glyph that flashes when playback stops (0..1). */
  pauseFlash: number;
  /** The full-screen player's title and control bar (shown while paused). */
  controls: number;
  /** The "More episodes" shelf raised by the two-finger scroll (0..1). */
  shelf: number;
  /** The TV app's focused poster (0-based). */
  focus: number;
  /** Point-and-talk: the Subscribe button's highlight ring (0..1) and whether it has been clicked. */
  highlight: number;
  subscribed: boolean;
  /** Windows' own volume flyout: "muted" shown briefly when the phone mutes PC audio (0..1). */
  muteOsd: number;
}

/** Position 0..1 within the episode: 38 % in when the film starts, advancing while playing. */
function videoPosition(t: number): number {
  const RATE = 1 / 1_860_000; // one 31-minute episode
  if (t < 5_900) return 0.38 + t * RATE; // episode 3 plays, paused by the tap at 5.9 s
  if (t < 15_800) return 0.38 + 5_900 * RATE;
  if (t < 25_300) return (t - 15_800) * RATE * 40; // episode 4 from the start (sped up so the bar visibly moves)
  return 9_500 * RATE * 40;
}

export function pcAt(t: number): PcView {
  const nextEpisode = t >= 15_800;
  const inTv = t >= 32_200 && t < 40_000;
  return {
    on: seg(t, 900, 1_100),
    app: inTv ? "tv" : "browser",
    fullscreen: t >= 40_000 ? 0 : seg(t, 16_600, 700) * (1 - seg(t, 39_800, 300)),
    episode: nextEpisode ? 4 : 3,
    position: videoPosition(t),
    paused: (t >= 5_900 && t < 15_800) || t >= 25_300,
    pauseFlash: win(t, 5_900, 7_200, 260) || win(t, 25_300, 26_800, 260),
    controls: t >= 25_300 && t < 32_200 ? seg(t, 25_300, 400) : 0,
    shelf: t < 32_200 ? seg(t, 27_200, 900) : 0,
    focus: clamp(stopIndex(t, [0, 34_000, 35_000]), 0, 2),
    highlight: t >= 43_400 && t < 50_000 ? seg(t, 43_400, 500) : 0,
    subscribed: t >= 45_200,
    muteOsd: win(t, 37_700, 39_400, 250),
  };
}

/* ── what the phone shows ───────────────────────────────────────────────────────────────────────────── */

export type PhonePage = "home" | "ask" | "screen" | "apps";

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
  /** Screen view: the mirror's arrival (0..1), the tap ripple, the mini-player state (0 = full, 1 = corner). */
  mirror: number;
  mini: number;
  shelfTouch: number;
  /** Apps: the swipe-in of the page, the ZStream press, the TV remote panel (0..1) and the pressed D-pad key. */
  zPress: number;
  tvRemote: number;
  dpadPress: "right" | null;
  /** The PC-audio chip (0..1), whether muted, and the Mute press. */
  sound: number;
  muted: boolean;
  mutePress: number;
  /** Point and talk: the hold ring under the resting finger, the spoken words, the name tag. */
  hold: number;
  spoken: string;
  nameTag: number;
  clickingText: string;
}

const ASK_TEXT = "Put on the next episode and make it full screen";
const BUTLER_TEXT = "Very good. Playing the next episode, full screen.";

export function phoneAt(t: number): PhoneView {
  const page: PhonePage = t < 8_000 ? "home" : t < 22_000 ? "ask" : t < 29_600 ? "screen" : t < 40_000 ? "apps" : "screen";
  const pageStart = t < 8_000 ? 0 : t < 22_000 ? 8_000 : t < 29_600 ? 22_000 : t < 40_000 ? 29_600 : 40_000;
  const listening = t >= 9_400 && t < 12_800;
  const planShown = t >= 13_900 && t < 22_000;
  return {
    entrance: seg(t, 1_800, 1_500),
    page,
    pageIn: seg(t, pageStart, pageStart === 29_600 ? 650 : 520),
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
    mini: t >= 40_000 ? 0 : seg(t, 29_600, 1_000),
    shelfTouch: win(t, 26_600, 28_400, 300),
    zPress: press(t, 31_400),
    tvRemote: t >= 32_600 && t < 40_000 ? seg(t, 32_600, 700) : 0,
    dpadPress: (t >= 33_800 && t < 34_150) || (t >= 34_800 && t < 35_150) ? "right" : null,
    sound: t >= 36_000 && t < 40_000 ? seg(t, 36_000, 600) : 0,
    muted: t >= 37_700,
    mutePress: press(t, 37_400),
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
  /** The tap ripple (0..1), and a second fingertip for the two-finger scroll. */
  ripple: number;
  second: boolean;
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

/** Phone-screen coordinates of the things the finger touches (keep in step with Phone.tsx). */
export const TOUCH = {
  pause: { x: 195, y: 400 },
  mic: { x: 195, y: 688 },
  run: { x: 140, y: 448 },
  mirrorCentre: { x: 195, y: 220 },
  mirrorScroll: { x: 170, y: 240 },
  swipeFrom: { x: 340, y: 470 },
  swipeTo: { x: 60, y: 470 },
  zstream: { x: 324, y: 158 },
  dpadRight: { x: 245, y: 504 },
  mute: { x: 311, y: 636 },
  subscribe: { x: 104, y: 297 },
} as const;

/** The mirror (the PC's screen inside the phone) at full size and as the corner mini-player, phone-screen px. */
export const MIRROR = { full: { x: 16, y: 120, w: 358, h: 201 }, mini: { x: 214, y: 676, w: 160, h: 90 } } as const;

export function handAt(t: number): HandView {
  const hidden: HandView = { visible: 0, x: 0, y: 0, ripple: 0, second: false };
  // scene 1: Pause
  if (t >= 4_300 && t < 6_000) {
    const p = along(t, [{ at: 4_300, x: 240, y: 520 }, { at: 5_000, ...TOUCH.pause }], 700);
    return { visible: win(t, 4_300, 6_000, 250), ...p, ripple: seg(t, 5_000, 600), second: false };
  }
  // scene 2: the mic, then Run
  if (t >= 8_800 && t < 10_400) {
    const p = along(t, [{ at: 8_800, x: 300, y: 790 }, { at: 9_400, ...TOUCH.mic }], 600);
    return { visible: win(t, 8_800, 10_400, 250), ...p, ripple: seg(t, 9_400, 600), second: false };
  }
  if (t >= 14_300 && t < 15_900) {
    const p = along(t, [{ at: 14_300, x: 300, y: 700 }, { at: 15_000, ...TOUCH.run }], 700);
    return { visible: win(t, 14_300, 15_900, 250), ...p, ripple: seg(t, 15_000, 600), second: false };
  }
  // scene 3: tap the mirror, two-finger scroll, swipe, ZStream, D-pad, Mute
  if (t >= 24_200 && t < 25_900) {
    const p = along(t, [{ at: 24_200, x: 300, y: 420 }, { at: 24_800, ...TOUCH.mirrorCentre }], 600);
    return { visible: win(t, 24_200, 25_900, 250), ...p, ripple: seg(t, 24_800, 600), second: false };
  }
  if (t >= 26_400 && t < 28_600) {
    const drag = seg(t, 26_900, 1_300, EASE_IN_OUT);
    return { visible: win(t, 26_400, 28_600, 300), x: TOUCH.mirrorScroll.x, y: TOUCH.mirrorScroll.y - drag * 70, ripple: 0, second: true };
  }
  if (t >= 29_000 && t < 30_500) {
    const p = along(t, [{ at: 29_000, ...TOUCH.swipeFrom }, { at: 30_100, ...TOUCH.swipeTo }], 800);
    return { visible: win(t, 29_000, 30_500, 250), ...p, ripple: 0, second: false };
  }
  if (t >= 30_800 && t < 32_300) {
    const p = along(t, [{ at: 30_800, x: 300, y: 420 }, { at: 31_400, ...TOUCH.zstream }], 600);
    return { visible: win(t, 30_800, 32_300, 250), ...p, ripple: seg(t, 31_400, 600), second: false };
  }
  if (t >= 33_200 && t < 35_800) {
    const p = along(t, [{ at: 33_200, x: 300, y: 760 }, { at: 33_800, ...TOUCH.dpadRight }], 600);
    const ripple = t < 34_800 ? seg(t, 33_800, 600) : seg(t, 34_800, 600);
    return { visible: win(t, 33_200, 35_800, 250), ...p, ripple, second: false };
  }
  if (t >= 36_800 && t < 38_600) {
    const p = along(t, [{ at: 36_800, x: 330, y: 700 }, { at: 37_400, ...TOUCH.mute }], 600);
    return { visible: win(t, 36_800, 38_600, 250), ...p, ripple: seg(t, 37_400, 600), second: false };
  }
  // scene 4: rest on Subscribe
  if (t >= 41_100 && t < 46_200) {
    const p = along(t, [{ at: 41_100, x: 300, y: 460 }, { at: 41_800, ...TOUCH.subscribe }], 700);
    return { visible: win(t, 41_100, 46_200, 300), ...p, ripple: 0, second: false };
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
  [31_500, 32_200],
  [33_850, 34_000],
  [34_850, 35_000],
  [37_450, 37_700],
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

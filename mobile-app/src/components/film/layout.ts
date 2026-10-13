/**
 * Where things sit on the stage. The stage is drawn on a fixed canvas (1600 × 900 "wide", 900 × 1600 "vertical")
 * and scaled to its container with one CSS transform, so every number here is a canvas pixel. The phone's screen
 * is authored at the real app's size (390 × 844) and scaled into its body; the PC's screen at 1280 × 720.
 */
export type FilmLayout = "wide" | "vertical";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface StageLayout {
  name: FilmLayout;
  w: number;
  h: number;
  /** The monitor's screen (16:9). */
  pc: Rect;
  /** The phone's body; the screen sits inside the bezel. */
  phone: Rect;
  bezel: number;
  /** Scale from the 390-wide phone screen to the body's screen. */
  phoneScale: number;
  /** The caption band (bottom-aligned inside the rect): the caption, the current beat and the status chip. */
  caption: Rect & { size: number; align: "left" | "center" };
  /** Scene 5's three plan cards. */
  cards: Rect[];
  /** Where the end card's lockup is centred and how wide it is. */
  lockup: { cx: number; cy: number; w: number };
}

export const PHONE_SCREEN = { w: 390, h: 844 } as const;
export const PC_SCREEN = { w: 1280, h: 720 } as const;

function phoneBody(x: number, y: number, w: number, bezel: number): { phone: Rect; bezel: number; phoneScale: number } {
  const scale = (w - bezel * 2) / PHONE_SCREEN.w;
  return { phone: { x, y, w, h: PHONE_SCREEN.h * scale + bezel * 2 }, bezel, phoneScale: scale };
}

export const WIDE: StageLayout = {
  name: "wide",
  w: 1600,
  h: 900,
  pc: { x: 150, y: 112, w: 840, h: 472.5 },
  ...phoneBody(1196, 78, 300, 9),
  caption: { x: 150, y: 676, w: 1000, h: 206, size: 40, align: "left" },
  cards: [
    { x: 136, y: 100, w: 400, h: 520 },
    { x: 600, y: 100, w: 400, h: 520 },
    { x: 1064, y: 100, w: 400, h: 520 },
  ],
  lockup: { cx: 800, cy: 330, w: 620 },
};

/**
 * 9:16: the monitor at the top, the phone large in the foreground (its top overlaps the monitor's stand, as a phone
 * held in front of the PC would), and a tall caption band under it. On a 390 px phone the canvas is scaled to about
 * 0.4, so everything that must be read lives in the caption band: the caption, the beat line and the status chip.
 */
export const VERTICAL: StageLayout = {
  name: "vertical",
  w: 900,
  h: 1600,
  pc: { x: 80, y: 60, w: 740, h: 416.25 },
  ...phoneBody(270, 440, 360, 10),
  caption: { x: 40, y: 1206, w: 820, h: 380, size: 40, align: "center" },
  cards: [
    { x: 70, y: 150, w: 760, h: 300 },
    { x: 70, y: 480, w: 760, h: 300 },
    { x: 70, y: 810, w: 760, h: 300 },
  ],
  lockup: { cx: 450, cy: 640, w: 560 },
};

/** The end card's lockup height (the full DoMe REMOTE lockup is 1345 × 408). */
export function lockupHeight(L: StageLayout): number {
  return L.lockup.w * (408 / 1345);
}

/**
 * Where the end card's lines sit (canvas y of each line's top): the tagline, the calls to action and the concept
 * note. Vertical puts the note above the buttons, so buttons that wrap onto two rows on a phone never cover it.
 */
export function endCardRows(L: StageLayout): { tagline: number; actions: number; concept: number } {
  const below = L.lockup.cy + lockupHeight(L) / 2;
  return L.name === "vertical" ? { tagline: below + 50, concept: below + 150, actions: below + 280 } : { tagline: below + 50, actions: below + 150, concept: below + 250 };
}

export function layoutFor(name: FilmLayout): StageLayout {
  return name === "vertical" ? VERTICAL : WIDE;
}

/** Below this container width the wide stage would be too small to read: restack vertically. */
export const VERTICAL_BELOW_PX = 600;

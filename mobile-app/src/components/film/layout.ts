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
  /** The caption band. */
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
  caption: { x: 150, y: 716, w: 960, h: 170, size: 40, align: "left" },
  cards: [
    { x: 136, y: 120, w: 400, h: 490 },
    { x: 600, y: 120, w: 400, h: 490 },
    { x: 1064, y: 120, w: 400, h: 490 },
  ],
  lockup: { cx: 800, cy: 330, w: 620 },
};

export const VERTICAL: StageLayout = {
  name: "vertical",
  w: 900,
  h: 1600,
  pc: { x: 70, y: 90, w: 760, h: 427.5 },
  ...phoneBody(298, 596, 304, 10),
  caption: { x: 60, y: 1250, w: 780, h: 330, size: 44, align: "center" },
  cards: [
    { x: 70, y: 110, w: 760, h: 330 },
    { x: 70, y: 470, w: 760, h: 330 },
    { x: 70, y: 830, w: 760, h: 330 },
  ],
  lockup: { cx: 450, cy: 560, w: 560 },
};

export function layoutFor(name: FilmLayout): StageLayout {
  return name === "vertical" ? VERTICAL : WIDE;
}

/** Below this container width the wide stage would be too small to read: restack vertically. */
export const VERTICAL_BELOW_PX = 600;

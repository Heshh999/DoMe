/**
 * The phone's own art for Windows' system pointers (protocol 1.5 `cursor_shape` named kinds, rules.screen_cursor;
 * design screen-view-2a §4.4, D17). The PC names the shape (IDC_ARROW → "arrow", …) and the phone draws it crisply at
 * any zoom instead of receiving a bitmap.
 *
 * Every glyph lives in a 24-unit box drawn at 24 CSS px (one unit is one CSS px): white shapes with a 1 px black edge
 * (a `black` path would be a detail drawn on top) and a soft shadow added by the overlay. `hotspot` is the point of
 * the glyph that sits exactly on the PC's pointer position (the tip of the arrow, the centre of the I-beam or the
 * cross). `wait` spins as a whole and `appstarting` spins only its small ring, once a second.
 *
 * Pure data: no DOM, no pixels.
 */
import type { CursorKind } from "@dome/protocol";

/** The box every glyph is drawn in (and its size in CSS px). */
export const CURSOR_GLYPH_SIZE = 24;
/** One turn of the busy ring. */
export const CURSOR_SPIN_MS = 1000;

export interface CursorGlyphPath {
  d: string;
  fill: "white" | "black";
  /** Inner shapes of this path are holes (the "no" sign's two openings). */
  evenOdd?: boolean;
  /** Spins about `spinAt` (the busy ring of `appstarting`); the rest of the glyph stays still. */
  spin?: boolean;
}

export interface CursorGlyph {
  paths: CursorGlyphPath[];
  hotspot: { x: number; y: number };
  /** The whole glyph spins about its hotspot (`wait`). */
  animate?: "spin";
  /** Where a spinning part turns (`appstarting`'s ring). */
  spinAt?: { x: number; y: number };
}

/** The standard arrow, tip at (1, 1). */
const ARROW = "M1 1 L1 18 L5.2 14.1 L8.2 21 L11 19.8 L8.1 13 L13.8 13 Z";
/** A ring of radius 7.5 (outer 9, inner 6) about (12, 12), with a gap the spin makes visible. */
const RING = "M12 3 A9 9 0 1 1 3 12 L6 12 A6 6 0 1 0 12 6 Z";
/** A small ring about (17, 17) for `appstarting`. */
const SMALL_RING = "M17 11.5 A5.5 5.5 0 1 1 11.5 17 L13.5 17 A3.5 3.5 0 1 0 17 13.5 Z";
/** A two-headed arrow along the vertical axis through (12, 12). */
const DOUBLE_VERTICAL = "M12 1 L17 7 L13.5 7 L13.5 17 L17 17 L12 23 L7 17 L10.5 17 L10.5 7 L7 7 Z";
/** A two-headed arrow along the horizontal axis through (12, 12). */
const DOUBLE_HORIZONTAL = "M1 12 L7 7 L7 10.5 L17 10.5 L17 7 L23 12 L17 17 L17 13.5 L7 13.5 L7 17 Z";
/** A two-headed arrow from the top left to the bottom right (`sizenwse`). */
const DOUBLE_NWSE = "M3 3 L11 3 L8.4 5.6 L18.4 15.6 L21 13 L21 21 L13 21 L15.6 18.4 L5.6 8.4 L3 11 Z";
/** A two-headed arrow from the top right to the bottom left (`sizenesw`). */
const DOUBLE_NESW = "M21 3 L21 11 L18.4 8.4 L8.4 18.4 L11 21 L3 21 L3 13 L5.6 15.6 L15.6 5.6 L13 3 Z";

export const CURSOR_GLYPHS: Readonly<Record<CursorKind, CursorGlyph>> = {
  arrow: { paths: [{ d: ARROW, fill: "white" }], hotspot: { x: 1, y: 1 } },
  ibeam: {
    paths: [{ d: "M7 2 L11 2 L12 3 L13 2 L17 2 L17 4 L13 4 L13 20 L17 20 L17 22 L13 22 L12 21 L11 22 L7 22 L7 20 L11 20 L11 4 L7 4 Z", fill: "white" }],
    hotspot: { x: 12, y: 12 },
  },
  hand: {
    paths: [
      {
        d: "M6.8 3 C6.8 1.6 9.2 1.6 9.2 3 L9.2 10 L11 10 L11 8.5 C11 7.4 13.5 7.4 13.5 8.5 L13.5 10.5 L14 10.5 L14 9.5 C14 8.4 16.5 8.4 16.5 9.5 L16.5 11 L17 11 C17 10 19.5 10 19.5 11 L19.5 16 C19.5 19.5 17.5 22 14.5 22 L12 22 C9.5 22 8.5 21 7 19 L3.5 14.5 C2.8 13.5 4.3 12.2 5.3 13 L6.8 14.5 Z",
        fill: "white",
      },
    ],
    hotspot: { x: 8, y: 2 },
  },
  wait: { paths: [{ d: RING, fill: "white" }], hotspot: { x: 12, y: 12 }, animate: "spin" },
  appstarting: {
    paths: [
      { d: ARROW, fill: "white" },
      { d: SMALL_RING, fill: "white", spin: true },
    ],
    hotspot: { x: 1, y: 1 },
    spinAt: { x: 17, y: 17 },
  },
  cross: { paths: [{ d: "M11 2 L13 2 L13 11 L22 11 L22 13 L13 13 L13 22 L11 22 L11 13 L2 13 L2 11 L11 11 Z", fill: "white" }], hotspot: { x: 12, y: 12 } },
  no: {
    // a ring with a slash from top left to bottom right: the two openings are holes
    paths: [{ d: "M12 2 A10 10 0 1 1 12 22 A10 10 0 1 1 12 2 Z M6.47 8.59 A6.5 6.5 0 0 0 15.41 17.53 Z M8.59 6.47 A6.5 6.5 0 0 1 17.53 15.41 Z", fill: "white", evenOdd: true }],
    hotspot: { x: 12, y: 12 },
  },
  sizeall: {
    paths: [{ d: "M12 1 L16 5.5 L13.2 5.5 L13.2 10.8 L18.5 10.8 L18.5 8 L23 12 L18.5 16 L18.5 13.2 L13.2 13.2 L13.2 18.5 L16 18.5 L12 23 L8 18.5 L10.8 18.5 L10.8 13.2 L5.5 13.2 L5.5 16 L1 12 L5.5 8 L5.5 10.8 L10.8 10.8 L10.8 5.5 L8 5.5 Z", fill: "white" }],
    hotspot: { x: 12, y: 12 },
  },
  sizens: { paths: [{ d: DOUBLE_VERTICAL, fill: "white" }], hotspot: { x: 12, y: 12 } },
  sizewe: { paths: [{ d: DOUBLE_HORIZONTAL, fill: "white" }], hotspot: { x: 12, y: 12 } },
  sizenesw: { paths: [{ d: DOUBLE_NESW, fill: "white" }], hotspot: { x: 12, y: 12 } },
  sizenwse: { paths: [{ d: DOUBLE_NWSE, fill: "white" }], hotspot: { x: 12, y: 12 } },
  uparrow: { paths: [{ d: "M12 1 L19 9 L14 9 L14 23 L10 23 L10 9 L5 9 Z", fill: "white" }], hotspot: { x: 12, y: 1 } },
  help: {
    paths: [
      { d: ARROW, fill: "white" },
      { d: "M15.5 6 A3.5 3.5 0 0 1 22.5 6 C22.5 7.8 21.2 8.6 20.2 9.3 L20.2 11.5 L17.8 11.5 L17.8 8.6 C17.8 7.9 18.5 7.5 19.3 7.1 C19.9 6.8 20.5 6.6 20.5 6 A1.5 1.5 0 0 0 17.5 6 Z M17.8 13 L20.2 13 L20.2 15.4 L17.8 15.4 Z", fill: "white" },
    ],
    hotspot: { x: 1, y: 1 },
  },
};

/** Every named kind, in the schema's order (the overlay's tests draw each one). */
export const CURSOR_KINDS: readonly CursorKind[] = ["arrow", "ibeam", "hand", "wait", "appstarting", "cross", "no", "sizeall", "sizens", "sizewe", "sizenesw", "sizenwse", "uparrow", "help"];

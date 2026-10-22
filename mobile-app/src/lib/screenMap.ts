/**
 * Screen view geometry (docs/design/screen-view.md §8.3): where a touch on the phone lands on the PC's streamed surface.
 *
 * The `<video>` is `object-fit: contain` inside a box; the frame is letterboxed with content scale `k = min(boxW/vw,
 * boxH/vh)` and offsets `offX = (boxW − vw·k)/2`, `offY` likewise. Pinch zoom `s ∈ [1, 4]` and pan `(tx, ty)` are a CSS
 * transform with `transform-origin: 0 0` on the box's content, so a touch at box-relative `(px, py)` maps back through
 * `((px − tx)/s − offX) / (vw·k)` to a fraction of the frame, then to the contract's 0..65535 integers (the same units as
 * `pointer_move_to` and `screen_rect`). Taps in the black bars and in the PC's `reserved` rectangles (DoMe's own bars)
 * are DROPPED, never clamped onto the nearest pixel: a tap that lands nowhere must press nothing.
 *
 * The `gen` the phone sends is the generation it SAW: `GeometryTracker` keeps the PC's last `geometry` message and
 * marks it stale when the `<video>` reports a `resize` (a frame size change may mean a new generation is on its way)
 * until the matching message arrives, for at most GEOMETRY_STALE_MS. Rotation needs no handling here: the streamed
 * frame is the surface, whatever way the monitor is turned; the PC maps normalised units onto its pixels.
 *
 * Pure and DOM-free, like `gestures.ts`.
 */
import { LIMITS, type ScreenDcPcToPhone } from "@dome/protocol";

export const NORM_MAX = 65535;
export const ZOOM_MIN = 1;
export const ZOOM_MAX = 4;
/** After a `<video>` resize, taps are blocked until the next geometry message, at most this long (§8.3). */
export const GEOMETRY_STALE_MS = 300;
/** How many generations the PC still maps (limits.screen_geometry_generations_kept): older `gen` values are refused. */
export const GENERATIONS_KEPT: number = LIMITS.screen_geometry_generations_kept;

/** The surface box in CSS px; touch coordinates are taken relative to its top-left corner. */
export interface Box {
  width: number;
  height: number;
}

/** A rectangle in normalised units (relay-frames `screen_rect`). */
export type Rect = { x: number; y: number; w: number; h: number };

/** The PC's `geometry` data-channel message, as the tracker keeps it. */
export type Geometry = Extract<ScreenDcPcToPhone, { type: "geometry" }>;

/** Letterbox fit of a `vw × vh` frame inside a box. */
export interface Fit {
  k: number;
  offX: number;
  offY: number;
  /** The drawn content size in box px (vw·k, vh·k). */
  cw: number;
  ch: number;
}

/** Zoom and pan of the box's content (`transform-origin: 0 0`). */
export interface View {
  s: number;
  tx: number;
  ty: number;
}

export const IDENTITY_VIEW: View = { s: 1, tx: 0, ty: 0 };

/** null when the box or the frame has no size yet (no frame decoded, element not laid out). */
export function fitContent(box: Box, vw: number, vh: number): Fit | null {
  if (!(box.width > 0 && box.height > 0 && vw > 0 && vh > 0)) return null;
  const k = Math.min(box.width / vw, box.height / vh);
  const cw = vw * k;
  const ch = vh * k;
  return { k, offX: (box.width - cw) / 2, offY: (box.height - ch) / 2, cw, ch };
}

function clampNumber(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

/**
 * Keep the zoomed content covering the whole box: `s` within [ZOOM_MIN, ZOOM_MAX] and the translation such that the
 * box never shows anything beyond the content's own edges (at 1× the view is the identity).
 */
export function clampView(view: View, box: Box): View {
  const s = clampNumber(Number.isFinite(view.s) ? view.s : 1, ZOOM_MIN, ZOOM_MAX);
  if (s === 1) return IDENTITY_VIEW;
  const tx = clampNumber(view.tx, box.width - box.width * s, 0);
  const ty = clampNumber(view.ty, box.height - box.height * s, 0);
  return { s, tx, ty };
}

/** Multiply the zoom by `factor` keeping the box point `(px, py)` under the same content point (pinch about the fingers). */
export function zoomAt(view: View, factor: number, px: number, py: number, box: Box): View {
  if (!(factor > 0) || !Number.isFinite(factor)) return clampView(view, box);
  const s = clampNumber(view.s * factor, ZOOM_MIN, ZOOM_MAX);
  const ratio = s / view.s;
  return clampView({ s, tx: px - (px - view.tx) * ratio, ty: py - (py - view.ty) * ratio }, box);
}

/** Pan by a finger displacement in box px; nothing moves at 1× (the content fills the box exactly). */
export function panBy(view: View, dx: number, dy: number, box: Box): View {
  if (view.s <= 1) return IDENTITY_VIEW;
  return clampView({ s: view.s, tx: view.tx + dx, ty: view.ty + dy }, box);
}

/** The CSS transform for the content layer. */
export function viewTransform(view: View): string {
  return `translate(${view.tx}px, ${view.ty}px) scale(${view.s})`;
}

/**
 * A box-relative touch point → fraction of the frame `(u, v)` in [0, 1), or null in the letterbox bars (and for any
 * point outside the box). The inverse of `fit` and `view`, in that order.
 */
export function toSurface(px: number, py: number, fit: Fit, view: View): { u: number; v: number } | null {
  const cx = (px - view.tx) / view.s;
  const cy = (py - view.ty) / view.s;
  const u = (cx - fit.offX) / fit.cw;
  const v = (cy - fit.offY) / fit.ch;
  if (!(u >= 0 && u < 1 && v >= 0 && v < 1)) return null;
  return { u, v };
}

/** Fractions → the contract's integers (rounded, clamped to 0..65535). */
export function normalise(u: number, v: number): { x: number; y: number } {
  return { x: clampNumber(Math.round(u * NORM_MAX), 0, NORM_MAX), y: clampNumber(Math.round(v * NORM_MAX), 0, NORM_MAX) };
}

/** Inclusive rectangle test in normalised units. */
export function inReserved(x: number, y: number, reserved: readonly Rect[]): boolean {
  return reserved.some((r) => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h);
}

/**
 * The whole chain for one touch: box point → frame fraction → normalised integers, dropping (null) anything in the
 * letterbox, outside the box or inside a reserved rectangle.
 */
export function mapPoint(px: number, py: number, box: Box, vw: number, vh: number, view: View, reserved: readonly Rect[] = []): { x: number; y: number } | null {
  const fit = fitContent(box, vw, vh);
  if (!fit) return null;
  const uv = toSurface(px, py, fit, view);
  if (!uv) return null;
  const p = normalise(uv.u, uv.v);
  return inReserved(p.x, p.y, reserved) ? null : p;
}

/**
 * The geometry generation the phone has seen (§8.3): the PC's last `geometry` message, held back for up to
 * GEOMETRY_STALE_MS after a `<video>` resize so a tap never carries a generation the frame may have outlived.
 */
export class GeometryTracker {
  private geometry: Geometry | null = null;
  private staleSince: number | null = null;

  /** Seed from `screen_session_result` (gen only; reserved rectangles arrive with the first geometry message). */
  seed(gen: number, surfaceIndex: number, width: number, height: number): void {
    this.geometry = { type: "geometry", gen, surface_index: surfaceIndex, width, height, rotation: 0, reserved: [] };
    this.staleSince = null;
  }

  /** A `geometry` data-channel message: the new current generation; a stale mark is cleared. Older generations are ignored. */
  apply(message: Geometry): boolean {
    if (this.geometry && message.gen < this.geometry.gen) return false;
    this.geometry = message;
    this.staleSince = null;
    return true;
  }

  /** The `<video>` fired `resize`: hold taps until the matching geometry message, at most GEOMETRY_STALE_MS. */
  markStale(now: number): void {
    if (this.staleSince === null) this.staleSince = now;
  }

  get known(): Geometry | null {
    return this.geometry;
  }

  /** The geometry a tap may use right now, or null while it is held back. */
  current(now: number): Geometry | null {
    if (this.staleSince !== null && now - this.staleSince <= GEOMETRY_STALE_MS) return null;
    return this.geometry;
  }

  get isStale(): boolean {
    return this.staleSince !== null;
  }

  reset(): void {
    this.geometry = null;
    this.staleSince = null;
  }
}

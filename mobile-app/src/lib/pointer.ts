/**
 * The pointer the phone draws itself (protocol 1.5, rules.screen_cursor; design screen-view-2a §4.2, D17-D19). Pure
 * and DOM-free: the Screen view client feeds it the PC's reports and the page's own gestures, and the overlay reads one
 * `sample()` per animation frame. Nothing here goes through React state; the positions never leave this object.
 *
 * Positions are normalised over the streamed surface (0..65535, like `cursor_pos` and `pointer_move_to`). The model
 * keeps a PREDICTED point that moves at once with the finger and is reconciled with the PC's reports:
 *   - touchpad mode adds every relative move that is sent (PC pixels, sensitivity already applied) scaled by
 *     65535 / (W − 1) of the PHYSICAL surface (`screen_surface`, never the encode size);
 *   - direct mode snaps to the tap or loupe point;
 *   - a report while a finger moves (or within IDLE_MS of the last local move) pulls the prediction BLEND_WHILE_MOVING
 *     of the way; after IDLE_MS without a local move the shown point eases to the report over EASE_MS (cubic
 *     ease-out); an error above SNAP_FRACTION of the surface snaps (the prediction was wrong, e.g. a jump on the PC);
 *   - the PC reports a position only when it changes and never repeats one (and the cursor channel does not
 *     retransmit), so the last report of a slide usually arrives while the finger still counts as moving and is only
 *     blended: once nothing was sent and no report came for IDLE_MS (the finger may still rest on the glass),
 *     `sample()` eases the shown point to that last report, once per report.
 * Nothing is visible before the session's first report (never a guess at 0,0), in a `drawn` or 1.4 session, after
 * `hide()` (paused) or `geometryChanged()` until the next report, and while the PC says the pointer is not visible.
 *
 * The follow point (`followSample`) exists in every mode, but only reports correct it: in a `drawn` or 1.4 session no
 * report ever arrives, so the page seeds it at the pinch centre (`seedFollow`) and the touchpad moves carry it from
 * there, a guess that can sit away from the PC's pointer and drift (KNOWN_ISSUES 47).
 *
 * The tunables are named so a device test can change them in one place.
 */
import type { CursorKind } from "@dome/protocol";

/** The shown point eases to a report over this long once the finger is idle. */
export const EASE_MS = 80;
/** No local move for this long: the finger is idle and reports ease instead of blending. */
export const IDLE_MS = 120;
/** While a finger moves, each report pulls the prediction this fraction of the way. */
export const BLEND_WHILE_MOVING = 0.35;
/** A report further than this fraction of the surface from the prediction snaps. */
export const SNAP_FRACTION = 0.1;

const NORM_MAX = 65535;

/** A `cursor_pos` the client accepted (its `gen` already matched the current geometry). */
export interface PointerReport {
  x: number;
  y: number;
  visible: boolean;
  gen: number;
}

/** A shape the overlay can draw: a Windows system kind (the phone's own art) or a validated custom bitmap. */
export type CursorShape = { kind: CursorKind } | { kind: "custom"; width: number; height: number; hotspotX: number; hotspotY: number; src: string };

/** What the overlay draws in one animation frame. `busy` is true while an ease runs (keep scheduling frames). */
export interface PointerSample {
  x: number;
  y: number;
  visible: boolean;
  shape: CursorShape | null;
  busy: boolean;
}

export interface SurfaceSize {
  width: number;
  height: number;
}

export interface PointerModelOptions {
  /** The client's one-slot latest `cursor_pos`, drained once per `sample()` (the last report of a frame wins). */
  pending?: () => PointerReport | null;
  /** The streamed surface in physical pixels, for the prediction error in PC pixels (the readout's `pred err max`). */
  surface?: () => SurfaceSize | null;
}

interface Pt {
  x: number;
  y: number;
}

function clampNorm(v: number): number {
  return Math.max(0, Math.min(NORM_MAX, v));
}

function easeOutCubic(t: number): number {
  const u = 1 - Math.max(0, Math.min(1, t));
  return 1 - u * u * u;
}

export class PointerModel {
  private readonly opts: PointerModelOptions;
  private mode: "drawn" | "overlay" | null = null;
  private report: PointerReport | null = null;
  /** Where the pointer is believed to be (the target the shown point moves to). */
  private predicted: Pt | null = null;
  private ease: { from: Pt; to: Pt; at: number } | null = null;
  private lastLocalMoveAt: number | null = null;
  /** When the last report arrived (an idle reconcile waits until the reports went quiet for IDLE_MS). */
  private reportAt: number | null = null;
  /** The last report was adopted, eased to or snapped to; false while it was only blended (`sample()` settles it). */
  private reconciled = true;
  /** A local move or tap changed the prediction since the last report (for the prediction error). */
  private localSinceReport = false;
  private finger = false;
  private hidden = false;
  private current: CursorShape | null = null;
  private errMaxPx: number | null = null;

  constructor(options: PointerModelOptions = {}) {
    this.opts = options;
  }

  /** `result.cursor` then `state.cursor`; `drawn`/null (a 1.4 session) → nothing is ever visible. Forgets positions and shape. */
  setMode(mode: "drawn" | "overlay" | null): void {
    this.forget();
    this.mode = mode;
  }

  get pointerMode(): "drawn" | "overlay" | null {
    return this.mode;
  }

  /** A report from the PC (the caller filtered `gen`); null is nothing (an empty slot). */
  reported(r: PointerReport | null, now: number): void {
    if (!r) return;
    const at = { x: clampNorm(r.x), y: clampNorm(r.y) };
    this.report = { ...r, x: at.x, y: at.y };
    this.reportAt = now;
    this.reconciled = true;
    this.hidden = false;
    const predicted = this.predicted;
    if (!predicted) {
      this.predicted = at;
      this.ease = null;
      this.localSinceReport = false;
      return;
    }
    const ex = at.x - predicted.x;
    const ey = at.y - predicted.y;
    if (this.localSinceReport) this.noteError(ex, ey);
    this.localSinceReport = false;
    if (Math.hypot(ex, ey) > SNAP_FRACTION * NORM_MAX) {
      this.predicted = at;
      this.ease = null;
      return;
    }
    if (this.moving(now)) {
      // blend from what is shown (a running ease stops there first)
      this.settle(now);
      const base = this.predicted ?? predicted;
      this.predicted = { x: clampNorm(base.x + BLEND_WHILE_MOVING * (at.x - base.x)), y: clampNorm(base.y + BLEND_WHILE_MOVING * (at.y - base.y)) };
      this.reconciled = false; // only blended: settled by sample() once the finger is idle and the reports went quiet
      return;
    }
    // idle: ease from what is shown now to the report
    this.ease = { from: this.shown(now) ?? predicted, to: at, at: now };
    this.predicted = at;
  }

  /** The current shape; only the latest one is kept. */
  shape(s: CursorShape): void {
    this.current = s;
  }

  /** Touchpad mode: the relative move that was just sent (PC pixels), on the PHYSICAL surface. Null surface: no prediction. */
  predictMoveBy(dxPc: number, dyPc: number, surface: SurfaceSize | null, now: number): void {
    this.lastLocalMoveAt = now;
    if (!surface || !this.predicted) return;
    const dx = (dxPc * NORM_MAX) / Math.max(surface.width - 1, 1);
    const dy = (dyPc * NORM_MAX) / Math.max(surface.height - 1, 1);
    const next = { x: clampNorm(this.predicted.x + dx), y: clampNorm(this.predicted.y + dy) };
    const shiftX = next.x - this.predicted.x;
    const shiftY = next.y - this.predicted.y;
    if (this.ease) this.ease = { from: { x: clampNorm(this.ease.from.x + shiftX), y: clampNorm(this.ease.from.y + shiftY) }, to: next, at: this.ease.at };
    this.predicted = next;
    this.localSinceReport = true;
  }

  /** Direct mode: the PC's pointer jumps to the tap, loupe or scroll point (normalised). */
  predictTo(x: number, y: number, now: number): void {
    this.lastLocalMoveAt = now;
    this.predicted = { x: clampNorm(x), y: clampNorm(y) };
    this.ease = null;
    this.localSinceReport = true;
  }

  /** A finger touched (true) or left (false) the glass in touchpad mode: a running ease stops where it is shown. */
  fingerActive(active: boolean, now: number = Date.now()): void {
    if (active && !this.finger) this.settle(now);
    this.finger = active;
  }

  /** A new geometry generation: the old positions mean nothing on it; hidden until the next report. */
  geometryChanged(): void {
    this.report = null;
    this.reportAt = null;
    this.reconciled = true;
    this.predicted = null;
    this.ease = null;
    this.localSinceReport = false;
  }

  /** The PC paused (it sends nothing while paused): hidden until the next report. */
  hide(): void {
    this.hidden = true;
  }

  /** A new session or none: everything forgotten, the mode included. */
  reset(): void {
    this.forget();
    this.mode = null;
    this.errMaxPx = null;
    this.lastLocalMoveAt = null;
    this.finger = false;
  }

  /**
   * Seed the follow point at a frame fraction (`toSurface` of the pinch centre) when none is known yet, so a zoomed
   * Touchpad view can follow the moves in a `drawn` or 1.4 session too (dead-reckoned from there, never corrected).
   * Never makes the pointer visible.
   */
  seedFollow(p: { u: number; v: number } | null): void {
    if (this.predicted || !p) return;
    this.predicted = { x: clampNorm(Math.round(p.u * NORM_MAX)), y: clampNorm(Math.round(p.v * NORM_MAX)) };
  }

  /** The point the zoomed view keeps in sight (normalised), in every mode; null while nothing is known. */
  followSample(): { x: number; y: number } | null {
    return this.predicted ? { ...this.predicted } : null;
  }

  /** One animation frame: drains the client's latest report, then where to draw what. */
  sample(now: number): PointerSample {
    const pending = this.opts.pending?.() ?? null;
    if (pending) this.reported(pending, now);
    this.reconcileIdle(now);
    const at = this.shown(now);
    if (this.ease && now - this.ease.at >= EASE_MS) this.ease = null;
    const visible = this.mode === "overlay" && at !== null && this.report !== null && this.report.visible && !this.hidden;
    return { x: at?.x ?? 0, y: at?.y ?? 0, visible, shape: this.current, busy: this.ease !== null };
  }

  /** The largest prediction error seen in this session, in PC pixels (null until a prediction met a report). */
  get predErrMaxPx(): number | null {
    return this.errMaxPx;
  }

  /**
   * The last report was only blended (it came while a finger moved, or one rested on the glass) and no other report
   * follows, because the PC sends a position only when it changes: once no move was sent and no report came for
   * IDLE_MS (so a report still in flight is not undone), the shown point eases to that report over EASE_MS. Once per
   * report; a touch that stops the ease leaves the rest for the next quiet moment.
   */
  private reconcileIdle(now: number): void {
    const r = this.report;
    const predicted = this.predicted;
    if (this.reconciled || !r || !predicted || this.ease) return;
    const lastActivity = Math.max(this.lastLocalMoveAt ?? Number.NEGATIVE_INFINITY, this.reportAt ?? Number.NEGATIVE_INFINITY);
    if (now - lastActivity <= IDLE_MS) return;
    this.reconciled = true;
    const to = { x: r.x, y: r.y };
    if (Math.hypot(to.x - predicted.x, to.y - predicted.y) < 1) return;
    this.ease = { from: this.shown(now) ?? predicted, to, at: now };
    this.predicted = to;
  }

  private moving(now: number): boolean {
    return this.finger || (this.lastLocalMoveAt !== null && now - this.lastLocalMoveAt <= IDLE_MS);
  }

  /** The point shown now: the ease in progress, else the prediction. */
  private shown(now: number): Pt | null {
    const e = this.ease;
    if (e) {
      const k = easeOutCubic((now - e.at) / EASE_MS);
      return { x: e.from.x + (e.to.x - e.from.x) * k, y: e.from.y + (e.to.y - e.from.y) * k };
    }
    return this.predicted ? { ...this.predicted } : null;
  }

  /** Stop a running ease where it is shown now (the finger takes over from what the person sees). */
  private settle(now: number): void {
    if (!this.ease) return;
    const at = this.shown(now);
    this.ease = null;
    if (at) this.predicted = at;
    this.reconciled = false; // the report was not reached: the next quiet moment finishes the way
  }

  private noteError(ex: number, ey: number): void {
    const surface = this.opts.surface?.() ?? null;
    if (!surface) return;
    const px = Math.round(Math.hypot((ex * Math.max(surface.width - 1, 1)) / NORM_MAX, (ey * Math.max(surface.height - 1, 1)) / NORM_MAX));
    this.errMaxPx = this.errMaxPx === null ? px : Math.max(this.errMaxPx, px);
  }

  private forget(): void {
    this.report = null;
    this.reportAt = null;
    this.reconciled = true;
    this.predicted = null;
    this.ease = null;
    this.hidden = false;
    this.current = null;
    this.localSinceReport = false;
  }
}

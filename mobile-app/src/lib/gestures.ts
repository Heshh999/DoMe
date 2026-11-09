/**
 * Touchpad gesture state machine (spec §10A A). Pure and DOM-free: the Touchpad page feeds it pointer
 * events (id, x, y, time) and the machine emits input primitives. Explicit thresholds:
 *
 *   tap            one finger, ≤ TAP_MAX_MS down time, ≤ TAP_MAX_MOVE px of movement   → left click
 *   two quick taps second tap within DOUBLE_TAP_MS and DOUBLE_TAP_MAX_DIST px        → second left click
 *                  (Windows combines two clicks within its own double-click time; the first click is
 *                  never delayed and the `double_click` primitive is not sent after an already-sent
 *                  click, which would be three presses — DECISIONS.md 22)
 *   move           one finger past either tap threshold                                 → relative motion
 *   two fingers    both lift quickly and nearly stationary                              → right click
 *                  the midpoint moves first (over scrollStartPx)                         → scroll notches
 *                  the spread changes first (over pinchRatio)                            → `pinch` (the Screen
 *                  view zooms its picture; nothing is sent; a pinch never clicks or scrolls). Decided on
 *                  pairs of moves with the direct machine's classifier (`twoFinger.ts`)
 *   finger count   a second finger arriving cancels the first finger's pending tap: changing from one
 *                  finger to two NEVER clicks; once a gesture was two-fingered, the remaining finger
 *                  neither clicks nor moves the cursor until every finger lifted
 *   drag mode      first finger down presses the left button (held); moves drag; lifting keeps the
 *                  hold (drag lock) until endDrag()/cancel()/setDragMode(false) releases it
 *   cancel         releases a held button and discards the current gesture without any tap
 *
 * The machine only uses deltas and distances, so it can be fed any coordinate space; a `pinch` centre is in the
 * coordinates it was fed (the Screen view feeds box px).
 */
import { spread, TwoFingerClassifier, type Pt } from "./twoFinger.ts";

export type GestureOutput =
  | { type: "move"; dx: number; dy: number }
  | { type: "click"; button: "left" | "right"; double: boolean }
  | { type: "scroll"; dx: number; dy: number }
  | { type: "button"; button: "left"; action: "down" | "up" }
  /** Two fingers spreading or closing: multiply the zoom by `scale` about (cx, cy). Local only, never an input event. */
  | { type: "pinch"; scale: number; cx: number; cy: number };

export interface GestureOptions {
  /** Cursor pixels per touch pixel (0.5 … 3). */
  sensitivity: number;
  /** "natural": content follows the fingers (two fingers down → content down, wheel notch +). */
  scrollDirection: "natural" | "standard";
  tapMaxMs: number;
  tapMaxMove: number;
  doubleTapMs: number;
  doubleTapMaxDist: number;
  /** Touch pixels per wheel notch. */
  scrollPxPerNotch: number;
  /** Spread-ratio change that turns two moving fingers into a pinch (the direct machine's value); `Infinity` never
   *  pinches (the Touchpad page, which has nothing to zoom: two fingers there scroll or right-click as before 1.5). */
  pinchRatio: number;
  /** Midpoint travel in px that turns two moving fingers into a scroll. */
  scrollStartPx: number;
}

export const DEFAULT_GESTURE_OPTIONS: GestureOptions = { sensitivity: 1.4, scrollDirection: "natural", tapMaxMs: 250, tapMaxMove: 10, doubleTapMs: 300, doubleTapMaxDist: 24, scrollPxPerNotch: 36, pinchRatio: 0.1, scrollStartPx: 8 };

/** schema: pointer_move / pointer_scroll values are integers within ±input_motion_max. */
const MOTION_MAX = 4096;

interface Finger {
  id: number;
  startX: number;
  startY: number;
  lastX: number;
  lastY: number;
  downAt: number;
  travelled: number;
}

type Phase = "idle" | "one_pending" | "one_moving" | "two_undecided" | "two_scroll" | "two_pinch" | "two_release" | "two_settling" | "drag";

function clamp(v: number): number {
  return Math.max(-MOTION_MAX, Math.min(MOTION_MAX, v));
}

export class GestureMachine {
  private opts: GestureOptions;
  private fingers = new Map<number, Finger>();
  private phase: Phase = "idle";
  private dragMode = false;
  private leftHeld = false;
  private lastTap: { at: number; x: number; y: number } | null = null;
  private twoMoved = false;
  /** In two_release: the first finger lifted quickly and nothing moved, so a quick second lift is a right click. */
  private twoTapCandidate = false;
  private remX = 0;
  private remY = 0;
  private scrollAccX = 0;
  private scrollAccY = 0;
  private twoLastCentroid: { x: number; y: number } | null = null;
  /** Two fingers down: pinch or scroll, decided on completed pairs of moves. */
  private two: TwoFingerClassifier | null = null;
  /** In two_pinch: the finger distance at the previous pinch step. */
  private twoPrevDistance = 1;
  private readonly out: GestureOutput[] = [];

  constructor(options: Partial<GestureOptions> = {}) {
    this.opts = { ...DEFAULT_GESTURE_OPTIONS, ...options };
  }

  setOptions(options: Partial<GestureOptions>): void {
    this.opts = { ...this.opts, ...options };
  }

  get options(): GestureOptions {
    return this.opts;
  }

  get isDragMode(): boolean {
    return this.dragMode;
  }

  /** The left button is currently held by this machine (drag in progress or drag-locked). */
  get isLeftHeld(): boolean {
    return this.leftHeld;
  }

  get activeFingers(): number {
    return this.fingers.size;
  }

  /** Whether pointer `id` is still down as far as this machine knows (lost-capture handling). */
  hasFinger(id: number): boolean {
    return this.fingers.has(id);
  }

  /** Drain the primitives produced since the last call, in order. */
  take(): GestureOutput[] {
    return this.out.splice(0, this.out.length);
  }

  /** Enable/disable drag mode. Leaving it releases a held button. */
  setDragMode(on: boolean): void {
    if (this.dragMode === on) return;
    this.dragMode = on;
    if (!on) this.endDrag();
    else if (this.phase === "one_pending" || this.phase === "one_moving") {
      // the finger already on the surface starts dragging from here
      this.press();
      this.phase = "drag";
    }
  }

  /** Release the held left button (End Drag). Keeps drag mode on so the next finger starts a new drag. */
  endDrag(): void {
    if (this.leftHeld) {
      this.leftHeld = false;
      this.out.push({ type: "button", button: "left", action: "up" });
    }
    if (this.phase === "drag") this.phase = this.fingers.size > 0 ? "one_moving" : "idle";
  }

  /** pointercancel / lost capture / orientation or visibility change: release everything, never click. */
  cancel(): void {
    this.fingers.clear();
    this.phase = "idle";
    this.lastTap = null;
    this.twoMoved = false;
    this.twoLastCentroid = null;
    this.two?.reset();
    this.scrollAccX = this.scrollAccY = 0;
    this.remX = this.remY = 0;
    if (this.leftHeld) {
      this.leftHeld = false;
      this.out.push({ type: "button", button: "left", action: "up" });
    }
  }

  pointerDown(id: number, x: number, y: number, t: number): void {
    if (this.fingers.has(id)) return;
    this.fingers.set(id, { id, startX: x, startY: y, lastX: x, lastY: y, downAt: t, travelled: 0 });
    const n = this.fingers.size;
    if (n === 1) {
      if (this.phase === "two_settling" || this.phase === "two_release") return; // a stray re-touch while settling: ignore until all are up
      if (this.dragMode) {
        this.press();
        this.phase = "drag";
      } else {
        this.phase = "one_pending";
      }
      return;
    }
    if (n === 2) {
      // one → two fingers: the first finger's pending tap is discarded (never a click); a drag in
      // progress keeps holding but stops following the fingers.
      if (this.phase === "drag") return;
      this.phase = "two_undecided";
      this.twoMoved = false;
      this.scrollAccX = this.scrollAccY = 0;
      this.twoLastCentroid = this.centroid();
      const [a, b] = this.pair();
      this.two = new TwoFingerClassifier({ pinchRatio: this.opts.pinchRatio, scrollStartPx: this.opts.scrollStartPx });
      this.two.start(a, b);
      this.twoPrevDistance = this.two.startDistance;
      return;
    }
    // three or more: nothing is interpreted until everything lifts
    this.phase = "two_settling";
    this.twoLastCentroid = null;
  }

  pointerMove(id: number, x: number, y: number, t: number): void {
    const f = this.fingers.get(id);
    if (!f) return;
    const dx = x - f.lastX;
    const dy = y - f.lastY;
    f.lastX = x;
    f.lastY = y;
    f.travelled += Math.hypot(dx, dy);
    switch (this.phase) {
      case "one_pending":
        if (f.travelled > this.opts.tapMaxMove || t - f.downAt > this.opts.tapMaxMs) {
          this.phase = "one_moving";
          // emit the whole displacement so far so the start of a slow move is not lost
          this.emitMove(x - f.startX, y - f.startY);
        }
        return;
      case "one_moving":
      case "drag":
        this.emitMove(dx, dy);
        return;
      case "two_undecided": {
        if (this.fingers.size !== 2 || !this.two) return;
        const [a, b] = this.pair();
        const decision = this.two.move(id, a, b);
        if (decision === "undecided") return;
        if (decision === "pinch") {
          this.phase = "two_pinch";
          // measured from the spread at touch-down, so the dead zone is part of the first step (no jump, nothing lost)
          this.twoPrevDistance = this.two.startDistance;
          this.emitPinch(a, b);
          return;
        }
        // a scroll: the midpoint's whole travel since touch-down counts, as before the decision existed
        this.phase = "two_scroll";
        const start = this.two.startMid;
        const c = this.centroid();
        this.twoLastCentroid = c;
        this.scrollAccX = c.x - start.x;
        this.scrollAccY = c.y - start.y;
        this.scrollStep();
        return;
      }
      case "two_scroll": {
        const c = this.centroid();
        if (!this.twoLastCentroid) {
          this.twoLastCentroid = c;
          return;
        }
        const cdx = c.x - this.twoLastCentroid.x;
        const cdy = c.y - this.twoLastCentroid.y;
        this.twoLastCentroid = c;
        this.scrollAccX += cdx;
        this.scrollAccY += cdy;
        this.scrollStep();
        return;
      }
      case "two_pinch": {
        if (this.fingers.size !== 2) return;
        const [a, b] = this.pair();
        this.emitPinch(a, b);
        return;
      }
      default:
        return;
    }
  }

  pointerUp(id: number, t: number): void {
    const f = this.fingers.get(id);
    if (!f) return;
    this.fingers.delete(id);
    switch (this.phase) {
      case "one_pending": {
        const quick = t - f.downAt <= this.opts.tapMaxMs && f.travelled <= this.opts.tapMaxMove;
        this.phase = "idle";
        if (!quick) return;
        const dbl = this.lastTap !== null && t - this.lastTap.at <= this.opts.doubleTapMs && Math.hypot(f.startX - this.lastTap.x, f.startY - this.lastTap.y) <= this.opts.doubleTapMaxDist;
        this.out.push({ type: "click", button: "left", double: dbl });
        this.lastTap = dbl ? null : { at: t, x: f.startX, y: f.startY };
        return;
      }
      case "one_moving":
        this.phase = "idle";
        this.lastTap = null;
        return;
      case "drag":
        // drag lock: the button stays held until End Drag
        if (this.fingers.size === 0) this.phase = "idle";
        return;
      case "two_undecided":
      case "two_scroll":
      case "two_pinch": {
        // a pinch never clicks; a still or barely moved pair is a right click as before
        const tapPossible = this.phase !== "two_pinch" && !this.twoMoved;
        if (this.fingers.size === 0) {
          const quick = tapPossible && t - f.downAt <= this.opts.tapMaxMs;
          this.phase = "idle";
          this.twoLastCentroid = null;
          if (quick) this.out.push({ type: "click", button: "right", double: false });
          this.lastTap = null;
          return;
        }
        // one of two fingers lifted: whatever remains must not move the cursor; a quick lift of the
        // second finger completes the two-finger tap (right click), anything else settles silently.
        this.twoTapCandidate = tapPossible && t - f.downAt <= this.opts.tapMaxMs;
        this.phase = "two_release";
        this.twoLastCentroid = null;
        return;
      }
      case "two_release": {
        if (this.fingers.size > 0) return;
        const quick = this.twoTapCandidate && t - f.downAt <= this.opts.tapMaxMs && f.travelled <= this.opts.tapMaxMove;
        this.phase = "idle";
        this.lastTap = null;
        if (quick) this.out.push({ type: "click", button: "right", double: false });
        return;
      }
      case "two_settling":
        if (this.fingers.size === 0) {
          this.phase = "idle";
          this.lastTap = null;
        }
        return;
      default:
        if (this.fingers.size === 0) this.phase = "idle";
    }
  }

  private press(): void {
    if (this.leftHeld) return;
    this.leftHeld = true;
    this.out.push({ type: "button", button: "left", action: "down" });
  }

  /** The two fingers' current positions (only called with exactly two down). */
  private pair(): [Pt, Pt] {
    const [a, b] = [...this.fingers.values()];
    return [
      { x: a!.lastX, y: a!.lastY },
      { x: b!.lastX, y: b!.lastY },
    ];
  }

  /** One pinch step: the spread now over the spread at the previous step, about the fingers' midpoint. */
  private emitPinch(a: Pt, b: Pt): void {
    const { distance, mid } = spread(a, b);
    const scale = distance / this.twoPrevDistance;
    this.twoPrevDistance = distance;
    if (scale !== 1) this.out.push({ type: "pinch", scale, cx: mid.x, cy: mid.y });
  }

  /** Scroll notches once the pair travelled past the tap threshold (a barely moved pair is still a right click). */
  private scrollStep(): void {
    if (!this.twoMoved && Math.hypot(this.scrollAccX, this.scrollAccY) > this.opts.tapMaxMove) this.twoMoved = true;
    if (this.twoMoved) this.emitScroll();
  }

  private centroid(): { x: number; y: number } {
    let x = 0;
    let y = 0;
    for (const f of this.fingers.values()) {
      x += f.lastX;
      y += f.lastY;
    }
    const n = Math.max(1, this.fingers.size);
    return { x: x / n, y: y / n };
  }

  private emitMove(dx: number, dy: number): void {
    const sx = dx * this.opts.sensitivity + this.remX;
    const sy = dy * this.opts.sensitivity + this.remY;
    const ix = Math.trunc(sx);
    const iy = Math.trunc(sy);
    this.remX = sx - ix;
    this.remY = sy - iy;
    if (ix === 0 && iy === 0) return;
    this.out.push({ type: "move", dx: clamp(ix), dy: clamp(iy) });
  }

  private emitScroll(): void {
    const per = this.opts.scrollPxPerNotch;
    const nx = Math.trunc(this.scrollAccX / per);
    const ny = Math.trunc(this.scrollAccY / per);
    if (nx === 0 && ny === 0) return;
    this.scrollAccX -= nx * per;
    this.scrollAccY -= ny * per;
    // Windows: positive dy = wheel away from the user = content scrolls up. Fingers moving down the
    // glass (ny > 0) in natural mode pull the content down, which is a positive notch.
    const sign = this.opts.scrollDirection === "natural" ? 1 : -1;
    this.out.push({ type: "scroll", dx: clamp(nx * sign), dy: clamp(ny * sign) });
  }
}

/** The short gesture guide (spec §10A A table), rendered by components/GestureGuide.tsx. */
export const GESTURES: Array<{ phone: string; pc: string }> = [
  { phone: "Slide one finger", pc: "Moves the cursor from where it is, like a laptop touchpad" },
  { phone: "Lift and put the finger down elsewhere", pc: "Keeps going from the current cursor position — no jump" },
  { phone: "Short, still tap", pc: "Left click" },
  { phone: "Two quick taps in the same spot", pc: "Double click (two clicks; Windows combines them)" },
  { phone: "Two-finger still tap, or the Right Click button", pc: "Right click" },
  { phone: "Two fingers moving", pc: "Scroll (vertical, and horizontal where the app supports it)" },
  { phone: "Drag mode, then move a finger", pc: "Holds the left button while moving; End Drag releases it" },
  { phone: "Keyboard button", pc: "Opens the phone keyboard and the key/shortcut rows" },
];

/** Touchpad mode over the Screen view picture ("With Touchpad on"), rendered by the Screen page's help sheet. */
export const TOUCHPAD_SCREEN_GESTURES: Array<{ phone: string; pc: string }> = [
  { phone: "Slide one finger", pc: "Moves the pointer, like a laptop touchpad" },
  { phone: "Tap", pc: "Clicks where the pointer is" },
  { phone: "Tap with two fingers", pc: "Right-click" },
  { phone: "Slide two fingers", pc: "Scrolls" },
  { phone: "Pinch", pc: "Zooms the picture; the view follows the pointer" },
  { phone: "Drag, then slide", pc: "Holds the mouse button while you move; End Drag lets go" },
];


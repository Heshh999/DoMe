/**
 * Direct-mode gestures over the live picture (docs/design/screen-view.md §8.3), pure and DOM-free like `gestures.ts`.
 * The Screen page feeds pointer events (id, x, y, time; x/y relative to the surface box) and the machine emits what the
 * picture should do; the page maps positions onto the streamed surface with `screenMap.ts` and builds the signed input
 * events. The touchpad rule "a second finger never clicks" is kept.
 *
 *   tap               one finger, ≤ tapMaxMs, ≤ tapMaxMove px            → `tap left` at the finger
 *   second tap        within doubleTapMs and doubleTapMaxDist px         → `tap left` at the FIRST tap's position, so
 *                                                                          Windows forms the double-click itself; no delay
 *   two-finger tap    both lift quickly without moving                   → `tap right` at the midpoint
 *   press and hold    holdMs still (the surface's timer calls `hold`)    → `loupe start`; slide → `loupe move`; lift →
 *                                                                          `loupe end` (left click at the crosshair)
 *   two fingers move  spread ratio changes > pinchRatio before the        → `pinch` (local zoom; nothing is sent)
 *                     midpoint moves scrollStartPx
 *                     otherwise                                          → `scroll` (midpoint + wheel units, 120 = one
 *                                                                          notch, direction preference applied)
 *                     (decided on pairs of moves, never on one finger's half of a move: see `twoPending`)
 *   one-finger drag   at zoom > 1                                        → `pan` (local); at 1× nothing at all
 *   cancel            releases the current gesture; a loupe is cancelled, never completed as a click
 */

export type DirectOutput =
  | { type: "tap"; button: "left" | "right"; x: number; y: number }
  | { type: "loupe"; phase: "start" | "move" | "end" | "cancel"; x: number; y: number }
  | { type: "scroll"; x: number; y: number; dx: number; dy: number }
  | { type: "pinch"; scale: number; cx: number; cy: number }
  | { type: "pan"; dx: number; dy: number };

export interface DirectOptions {
  tapMaxMs: number;
  tapMaxMove: number;
  doubleTapMs: number;
  doubleTapMaxDist: number;
  holdMs: number;
  /** A finger that travelled more than this before the hold timer fires is a slide, not a press and hold. */
  holdMaxMove: number;
  /** Spread-ratio change that turns two moving fingers into a pinch. */
  pinchRatio: number;
  /** Midpoint travel that turns two moving fingers into a scroll. */
  scrollStartPx: number;
  /** Wheel units per CSS px of two-finger travel (120 = one notch; 120/36 keeps the touchpad's 36 px per notch). */
  wheelUnitsPerPx: number;
  /** "natural": content follows the fingers (fingers down → positive dy, like `gestures.ts`). */
  scrollDirection: "natural" | "standard";
  /** Set by the page from the view: a one-finger drag pans only while zoomed. */
  zoomed: boolean;
}

export const DEFAULT_DIRECT_OPTIONS: DirectOptions = {
  tapMaxMs: 250,
  tapMaxMove: 10,
  doubleTapMs: 500,
  doubleTapMaxDist: 12,
  holdMs: 350,
  holdMaxMove: 10,
  pinchRatio: 0.1,
  scrollStartPx: 8,
  wheelUnitsPerPx: 120 / 36,
  scrollDirection: "natural",
  zoomed: false,
};

/** schema: pointer_wheel values are integers within ±input_wheel_max (version.json). */
export const WHEEL_MAX = 1200;

interface Finger {
  id: number;
  startX: number;
  startY: number;
  lastX: number;
  lastY: number;
  downAt: number;
  travelled: number;
}

type Phase = "idle" | "one_pending" | "one_moving" | "loupe" | "two_undecided" | "two_release" | "two_scroll" | "two_pinch" | "settling";

function clampWheel(v: number): number {
  return Math.max(-WHEEL_MAX, Math.min(WHEEL_MAX, v));
}

export class DirectGestureMachine {
  private opts: DirectOptions;
  private fingers = new Map<number, Finger>();
  private phase: Phase = "idle";
  private lastTap: { at: number; x: number; y: number } | null = null;
  private twoStart: { d: number; mx: number; my: number; at: number } | null = null;
  private twoPrevDistance = 0;
  private twoPrevMid: { x: number; y: number } | null = null;
  /** In two_release: the first of two still fingers lifted quickly, so a quick second lift is a right click. */
  private twoTapCandidate = false;
  /**
   * In two_undecided: fingers that reported a move since the last decision check. Browsers deliver two fingers'
   * moves one at a time, so between the two events one finger has moved and the other not yet: judged then, a fast
   * two-finger scroll would look like a spread change (a pinch). The check therefore runs once both fingers have
   * reported, or when one finger moves again before the other did (the other is really still, e.g. an anchored thumb).
   */
  private twoPending = new Set<number>();
  private wheelAccX = 0;
  private wheelAccY = 0;
  private readonly out: DirectOutput[] = [];

  constructor(options: Partial<DirectOptions> = {}) {
    this.opts = { ...DEFAULT_DIRECT_OPTIONS, ...options };
  }

  setOptions(options: Partial<DirectOptions>): void {
    this.opts = { ...this.opts, ...options };
  }

  get options(): DirectOptions {
    return this.opts;
  }

  get activeFingers(): number {
    return this.fingers.size;
  }

  hasFinger(id: number): boolean {
    return this.fingers.has(id);
  }

  /** True while a press-and-hold loupe is open (the page draws it). */
  get loupeOpen(): boolean {
    return this.phase === "loupe";
  }

  /** When the surface should call `hold(id, t)`: the hold deadline of the pending finger, or null. */
  holdDeadline(): { id: number; at: number } | null {
    if (this.phase !== "one_pending") return null;
    const f = [...this.fingers.values()][0];
    return f ? { id: f.id, at: f.downAt + this.opts.holdMs } : null;
  }

  /** Drain the outputs produced since the last call, in order. */
  take(): DirectOutput[] {
    return this.out.splice(0, this.out.length);
  }

  /** pointercancel / lost capture / rotation / hidden: forget the gesture; a loupe is cancelled, nothing clicks. */
  cancel(): void {
    if (this.phase === "loupe") {
      const f = [...this.fingers.values()][0];
      this.out.push({ type: "loupe", phase: "cancel", x: f?.lastX ?? 0, y: f?.lastY ?? 0 });
    }
    this.fingers.clear();
    this.phase = "idle";
    this.lastTap = null;
    this.twoStart = null;
    this.twoPrevMid = null;
    this.twoTapCandidate = false;
    this.twoPending.clear();
    this.wheelAccX = this.wheelAccY = 0;
  }

  pointerDown(id: number, x: number, y: number, t: number): void {
    if (this.fingers.has(id)) return;
    this.fingers.set(id, { id, startX: x, startY: y, lastX: x, lastY: y, downAt: t, travelled: 0 });
    const n = this.fingers.size;
    if (n === 1) {
      if (this.phase === "settling" || this.phase === "two_release") {
        // a stray re-touch while the other finger of a two-finger gesture is still down: nothing until everything lifted
        this.phase = "settling";
        return;
      }
      this.phase = "one_pending";
      return;
    }
    if (n === 2) {
      // one → two fingers: the first finger's pending tap is discarded (never a click); an open loupe is cancelled.
      if (this.phase === "loupe") {
        const first = [...this.fingers.values()][0]!;
        this.out.push({ type: "loupe", phase: "cancel", x: first.lastX, y: first.lastY });
      }
      if (this.phase === "settling" || this.phase === "two_release") {
        this.phase = "settling";
        return;
      }
      const [a, b] = [...this.fingers.values()];
      const d = Math.hypot(b!.lastX - a!.lastX, b!.lastY - a!.lastY);
      this.phase = "two_undecided";
      this.twoStart = { d: Math.max(d, 1), mx: (a!.lastX + b!.lastX) / 2, my: (a!.lastY + b!.lastY) / 2, at: t };
      this.twoPrevDistance = Math.max(d, 1);
      this.twoPrevMid = { x: this.twoStart.mx, y: this.twoStart.my };
      this.twoPending.clear();
      this.wheelAccX = this.wheelAccY = 0;
      return;
    }
    // three or more: nothing is interpreted until everything lifts
    this.phase = "settling";
    this.twoStart = null;
  }

  /** The surface's hold timer fired for finger `id`: a still finger opens the loupe. */
  hold(id: number, t: number): void {
    const f = this.fingers.get(id);
    if (!f || this.phase !== "one_pending") return;
    if (f.travelled > this.opts.holdMaxMove || t - f.downAt < this.opts.holdMs) return;
    this.phase = "loupe";
    this.lastTap = null;
    this.out.push({ type: "loupe", phase: "start", x: f.lastX, y: f.lastY });
  }

  pointerMove(id: number, x: number, y: number, _t: number): void {
    const f = this.fingers.get(id);
    if (!f) return;
    const dx = x - f.lastX;
    const dy = y - f.lastY;
    f.lastX = x;
    f.lastY = y;
    f.travelled += Math.hypot(dx, dy);
    switch (this.phase) {
      case "one_pending":
        if (f.travelled > this.opts.tapMaxMove) {
          this.phase = "one_moving";
          if (this.opts.zoomed) this.out.push({ type: "pan", dx: x - f.startX, dy: y - f.startY });
        }
        return;
      case "one_moving":
        if (this.opts.zoomed && (dx !== 0 || dy !== 0)) this.out.push({ type: "pan", dx, dy });
        return;
      case "loupe":
        this.out.push({ type: "loupe", phase: "move", x, y });
        return;
      case "two_undecided": {
        const pairComplete = this.twoPending.size === 1 && !this.twoPending.has(id);
        const movedAgain = this.twoPending.has(id);
        if (!pairComplete && !movedAgain) {
          this.twoPending.add(id);
          return;
        }
        this.twoPending.clear();
        if (movedAgain) this.twoPending.add(id);
        this.twoFingerMove();
        return;
      }
      case "two_scroll":
      case "two_pinch":
        this.twoFingerMove();
        return;
      default:
        return;
    }
  }

  private twoFingerMove(): void {
    if (this.fingers.size !== 2 || !this.twoStart) return;
    const [a, b] = [...this.fingers.values()];
    const d = Math.max(Math.hypot(b!.lastX - a!.lastX, b!.lastY - a!.lastY), 1);
    const mid = { x: (a!.lastX + b!.lastX) / 2, y: (a!.lastY + b!.lastY) / 2 };
    if (this.phase === "two_undecided") {
      const ratioChange = Math.abs(d / this.twoStart.d - 1);
      const midTravel = Math.hypot(mid.x - this.twoStart.mx, mid.y - this.twoStart.my);
      if (ratioChange > this.opts.pinchRatio) this.phase = "two_pinch";
      else if (midTravel > this.opts.scrollStartPx) this.phase = "two_scroll";
      else {
        this.twoPrevDistance = d;
        this.twoPrevMid = mid;
        return;
      }
      // the decision point: start measuring from here so the dead zone itself is not replayed as a jump
      this.twoPrevDistance = this.phase === "two_pinch" ? this.twoStart.d : d;
      this.twoPrevMid = this.phase === "two_scroll" ? { x: this.twoStart.mx, y: this.twoStart.my } : mid;
    }
    if (this.phase === "two_pinch") {
      const scale = d / this.twoPrevDistance;
      this.twoPrevDistance = d;
      if (scale !== 1) this.out.push({ type: "pinch", scale, cx: mid.x, cy: mid.y });
      return;
    }
    // two_scroll
    const prev = this.twoPrevMid ?? mid;
    this.twoPrevMid = mid;
    this.wheelAccX += (mid.x - prev.x) * this.opts.wheelUnitsPerPx;
    this.wheelAccY += (mid.y - prev.y) * this.opts.wheelUnitsPerPx;
    const ux = Math.trunc(this.wheelAccX);
    const uy = Math.trunc(this.wheelAccY);
    if (ux === 0 && uy === 0) return;
    const sign = this.opts.scrollDirection === "natural" ? 1 : -1;
    const dx = clampWheel(ux * sign);
    const dy = clampWheel(uy * sign);
    // what was clamped off stays in the accumulator for the next move
    this.wheelAccX -= dx * sign;
    this.wheelAccY -= dy * sign;
    this.out.push({ type: "scroll", x: mid.x, y: mid.y, dx, dy });
  }

  pointerUp(id: number, t: number): void {
    const f = this.fingers.get(id);
    if (!f) return;
    this.fingers.delete(id);
    switch (this.phase) {
      case "one_pending": {
        const quick = t - f.downAt <= this.opts.tapMaxMs && f.travelled <= this.opts.tapMaxMove;
        this.phase = "idle";
        if (!quick) {
          this.lastTap = null;
          return;
        }
        const dbl = this.lastTap !== null && t - this.lastTap.at <= this.opts.doubleTapMs && Math.hypot(f.startX - this.lastTap.x, f.startY - this.lastTap.y) <= this.opts.doubleTapMaxDist;
        if (dbl && this.lastTap) {
          // the second click lands exactly where the first did, so Windows pairs them into a double-click
          this.out.push({ type: "tap", button: "left", x: this.lastTap.x, y: this.lastTap.y });
          this.lastTap = null;
        } else {
          this.out.push({ type: "tap", button: "left", x: f.startX, y: f.startY });
          this.lastTap = { at: t, x: f.startX, y: f.startY };
        }
        return;
      }
      case "one_moving":
        this.phase = "idle";
        this.lastTap = null;
        return;
      case "loupe":
        this.phase = "idle";
        this.lastTap = null;
        this.out.push({ type: "loupe", phase: "end", x: f.lastX, y: f.lastY });
        return;
      case "two_undecided": {
        // a quick lift of both still fingers without a decision is a two-finger tap (right click at the initial midpoint)
        const start = this.twoStart;
        if (this.fingers.size === 0) {
          this.phase = "idle";
          this.lastTap = null;
          if (start && t - f.downAt <= this.opts.tapMaxMs && f.travelled <= this.opts.tapMaxMove) this.out.push({ type: "tap", button: "right", x: start.mx, y: start.my });
          this.twoStart = null;
          return;
        }
        // one of two lifted: the remaining finger must neither click nor pan; a quick lift of it completes the
        // two-finger tap, anything else settles silently
        this.twoTapCandidate = t - f.downAt <= this.opts.tapMaxMs && f.travelled <= this.opts.tapMaxMove;
        this.phase = "two_release";
        return;
      }
      case "two_release": {
        if (this.fingers.size > 0) return;
        const start = this.twoStart;
        const quick = this.twoTapCandidate && t - f.downAt <= this.opts.tapMaxMs && f.travelled <= this.opts.tapMaxMove;
        this.phase = "idle";
        this.lastTap = null;
        this.twoStart = null;
        if (quick && start) this.out.push({ type: "tap", button: "right", x: start.mx, y: start.my });
        return;
      }
      case "two_scroll":
      case "two_pinch":
        this.phase = this.fingers.size === 0 ? "idle" : "settling";
        this.lastTap = null;
        this.twoStart = null;
        return;
      case "settling":
        if (this.fingers.size === 0) {
          this.phase = "idle";
          this.lastTap = null;
          this.twoStart = null;
        }
        return;
      default:
        if (this.fingers.size === 0) this.phase = "idle";
    }
  }
}

/** The direct-mode gesture guide, rendered by the Screen page's help sheet. */
export const DIRECT_GESTURES: Array<{ phone: string; pc: string }> = [
  { phone: "Tap the picture", pc: "Left click exactly there" },
  { phone: "Two quick taps in the same spot", pc: "Double click (Windows pairs the two clicks)" },
  { phone: "Two-finger tap", pc: "Right click at the midpoint" },
  { phone: "Press and hold, then slide", pc: "A magnifier for aiming; lifting clicks at the crosshair" },
  { phone: "Two fingers sliding", pc: "Smooth scroll under the fingers" },
  { phone: "Pinch", pc: "Zooms the picture on the phone only; nothing is sent" },
  { phone: "One finger while zoomed", pc: "Pans the picture on the phone only" },
  { phone: "Touchpad mode", pc: "The relative touchpad over the picture, for drags and precision" },
];

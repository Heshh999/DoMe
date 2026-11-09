/**
 * Two fingers on the glass: a pinch or a scroll? (docs/design/screen-view.md §8.3; design screen-view-2a §4.5). Pure
 * and DOM-free, shared by the direct-mode machine (`directGestures.ts`) and the relative touchpad machine
 * (`gestures.ts`), so both decide the same way:
 *
 *   pinch    the spread (finger distance) changes by more than `pinchRatio` (10 %) of its start value…
 *   scroll   …before the midpoint travels more than `scrollStartPx` (8 px); otherwise still undecided.
 *
 * Browsers deliver two fingers' moves one at a time, so between the two events one finger has moved and the other not
 * yet: judged then, a fast two-finger scroll would look like a spread change (a pinch). The decision is therefore taken
 * on completed PAIRS of moves: once both fingers have reported, or when one finger moves again before the other did
 * (the other is really still, e.g. an anchored thumb). Once decided, the answer stays until `start()` or `reset()`.
 */

export interface Pt {
  x: number;
  y: number;
}

export interface TwoFingerOptions {
  /** Spread-ratio change that makes it a pinch (0.1 = 10 %). */
  pinchRatio: number;
  /** Midpoint travel in px that makes it a scroll. */
  scrollStartPx: number;
}

export type TwoFingerDecision = "undecided" | "pinch" | "scroll";

function distance(a: Pt, b: Pt): number {
  return Math.max(Math.hypot(b.x - a.x, b.y - a.y), 1);
}

function midpoint(a: Pt, b: Pt): Pt {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

export class TwoFingerClassifier {
  private readonly opts: TwoFingerOptions;
  /** Fingers that reported a move since the last decision check (the pair guard). */
  private readonly pending = new Set<number>();
  private result: "pinch" | "scroll" | null = null;
  private distance0 = 1;
  private mid0: Pt = { x: 0, y: 0 };

  constructor(opts: TwoFingerOptions) {
    this.opts = { ...opts };
  }

  /** The second finger came down: both fingers' positions now (at least 1 px apart is assumed). */
  start(a: Pt, b: Pt): void {
    this.distance0 = distance(a, b);
    this.mid0 = midpoint(a, b);
    this.pending.clear();
    this.result = null;
  }

  /** Finger `id` moved; `a` and `b` are both fingers' positions after the move. */
  move(id: number, a: Pt, b: Pt): TwoFingerDecision {
    if (this.result) return this.result;
    const pairComplete = this.pending.size === 1 && !this.pending.has(id);
    const movedAgain = this.pending.has(id);
    if (!pairComplete && !movedAgain) {
      this.pending.add(id);
      return "undecided";
    }
    this.pending.clear();
    if (movedAgain) this.pending.add(id);
    const ratioChange = Math.abs(distance(a, b) / this.distance0 - 1);
    const mid = midpoint(a, b);
    const midTravel = Math.hypot(mid.x - this.mid0.x, mid.y - this.mid0.y);
    if (ratioChange > this.opts.pinchRatio) this.result = "pinch";
    else if (midTravel > this.opts.scrollStartPx) this.result = "scroll";
    return this.result ?? "undecided";
  }

  get decided(): "pinch" | "scroll" | null {
    return this.result;
  }

  /** The finger distance when the second finger came down (never below 1 px). */
  get startDistance(): number {
    return this.distance0;
  }

  /** The midpoint when the second finger came down. */
  get startMid(): Pt {
    return { ...this.mid0 };
  }

  /** Forget the decision and the pair guard (cancel). */
  reset(): void {
    this.pending.clear();
    this.result = null;
  }
}

/** Both fingers' distance and midpoint, for the machines that emit pinches and scrolls after the decision. */
export function spread(a: Pt, b: Pt): { distance: number; mid: Pt } {
  return { distance: distance(a, b), mid: midpoint(a, b) };
}

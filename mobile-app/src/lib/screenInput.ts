/**
 * Screen view taps and scrolls → signed input events (docs/design/screen-view.md §8.1, §8.3), pure and DOM-free.
 *
 * `ScreenSurface` feeds each `DirectGestureMachine` output here together with what the phone currently shows (the
 * surface box, the decoded frame size, the zoom/pan view and the PC's last `geometry` message as `GeometryTracker`
 * releases it). A tap becomes `[pointer_move_to{x, y, gen}, pointer_button click]`, a two-finger scroll becomes
 * `[pointer_move_to(midpoint), pointer_wheel{dx, dy}]`; the batch is then stamped 1.4 by the shared library because
 * of those events. Anything that would land nowhere is DROPPED with a reason, never clamped onto the nearest pixel:
 * the letterbox bars, DoMe's own "Screen view on" bars (`reserved`), a geometry held back after a `<video>` resize, and
 * a session with no geometry yet. `gen` is always the generation the phone SAW, so the PC refuses a tap aimed at an
 * older layout (SCREEN_GEOMETRY_CHANGED) instead of clicking somewhere else.
 */
import type { InputEvent } from "./input.ts";
import type { DirectOutput } from "./directGestures.ts";
import { fitContent, inReserved, normalise, toSurface, type Box, type Geometry, type View } from "./screenMap.ts";

/** Why a tap or scroll was not sent (the surface shows a short hint for some of these). */
export type DropReason = "no_geometry" | "settling" | "outside" | "reserved";

export interface SurfaceContext {
  box: Box;
  /** The decoded frame size (`videoWidth`/`videoHeight`); 0 until the first frame, then the geometry's size is used. */
  videoWidth: number;
  videoHeight: number;
  view: View;
  /** `GeometryTracker.current(now)`: null while held back after a resize (≤ 300 ms) or before the first geometry. */
  geometry: Geometry | null;
  /** True while the tracker holds a geometry back (a resize was seen): "settling" rather than "no_geometry". */
  geometryStale: boolean;
}

export type Located = { ok: true; x: number; y: number; gen: number } | { ok: false; reason: DropReason };

/** One box point → normalised surface integers at the generation the phone saw, or why not. */
export function locate(px: number, py: number, ctx: SurfaceContext): Located {
  const g = ctx.geometry;
  if (!g) return { ok: false, reason: ctx.geometryStale ? "settling" : "no_geometry" };
  const vw = ctx.videoWidth > 0 ? ctx.videoWidth : g.width;
  const vh = ctx.videoHeight > 0 ? ctx.videoHeight : g.height;
  const fit = fitContent(ctx.box, vw, vh);
  if (!fit) return { ok: false, reason: "no_geometry" };
  const uv = toSurface(px, py, fit, ctx.view);
  if (!uv) return { ok: false, reason: "outside" };
  const p = normalise(uv.u, uv.v);
  if (inReserved(p.x, p.y, g.reserved)) return { ok: false, reason: "reserved" };
  return { ok: true, x: p.x, y: p.y, gen: g.gen };
}

export type DirectResult = { kind: "send"; events: InputEvent[] } | { kind: "drop"; reason: DropReason } | { kind: "local" };

/**
 * The input events for one gesture output. `tap` and the loupe's `end` click; `scroll` moves the cursor under the
 * fingers then turns the wheel; `pinch`, `pan` and the loupe's `start`/`move`/`cancel` are local only (`local`).
 * `loupeAim` gives the crosshair point for a loupe output (the surface tracks it; see `loupeAim`).
 */
export function directEvents(out: DirectOutput, ctx: SurfaceContext, loupePoint?: { x: number; y: number }): DirectResult {
  switch (out.type) {
    case "tap": {
      const at = locate(out.x, out.y, ctx);
      if (!at.ok) return { kind: "drop", reason: at.reason };
      return { kind: "send", events: [{ type: "pointer_move_to", x: at.x, y: at.y, gen: at.gen }, { type: "pointer_button", button: out.button, action: "click" }] };
    }
    case "loupe": {
      if (out.phase !== "end") return { kind: "local" };
      const point = loupePoint ?? { x: out.x, y: out.y };
      const at = locate(point.x, point.y, ctx);
      if (!at.ok) return { kind: "drop", reason: at.reason };
      return { kind: "send", events: [{ type: "pointer_move_to", x: at.x, y: at.y, gen: at.gen }, { type: "pointer_button", button: "left", action: "click" }] };
    }
    case "scroll": {
      if (out.dx === 0 && out.dy === 0) return { kind: "local" };
      const at = locate(out.x, out.y, ctx);
      if (!at.ok) return { kind: "drop", reason: at.reason };
      return { kind: "send", events: [{ type: "pointer_move_to", x: at.x, y: at.y, gen: at.gen }, { type: "pointer_wheel", dx: out.dx, dy: out.dy }] };
    }
    case "pinch":
    case "pan":
      return { kind: "local" };
  }
}

/** The loupe's magnification (§8.3: a 2.5× circle). */
export const LOUPE_ZOOM = 2.5;

/**
 * The precision loupe's aim: it starts under the finger and then moves at 1/LOUPE_ZOOM of the finger's travel, so a
 * slide of 2.5 CSS px moves the crosshair by one CSS px of the picture. Lifting clicks at the aim, not at the finger.
 */
export function loupeAim(start: { x: number; y: number }, finger: { x: number; y: number }, zoom: number = LOUPE_ZOOM): { x: number; y: number } {
  return { x: start.x + (finger.x - start.x) / zoom, y: start.y + (finger.y - start.y) / zoom };
}

/** The hint for a dropped tap, or null when nothing needs saying (a tap in the black bars is simply nothing). */
export function dropHint(reason: DropReason): string | null {
  switch (reason) {
    case "reserved":
      return "That is DoMe’s “Screen view on” bar; taps there are not sent.";
    case "settling":
      return "The picture is changing size; tap again in a moment.";
    case "no_geometry":
      return "Waiting for the PC’s screen layout; tap again in a moment.";
    case "outside":
      return null;
  }
}

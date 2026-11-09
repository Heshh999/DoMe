/**
 * The pointer drawn by the phone (protocol 1.5, design screen-view-2a §4.4, D17): every Windows system kind the PC can
 * name has its own glyph, placed so its hotspot sits on the reported point through the letterbox fit and the zoom; a
 * custom bitmap is an `<img>` from a `data:` URL, scaled down to 24 px but never up; the pointer stays 24 CSS px at
 * every zoom; it is hidden before the first report, while the PC says it is not visible, without a fit, whenever the
 * surface hides it (the loupe) and not rendered at all outside overlay mode. One `pointer.sample()` per animation
 * frame, which drains the client's one-slot report. Over a manual frame queue; nothing reads a pixel.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ScreenDcPcToPhone } from "@dome/protocol";

import { POINTER_CSS_PX, ScreenPointer } from "../../src/components/ScreenPointer.tsx";
import { CURSOR_GLYPHS, CURSOR_KINDS } from "../../src/lib/cursorGlyphs.ts";
import { parseCursorShape, type CursorShapeMessage } from "../../src/lib/cursorShape.ts";
import { PointerModel, type CursorShape, type PointerReport } from "../../src/lib/pointer.ts";
import { fitContent, fromSurface, IDENTITY_VIEW, type Fit, type View } from "../../src/lib/screenMap.ts";

/** A 1600×900 picture in a 400×300 box: content 400×225 from y = 37.5. */
const FIT: Fit = fitContent({ width: 400, height: 300 }, 1600, 900)!;
const CENTRE: PointerReport = { x: 32768, y: 32768, visible: true, gen: 1 };

const DC_CASES = JSON.parse(readFileSync(resolve(__dirname, "../../../shared/protocol/fixtures/screen-dc-cases.json"), "utf8")) as { accepted: Array<{ name: string; text: string }> };
const RELAY_FRAMES = JSON.parse(readFileSync(resolve(__dirname, "../../../shared/protocol/schemas/relay-frames.schema.json"), "utf8")) as { $defs: Record<string, { properties: { kind: { enum: string[] } } }> };

let queue: Map<number, FrameRequestCallback>;
let nextHandle: number;

beforeEach(() => {
  queue = new Map();
  nextHandle = 0;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    nextHandle += 1;
    queue.set(nextHandle, cb);
    return nextHandle;
  });
  vi.stubGlobal("cancelAnimationFrame", (h: number) => queue.delete(h));
});
afterEach(() => {
  vi.unstubAllGlobals();
});

/** One animation frame: every callback queued so far runs once. */
function frame(): void {
  act(() => {
    const due = [...queue.values()];
    queue.clear();
    for (const cb of due) cb(performance.now());
  });
}

function overlay(shape: CursorShape | null = { kind: "arrow" }, report: PointerReport | null = CENTRE): PointerModel {
  const p = new PointerModel();
  p.setMode("overlay");
  if (shape) p.shape(shape);
  if (report) p.reported(report, performance.now());
  return p;
}

function renderPointer(pointer: PointerModel, o: { fit?: Fit | null; view?: View; enabled?: boolean; hidden?: boolean } = {}) {
  const fitRef = { current: o.fit === undefined ? FIT : o.fit };
  const viewRef = { current: o.view ?? IDENTITY_VIEW };
  const props = { pointer, fitRef, viewRef, enabled: o.enabled ?? true, hidden: o.hidden ?? false };
  const view = render(<ScreenPointer {...props} />);
  return { ...view, fitRef, viewRef, rerenderWith: (over: Partial<typeof props>) => view.rerender(<ScreenPointer {...props} {...over} />) };
}

const el = () => screen.getByTestId("screen-pointer");
function placed(x: number, y: number, hx: number, hy: number, view: View = IDENTITY_VIEW): string {
  const { px, py } = fromSurface(x, y, FIT, view);
  return `translate3d(${px - hx}px, ${py - hy}px, 0)`;
}

describe("ScreenPointer: the glyphs", () => {
  it("has a glyph for exactly the kinds the contract names, each hotspot inside its 24-unit box", () => {
    const named = RELAY_FRAMES.$defs.screen_dc_cursor_shape_named!.properties.kind.enum;
    expect([...CURSOR_KINDS].sort()).toEqual([...named].sort());
    expect(Object.keys(CURSOR_GLYPHS).sort()).toEqual([...named].sort());
    for (const kind of CURSOR_KINDS) {
      const g = CURSOR_GLYPHS[kind];
      expect(g.paths.length, kind).toBeGreaterThan(0);
      for (const v of [g.hotspot.x, g.hotspot.y]) expect(v >= 0 && v < POINTER_CSS_PX, kind).toBe(true);
    }
    expect(CURSOR_GLYPHS.arrow.hotspot).toEqual({ x: 1, y: 1 });
    expect(CURSOR_GLYPHS.ibeam.hotspot).toEqual({ x: 12, y: 12 });
    expect(CURSOR_GLYPHS.hand.hotspot).toEqual({ x: 8, y: 2 });
    expect(CURSOR_GLYPHS.uparrow.hotspot).toEqual({ x: 12, y: 1 });
    expect(CURSOR_GLYPHS.help.hotspot).toEqual({ x: 1, y: 1 });
    expect(CURSOR_GLYPHS.appstarting.hotspot).toEqual({ x: 1, y: 1 });
  });

  it("each named kind renders its SVG, 24 px, with the hotspot on the reported point", () => {
    const pointer = overlay();
    renderPointer(pointer);
    for (const kind of CURSOR_KINDS) {
      pointer.shape({ kind });
      frame();
      const svg = screen.getByTestId("screen-pointer-glyph");
      expect(svg).toHaveAttribute("data-kind", kind);
      expect(svg).toHaveAttribute("width", "24");
      expect(svg).toHaveAttribute("height", "24");
      expect(svg.querySelectorAll("path").length).toBe(CURSOR_GLYPHS[kind].paths.length);
      const { x, y } = CURSOR_GLYPHS[kind].hotspot;
      expect(el().style.transform, kind).toBe(placed(CENTRE.x, CENTRE.y, x, y));
      expect(el().style.opacity).toBe("1");
    }
    // the busy ring spins as a whole; "working in the background" spins only its small ring
    pointer.shape({ kind: "wait" });
    frame();
    expect(screen.getByTestId("screen-pointer-glyph")).toHaveClass("animate-spin");
    pointer.shape({ kind: "appstarting" });
    frame();
    expect(screen.getByTestId("screen-pointer-glyph")).not.toHaveClass("animate-spin");
    expect(screen.getByTestId("screen-pointer-glyph").querySelector("g.animate-spin path")).not.toBeNull();
  });

  it("is drawn outside the zoom: 24 px at 1× and at 4×, placed through the zoom", () => {
    const pointer = overlay({ kind: "ibeam" });
    const { viewRef } = renderPointer(pointer);
    frame();
    expect(screen.getByTestId("screen-pointer-glyph")).toHaveAttribute("height", "24");
    expect(el().style.transform).toBe(placed(CENTRE.x, CENTRE.y, 12, 12));
    const zoomed: View = { s: 4, tx: -600, ty: -450 };
    viewRef.current = zoomed;
    frame();
    expect(screen.getByTestId("screen-pointer-glyph")).toHaveAttribute("height", "24");
    expect(el().style.transform).toBe(placed(CENTRE.x, CENTRE.y, 12, 12, zoomed));
    expect(el().getAttribute("style")).not.toMatch(/scale/);
  });

  it("a missing shape (none received yet) is drawn as the arrow", () => {
    renderPointer(overlay(null));
    frame();
    expect(screen.getByTestId("screen-pointer-glyph")).toHaveAttribute("data-kind", "arrow");
    expect(el().style.transform).toBe(placed(CENTRE.x, CENTRE.y, 1, 1));
  });
});

describe("ScreenPointer: custom bitmaps", () => {
  it("a custom shape is an <img> from a data:image/png;base64 URL (the shared 1×1 case: never scaled up)", () => {
    const message = JSON.parse(DC_CASES.accepted.find((c) => c.name.includes("real 1x1"))!.text) as ScreenDcPcToPhone;
    const shape = parseCursorShape(message as CursorShapeMessage);
    expect(shape).not.toBeNull();
    renderPointer(overlay(shape));
    frame();
    const img = screen.getByTestId("screen-pointer-image");
    expect(img.getAttribute("src")!.startsWith("data:image/png;base64,")).toBe(true);
    expect(img).toHaveAttribute("width", "1");
    expect(img).toHaveAttribute("height", "1");
    expect(img).toHaveAttribute("alt", "");
    expect(img).toHaveAttribute("draggable", "false");
    expect(screen.queryByTestId("screen-pointer-glyph")).toBeNull();
  });

  it("a taller bitmap is scaled down to 24 px with its hotspot; a smaller one keeps its size", () => {
    const src = "data:image/png;base64,iVBORw0KGgo=";
    const pointer = overlay({ kind: "custom", width: 48, height: 48, hotspotX: 6, hotspotY: 4, src });
    renderPointer(pointer);
    frame();
    let img = screen.getByTestId("screen-pointer-image");
    expect(img).toHaveAttribute("width", "24");
    expect(img).toHaveAttribute("height", "24");
    expect(el().style.transform).toBe(placed(CENTRE.x, CENTRE.y, 3, 2));
    pointer.shape({ kind: "custom", width: 16, height: 16, hotspotX: 5, hotspotY: 7, src });
    frame();
    img = screen.getByTestId("screen-pointer-image");
    expect(img).toHaveAttribute("width", "16");
    expect(img).toHaveAttribute("height", "16");
    expect(el().style.transform).toBe(placed(CENTRE.x, CENTRE.y, 5, 7));
  });
});

describe("ScreenPointer: when it is hidden", () => {
  it("hidden before the session's first report, while the PC says it is not visible, and without a fit", () => {
    const pointer = overlay({ kind: "arrow" }, null);
    const { fitRef } = renderPointer(pointer);
    frame();
    expect(el().style.opacity).toBe("0");
    pointer.reported(CENTRE, performance.now());
    frame();
    expect(el().style.opacity).toBe("1");
    pointer.reported({ ...CENTRE, visible: false }, performance.now());
    frame();
    expect(el().style.opacity).toBe("0");
    pointer.reported(CENTRE, performance.now());
    frame();
    expect(el().style.opacity).toBe("1");
    fitRef.current = null; // no frame laid out (or the element was swapped and not measured yet)
    frame();
    expect(el().style.opacity).toBe("0");
  });

  it("hidden while the surface says so (the loupe is open), shown again after", () => {
    const pointer = overlay();
    const { rerenderWith } = renderPointer(pointer);
    frame();
    expect(el().style.opacity).toBe("1");
    rerenderWith({ hidden: true });
    frame();
    expect(el().style.opacity).toBe("0");
    rerenderWith({ hidden: false });
    frame();
    expect(el().style.opacity).toBe("1");
  });

  it("in drawn mode (and a 1.4 session) nothing is rendered; a model told `drawn` never shows", () => {
    const { rerenderWith } = renderPointer(overlay(), { enabled: false });
    expect(screen.queryByTestId("screen-pointer")).toBeNull();
    expect(queue.size).toBe(0); // no frame loop either
    const drawn = new PointerModel();
    drawn.setMode("drawn");
    drawn.shape({ kind: "arrow" });
    drawn.reported(CENTRE, performance.now());
    rerenderWith({ pointer: drawn, enabled: true });
    frame();
    expect(el().style.opacity).toBe("0");
  });

  it("the frame loop drains the client's one-slot report once per frame (the last report of a frame wins)", () => {
    let slot: PointerReport | null = null;
    const pending = vi.fn(() => {
      const r = slot;
      slot = null;
      return r;
    });
    const pointer = new PointerModel({ pending });
    pointer.setMode("overlay");
    pointer.shape({ kind: "arrow" });
    renderPointer(pointer);
    frame();
    expect(pending).toHaveBeenCalledTimes(1);
    expect(el().style.opacity).toBe("0");
    slot = { x: 0, y: 0, visible: true, gen: 1 };
    slot = { x: 65535, y: 65535, visible: true, gen: 1 }; // two messages in one frame: one slot
    frame();
    expect(pending).toHaveBeenCalledTimes(2);
    expect(el().style.transform).toBe(placed(65535, 65535, 1, 1));
    expect(el().style.opacity).toBe("1");
  });
});

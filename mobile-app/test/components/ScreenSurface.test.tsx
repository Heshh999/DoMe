/**
 * The `<video>`'s metadata probe (device round 2, 2026-10-27): ScreenSurface registers a probe with the session client
 * that reports what the element and the stream say about themselves — ready state, size, the last play() outcome, the
 * frame-callback ticks (requestVideoFrameCallback, or `timeupdate` where it is missing), the stream's and the track's
 * flags — and unregisters it when the picture goes. Over a fake video element; nothing reads a pixel.
 *
 * Protocol 1.5 (design screen-view-2a §4.7): the phone-drawn pointer is a sibling right after the zoom layer (never
 * scaled by it) and hides under the loupe and around a tap's ripple; the touchpad machine is fed box coordinates, its
 * pinch zooms about the fingers and sends nothing; a touchpad move feeds the pointer's prediction with the dx, dy
 * that are sent; taps, loupe clicks and scrolls move it to their point first; zoomed in Touchpad mode the view follows
 * the pointer once a frame, in every pointer mode of the PC; the 1× button appears while zoomed in both modes; a mode
 * switch shows the whole picture again. Two `<video>` elements: a switch's new stream plays on the hidden one, which
 * alone reports frames, and the two swap roles without the visible element's `srcObject` ever being reassigned. The
 * fit the pointer and the follow read is refreshed on the front's metadata and resize.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RIPPLE_POINTER_HIDE_MS, ScreenSurface, type ScreenSurfaceProps } from "../../src/components/ScreenSurface.tsx";
import { PointerModel } from "../../src/lib/pointer.ts";
import { fitContent, followPointer, fromSurface, IDENTITY_VIEW, snapIdentity, viewTransform, zoomAt, type Geometry, type View } from "../../src/lib/screenMap.ts";
import type { ScreenMediaFacts } from "../../src/lib/screenStats.ts";

type Probe = (() => ScreenMediaFacts) | null;

const stream = { id: "s", active: true, getVideoTracks: () => [{ readyState: "live", muted: false, enabled: true }] } as unknown as MediaStream;

function renderSurface(onProbe: (p: Probe) => void, onFrame = vi.fn()) {
  return render(<ScreenSurface stream={stream} mode="direct" canSend={false} scrollDirection="natural" sensitivity={1} dragMode={false} geometry={() => ({ geometry: null, stale: false })} onInput={() => undefined} onViewOnlyTap={() => undefined} onFrame={onFrame} onResize={() => undefined} onPlayBlocked={() => undefined} playEpoch={0} label="Picture" onProbe={onProbe} pointer={new PointerModel()} incomingStream={null} surfaceSize={() => null} cursorMode={null} pcPaused={false} switching={false} />);
}

/** What a real element would report once frames play (jsdom's own element reports nothing useful). */
function looksPlaying(v: HTMLElement) {
  for (const [k, value] of Object.entries({ readyState: 4, networkState: 2, paused: false, videoWidth: 1280, videoHeight: 720, currentTime: 2.04 })) Object.defineProperty(v, k, { configurable: true, value });
  Object.defineProperty(v, "getVideoPlaybackQuality", { configurable: true, value: () => ({ totalVideoFrames: 61, droppedVideoFrames: 1 }) });
}

afterEach(() => {
  vi.restoreAllMocks();
  delete (HTMLVideoElement.prototype as { requestVideoFrameCallback?: unknown }).requestVideoFrameCallback;
});

describe("ScreenSurface: the <video> probe", () => {
  it("registers a probe that reports the element, play() resolved, timeupdate ticks (no rVFC in this browser) and the stream's flags; unregisters on unmount", async () => {
    HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
    let probe: Probe = null;
    const onProbe = vi.fn((p: Probe) => (probe = p));
    const onFrame = vi.fn();
    const view = renderSurface(onProbe, onFrame);
    await act(async () => undefined);
    expect(probe).not.toBeNull();
    const video = screen.getByTestId("screen-video");
    looksPlaying(video);
    fireEvent(video, new Event("timeupdate"));
    fireEvent(video, new Event("timeupdate"));
    expect(onFrame).toHaveBeenCalledTimes(2);
    const facts = probe!();
    expect(facts).toMatchObject({ ready_state: 4, network_state: 2, paused: false, width: 1280, height: 720, current_time: 2, error: null, total_frames: 61, dropped_frames: 1, play: "resolved", rvfc: false, frame_ticks: 2, stream_active: true, track_state: "live", track_muted: false, track_enabled: true });
    expect(facts.first_tick_ms).toEqual(expect.any(Number));
    view.unmount();
    expect(onProbe).toHaveBeenLastCalledWith(null);
  });

  it("a refused play() is reported by the exception's name; requestVideoFrameCallback ticks are counted where it exists", async () => {
    HTMLMediaElement.prototype.play = vi.fn(() => Promise.reject(new DOMException("needs a gesture", "NotAllowedError")));
    const callbacks: Array<() => void> = [];
    (HTMLVideoElement.prototype as { requestVideoFrameCallback?: unknown }).requestVideoFrameCallback = vi.fn((cb: () => void) => callbacks.push(cb));
    let probe: Probe = null;
    renderSurface((p) => (probe = p));
    await act(async () => undefined);
    expect(probe!()).toMatchObject({ play: "rejected:NotAllowedError", rvfc: true, frame_ticks: 0, first_tick_ms: null });
    act(() => callbacks.shift()!());
    act(() => callbacks.shift()!());
    expect(probe!()).toMatchObject({ frame_ticks: 2 });
  });

  it("a play() that throws at once is reported as such", async () => {
    HTMLMediaElement.prototype.play = vi.fn(() => {
      throw new TypeError("no");
    });
    let probe: Probe = null;
    renderSurface((p) => (probe = p));
    await act(async () => undefined);
    expect(probe!().play).toBe("threw:TypeError");
  });
});

// ---- protocol 1.5: the pointer, Touchpad zoom and follow, the 1× button, the two <video> elements ----------------------

const STREAM_A = { id: "a", active: true, getVideoTracks: () => [{ readyState: "live", muted: false, enabled: true }] } as unknown as MediaStream;
const STREAM_B = { id: "b", active: true, getVideoTracks: () => [{ readyState: "live", muted: false, enabled: true }] } as unknown as MediaStream;
const GEOMETRY: Geometry = { type: "geometry", gen: 1, surface_index: 0, width: 1600, height: 900, rotation: 0, reserved: [] };
/** The box sits at (100, 50) in the page, 400×300: a 1600×900 picture fills 400×225 from y = 37.5. */
const BOX = { left: 100, top: 50, width: 400, height: 300 };
const FIT = fitContent({ width: BOX.width, height: BOX.height }, 1600, 900)!;
const PHYSICAL = { width: 1920, height: 1080 };

let frameQueue: Map<number, FrameRequestCallback>;
let frameHandle: number;
let clock: number;

beforeEach(() => {
  frameQueue = new Map();
  frameHandle = 0;
  clock = 10_000;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    frameHandle += 1;
    frameQueue.set(frameHandle, cb);
    return frameHandle;
  });
  vi.stubGlobal("cancelAnimationFrame", (h: number) => frameQueue.delete(h));
  HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
  if (!(globalThis as { PointerEvent?: unknown }).PointerEvent) (globalThis as { PointerEvent?: unknown }).PointerEvent = MouseEvent;
  if (!Element.prototype.setPointerCapture) Element.prototype.setPointerCapture = () => undefined;
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** One animation frame: every callback queued so far runs once. */
function frame(): void {
  act(() => {
    const due = [...frameQueue.values()];
    frameQueue.clear();
    for (const cb of due) cb(performance.now());
  });
}

function surfaceProps(over: Partial<ScreenSurfaceProps> = {}): ScreenSurfaceProps {
  return {
    stream: STREAM_A,
    mode: "direct",
    canSend: true,
    scrollDirection: "natural",
    sensitivity: 1,
    dragMode: false,
    geometry: () => ({ geometry: GEOMETRY, stale: false }),
    onInput: vi.fn(),
    onViewOnlyTap: vi.fn(),
    onFrame: vi.fn(),
    onResize: vi.fn(),
    onPlayBlocked: vi.fn(),
    playEpoch: 0,
    label: "Picture",
    pointer: new PointerModel(),
    incomingStream: null,
    surfaceSize: () => PHYSICAL,
    cursorMode: null,
    pcPaused: false,
    switching: false,
    ...over,
  };
}

/** The box at BOX and the shown picture 1600×900 (jsdom lays nothing out). */
function laidOut(): void {
  const surface = screen.getByTestId("screen-surface");
  surface.getBoundingClientRect = () => ({ ...BOX, right: BOX.left + BOX.width, bottom: BOX.top + BOX.height, x: BOX.left, y: BOX.top, toJSON: () => ({}) }) as DOMRect;
  sized(screen.getByTestId("screen-video"), 1600, 900);
}

function sized(v: HTMLElement, width: number, height: number): void {
  Object.defineProperty(v, "videoWidth", { configurable: true, value: width });
  Object.defineProperty(v, "videoHeight", { configurable: true, value: height });
}

function renderLive(over: Partial<ScreenSurfaceProps> = {}) {
  let p = surfaceProps(over);
  const view = render(<ScreenSurface {...p} />);
  laidOut();
  return {
    ...view,
    props: () => p,
    update: (next: Partial<ScreenSurfaceProps>) => {
      p = { ...p, ...next };
      view.rerender(<ScreenSurface {...p} />);
    },
  };
}

/** A touch at page coordinates (x, y). */
function touch(el: Element, type: "pointerdown" | "pointermove" | "pointerup", id: number, x: number, y: number, dt = 20): void {
  clock += dt;
  const ev = new (window.PointerEvent ?? MouseEvent)(type, { pointerId: id, clientX: x, clientY: y, bubbles: true, pointerType: "touch", isPrimary: id === 1, button: 0 } as PointerEventInit);
  Object.defineProperty(ev, "timeStamp", { value: clock });
  if (!("pointerId" in ev)) Object.defineProperty(ev, "pointerId", { value: id });
  fireEvent(el, ev);
}

/** Two fingers 100 px apart about the box centre (200, 150) spread to 180 px: one pinch step of 1.8 there. */
function pinchOpen(el: Element): void {
  touch(el, "pointerdown", 1, 250, 200, 600);
  touch(el, "pointerdown", 2, 350, 200);
  touch(el, "pointermove", 1, 210, 200);
  touch(el, "pointermove", 2, 390, 200);
  touch(el, "pointerup", 1, 210, 200);
  touch(el, "pointerup", 2, 390, 200);
}

const layer = () => screen.getByTestId("screen-view-layer");
const sentEvents = (onInput: ScreenSurfaceProps["onInput"]) => (onInput as ReturnType<typeof vi.fn>).mock.calls.flatMap((c) => c[0] as Array<Record<string, unknown>>);

describe("ScreenSurface: the pointer drawn by the phone", () => {
  it("sits right after the zoom layer (outside it) and before the loupe; hidden under the loupe; absent unless the session draws it", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const pointer = new PointerModel();
    pointer.setMode("overlay");
    pointer.shape({ kind: "arrow" });
    pointer.reported({ x: 32768, y: 32768, visible: true, gen: 1 }, performance.now());
    const { update } = renderLive({ pointer, cursorMode: "overlay" });
    const el = screen.getByTestId("screen-pointer");
    expect(layer().nextElementSibling).toBe(el);
    expect(layer().contains(el)).toBe(false);
    fireEvent(screen.getByTestId("screen-video"), new Event("loadedmetadata")); // the fit is known now
    frame();
    expect(el.style.opacity).toBe("1");
    const { px, py } = fromSurface(32768, 32768, FIT, IDENTITY_VIEW);
    expect(el.style.transform).toBe(`translate3d(${px - 1}px, ${py - 1}px, 0)`);
    // press and hold: the loupe opens after the pointer in the DOM and the pointer hides under it
    touch(screen.getByTestId("screen-surface"), "pointerdown", 1, 300, 200, 600);
    act(() => {
      vi.advanceTimersByTime(400);
    });
    const loupe = screen.getByTestId("screen-loupe");
    expect(el.compareDocumentPosition(loupe) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    frame();
    expect(el.style.opacity).toBe("0");
    touch(screen.getByTestId("screen-surface"), "pointerup", 1, 300, 200);
    // a `drawn` session (and a 1.4 one) has the pointer in the picture: nothing is rendered here
    update({ cursorMode: "drawn" });
    expect(screen.queryByTestId("screen-pointer")).toBeNull();
    update({ cursorMode: null });
    expect(screen.queryByTestId("screen-pointer")).toBeNull();
  });

  it("direct mode: a tap moves the pointer to its point before it is sent, and the pointer hides for 150 ms around the ripple", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const pointer = new PointerModel();
    pointer.setMode("overlay");
    pointer.reported({ x: 1000, y: 1000, visible: true, gen: 1 }, performance.now());
    const predictTo = vi.spyOn(pointer, "predictTo");
    const onInput = vi.fn();
    renderLive({ pointer, cursorMode: "overlay", onInput });
    const surface = screen.getByTestId("screen-surface");
    fireEvent(screen.getByTestId("screen-video"), new Event("loadedmetadata"));
    frame();
    expect(screen.getByTestId("screen-pointer").style.opacity).toBe("1");
    touch(surface, "pointerdown", 1, 300, 200, 600); // box (200, 150): the middle of the picture
    touch(surface, "pointerup", 1, 300, 200);
    expect(sentEvents(onInput)).toEqual([
      { type: "pointer_move_to", x: 32768, y: 32768, gen: 1 },
      { type: "pointer_button", button: "left", action: "click" },
    ]);
    expect(predictTo).toHaveBeenCalledWith(32768, 32768, expect.any(Number));
    expect(predictTo.mock.invocationCallOrder[0]!).toBeLessThan(onInput.mock.invocationCallOrder[0]!);
    frame();
    expect(screen.getByTestId("screen-pointer").style.opacity).toBe("0"); // the ripple marks the spot
    act(() => {
      vi.advanceTimersByTime(RIPPLE_POINTER_HIDE_MS);
    });
    frame();
    expect(screen.getByTestId("screen-pointer").style.opacity).toBe("1");
    expect(pointer.followSample()).toEqual({ x: 32768, y: 32768 });
  });

  it("direct mode: a two-finger scroll moves the pointer to the midpoint first (the batch jumps the PC's pointer there)", () => {
    const pointer = new PointerModel();
    const predictTo = vi.spyOn(pointer, "predictTo");
    const onInput = vi.fn();
    renderLive({ pointer, onInput });
    const surface = screen.getByTestId("screen-surface");
    touch(surface, "pointerdown", 1, 280, 170, 600);
    touch(surface, "pointerdown", 2, 320, 170);
    for (let i = 1; i <= 6; i++) {
      touch(surface, "pointermove", 1, 280, 170 + i * 12);
      touch(surface, "pointermove", 2, 320, 170 + i * 12);
    }
    touch(surface, "pointerup", 1, 280, 242);
    touch(surface, "pointerup", 2, 320, 242);
    const events = sentEvents(onInput);
    const moveTo = events.find((e) => e.type === "pointer_move_to")!;
    expect(events.some((e) => e.type === "pointer_wheel")).toBe(true);
    expect(predictTo).toHaveBeenCalledWith(moveTo.x, moveTo.y, expect.any(Number));
    expect(predictTo.mock.invocationCallOrder[0]!).toBeLessThan(onInput.mock.invocationCallOrder[0]!);
  });

  it("a view-only surface sends nothing and predicts nothing", () => {
    const pointer = new PointerModel();
    const predictTo = vi.spyOn(pointer, "predictTo");
    const predictMoveBy = vi.spyOn(pointer, "predictMoveBy");
    const onViewOnlyTap = vi.fn();
    const { update } = renderLive({ pointer, canSend: false, onViewOnlyTap });
    const surface = screen.getByTestId("screen-surface");
    touch(surface, "pointerdown", 1, 300, 200, 600);
    touch(surface, "pointerup", 1, 300, 200);
    update({ mode: "touchpad" });
    touch(surface, "pointerdown", 1, 300, 200, 600);
    touch(surface, "pointermove", 1, 340, 210);
    touch(surface, "pointerup", 1, 340, 210);
    expect(onViewOnlyTap).toHaveBeenCalled();
    expect(predictTo).not.toHaveBeenCalled();
    expect(predictMoveBy).not.toHaveBeenCalled();
  });
});

describe("ScreenSurface: Touchpad mode over the picture", () => {
  it("the machine is fed box coordinates: a pinch zooms about the fingers' centre in the box and sends nothing", () => {
    const onInput = vi.fn();
    const onZoom = vi.fn();
    renderLive({ mode: "touchpad", onInput, onZoom });
    pinchOpen(screen.getByTestId("screen-surface"));
    const expected = snapIdentity(zoomAt(IDENTITY_VIEW, 1.8, 200, 150, BOX)); // the page point (300, 200) would zoom elsewhere
    expect(layer().style.transform).toBe(viewTransform(expected));
    expect(onInput).not.toHaveBeenCalled(); // no click, no scroll, no move: a pinch is local
    expect(onZoom).toHaveBeenLastCalledWith(expected.s);
  });

  it("a small pinch snaps back to the whole picture, but steps within one pinch add up", () => {
    renderLive({ mode: "touchpad" });
    const surface = screen.getByTestId("screen-surface");
    touch(surface, "pointerdown", 1, 250, 200, 600);
    touch(surface, "pointerdown", 2, 350, 200);
    touch(surface, "pointermove", 1, 244, 200); // 112 px: 1.12 at the decision, shown
    touch(surface, "pointermove", 2, 356, 200);
    expect(layer().style.transform).not.toBe(viewTransform(IDENTITY_VIEW));
    touch(surface, "pointermove", 1, 248, 200); // back to 106 px: 1.06 is shown as 1×…
    touch(surface, "pointermove", 2, 352, 200);
    expect(layer().style.transform).toBe(viewTransform(IDENTITY_VIEW));
    touch(surface, "pointermove", 1, 230, 200); // …and the same pinch goes on from 1.06, not from 1×
    touch(surface, "pointermove", 2, 370, 200);
    expect(layer().style.transform).toMatch(/scale\(1\.4/);
    touch(surface, "pointerup", 1, 230, 200);
    touch(surface, "pointerup", 2, 370, 200);
  });

  it("a move feeds the pointer's prediction with the dx, dy that are sent, on the screen's physical size; a finger on the glass is reported", () => {
    const pointer = new PointerModel();
    const predictMoveBy = vi.spyOn(pointer, "predictMoveBy");
    const fingerActive = vi.spyOn(pointer, "fingerActive");
    const onInput = vi.fn();
    renderLive({ mode: "touchpad", pointer, onInput });
    const surface = screen.getByTestId("screen-surface");
    touch(surface, "pointerdown", 1, 200, 150, 600);
    expect(fingerActive).toHaveBeenLastCalledWith(true, expect.any(Number));
    touch(surface, "pointermove", 1, 230, 160);
    touch(surface, "pointermove", 1, 260, 175);
    touch(surface, "pointerup", 1, 260, 175);
    expect(fingerActive).toHaveBeenLastCalledWith(false, expect.any(Number));
    const moves = sentEvents(onInput).filter((e) => e.type === "pointer_move");
    expect(moves.length).toBeGreaterThan(0);
    expect(predictMoveBy.mock.calls.map((c) => [c[0], c[1], c[2]])).toEqual(moves.map((m) => [m.dx, m.dy, PHYSICAL]));
    for (const m of moves) {
      const call = predictMoveBy.mock.calls.findIndex((c) => c[0] === m.dx && c[1] === m.dy);
      expect(predictMoveBy.mock.invocationCallOrder[call]!).toBeLessThan(onInput.mock.invocationCallOrder[onInput.mock.calls.findIndex((c) => (c[0] as unknown[]).includes(m))]!);
    }
  });

  it("zoomed, the view follows the pointer once a frame, in a `drawn` or 1.4 session too (seeded at the pinch centre); pans are counted", () => {
    const pointer = new PointerModel();
    const onFollowPan = vi.fn();
    renderLive({ mode: "touchpad", pointer, onFollowPan, cursorMode: null });
    const surface = screen.getByTestId("screen-surface");
    expect(frameQueue.size).toBe(0); // no follow loop at 1×
    pinchOpen(surface);
    expect(pointer.followSample()).toEqual({ x: 32768, y: 32768 }); // the pinch centre, the middle of the picture
    const zoomed = snapIdentity(zoomAt(IDENTITY_VIEW, 1.8, 200, 150, BOX));
    frame();
    expect(layer().style.transform).toBe(viewTransform(zoomed)); // the centre is well inside: nothing moves
    expect(onFollowPan).not.toHaveBeenCalled();
    // slide right a long way: the pointer leaves the inner window and the view pans after it
    touch(surface, "pointerdown", 1, 150, 200, 600);
    for (let i = 1; i <= 12; i++) touch(surface, "pointermove", 1, 150 + i * 50, 200);
    touch(surface, "pointerup", 1, 750, 200);
    const p = pointer.followSample()!;
    expect(p.x).toBeGreaterThan(50_000);
    const expected: View = followPointer(zoomed, fromSurface(p.x, p.y, FIT, zoomed), BOX);
    expect(expected.tx).toBeLessThan(zoomed.tx);
    frame();
    expect(layer().style.transform).toBe(viewTransform(expected));
    expect(onFollowPan).toHaveBeenCalledTimes(1);
    frame();
    expect(onFollowPan).toHaveBeenCalledTimes(1); // nothing moved since: no pan, no render
  });

  it("the 1× button: shown while zoomed in both modes, its touches never reach the gestures, a tap shows the whole picture; a mode switch does too", () => {
    const pointer = new PointerModel();
    const fingerActive = vi.spyOn(pointer, "fingerActive");
    const onInput = vi.fn();
    const { update } = renderLive({ mode: "touchpad", pointer, onInput });
    expect(screen.queryByTestId("screen-zoom-reset")).toBeNull();
    pinchOpen(screen.getByTestId("screen-surface"));
    const reset = screen.getByRole("button", { name: "Back to the whole screen" });
    expect(reset).toHaveTextContent("1×");
    expect(reset).toHaveAttribute("data-testid", "screen-zoom-reset");
    expect(reset).toHaveClass("pointer-events-auto", "absolute", "bottom-3", "left-3", "z-[11]"); // above the page's bottom notices (z-10, later in the tree)
    touch(reset, "pointerdown", 7, 120, 330, 600);
    touch(reset, "pointerup", 7, 120, 330);
    fireEvent.click(reset);
    expect(onInput).not.toHaveBeenCalled(); // not a click on the PC
    expect(layer().style.transform).toBe(viewTransform(IDENTITY_VIEW));
    expect(screen.queryByTestId("screen-zoom-reset")).toBeNull();
    expect(frameQueue.size).toBe(0); // the follow loop stopped with the zoom
    // direct mode: the same button after a pinch
    update({ mode: "direct" });
    pinchOpen(screen.getByTestId("screen-surface"));
    expect(screen.getByRole("button", { name: "Back to the whole screen" })).toBeInTheDocument();
    // switching mode shows the whole picture again, with no finger left on the glass
    fingerActive.mockClear();
    update({ mode: "touchpad" });
    expect(layer().style.transform).toBe(viewTransform(IDENTITY_VIEW));
    expect(screen.queryByTestId("screen-zoom-reset")).toBeNull();
    expect(fingerActive).toHaveBeenCalledWith(false, expect.any(Number));
  });
});

describe("ScreenSurface: two <video> elements for a switch of screens", () => {
  it("the new stream plays on the hidden element, which alone reports frames; the roles swap without the visible element's srcObject being set again", () => {
    const onFrame = vi.fn();
    const onIncomingFrame = vi.fn();
    const { update } = renderLive({ onFrame, onIncomingFrame });
    const front = screen.getByTestId("screen-video") as HTMLVideoElement;
    const back = screen.getByTestId("screen-video-incoming") as HTMLVideoElement;
    expect(front.srcObject).toBe(STREAM_A);
    expect(back.srcObject).toBeNull();
    expect(front).toHaveClass("z-[1]", "bg-black");
    expect(back).not.toHaveClass("z-[1]");
    expect(back.style.display).not.toBe("none"); // under the front one, not display:none, so Safari keeps decoding it
    // every srcObject write on the element that is about to become the visible one
    const writes: Array<MediaStream | null> = [];
    let value: MediaStream | null = null;
    Object.defineProperty(back, "srcObject", { configurable: true, get: () => value, set: (v: MediaStream | null) => (writes.push(v), (value = v)) });
    // zoomed in before the switch
    pinchOpen(screen.getByTestId("screen-surface"));
    expect(layer().style.transform).not.toBe(viewTransform(IDENTITY_VIEW));

    update({ incomingStream: STREAM_B, switching: true });
    expect(back.srcObject).toBe(STREAM_B);
    expect(front.srcObject).toBe(STREAM_A); // the last picture stays under the overlay
    fireEvent(front, new Event("timeupdate"));
    expect(onFrame).not.toHaveBeenCalled(); // the client could not tell it from the new picture's first frame
    fireEvent(back, new Event("timeupdate"));
    expect(onIncomingFrame).toHaveBeenCalledTimes(1);
    expect(onFrame).not.toHaveBeenCalled();

    // the client moved the new stream into `stream`: the roles swap by CSS
    update({ stream: STREAM_B, incomingStream: null, switching: false });
    expect(screen.getByTestId("screen-video")).toBe(back);
    expect(screen.getByTestId("screen-video-incoming")).toBe(front);
    expect(back).toHaveClass("z-[1]", "bg-black");
    expect(writes).toEqual([STREAM_B]); // set once while it was hidden, never again while visible
    expect(back.srcObject).toBe(STREAM_B);
    expect(front.srcObject).toBeNull(); // the old picture is let go
    expect(layer().style.transform).toBe(viewTransform(IDENTITY_VIEW)); // the new screen starts whole
    fireEvent(back, new Event("timeupdate"));
    expect(onFrame).toHaveBeenCalledTimes(1);
    fireEvent(front, new Event("timeupdate"));
    expect(onFrame).toHaveBeenCalledTimes(1);
    expect(onIncomingFrame).toHaveBeenCalledTimes(1);
  });

  it("the hidden element's refused play() asks for the tap; the tap plays both; a failed switch takes the request back", async () => {
    const played: HTMLMediaElement[] = [];
    HTMLMediaElement.prototype.play = vi.fn(function (this: HTMLMediaElement) {
      played.push(this);
      return this.srcObject === STREAM_B ? Promise.reject(new DOMException("needs a gesture", "NotAllowedError")) : Promise.resolve();
    });
    const onPlayBlocked = vi.fn();
    const { update } = renderLive({ onPlayBlocked });
    await act(async () => undefined);
    expect(onPlayBlocked).toHaveBeenLastCalledWith(false);
    const back = screen.getByTestId("screen-video-incoming");
    update({ incomingStream: STREAM_B, switching: true });
    await act(async () => undefined);
    expect(onPlayBlocked).toHaveBeenLastCalledWith(true);
    played.length = 0;
    update({ playEpoch: 1 });
    await act(async () => undefined);
    expect(new Set(played)).toEqual(new Set([screen.getByTestId("screen-video"), back]));
    // the switch failed and the old session was kept: nothing is waiting for a tap any more
    update({ incomingStream: null, switching: false });
    await act(async () => undefined);
    expect(onPlayBlocked).toHaveBeenLastCalledWith(false);
  });

  it("the fit the pointer reads follows the front's metadata and its resize", () => {
    const pointer = new PointerModel();
    pointer.setMode("overlay");
    pointer.reported({ x: 65535, y: 65535, visible: true, gen: 1 }, performance.now());
    const onResize = vi.fn();
    render(<ScreenSurface {...surfaceProps({ pointer, cursorMode: "overlay", onResize })} />);
    frame();
    expect(screen.getByTestId("screen-pointer").style.opacity).toBe("0"); // nothing laid out: no fit, no pointer
    laidOut();
    const video = screen.getByTestId("screen-video");
    fireEvent(video, new Event("loadedmetadata"));
    frame();
    let at = fromSurface(65535, 65535, FIT, IDENTITY_VIEW);
    expect(screen.getByTestId("screen-pointer").style.transform).toBe(`translate3d(${at.px - 1}px, ${at.py - 1}px, 0)`);
    // the PC turned its screen: a portrait picture in the same box
    sized(video, 900, 1600);
    fireEvent(video, new Event("resize"));
    expect(onResize).toHaveBeenCalledTimes(1);
    frame();
    at = fromSurface(65535, 65535, fitContent({ width: BOX.width, height: BOX.height }, 900, 1600)!, IDENTITY_VIEW);
    expect(screen.getByTestId("screen-pointer").style.transform).toBe(`translate3d(${at.px - 1}px, ${at.py - 1}px, 0)`);
  });
});

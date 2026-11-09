/**
 * The live picture and the touch layer above it (docs/design/screen-view.md §8.3, §16), following the device-proven
 * `TouchpadSurface` pattern: `touch-action: none`, a non-passive `touchmove` that prevents scrolling, pointer capture,
 * cancel on rotation or when the page is hidden.
 *
 * Layers, bottom to top: the box (`object-fit: contain`, black letterbox) → the zoom/pan layer (`transform-origin: 0 0`)
 * holding two `<video playsinline muted>` (no controls, `pointer-events: none`, no callout) → the pointer drawn by the
 * phone (`ScreenPointer`, outside the zoom layer so a pinch never scales it) → the tap ripple, the loupe, the hint and
 * the 1× button. A `<video>` is only ever a `srcObject` sink: frame timing comes from `requestVideoFrameCallback`
 * (metadata only), with `timeupdate` as the fallback; nothing here reads a pixel (test/no-pixel-access.test.ts).
 * `onProbe` hands the session client a probe of the shown element's own metadata (ready state, size, the last play()
 * outcome, frame-callback ticks, the stream's and track's flags; lib/screenStats.ts `probeVideo`) for the diagnostics
 * bundle and the test-build readout.
 *
 * Two `<video>` elements (protocol 1.5 Next screen; design screen-view-2a §4.7, D29): the FRONT one shows `stream`;
 * during a switch of screens the BACK one, under the front (which has a black background), plays the new session's
 * `incomingStream` and feeds `onIncomingFrame` from its own frame callback, while the front's frames are not reported
 * (the client cannot tell them apart once the new stream exists). When the client moves the new stream into `stream`
 * (its first frame), the two swap roles by CSS: `srcObject` is never reassigned on the visible element during a
 * switch, so the last picture stays under the "Switching…" overlay. Outside a switch the front behaves as before.
 *
 * Direct mode feeds `DirectGestureMachine`: taps, the loupe click and two-finger scrolls become signed input events
 * through `lib/screenInput.ts`; pinch and one-finger pan only change the local view. Touchpad mode swaps in the
 * relative `GestureMachine` over the same picture (drags and precision), with the Drag toggle the page passes in; it is
 * fed box coordinates, and its pinch zooms the picture too (nothing is sent). Zoomed in Touchpad mode, the view follows
 * the pointer (`PointerModel.followSample()`) once a frame; in a `drawn` or 1.4 session nothing reports the pointer, so
 * what it follows is a guess from the first pinch centre and the moves sent, never corrected (KNOWN_ISSUES 47). The
 * gestures that are sent also move the phone-drawn pointer at once: a touchpad move by the same `dx, dy` the batch
 * carries, a tap, loupe click or scroll to its point.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";

import { DirectGestureMachine } from "../lib/directGestures.ts";
import { GestureMachine, type GestureOutput } from "../lib/gestures.ts";
import type { InputEvent } from "../lib/input.ts";
import type { PointerModel } from "../lib/pointer.ts";
import { directEvents, dropHint, loupeAim, type SurfaceContext } from "../lib/screenInput.ts";
import { playOutcome, probeVideo, type FrameCounters, type MediaLike, type ScreenMediaFacts } from "../lib/screenStats.ts";
import { fitContent, followPointer, fromSurface, IDENTITY_VIEW, panBy, snapIdentity, toSurface, viewTransform, zoomAt, type Box, type Fit, type Geometry, type View } from "../lib/screenMap.ts";
import { ScreenLoupe } from "./ScreenLoupe.tsx";
import { ScreenPointer } from "./ScreenPointer.tsx";

export type SurfaceMode = "direct" | "touchpad";

/** The stage's strip buttons (the page's header, the Drag toggle, the 1× button over the picture). */
export const STRIP_BUTTON = "tap dome-press inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-white/25 bg-white/10 px-3 text-xs font-semibold text-white disabled:opacity-40";

/** The phone-drawn pointer stays hidden this long after a tap's ripple (D17). */
export const RIPPLE_POINTER_HIDE_MS = 150;

export interface ScreenSurfaceProps {
  stream: MediaStream | null;
  mode: SurfaceMode;
  /** Pointer input is live and granted: taps and scrolls are sent. Otherwise they are dropped (view only). */
  canSend: boolean;
  scrollDirection: "natural" | "standard";
  sensitivity: number;
  /** Touchpad mode's Drag toggle (holding the left button while moving). */
  dragMode: boolean;
  /** The geometry a tap may use right now (`GeometryTracker.current`), and whether one is being held back. */
  geometry: () => { geometry: Geometry | null; stale: boolean };
  onInput: (events: InputEvent[]) => void;
  /** A tap that could not be sent because the grant is view-only or input is not live. */
  onViewOnlyTap: () => void;
  /** A frame of the shown picture (not reported while a switch's new picture exists: see `onIncomingFrame`). */
  onFrame: () => void;
  onResize: () => void;
  onPlayBlocked: (blocked: boolean) => void;
  /** Bumped by the page's "Tap to show the picture" button: play() again inside that user gesture (both elements). */
  playEpoch: number;
  label: string;
  /** Registers a probe of the shown `<video>`'s metadata (never pixels) while mounted; null on unmount. */
  onProbe?: (probe: (() => ScreenMediaFacts) | null) => void;
  /** The pointer drawn by the phone (`client.pointer`). */
  pointer: PointerModel;
  /** The new session's stream during a switch of screens, for the hidden second `<video>`. */
  incomingStream: MediaStream | null;
  /** The shown screen's PHYSICAL size (`client.surfaceSize()`), for the Touchpad prediction. */
  surfaceSize: () => { width: number; height: number } | null;
  /** The session's pointer mode: `overlay` draws the pointer here; `drawn` or null (1.4): it is in the picture. */
  cursorMode: "drawn" | "overlay" | null;
  /** The PC paused the picture (the pointer is hidden). */
  pcPaused: boolean;
  /** A switch of screens is in flight (the pointer is hidden). */
  switching: boolean;
  /** The zoomed Touchpad view panned to keep the pointer in sight (`client.noteFollowPan`). */
  onFollowPan?: () => void;
  /** A frame of the switch's new picture (`client.onVideoFrame`); defaults to `onFrame`. */
  onIncomingFrame?: () => void;
  /** The zoom shown after a pinch step (`client.noteZoom`, the readout's `zoom: max`). */
  onZoom?: (scale: number) => void;
}

function touchpadEvent(o: Exclude<GestureOutput, { type: "pinch" }>): InputEvent {
  switch (o.type) {
    case "move":
      return { type: "pointer_move", dx: o.dx, dy: o.dy };
    case "scroll":
      return { type: "pointer_scroll", dx: o.dx, dy: o.dy };
    case "click":
      return { type: "pointer_button", button: o.button, action: "click" };
    case "button":
      return { type: "pointer_button", button: o.button, action: o.action };
  }
}

/** The touchpad machine's outputs as input events; a pinch zooms the picture on the phone and is never sent. */
function touchpadEvents(outs: GestureOutput[]): InputEvent[] {
  return outs.flatMap((o) => (o.type === "pinch" ? [] : [touchpadEvent(o)]));
}

type VideoWithFrames = HTMLVideoElement & { requestVideoFrameCallback?: (cb: () => void) => number; cancelVideoFrameCallback?: (h: number) => void };

/** Which of the two elements is which. */
type Role = "a" | "b";
const otherRole = (r: Role): Role => (r === "a" ? "b" : "a");

/** play() outcome and frame-callback ticks for the probe; `attachedAt` is when `stream` was attached to the element. */
interface VideoCounters extends FrameCounters {
  attachedAt: number;
  stream: MediaStream | null;
}

const newCounters = (): VideoCounters => ({ play: "none", rvfc: false, ticks: 0, firstTickMs: null, attachedAt: 0, stream: null });

/** What one `<video>` reports to the surface (stable; reads the latest props through the surface's own refs). */
interface VideoEvents {
  frame(isFront: boolean): void;
  resized(): void;
  metadata(): void;
  played(role: Role, blocked: boolean | "detached"): void;
}

/** Show `stream` on the element (written only when it differs); undone (null) when it changes or the element goes. */
function attachStream(v: HTMLVideoElement | null, stream: MediaStream | null): (() => void) | undefined {
  if (!v) return undefined;
  if (v.srcObject !== stream) v.srcObject = stream;
  if (!stream) return undefined;
  return () => {
    v.srcObject = null;
  };
}

/** Frame timing (metadata only) of the element while it has a stream; the counters restart with each new stream. */
function watchFrames(video: HTMLVideoElement | null, c: VideoCounters, assigned: MediaStream | null, isFront: boolean, events: VideoEvents): (() => void) | undefined {
  const v = video as VideoWithFrames | null;
  if (!v || !assigned) return undefined;
  if (c.stream !== assigned) {
    c.stream = assigned;
    c.rvfc = typeof v.requestVideoFrameCallback === "function";
    c.ticks = 0;
    c.firstTickMs = null;
    c.attachedAt = Date.now();
  }
  let cancelled = false;
  let handle: number | null = null;
  const onFrame = () => {
    c.ticks += 1;
    if (c.firstTickMs === null) c.firstTickMs = Date.now() - c.attachedAt;
    events.frame(isFront);
  };
  const tick = () => {
    if (cancelled) return;
    onFrame();
    handle = v.requestVideoFrameCallback ? v.requestVideoFrameCallback(tick) : null;
  };
  if (c.rvfc) handle = v.requestVideoFrameCallback!(tick);
  else v.addEventListener("timeupdate", onFrame);
  const onResize = () => {
    if (isFront) events.resized();
  };
  const onMetadata = () => {
    if (isFront) events.metadata();
  };
  v.addEventListener("resize", onResize);
  v.addEventListener("loadedmetadata", onMetadata);
  return () => {
    cancelled = true;
    if (handle !== null) v.cancelVideoFrameCallback?.(handle);
    v.removeEventListener("timeupdate", onFrame);
    v.removeEventListener("resize", onResize);
    v.removeEventListener("loadedmetadata", onMetadata);
  };
}

/** play(): the "Show screen" tap is the gesture iOS wants; a refusal (Low Power Mode) asks for one more tap. */
function playVideo(role: Role, v: HTMLVideoElement | null, c: VideoCounters, assigned: MediaStream | null, events: VideoEvents): void {
  if (!v) return;
  if (!assigned) {
    events.played(role, "detached");
    return;
  }
  let result: Promise<void> | undefined;
  try {
    result = v.play?.();
  } catch (e) {
    c.play = playOutcome("threw", e);
    events.played(role, true);
    return;
  }
  if (result && typeof result.then === "function") {
    c.play = "pending";
    result.then(
      () => {
        c.play = "resolved";
        events.played(role, false);
      },
      (e: unknown) => {
        c.play = playOutcome("rejected", e);
        events.played(role, true);
      },
    );
  } else c.play = "returned";
}

/**
 * One of the two `<video>` elements: its stream (set once per stream, so a change of role alone never touches
 * `srcObject`), its frame timing (the front reports `onFrame`, the back a switch's `onIncomingFrame`) and its play()
 * (the same handling for both).
 */
function useVideoElement(role: Role, video: RefObject<HTMLVideoElement | null>, counters: RefObject<VideoCounters>, assigned: MediaStream | null, isFront: boolean, playEpoch: number, events: VideoEvents): void {
  useEffect(() => attachStream(video.current, assigned), [video, assigned]);
  useEffect(() => watchFrames(video.current, counters.current, assigned, isFront, events), [video, counters, assigned, isFront, events]);
  useEffect(() => playVideo(role, video.current, counters.current, assigned, events), [role, video, counters, assigned, playEpoch, events]);
}

export function ScreenSurface(props: ScreenSurfaceProps) {
  const { stream, incomingStream, mode, scrollDirection, sensitivity, dragMode, playEpoch, label, pointer, cursorMode, pcPaused, switching } = props;
  const boxRef = useRef<HTMLDivElement>(null);
  const videoA = useRef<HTMLVideoElement>(null);
  const videoB = useRef<HTMLVideoElement>(null);
  const countersA = useRef<VideoCounters>(newCounters());
  const countersB = useRef<VideoCounters>(newCounters());
  const cb = useRef(props);
  // a layout effect: a frame callback must never see the props of the render before (D29: the front's frames stop
  // the moment a switch's new stream exists)
  useLayoutEffect(() => {
    cb.current = props;
  });
  const [direct] = useState(() => new DirectGestureMachine({ scrollDirection }));
  const [touchpad] = useState(() => new GestureMachine({ scrollDirection, sensitivity }));
  const [view, setView] = useState<View>(IDENTITY_VIEW);
  const viewRef = useRef<View>(IDENTITY_VIEW);
  // a view reset during render (a new picture, a mode switch) reaches the ref and the direct machine here
  useLayoutEffect(() => {
    viewRef.current = view;
    direct.setOptions({ zoomed: view.s > 1 });
  }, [view, direct]);
  /** Within one pinch the unsnapped zoom is kept (each step is small); `snapIdentity` applies to what is shown. */
  const pinchViewRef = useRef<View | null>(null);
  /** The picture's letterbox fit in the box and the box's size, for the pointer and the follow (null: no frame yet). */
  const fitRef = useRef<Fit | null>(null);
  const boxSizeRef = useRef<Box | null>(null);
  const [loupe, setLoupe] = useState<{ start: { x: number; y: number }; aim: { x: number; y: number }; box: Box } | null>(null);
  const loupeRef = useRef<{ start: { x: number; y: number }; aim: { x: number; y: number } } | null>(null);
  const [ripple, setRipple] = useState<{ x: number; y: number; n: number } | null>(null);
  const [rippleRecent, setRippleRecent] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const holdTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rippleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // the two <video> roles: the client moving the incoming stream into `stream` is the swap (render-time update)
  const [roles, setRoles] = useState<{ front: Role; stream: MediaStream | null; incoming: MediaStream | null }>(() => ({ front: "a", stream, incoming: incomingStream }));
  let current = roles;
  if (roles.stream !== stream || roles.incoming !== incomingStream) {
    const swap = roles.incoming !== null && roles.incoming === stream && roles.stream !== stream;
    current = { front: swap ? otherRole(roles.front) : roles.front, stream, incoming: incomingStream };
    setRoles(current);
  }
  const front = current.front;
  // a new picture on the front (a fresh session, or the swap of a switch) starts unzoomed (render-time reset)
  const [seenPicture, setSeenPicture] = useState({ stream, front });
  if (seenPicture.stream !== stream || seenPicture.front !== front) {
    setSeenPicture({ stream, front });
    setView(IDENTITY_VIEW);
  }
  const frontRole = useRef<Role>(front);
  useLayoutEffect(() => {
    frontRole.current = front;
  }, [front]);

  const frontVideo = useCallback((): HTMLVideoElement | null => (frontRole.current === "a" ? videoA : videoB).current, []);

  /** The fit of the shown picture in the box, for every reader that runs outside a gesture (the pointer, the follow). */
  const refreshFit = useCallback(() => {
    const box = boxRef.current;
    if (!box) return;
    const r = box.getBoundingClientRect();
    const size = { width: r.width, height: r.height };
    boxSizeRef.current = size;
    const v = frontVideo();
    fitRef.current = v ? fitContent(size, v.videoWidth, v.videoHeight) : null;
  }, [frontVideo]);

  // the probe: metadata the shown element and its stream report about themselves. Registered first, so it is
  // unregistered before the elements' streams are detached on unmount.
  const onProbe = props.onProbe;
  useEffect(() => {
    if (!onProbe) return;
    onProbe(() => {
      const v = frontVideo();
      // the stream's flags are read where it already is (the element's srcObject); it is kept nowhere else
      return probeVideo(v, (v?.srcObject ?? null) as MediaLike | null, (frontRole.current === "a" ? countersA : countersB).current, Date.now());
    });
    return () => onProbe(null);
  }, [onProbe, frontVideo]);

  const applyView = useCallback(
    (next: View) => {
      viewRef.current = next;
      setView(next);
      direct.setOptions({ zoomed: next.s > 1 });
    },
    [direct],
  );

  useEffect(() => direct.setOptions({ scrollDirection }), [direct, scrollDirection]);
  useEffect(() => touchpad.setOptions({ scrollDirection, sensitivity }), [touchpad, scrollDirection, sensitivity]);

  // both elements report through one stable object that reads the latest props
  const [videoEvents] = useState<VideoEvents>(() => {
    const blocked: Record<Role, boolean> = { a: false, b: false };
    return {
      frame: (isFront) => {
        const p = cb.current;
        if (!isFront) (p.onIncomingFrame ?? p.onFrame)();
        else if (p.incomingStream === null) p.onFrame();
      },
      resized: () => {
        refreshFit();
        cb.current.onResize();
      },
      metadata: () => refreshFit(),
      played: (role, value) => {
        if (value === "detached") {
          // an element without a stream has nothing to play: its refusal no longer asks for a tap
          if (!blocked[role]) return;
          blocked[role] = false;
        } else blocked[role] = value;
        cb.current.onPlayBlocked(blocked.a || blocked.b);
      },
    };
  });
  useVideoElement("a", videoA, countersA, front === "a" ? stream : incomingStream, front === "a", playEpoch, videoEvents);
  useVideoElement("b", videoB, countersB, front === "b" ? stream : incomingStream, front === "b", playEpoch, videoEvents);

  // …with its own fit, and no pinch carried over
  useEffect(() => {
    pinchViewRef.current = null;
    refreshFit();
  }, [stream, front, refreshFit]);

  // the box changes size (rotation, the keyboard panel): the fit follows
  useEffect(() => {
    const box = boxRef.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => refreshFit());
    observer.observe(box);
    return () => observer.disconnect();
  }, [refreshFit]);

  const flashHint = useCallback((text: string | null) => {
    if (hintTimer.current) clearTimeout(hintTimer.current);
    setHint(text);
    if (text) hintTimer.current = setTimeout(() => setHint(null), 2200);
  }, []);

  const context = useCallback((): { ctx: SurfaceContext; rect: DOMRect } | null => {
    const box = boxRef.current;
    const video = frontVideo();
    if (!box) return null;
    refreshFit();
    const rect = box.getBoundingClientRect();
    const g = cb.current.geometry();
    return { rect, ctx: { box: { width: rect.width, height: rect.height }, videoWidth: video?.videoWidth ?? 0, videoHeight: video?.videoHeight ?? 0, view: viewRef.current, geometry: g.geometry, geometryStale: g.stale } };
  }, [frontVideo, refreshFit]);

  const send = useCallback(
    (events: InputEvent[], at?: { x: number; y: number }) => {
      const p = cb.current;
      if (!p.canSend) {
        p.onViewOnlyTap();
        return;
      }
      // the PC's pointer jumps to a tap, loupe click or scroll point: so does the phone-drawn one, before the send
      const first = events[0];
      if (first?.type === "pointer_move_to") p.pointer.predictTo(first.x, first.y, performance.now());
      p.onInput(events);
      if (at) {
        setRipple((r) => ({ x: at.x, y: at.y, n: (r?.n ?? 0) + 1 }));
        setRippleRecent(true);
        if (rippleTimer.current) clearTimeout(rippleTimer.current);
        rippleTimer.current = setTimeout(() => setRippleRecent(false), RIPPLE_POINTER_HIDE_MS);
      }
    },
    [],
  );

  /** A pinch step in either mode: about the fingers' centre (box px), snapped back to 1× close to it. */
  const zoomBy = useCallback(
    (scale: number, cx: number, cy: number, box: Box) => {
      const raw = zoomAt(pinchViewRef.current ?? viewRef.current, scale, cx, cy, box);
      pinchViewRef.current = raw;
      const shown = snapIdentity(raw);
      applyView(shown);
      cb.current.onZoom?.(shown.s);
    },
    [applyView],
  );

  /** Drain the direct machine: local view changes, the loupe, and the events to sign. */
  const drainDirect = useCallback(() => {
    const c = context();
    for (const out of direct.take()) {
      if (!c) continue;
      const box = c.ctx.box;
      switch (out.type) {
        case "pinch":
          zoomBy(out.scale, out.cx, out.cy, box);
          continue;
        case "pan":
          applyView(panBy(viewRef.current, out.dx, out.dy, box));
          continue;
        case "loupe": {
          if (out.phase === "start") {
            loupeRef.current = { start: { x: out.x, y: out.y }, aim: { x: out.x, y: out.y } };
            setLoupe({ ...loupeRef.current, box });
            continue;
          }
          if (out.phase === "move" && loupeRef.current) {
            loupeRef.current = { ...loupeRef.current, aim: loupeAim(loupeRef.current.start, { x: out.x, y: out.y }) };
            setLoupe({ ...loupeRef.current, box });
            continue;
          }
          const aim = loupeRef.current?.aim;
          loupeRef.current = null;
          setLoupe(null);
          if (out.phase !== "end") continue;
          const r = directEvents(out, { ...c.ctx, view: viewRef.current }, aim);
          if (r.kind === "send") send(r.events, aim);
          else if (r.kind === "drop") flashHint(dropHint(r.reason));
          continue;
        }
        default: {
          const r = directEvents(out, { ...c.ctx, view: viewRef.current });
          if (r.kind === "send") send(r.events, out.type === "tap" ? { x: out.x, y: out.y } : undefined);
          else if (r.kind === "drop") flashHint(dropHint(r.reason));
        }
      }
    }
  }, [context, direct, applyView, send, flashHint, zoomBy]);

  /** Drain the touchpad machine: a pinch zooms the picture (nothing is sent); everything else is signed and sent. */
  const drainTouchpad = useCallback(() => {
    const outs = touchpad.take();
    const p = cb.current;
    for (const o of outs) {
      if (o.type !== "pinch") continue;
      const c = context();
      if (!c) continue;
      // nothing known to follow yet (a `drawn` or 1.4 session never reports): the pinch centre is the follow point
      if (p.cursorMode !== "overlay" && p.pointer.followSample() === null) p.pointer.seedFollow(fitRef.current ? toSurface(o.cx, o.cy, fitRef.current, viewRef.current) : null);
      zoomBy(o.scale, o.cx, o.cy, c.ctx.box);
    }
    const events = touchpadEvents(outs);
    if (events.length === 0) return;
    if (p.canSend) {
      // the same dx, dy the batch carries (PC pixels, sensitivity applied), on the shown screen's physical size
      const now = performance.now();
      for (const o of outs) if (o.type === "move") p.pointer.predictMoveBy(o.dx, o.dy, p.surfaceSize(), now);
    }
    send(events);
  }, [touchpad, context, zoomBy, send]);

  const drain = useCallback(() => (cb.current.mode === "direct" ? drainDirect() : drainTouchpad()), [drainDirect, drainTouchpad]);

  // switching modes releases whatever the other machine held (never a click); an open loupe closes and the picture
  // is whole again (render-time reset)
  const [seenMode, setSeenMode] = useState(mode);
  if (seenMode !== mode) {
    setSeenMode(mode);
    setLoupe(null);
    setView(IDENTITY_VIEW);
  }
  useEffect(() => {
    direct.cancel();
    direct.take();
    loupeRef.current = null;
    touchpad.cancel();
    touchpad.setDragMode(false);
    const release = touchpadEvents(touchpad.take());
    if (release.length > 0 && cb.current.canSend) cb.current.onInput(release);
    // no pinch carries over and no finger is on the glass for the new machine
    pinchViewRef.current = null;
    cb.current.pointer.fingerActive(false, performance.now());
  }, [mode, direct, touchpad]);
  // touchpad mode's Drag toggle: holding or releasing the left button is sent at once
  useEffect(() => {
    if (mode !== "touchpad") return;
    touchpad.setDragMode(dragMode);
    const events = touchpadEvents(touchpad.take());
    if (events.length > 0 && cb.current.canSend) cb.current.onInput(events);
  }, [dragMode, mode, touchpad]);

  // zoomed in Touchpad mode the view follows the pointer, once a frame, whatever the PC's pointer mode (D19)
  const zoomed = view.s > 1;
  useEffect(() => {
    if (mode !== "touchpad" || !zoomed || typeof requestAnimationFrame !== "function") return;
    let handle = 0;
    const frame = () => {
      const fit = fitRef.current;
      const box = boxSizeRef.current;
      const v = viewRef.current;
      const p = cb.current.pointer.followSample();
      // the fingers own the view while they pinch
      if (fit && box && p && v.s > 1 && pinchViewRef.current === null) {
        const next = followPointer(v, fromSurface(p.x, p.y, fit, v), box);
        if (next.s !== v.s || next.tx !== v.tx || next.ty !== v.ty) {
          applyView(next);
          cb.current.onFollowPan?.();
        }
      }
      handle = requestAnimationFrame(frame);
    };
    handle = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(handle);
  }, [mode, zoomed, applyView]);

  const cancelAll = useCallback(() => {
    if (holdTimer.current) clearTimeout(holdTimer.current);
    holdTimer.current = null;
    pinchViewRef.current = null;
    if (cb.current.mode === "direct") direct.cancel();
    else {
      touchpad.cancel();
      cb.current.pointer.fingerActive(false, performance.now());
    }
    drain();
  }, [direct, touchpad, drain]);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    // React's touchmove is passive; iOS needs preventDefault to keep the page still under the fingers
    const block = (e: TouchEvent) => e.preventDefault();
    el.addEventListener("touchmove", block, { passive: false });
    const onVisibility = () => {
      if (document.visibilityState !== "visible") cancelAll();
    };
    window.addEventListener("orientationchange", cancelAll);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      el.removeEventListener("touchmove", block);
      window.removeEventListener("orientationchange", cancelAll);
      document.removeEventListener("visibilitychange", onVisibility);
      if (holdTimer.current) clearTimeout(holdTimer.current);
      if (hintTimer.current) clearTimeout(hintTimer.current);
      if (rippleTimer.current) clearTimeout(rippleTimer.current);
    };
  }, [cancelAll]);

  const point = (e: ReactPointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const armHold = () => {
    if (holdTimer.current) clearTimeout(holdTimer.current);
    holdTimer.current = null;
    const deadline = direct.holdDeadline();
    if (!deadline) return;
    holdTimer.current = setTimeout(() => {
      holdTimer.current = null;
      direct.hold(deadline.id, deadline.at);
      drainDirect();
    }, direct.options.holdMs);
  };

  const down = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* capture unsupported */
    }
    pinchViewRef.current = null;
    const p = point(e);
    if (mode === "direct") {
      direct.pointerDown(e.pointerId, p.x, p.y, e.timeStamp);
      armHold();
      drainDirect();
    } else {
      // box coordinates: the machine uses deltas and distances only, and a pinch centre is then in box px
      touchpad.pointerDown(e.pointerId, p.x, p.y, e.timeStamp);
      pointer.fingerActive(true, performance.now());
      drainTouchpad();
    }
  };
  const move = (e: ReactPointerEvent<HTMLDivElement>) => {
    const p = point(e);
    if (mode === "direct") {
      direct.pointerMove(e.pointerId, p.x, p.y, e.timeStamp);
      if (!direct.holdDeadline() && holdTimer.current) {
        clearTimeout(holdTimer.current);
        holdTimer.current = null;
      }
      drainDirect();
    } else {
      touchpad.pointerMove(e.pointerId, p.x, p.y, e.timeStamp);
      drainTouchpad();
    }
  };
  const up = (e: ReactPointerEvent<HTMLDivElement>) => {
    pinchViewRef.current = null;
    if (mode === "direct") {
      if (holdTimer.current && direct.activeFingers <= 1) {
        clearTimeout(holdTimer.current);
        holdTimer.current = null;
      }
      direct.pointerUp(e.pointerId, e.timeStamp);
      drainDirect();
    } else {
      touchpad.pointerUp(e.pointerId, e.timeStamp);
      if (touchpad.activeFingers === 0) pointer.fingerActive(false, performance.now());
      drainTouchpad();
    }
  };

  /** The 1× button is not part of the picture: its touches never reach the gesture machines. */
  const keepToButton = (e: ReactPointerEvent<HTMLButtonElement>) => e.stopPropagation();

  const videoClass = (isFront: boolean) => `pointer-events-none absolute inset-0 h-full w-full object-contain${isFront ? " z-[1] bg-black" : ""}`;

  return (
    <div
      ref={boxRef}
      role="application"
      aria-label={label}
      data-testid="screen-surface"
      data-mode={mode}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={cancelAll}
      onLostPointerCapture={(e) => {
        // capture is released after every pointerup; only a capture lost by a finger still down cancels (never a click)
        if (mode === "direct" ? direct.hasFinger(e.pointerId) : touchpad.hasFinger(e.pointerId)) cancelAll();
      }}
      onContextMenu={(e) => e.preventDefault()}
      style={{ touchAction: "none", WebkitTouchCallout: "none", WebkitUserSelect: "none" }}
      className="relative h-full w-full select-none overflow-hidden bg-black"
    >
      <div className="absolute left-0 top-0 h-full w-full" style={{ transform: viewTransform(view), transformOrigin: "0 0" }} data-testid="screen-view-layer">
        {/* the back element sits UNDER the front one (not display:none), so Safari keeps decoding it */}
        <video ref={videoA} className={videoClass(front === "a")} playsInline muted disablePictureInPicture aria-hidden={front !== "a"} data-testid={front === "a" ? "screen-video" : "screen-video-incoming"} />
        <video ref={videoB} className={videoClass(front === "b")} playsInline muted disablePictureInPicture aria-hidden={front !== "b"} data-testid={front === "b" ? "screen-video" : "screen-video-incoming"} />
      </div>
      <ScreenPointer pointer={pointer} fitRef={fitRef} viewRef={viewRef} enabled={cursorMode === "overlay"} hidden={loupe !== null || pcPaused || switching || rippleRecent} />
      {ripple ? <span key={ripple.n} aria-hidden="true" className="dome-tap-ripple pointer-events-none absolute h-9 w-9 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white/90" style={{ left: ripple.x, top: ripple.y }} /> : null}
      {loupe ? <ScreenLoupe stream={stream} aim={loupe.aim} box={loupe.box} view={view} /> : null}
      {hint ? (
        <p role="status" className="pointer-events-none absolute inset-x-4 bottom-4 z-10 rounded-full bg-black/75 px-4 py-2 text-center text-sm text-white">
          {hint}
        </p>
      ) : null}
      {zoomed ? (
        <button
          type="button"
          data-testid="screen-zoom-reset"
          aria-label="Back to the whole screen"
          // z-[11]: above the page's bottom notices ("View only", the input notice, the short notes; z-10 and later in
          // the tree, pointer-events-none), which would otherwise paint over it
          className={`${STRIP_BUTTON} pointer-events-auto absolute bottom-3 left-3 z-[11]`}
          onPointerDown={keepToButton}
          onPointerMove={keepToButton}
          onPointerUp={keepToButton}
          onPointerCancel={keepToButton}
          onClick={() => {
            pinchViewRef.current = null;
            applyView(IDENTITY_VIEW);
          }}
        >
          1×
        </button>
      ) : null}
    </div>
  );
}

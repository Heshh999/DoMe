/**
 * The live picture and the touch layer above it (docs/design/screen-view.md §8.3, §16), following the device-proven
 * `TouchpadSurface` pattern: `touch-action: none`, a non-passive `touchmove` that prevents scrolling, pointer capture,
 * cancel on rotation or when the page is hidden.
 *
 * Layers, bottom to top: the box (`object-fit: contain`, black letterbox) → the zoom/pan layer (`transform-origin: 0 0`)
 * holding the `<video playsinline muted>` (no controls, `pointer-events: none`, no callout) → the touch layer. The
 * `<video>` is only ever a `srcObject` sink: frame timing comes from `requestVideoFrameCallback` (metadata only), with
 * `timeupdate` as the fallback; nothing here reads a pixel (test/no-pixel-access.test.ts). `onProbe` hands the session
 * client a probe of the element's own metadata (ready state, size, the last play() outcome, frame-callback ticks, the
 * stream's and track's flags; lib/screenStats.ts `probeVideo`) for the diagnostics bundle and the test-build readout.
 *
 * Direct mode feeds `DirectGestureMachine`: taps, the loupe click and two-finger scrolls become signed input events
 * through `lib/screenInput.ts`; pinch and one-finger pan only change the local view. Touchpad mode swaps in the
 * relative `GestureMachine` over the same picture (drags and precision), with the Drag toggle the page passes in.
 */
import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { DirectGestureMachine } from "../lib/directGestures.ts";
import { GestureMachine, type GestureOutput } from "../lib/gestures.ts";
import type { InputEvent } from "../lib/input.ts";
import { directEvents, dropHint, loupeAim, type SurfaceContext } from "../lib/screenInput.ts";
import { playOutcome, probeVideo, type FrameCounters, type MediaLike, type ScreenMediaFacts } from "../lib/screenStats.ts";
import { IDENTITY_VIEW, panBy, viewTransform, zoomAt, type Box, type Geometry, type View } from "../lib/screenMap.ts";
import { ScreenLoupe } from "./ScreenLoupe.tsx";

export type SurfaceMode = "direct" | "touchpad";

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
  onFrame: () => void;
  onResize: () => void;
  onPlayBlocked: (blocked: boolean) => void;
  /** Bumped by the page's "Tap to show the picture" button: play() again inside that user gesture. */
  playEpoch: number;
  label: string;
  /** Registers a probe of the `<video>`'s metadata (never pixels) while mounted; null on unmount. */
  onProbe?: (probe: (() => ScreenMediaFacts) | null) => void;
}

function touchpadEvent(o: GestureOutput): InputEvent {
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

type VideoWithFrames = HTMLVideoElement & { requestVideoFrameCallback?: (cb: () => void) => number; cancelVideoFrameCallback?: (h: number) => void };

export function ScreenSurface(props: ScreenSurfaceProps) {
  const { stream, mode, scrollDirection, sensitivity, dragMode, playEpoch, label } = props;
  const boxRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const cb = useRef(props);
  useEffect(() => {
    cb.current = props;
  });
  const [direct] = useState(() => new DirectGestureMachine({ scrollDirection }));
  const [touchpad] = useState(() => new GestureMachine({ scrollDirection, sensitivity }));
  const [view, setView] = useState<View>(IDENTITY_VIEW);
  const viewRef = useRef<View>(IDENTITY_VIEW);
  const [loupe, setLoupe] = useState<{ start: { x: number; y: number }; aim: { x: number; y: number }; box: Box } | null>(null);
  const loupeRef = useRef<{ start: { x: number; y: number }; aim: { x: number; y: number } } | null>(null);
  const [ripple, setRipple] = useState<{ x: number; y: number; n: number } | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const holdTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** play() outcome and frame-callback ticks for the probe; `attachedAt` is when the current stream was attached. */
  const counters = useRef<FrameCounters & { attachedAt: number }>({ play: "none", rvfc: false, ticks: 0, firstTickMs: null, attachedAt: 0 });

  // the probe: metadata the element and the stream report about themselves. Registered first, so it is
  // unregistered before the stream effect below detaches the stream on unmount.
  const onProbe = props.onProbe;
  useEffect(() => {
    if (!onProbe) return;
    onProbe(() => {
      const v = videoRef.current;
      // the stream's flags are read where it already is (the element's srcObject); it is kept nowhere else
      return probeVideo(v, (v?.srcObject ?? null) as MediaLike | null, counters.current, Date.now());
    });
    return () => onProbe(null);
  }, [onProbe]);

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

  // the stream → the <video>; frame timing (metadata only) and resizes go to the session client
  useEffect(() => {
    const v = videoRef.current as VideoWithFrames | null;
    if (!v) return;
    v.srcObject = stream;
    applyView(IDENTITY_VIEW);
    if (!stream) return;
    const c = counters.current;
    c.rvfc = typeof v.requestVideoFrameCallback === "function";
    c.ticks = 0;
    c.firstTickMs = null;
    c.attachedAt = Date.now();
    let cancelled = false;
    let handle: number | null = null;
    const onFrame = () => {
      c.ticks += 1;
      if (c.firstTickMs === null) c.firstTickMs = Date.now() - c.attachedAt;
      cb.current.onFrame();
    };
    const tick = () => {
      if (cancelled) return;
      onFrame();
      handle = v.requestVideoFrameCallback ? v.requestVideoFrameCallback(tick) : null;
    };
    if (c.rvfc) handle = v.requestVideoFrameCallback!(tick);
    else v.addEventListener("timeupdate", onFrame);
    const onResize = () => cb.current.onResize();
    v.addEventListener("resize", onResize);
    return () => {
      cancelled = true;
      if (handle !== null) v.cancelVideoFrameCallback?.(handle);
      v.removeEventListener("timeupdate", onFrame);
      v.removeEventListener("resize", onResize);
      v.srcObject = null;
    };
  }, [stream, applyView]);

  // play(): the "Show screen" tap is the gesture iOS wants; a refusal (Low Power Mode) asks for one more tap
  useEffect(() => {
    const v = videoRef.current;
    if (!v || !stream) return;
    const c = counters.current;
    let result: Promise<void> | undefined;
    try {
      result = v.play?.();
    } catch (e) {
      c.play = playOutcome("threw", e);
      cb.current.onPlayBlocked(true);
      return;
    }
    if (result && typeof result.then === "function") {
      c.play = "pending";
      result.then(
        () => {
          c.play = "resolved";
          cb.current.onPlayBlocked(false);
        },
        (e: unknown) => {
          c.play = playOutcome("rejected", e);
          cb.current.onPlayBlocked(true);
        },
      );
    } else c.play = "returned";
  }, [stream, playEpoch]);

  const flashHint = useCallback((text: string | null) => {
    if (hintTimer.current) clearTimeout(hintTimer.current);
    setHint(text);
    if (text) hintTimer.current = setTimeout(() => setHint(null), 2200);
  }, []);

  const context = useCallback((): { ctx: SurfaceContext; rect: DOMRect } | null => {
    const box = boxRef.current;
    const video = videoRef.current;
    if (!box) return null;
    const rect = box.getBoundingClientRect();
    const g = cb.current.geometry();
    return { rect, ctx: { box: { width: rect.width, height: rect.height }, videoWidth: video?.videoWidth ?? 0, videoHeight: video?.videoHeight ?? 0, view: viewRef.current, geometry: g.geometry, geometryStale: g.stale } };
  }, []);

  const send = useCallback(
    (events: InputEvent[], at?: { x: number; y: number }) => {
      if (!cb.current.canSend) {
        cb.current.onViewOnlyTap();
        return;
      }
      cb.current.onInput(events);
      if (at) setRipple((r) => ({ x: at.x, y: at.y, n: (r?.n ?? 0) + 1 }));
    },
    [],
  );

  /** Drain the direct machine: local view changes, the loupe, and the events to sign. */
  const drainDirect = useCallback(() => {
    const c = context();
    for (const out of direct.take()) {
      if (!c) continue;
      const box = c.ctx.box;
      switch (out.type) {
        case "pinch":
          applyView(zoomAt(viewRef.current, out.scale, out.cx, out.cy, box));
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
  }, [context, direct, applyView, send, flashHint]);

  const drainTouchpad = useCallback(() => {
    const events = touchpad.take().map(touchpadEvent);
    if (events.length > 0) send(events);
  }, [touchpad, send]);

  const drain = useCallback(() => (cb.current.mode === "direct" ? drainDirect() : drainTouchpad()), [drainDirect, drainTouchpad]);

  // switching modes releases whatever the other machine held (never a click); an open loupe closes (render-time reset)
  const [seenMode, setSeenMode] = useState(mode);
  if (seenMode !== mode) {
    setSeenMode(mode);
    setLoupe(null);
  }
  useEffect(() => {
    direct.cancel();
    direct.take();
    loupeRef.current = null;
    touchpad.cancel();
    touchpad.setDragMode(false);
    const release = touchpad.take().map(touchpadEvent);
    if (release.length > 0 && cb.current.canSend) cb.current.onInput(release);
  }, [mode, direct, touchpad]);
  // touchpad mode's Drag toggle: holding or releasing the left button is sent at once
  useEffect(() => {
    if (mode !== "touchpad") return;
    touchpad.setDragMode(dragMode);
    const events = touchpad.take().map(touchpadEvent);
    if (events.length > 0 && cb.current.canSend) cb.current.onInput(events);
  }, [dragMode, mode, touchpad]);

  const cancelAll = useCallback(() => {
    if (holdTimer.current) clearTimeout(holdTimer.current);
    holdTimer.current = null;
    if (cb.current.mode === "direct") direct.cancel();
    else touchpad.cancel();
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
    const p = point(e);
    if (mode === "direct") {
      direct.pointerDown(e.pointerId, p.x, p.y, e.timeStamp);
      armHold();
      drainDirect();
    } else {
      touchpad.pointerDown(e.pointerId, e.clientX, e.clientY, e.timeStamp);
      drainTouchpad();
    }
  };
  const move = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (mode === "direct") {
      const p = point(e);
      direct.pointerMove(e.pointerId, p.x, p.y, e.timeStamp);
      if (!direct.holdDeadline() && holdTimer.current) {
        clearTimeout(holdTimer.current);
        holdTimer.current = null;
      }
      drainDirect();
    } else {
      touchpad.pointerMove(e.pointerId, e.clientX, e.clientY, e.timeStamp);
      drainTouchpad();
    }
  };
  const up = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (mode === "direct") {
      if (holdTimer.current && direct.activeFingers <= 1) {
        clearTimeout(holdTimer.current);
        holdTimer.current = null;
      }
      direct.pointerUp(e.pointerId, e.timeStamp);
      drainDirect();
    } else {
      touchpad.pointerUp(e.pointerId, e.timeStamp);
      drainTouchpad();
    }
  };

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
        <video ref={videoRef} className="pointer-events-none h-full w-full object-contain" playsInline muted disablePictureInPicture data-testid="screen-video" />
      </div>
      {ripple ? <span key={ripple.n} aria-hidden="true" className="dome-tap-ripple pointer-events-none absolute h-9 w-9 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white/90" style={{ left: ripple.x, top: ripple.y }} /> : null}
      {loupe ? <ScreenLoupe stream={stream} aim={loupe.aim} box={loupe.box} view={view} /> : null}
      {hint ? (
        <p role="status" className="pointer-events-none absolute inset-x-4 bottom-4 z-10 rounded-full bg-black/75 px-4 py-2 text-center text-sm text-white">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

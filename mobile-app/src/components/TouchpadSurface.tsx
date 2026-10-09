/**
 * The touchpad surface: the only element with `touch-action: none`. Pointer events are captured per
 * pointer and fed to the GestureMachine; `pointercancel`, lost capture, orientation and visibility
 * changes cancel the gesture (releasing a held drag through the machine). The surface never
 * interprets anything itself.
 */
import { useEffect, useRef, type PointerEvent as ReactPointerEvent } from "react";

import type { GestureMachine } from "../lib/gestures.ts";

export interface TouchpadSurfaceProps {
  machine: GestureMachine;
  /** Called after every machine interaction so the owner can drain `machine.take()`. */
  onOutput: () => void;
  disabled: boolean;
  dragActive: boolean;
  label: string;
  compact?: boolean;
}

export function TouchpadSurface({ machine, onOutput, disabled, dragActive, label, compact = false }: TouchpadSurfaceProps) {
  const ref = useRef<HTMLDivElement>(null);
  const machineRef = useRef(machine);
  machineRef.current = machine;
  const outputRef = useRef(onOutput);
  outputRef.current = onOutput;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // React registers touchmove as passive; older iOS Safari still needs preventDefault to stop the
    // page from scrolling under the finger, so a native non-passive listener is attached here.
    const block = (e: TouchEvent) => e.preventDefault();
    el.addEventListener("touchmove", block, { passive: false });
    const cancel = () => {
      machineRef.current.cancel();
      outputRef.current();
    };
    const onVisibility = () => {
      if (document.visibilityState !== "visible") cancel();
    };
    window.addEventListener("orientationchange", cancel);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      el.removeEventListener("touchmove", block);
      window.removeEventListener("orientationchange", cancel);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const down = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (disabled) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* capture unsupported: events still arrive while the pointer stays over the surface */
    }
    machine.pointerDown(e.pointerId, e.clientX, e.clientY, e.timeStamp);
    onOutput();
  };
  const move = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (disabled) return;
    machine.pointerMove(e.pointerId, e.clientX, e.clientY, e.timeStamp);
    onOutput();
  };
  const up = (e: ReactPointerEvent<HTMLDivElement>) => {
    machine.pointerUp(e.pointerId, e.timeStamp);
    onOutput();
  };
  const cancel = () => {
    machine.cancel();
    onOutput();
  };

  return (
    <div
      ref={ref}
      role="application"
      aria-label={label}
      aria-disabled={disabled || undefined}
      data-testid="touchpad-surface"
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={cancel}
      onLostPointerCapture={(e) => {
        // Capture lost while a finger is still tracked (OS gesture, alert, rotation): never a tap.
        if (machine.activeFingers > 0) {
          machine.pointerUp(e.pointerId, e.timeStamp);
          if (machine.activeFingers > 0) machine.cancel();
          onOutput();
        }
      }}
      onContextMenu={(e) => e.preventDefault()}
      style={{ touchAction: "none" }}
      className={`relative select-none rounded-card border-2 ${dragActive ? "border-warning bg-warning/10" : disabled ? "border-border bg-bg-sunken/60" : "border-border-strong bg-bg-sunken"} ${compact ? "h-[34dvh] min-h-[180px]" : "h-[52dvh] min-h-[260px]"} w-full transition-colors`}
    >
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center px-6">
        {dragActive ? (
          <>
            <span className="text-warning font-bold text-lg">DRAG MODE — holding the left button</span>
            <span className="text-sm text-text-muted mt-1">Move to drag. Tap End Drag to release.</span>
          </>
        ) : disabled ? (
          <span className="text-sm text-text-faint">Touchpad inactive</span>
        ) : (
          <span className="text-sm text-text-faint">Slide to move · tap to click · two fingers to scroll or right-click</span>
        )}
      </div>
    </div>
  );
}

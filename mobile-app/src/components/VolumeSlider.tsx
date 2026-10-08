/**
 * Volume control with an explicit scope label (YouTube player vs Windows system volume). Emits at
 * most one value per 250 ms while dragging plus the final value on release (plans.json coalescable
 * rate). The displayed value follows the PC's reported state when the customer is not dragging.
 */
import { useEffect, useId, useRef, useState } from "react";

import { createThrottledEmitter, type ThrottledEmitter } from "../lib/throttle.ts";
import { IconButton } from "./ui.tsx";

export function VolumeSlider({ label, scopeHint, value, muted, disabled, onChange, onToggleMute }: { label: string; scopeHint: string; value: number | null; muted: boolean | null; disabled: boolean; onChange: (value: number) => void; onToggleMute?: () => void }) {
  const id = useId();
  const [dragging, setDragging] = useState(false);
  const [local, setLocal] = useState<number>(0);
  // One throttled emitter per mounted slider (created in an effect, used only from event handlers);
  // it always calls the latest onChange without being recreated mid-drag.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);
  const emitterRef = useRef<ThrottledEmitter<number> | null>(null);
  useEffect(() => {
    const em = createThrottledEmitter<number>((v) => onChangeRef.current(v), 250);
    emitterRef.current = em;
    return () => {
      em.cancel();
      emitterRef.current = null;
    };
  }, []);

  // While dragging the slider shows the finger position; otherwise it follows the PC's reported value.
  const shown = dragging ? local : (value ?? 0);
  const unknown = value === null && !dragging;
  const beginDrag = () => {
    if (!dragging) setLocal(value ?? 0);
    setDragging(true);
  };
  const endDrag = () => {
    emitterRef.current?.flush();
    setDragging(false);
  };
  return (
    <div className="rounded-card bg-surface border border-border p-3">
      <div className="flex items-center justify-between gap-2 mb-1">
        <label htmlFor={id} className="font-semibold text-sm">
          {label}
        </label>
        <span className="text-sm font-mono tabular-nums text-text-muted" aria-live="polite">
          {unknown ? "—" : `${shown}%`}
          {muted ? " · muted" : ""}
        </span>
      </div>
      <p className="text-xs text-text-faint mb-1">{scopeHint}</p>
      <div className="flex items-center gap-3">
        {onToggleMute ? (
          <IconButton label={muted ? `Unmute ${label}` : `Mute ${label}`} size={44} disabled={disabled || muted === null} onClick={onToggleMute} aria-pressed={muted === true}>
            <span aria-hidden="true" className="text-base">
              {muted ? "🔇" : "🔊"}
            </span>
          </IconButton>
        ) : null}
        <input
          id={id}
          type="range"
          className="slider flex-1"
          min={0}
          max={100}
          step={1}
          value={shown}
          disabled={disabled}
          aria-valuetext={unknown ? "unknown" : `${shown} percent`}
          onPointerDown={beginDrag}
          onChange={(e) => {
            const v = Number(e.currentTarget.value);
            setLocal(v);
            setDragging(true);
            emitterRef.current?.push(v);
          }}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onKeyUp={() => emitterRef.current?.flush()}
          onBlur={endDrag}
        />
      </div>
    </div>
  );
}

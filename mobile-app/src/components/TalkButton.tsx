/**
 * The Assistant's talk button (docs/design/assistant.md §4.2, §4.7): tap to talk, tap again to stop; no hold-to-talk.
 * Listening starts only from its click (user activation on iOS). It says what is happening — getting ready until the
 * microphone is live, listening (with a recording dot), connecting to the PC or checking its apps, thinking (with
 * Cancel), working (with Stop), speaking (tap to interrupt) — and becomes "Dictate" where web speech recognition is not
 * available.
 */
import { useEffect, useState } from "react";

import { Icon } from "./icons.tsx";
import { Button, Spinner } from "./ui.tsx";

export type TalkState = "idle" | "starting" | "listening" | "connecting" | "checking" | "thinking" | "working" | "speaking";

/** "Still working…" after this long thinking or working. */
export const STILL_WORKING_MS = 8_000;

const LABEL: Record<TalkState, string> = {
  idle: "Tap to talk",
  starting: "Getting ready…",
  listening: "Listening… tap to stop",
  connecting: "Connecting…",
  checking: "Checking…",
  thinking: "Thinking…",
  working: "Working…",
  speaking: "Tap to interrupt",
};

export function TalkButton({ state, dictate, onTap, onCancel, onStop }: { state: TalkState; dictate: boolean; onTap(): void; onCancel(): void; onStop(): void }) {
  /** The busy state that has lasted STILL_WORKING_MS (a new state starts counting again). */
  const [stillIn, setStillIn] = useState<TalkState | null>(null);
  const busy = state === "thinking" || state === "working";
  useEffect(() => {
    const t = busy ? setTimeout(() => setStillIn(state), STILL_WORKING_MS) : setTimeout(() => setStillIn(null), 0);
    return () => clearTimeout(t);
  }, [busy, state]);
  const still = busy && stillIn === state;
  const listening = state === "listening";
  const waiting = state === "connecting" || state === "checking";
  const label = dictate && (state === "idle" || state === "speaking") ? "Dictate" : LABEL[state];
  return (
    <div className="flex flex-col items-center gap-1.5">
      <span className="relative flex h-[72px] w-[72px] items-center justify-center">
        {listening ? (
          <>
            <span aria-hidden="true" className="dome-ring absolute inset-0 rounded-full border-2 border-coral" />
            <span aria-hidden="true" className="dome-ring absolute inset-0 rounded-full border-2 border-coral [animation-delay:0.9s]" />
          </>
        ) : null}
        <button
          type="button"
          data-talk-button=""
          aria-pressed={listening}
          aria-label={label}
          disabled={busy || waiting}
          onClick={onTap}
          className={`dome-primary dome-press relative flex h-[72px] w-[72px] items-center justify-center rounded-full select-none [-webkit-touch-callout:none] [touch-action:manipulation] disabled:opacity-70 ${listening ? "ring-2 ring-coral ring-offset-2 ring-offset-[var(--dome-color-bg)]" : ""}`}
        >
          {busy || waiting || state === "starting" ? <Spinner label={label} /> : <Icon name={dictate ? "keyboard" : "mic"} size={30} strokeWidth={1.9} />}
          {listening ? <span aria-hidden="true" className="absolute right-1.5 top-1.5 h-3 w-3 rounded-full border-2 border-white bg-coral" /> : null}
        </button>
      </span>
      <p className="text-sm font-semibold text-text" aria-hidden="true">
        {label}
      </p>
      {state === "thinking" ? (
        <div className="flex flex-col items-center gap-1">
          <Button size="md" onClick={onCancel}>
            Cancel
          </Button>
          <p className="text-xs text-text-muted">{still ? "Still working…" : "Keep DoMe open until I answer."}</p>
        </div>
      ) : null}
      {state === "working" ? (
        <div className="flex flex-col items-center gap-1">
          <Button size="md" onClick={onStop}>
            Stop
          </Button>
          {still ? <p className="text-xs text-text-muted">Still working…</p> : null}
        </div>
      ) : null}
    </div>
  );
}

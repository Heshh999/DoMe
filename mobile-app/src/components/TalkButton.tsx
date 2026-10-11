/**
 * The Assistant's talk button (docs/design/assistant.md §4.2, §4.7): tap to talk, tap again to stop; no hold-to-talk.
 * Listening starts only from its click (user activation on iOS). It says what is happening — getting ready until the
 * microphone is live, listening (with a recording dot), connecting to the PC or checking its apps, thinking (with
 * Cancel), working (with Stop), speaking (tap to interrupt) — and becomes "Dictate" where web speech recognition is not
 * available.
 */
import { useEffect, useState } from "react";

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
    <div className="flex flex-col items-center gap-1">
      <button
        type="button"
        data-talk-button=""
        aria-pressed={listening}
        aria-label={label}
        disabled={busy || waiting}
        onClick={onTap}
        className={`relative flex h-[72px] w-[72px] items-center justify-center rounded-full text-on-accent shadow-lg select-none [-webkit-touch-callout:none] [touch-action:manipulation] disabled:opacity-70 ${listening ? "bg-danger ring-4 ring-danger/40 motion-safe:animate-pulse" : "bg-accent"}`}
      >
        {busy || waiting || state === "starting" ? (
          <Spinner label={label} />
        ) : (
          <svg width="30" height="30" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            {dictate ? <path d="M4 7h16M4 12h16M4 17h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /> : <path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Zm-7 9a7 7 0 0 0 14 0M12 19v3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />}
          </svg>
        )}
        {listening ? <span aria-hidden="true" className="absolute right-1 top-1 h-3 w-3 rounded-full bg-white" /> : null}
      </button>
      <p className="text-sm font-semibold" aria-hidden="true">
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

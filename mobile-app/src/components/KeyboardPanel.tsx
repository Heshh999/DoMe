/**
 * Keyboard input into the PC's focused field (spec §10A B). Inline panel (not a modal) so the touchpad
 * stays usable while it is open. Two modes:
 *
 *  - Live: a real <textarea> invokes the phone keyboard; native beforeinput/input/composition
 *    listeners feed `LiveTyping`, which commits each edit once and only when its mapping is certain.
 *    Enter is an explicit key (never inserted into the echo, never sent automatically after text).
 *  - Compose and Send: a memory-only buffer sent as a whole on request. After an uncertain send the
 *    buffer stays in a review state and only a deliberate "Send again" resends it.
 *
 * Nothing typed here is logged, stored or kept after the panel closes; the echo shows what this
 * phone sent, which is not necessarily what the PC field contains.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { AckOutcome, InputEvent as ProtoEvent, InputSessionState } from "../lib/input.ts";
import { foregroundLabel } from "../lib/inputStatus.ts";
import { recoverySteps } from "../lib/labels.ts";
import { LiveTyping, PAUSE_EXPLANATION, splitText, TEXT_EVENT_MAX, type TypingCommit } from "../lib/typing.ts";
import { Button, Notice, Steps } from "./ui.tsx";

export interface KeyboardPanelProps {
  session: InputSessionState;
  enqueue: (event: ProtoEvent) => boolean;
  flush: () => Promise<number | null>;
  awaitAck: (seq: number) => Promise<AckOutcome>;
  onClose: () => void;
}

type Mode = "live" | "compose";
type SendState = { kind: "idle" } | { kind: "sending" } | { kind: "sent"; chars: number } | { kind: "review"; outcome: Exclude<AckOutcome, "accepted"> };

const KEYS: Array<{ key: Extract<ProtoEvent, { type: "key" }>["key"]; label: string; aria: string }> = [
  { key: "enter", label: "Enter", aria: "Enter" },
  { key: "tab", label: "Tab", aria: "Tab" },
  { key: "escape", label: "Esc", aria: "Escape" },
  { key: "backspace", label: "⌫", aria: "Backspace" },
  { key: "delete", label: "Del", aria: "Delete" },
  { key: "arrow_left", label: "←", aria: "Arrow left" },
  { key: "arrow_up", label: "↑", aria: "Arrow up" },
  { key: "arrow_down", label: "↓", aria: "Arrow down" },
  { key: "arrow_right", label: "→", aria: "Arrow right" },
];

export function KeyboardPanel({ session, enqueue, flush, awaitAck, onClose }: KeyboardPanelProps) {
  const [mode, setMode] = useState<Mode>("live");
  const [echo, setEcho] = useState("");
  const [paused, setPaused] = useState<string | null>(null);
  const [composer, setComposer] = useState("");
  const [sendState, setSendState] = useState<SendState>({ kind: "idle" });
  const typing = useRef(new LiveTyping());
  const liveRef = useRef<HTMLTextAreaElement>(null);
  const foreground = session.foregroundApp;
  const foregroundKey = foreground ? `${foreground.process_name}|${foreground.window_title ?? ""}|${foreground.browser ?? ""}` : "";
  const lastForeground = useRef(foregroundKey);
  const live = session.phase === "live" && session.keyboard;
  const browser = !!foreground?.browser;

  const pauseLive = useCallback((reason: string) => {
    typing.current.reset();
    setEcho("");
    setPaused(reason);
    setMode("compose");
  }, []);

  // A new window in front of the PC: whatever we were mirroring is no longer the target.
  useEffect(() => {
    if (lastForeground.current === foregroundKey) return;
    lastForeground.current = foregroundKey;
    if (mode === "live" && typing.current.sentText !== "") pauseLive("The window in front of the PC changed, so live typing paused rather than typing into the wrong window. Click where you want to type on the PC, then continue.");
    else {
      typing.current.reset();
      setEcho("");
    }
  }, [foregroundKey, mode, pauseLive]);

  // Leaving the panel clears every buffer (memory only; nothing was stored anywhere).
  useEffect(
    () => () => {
      typing.current.reset();
    },
    [],
  );

  const commitLive = useCallback(
    (result: TypingCommit) => {
      if (result.ok) {
        for (const ev of result.events) enqueue(ev);
        return;
      }
      if (result.reason === "composing") return;
      pauseLive(PAUSE_EXPLANATION[result.reason]);
    },
    [enqueue, pauseLive],
  );

  // Native listeners: React's synthetic beforeinput/composition events do not expose inputType reliably.
  useEffect(() => {
    const el = liveRef.current;
    if (!el || mode !== "live") return;
    const t = typing.current;
    const onBeforeInput = (e: Event) => {
      const ie = e as InputEvent & { inputType?: string; data?: string | null };
      const type = ie.inputType ?? "";
      if (type === "insertLineBreak" || type === "insertParagraph") {
        e.preventDefault();
        if (!t.isComposing) enqueue({ type: "key", key: "enter" });
        return;
      }
      if (type === "deleteContentBackward") {
        const ev = t.bareBackspace(el.value);
        if (ev) {
          e.preventDefault();
          enqueue(ev);
        }
      }
    };
    const onInput = () => {
      setEcho(el.value);
      commitLive(t.commit(el.value));
    };
    const onCompositionStart = () => t.compositionStart();
    const onCompositionEnd = () => {
      setEcho(el.value);
      commitLive(t.compositionEnd(el.value));
    };
    el.addEventListener("beforeinput", onBeforeInput as EventListener);
    el.addEventListener("input", onInput);
    el.addEventListener("compositionstart", onCompositionStart);
    el.addEventListener("compositionend", onCompositionEnd);
    return () => {
      el.removeEventListener("beforeinput", onBeforeInput as EventListener);
      el.removeEventListener("input", onInput);
      el.removeEventListener("compositionstart", onCompositionStart);
      el.removeEventListener("compositionend", onCompositionEnd);
    };
  }, [mode, enqueue, commitLive]);

  const sendComposer = async () => {
    const text = composer;
    if (!text) return;
    setSendState({ kind: "sending" });
    for (const ev of splitText(text)) enqueue(ev);
    const seq = await flush();
    if (seq === null) {
      setSendState({ kind: "review", outcome: "ended" });
      return;
    }
    const outcome = await awaitAck(seq);
    if (outcome === "accepted") {
      setComposer("");
      setSendState({ kind: "sent", chars: text.length });
    } else {
      // The text stays in memory for a deliberate retry; nothing is resent on its own.
      setSendState({ kind: "review", outcome });
    }
  };

  const clearEcho = () => {
    typing.current.reset();
    setEcho("");
    if (liveRef.current) liveRef.current.value = "";
  };

  return (
    <section aria-label="Keyboard" className="rounded-card bg-bg-elevated border border-border p-3 space-y-3" data-testid="keyboard-panel">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold">Keyboard</p>
          <p className="text-xs text-text-muted truncate" title={foreground?.window_title}>
            Typing into: {foregroundLabel(foreground)}
            {foreground?.elevated ? " · administrator window: Windows will refuse input" : ""}
          </p>
          <p className="text-[11px] text-text-faint">Click the field on the PC first. DoMe cannot see which field has focus inside a window.</p>
        </div>
        <Button size="md" variant="ghost" onClick={onClose} aria-label="Close keyboard">
          Close
        </Button>
      </div>

      {!session.keyboard ? (
        <Notice tone="warning" title="Keyboard permission is not granted on this PC">
          <Steps steps={recoverySteps("INPUT_NOT_PERMITTED")} />
        </Notice>
      ) : null}

      <div role="tablist" aria-label="Typing mode" className="grid grid-cols-2 gap-1 rounded-control bg-bg-sunken p-1">
        <button role="tab" type="button" aria-selected={mode === "live"} onClick={() => (setMode("live"), setPaused(null))} className={`rounded-[0.7rem] py-2 text-sm font-semibold ${mode === "live" ? "bg-bg-elevated text-text shadow" : "text-text-muted"}`}>
          Type live
        </button>
        <button role="tab" type="button" aria-selected={mode === "compose"} onClick={() => setMode("compose")} className={`rounded-[0.7rem] py-2 text-sm font-semibold ${mode === "compose" ? "bg-bg-elevated text-text shadow" : "text-text-muted"}`}>
          Compose and Send
        </button>
      </div>

      {paused && mode === "compose" ? (
        <Notice tone="warning" title="Live typing paused">
          <p>{paused}</p>
        </Notice>
      ) : null}

      {mode === "live" ? (
        <div className="space-y-2">
          <label htmlFor="live-typing" className="sr-only">
            Type here; each character is sent to the PC as you type
          </label>
          <textarea id="live-typing" ref={liveRef} rows={2} disabled={!live} autoCapitalize="off" autoCorrect="on" spellCheck={false} placeholder={live ? "Type here — sent as you type" : "Keyboard inactive"} className="w-full rounded-control bg-bg-sunken border border-border px-3 py-2.5 text-base text-text placeholder:text-text-faint focus:border-border-strong min-h-[56px]" data-testid="live-textarea" />
          <div className="flex items-center justify-between text-[11px] text-text-faint">
            <span>{echo ? `${echo.length} characters sent in this run` : "Shows what this phone sent, not the PC’s field."}</span>
            <button type="button" onClick={clearEcho} className="underline">
              Clear here only
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          <label htmlFor="composer" className="sr-only">
            Compose text to send
          </label>
          <textarea id="composer" value={composer} onChange={(e) => setComposer(e.currentTarget.value.slice(0, TEXT_EVENT_MAX * 4))} rows={3} disabled={!live || sendState.kind === "sending"} placeholder="Write here, then Send. Enter is a separate key." className="w-full rounded-control bg-bg-sunken border border-border px-3 py-2.5 text-base text-text placeholder:text-text-faint focus:border-border-strong min-h-[72px]" data-testid="composer" />
          {sendState.kind === "review" ? (
            <Notice tone="warning" title="DoMe cannot tell whether this text reached the PC">
              <p>{sendState.outcome === "timeout" ? "The PC did not confirm it in time." : sendState.outcome === "dropped" ? "The PC reported dropped input around the time it was sent." : "The touchpad session ended before the PC confirmed it."} Look at the PC: if the text is there, discard it here; if not, send it again. Nothing is resent on its own.</p>
              <div className="grid grid-cols-2 gap-2 mt-2">
                <Button size="md" onClick={() => (setComposer(""), setSendState({ kind: "idle" }))}>
                  Discard
                </Button>
                <Button size="md" variant="primary" disabled={!live} onClick={() => void sendComposer()}>
                  Send again
                </Button>
              </div>
            </Notice>
          ) : (
            <div className="flex items-center gap-2">
              <Button variant="primary" size="md" disabled={!live || !composer || sendState.kind === "sending"} busy={sendState.kind === "sending"} onClick={() => void sendComposer()}>
                Send to PC
              </Button>
              <Button size="md" variant="ghost" disabled={!composer} onClick={() => (setComposer(""), setSendState({ kind: "idle" }))}>
                Clear
              </Button>
              {sendState.kind === "sent" ? (
                <span className="text-xs text-success" role="status">
                  Windows accepted {sendState.chars} characters.
                </span>
              ) : null}
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-5 gap-1.5" role="group" aria-label="Keys">
        {KEYS.map((k) => (
          <button key={k.key} type="button" aria-label={k.aria} disabled={!live} onClick={() => enqueue({ type: "key", key: k.key })} className="min-h-[44px] rounded-control bg-surface border border-border text-sm font-semibold disabled:opacity-40 active:bg-surface-hover">
            {k.label}
          </button>
        ))}
        <button type="button" aria-label="Space" disabled={!live} onClick={() => enqueue({ type: "key", key: "space" })} className="min-h-[44px] rounded-control bg-surface border border-border text-sm font-semibold disabled:opacity-40 active:bg-surface-hover">
          Space
        </button>
      </div>

      <div role="group" aria-label="Shortcuts" className="flex flex-wrap gap-1.5">
        {(
          [
            ["ctrl_a", "Select all", "Ctrl+A"],
            ["ctrl_c", "Copy", "Ctrl+C"],
            ["ctrl_v", "Paste", "Ctrl+V"],
            ["ctrl_z", "Undo", "Ctrl+Z"],
          ] as const
        ).map(([name, label, combo]) => (
          <button key={name} type="button" disabled={!live} onClick={() => enqueue({ type: "shortcut", name })} className="min-h-[44px] rounded-control bg-surface border border-border px-3 text-sm disabled:opacity-40 active:bg-surface-hover">
            {label} <span className="text-text-faint">{combo}</span>
          </button>
        ))}
        {browser ? (
          <button type="button" disabled={!live} onClick={() => enqueue({ type: "shortcut", name: "ctrl_l" })} className="min-h-[44px] rounded-control bg-surface border border-border px-3 text-sm disabled:opacity-40 active:bg-surface-hover" aria-label="Address bar (Ctrl+L, browser address-bar shortcut)">
            Address bar <span className="text-text-faint">Ctrl+L</span>
          </button>
        ) : null}
      </div>
      <p className="text-[11px] text-text-faint">Copy and Paste use the PC’s own clipboard. Pasting into the composer above is ordinary text entry; nothing syncs clipboards between devices.</p>
    </section>
  );
}

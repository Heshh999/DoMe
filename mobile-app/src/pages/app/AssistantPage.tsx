/**
 * Ask: talk to DoMe (docs/design/assistant.md §4.2). The owner taps the microphone and speaks, or types, or dictates
 * with the keyboard's microphone; DoMe answers aloud as a formal butler and works the PC with the same signed,
 * registry-checked commands as the buttons and the Type page. Requests DoMe understands by itself never need the AI;
 * with the AI assistant on, only the rest goes to it. Every plan is shown before it runs.
 */
import { useEffect, useRef, useState, type RefObject } from "react";
import { Link } from "react-router";

import { useApprovedApps, useMyGrant, useSelectedPc } from "../../app/hooks.ts";
import { getAssistant, getRecognizer, getSpeaker, loadAssistantStatus } from "../../app/voice.ts";
import { Conversation, type ConversationHandlers } from "../../components/Conversation.tsx";
import { PcSwitcher } from "../../components/PcSwitcher.tsx";
import { TalkButton, type TalkState } from "../../components/TalkButton.tsx";
import { Button, inputClass, Notice } from "../../components/ui.tsx";
import { VoiceNoticeSheet } from "../../components/VoiceNoticeSheet.tsx";
import { aiErrorText, aiUsable, MAX_TEXT } from "../../lib/assistant.ts";
import type { RecognitionProblem } from "../../lib/speech.ts";
import { useAssistantStore, type ChatChip } from "../../store/assistant.ts";
import { useDevicesStore } from "../../store/devices.ts";

/** Height of the bottom tab bar (AppShell): the dock sits above it, or above the keyboard while it is open. */
const TAB_BAR = "calc(57px + var(--safe-bottom))";

const PROBLEM_TEXT: Record<RecognitionProblem["kind"], string> = {
  unavailable: "Voice input isn't available in this app on your iPhone. Use Dictate: the microphone key on the keyboard.",
  denied: "DoMe isn't allowed to use the microphone or speech recognition. Use Dictate, or allow it in the iPhone's Settings.",
  language: "Speech recognition isn't offered for this language here. Use Dictate: the microphone key on the keyboard.",
  transient: "I couldn't hear you just then. Please try again.",
  no_speech: "I didn't hear anything.",
  not_ready: "The microphone didn't start. Tap to try again.",
};

/** The element's rendered height, followed with a ResizeObserver (null until measured, or where there is none). */
function useHeight(ref: RefObject<HTMLElement | null>): number | null {
  const [height, setHeight] = useState<number | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setHeight(Math.ceil(el.getBoundingClientRect().height)));
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return height;
}

/** Distance from the layout viewport's bottom to the visual one's: the iOS keyboard's height while it is open. */
function useKeyboardInset(): number {
  const [inset, setInset] = useState(0);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const update = () => setInset(Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop)));
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    update();
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    };
  }, []);
  return inset;
}

export function AssistantPage() {
  const { pc, pcId, live } = useSelectedPc();
  // the approved apps and this phone's grant, as the Type page loads them ("open Notepad" without visiting Apps first)
  useApprovedApps(pcId);
  const grant = useMyGrant(pcId);
  const loadGrants = useDevicesStore((s) => s.loadGrants);
  const items = useAssistantStore((s) => s.items);
  const phase = useAssistantStore((s) => s.phase);
  const notice = useAssistantStore((s) => s.notice);
  const speaking = useAssistantStore((s) => s.speaking);
  const prefs = useAssistantStore((s) => s.prefs);
  const status = useAssistantStore((s) => s.status);
  const allowance = useAssistantStore((s) => s.allowance);
  const aiBlocked = useAssistantStore((s) => s.aiBlocked);
  const setPrefs = useAssistantStore((s) => s.setPrefs);
  const [listen, setListen] = useState<"idle" | "starting" | "listening">("idle");
  const [interim, setInterim] = useState("");
  const [typing, setTyping] = useState(false);
  const [dictated, setDictated] = useState(false);
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<RecognitionProblem | null>(null);
  const [voiceOff, setVoiceOff] = useState(() => !getRecognizer().available());
  const [noticeOpen, setNoticeOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const announceRef = useRef<HTMLParagraphElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const dockRef = useRef<HTMLDivElement>(null);
  const inset = useKeyboardInset();
  const dockHeight = useHeight(dockRef);

  useEffect(() => {
    void loadAssistantStatus();
    getAssistant().expireIdle();
    // leaving the page never leaves the microphone on
    return () => {
      getRecognizer().abort();
      getSpeaker().setListening(false);
    };
  }, []);
  useEffect(() => {
    if (pcId && grant === undefined) void loadGrants(pcId);
  }, [pcId, grant, loadGrants]);
  // after the spacer below has taken the dock's height, so the newest card's buttons end up above the dock
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [items.length, dockHeight, inset]);
  useEffect(() => {
    if (typing) inputRef.current?.focus();
  }, [typing]);

  const dictateMode = voiceOff;
  const usable = aiUsable({ status, prefs, aiBlocked });
  const listening = listen !== "idle";
  const talkState: TalkState = listen === "starting" ? "starting" : listen === "listening" ? "listening" : phase === "idle" ? (speaking ? "speaking" : "idle") : phase;
  const newestButler = [...items].reverse().find((i) => i.kind === "butler");
  // The polite live region reads each new butler line once: it goes silent while listening and is never refilled
  // with an older line afterwards (only a line newer than the last one announced is put in it).
  const [announced, setAnnounced] = useState(() => ({ id: newestButler?.id ?? 0, text: "" }));
  if (listening && announced.text !== "") setAnnounced({ id: announced.id, text: "" });
  else if (!listening && newestButler?.kind === "butler" && newestButler.id > announced.id) setAnnounced({ id: newestButler.id, text: newestButler.text });

  const openDictation = () => {
    setTyping(true);
    setDictated(true);
    // focus inside the tap, so the iPhone keyboard (and its microphone key) opens
    inputRef.current?.focus();
  };

  const startListening = () => {
    const speaker = getSpeaker();
    speaker.cancel();
    speaker.setListening(true);
    setProblem(null);
    // announced before start(): VoiceOver's own speech must not end up in the transcript
    if (announceRef.current) announceRef.current.textContent = "Listening.";
    getRecognizer().start({
      onState: (s) => {
        setListen(s);
        if (s === "idle") {
          speaker.setListening(false);
          if (announceRef.current) announceRef.current.textContent = "";
        }
      },
      onInterim: setInterim,
      onFinal: (words, endedAt) => {
        setInterim("");
        void getAssistant().handleText(words, "speech", { endedAt });
      },
      onProblem: (p) => {
        setInterim("");
        setProblem(p);
        if (p.kind === "unavailable" || p.kind === "denied" || p.kind === "language" || (p.kind === "transient" && p.disabled)) {
          setVoiceOff(true);
          setTyping(true);
          setDictated(true);
        }
      },
    });
  };

  const tap = () => {
    if (listening) {
      getRecognizer().stop();
      return;
    }
    if (dictateMode) {
      openDictation();
      return;
    }
    if (!prefs.voiceNoticeSeen) {
      setNoticeOpen(true);
      return;
    }
    startListening();
  };

  const tryVoiceAgain = () => {
    getRecognizer().tryAgain();
    setVoiceOff(!getRecognizer().available());
    setProblem(null);
    setDictated(false);
  };

  const send = () => {
    const words = text.trim();
    if (!words || phase !== "idle") return;
    setText("");
    void getAssistant().handleText(words, dictated ? "dictated" : "typed", { endedAt: Date.now() });
  };

  const handlers: ConversationHandlers = {
    onChip: (c: ChatChip) => {
      const ctl = getAssistant();
      if (c.kind === "choice") void ctl.handleText(c.text, "choice");
      else if (c.kind === "ask_ai") void ctl.handleText(c.text, c.source ?? "typed", { forceAi: true });
      else void ctl.handleText(c.text, "local", { fromChip: true });
    },
    onRun: (id) => void getAssistant().run(id),
    onDismiss: (id) => getAssistant().dismiss(id),
    onStop: () => getAssistant().stop(),
    onHear: (id) => getAssistant().hear(id),
    onCancelPower: (id) => void getAssistant().cancelPower(id),
  };

  const consented = !!status?.consent && status.consent.version === status.consent_version;
  const aiOn = consented && prefs.useAi;
  const exhausted = aiBlocked?.code === "AI_ALLOWANCE_EXHAUSTED" || aiBlocked?.code === "AI_SPEND_LIMIT_REACHED" || (aiOn && (status?.unavailable_reason === "allowance_exhausted" || status?.unavailable_reason === "spend_limit"));
  const banner = !exhausted ? null : aiBlocked ? aiErrorText(aiBlocked, allowance) : status?.unavailable_reason === "spend_limit" ? aiErrorText({ code: "AI_SPEND_LIMIT_REACHED" }, allowance) : aiErrorText({ code: "AI_ALLOWANCE_EXHAUSTED", ...(allowance?.exhausted ? { reason: allowance.exhausted } : {}) }, allowance);

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between gap-2">
        <h1 className="text-2xl font-bold tracking-tight truncate">{prefs.butlerName}</h1>
        <div className="flex items-center gap-2 shrink-0">
          {aiOn && allowance ? (
            <Link to="/app/settings" className="tap inline-flex items-center rounded-full border border-border px-3 text-xs font-semibold" aria-label={allowance.exhausted || exhausted ? "AI limit reached: open Settings" : `${Math.max(0, allowance.limit - allowance.used)} AI requests left: open Settings`}>
              {allowance.exhausted || exhausted ? "Limit reached" : `${Math.max(0, allowance.limit - allowance.used)} left`}
            </Link>
          ) : null}
          <button type="button" aria-pressed={prefs.muted} aria-label="Mute replies" title="Mute replies" onClick={() => setPrefs({ muted: !prefs.muted })} className="tap inline-flex h-11 w-11 items-center justify-center rounded-full border border-border bg-surface">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 9h4l5-4v14l-5-4H4z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
              {prefs.muted ? <path d="M17 9l4 6M21 9l-4 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /> : <path d="M17 8a5 5 0 0 1 0 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />}
            </svg>
          </button>
        </div>
      </header>
      <PcSwitcher pc={pc} live={live} />
      {banner ? (
        <Notice tone="warning" title="AI assistant paused">
          <p>{banner}</p>
        </Notice>
      ) : null}

      {items.length === 0 ? (
        <div className="rounded-card border border-dashed border-border p-4 text-sm text-text-muted">
          <p>Tap the microphone and say what you'd like, for example “pause YouTube”, “set the volume to 40” or “open Notepad”.</p>
          <p className="mt-2">Hear nothing? Check the side switch isn't on Silent and the volume is up.</p>
        </div>
      ) : (
        <Conversation items={items} handlers={handlers} butlerName={prefs.butlerName} />
      )}
      {/* new butler lines, read once by assistive technology; silent while listening */}
      <p className="sr-only" aria-live="polite" data-testid="assistant-live">
        {listening ? "" : announced.text}
      </p>
      <p className="sr-only" aria-live="assertive" ref={announceRef} />
      {interim ? <p className="text-sm italic text-text-muted">{interim}</p> : null}
      <p className="text-sm text-text-muted" role="status" data-testid="assistant-notice">
        {notice ?? ""}
      </p>

      <div className="flex flex-wrap items-center gap-x-2 text-xs text-text-faint">
        <Link to="/app/settings" className="tap inline-flex items-center underline">
          {usable ? "AI on · requests DoMe can't work out go to Anthropic" : "Voice without AI · only commands DoMe understands by itself"}
        </Link>
        <span aria-hidden="true">·</span>
        <Link to="/app/command" className="tap inline-flex items-center underline">
          Type a command without AI
        </Link>
      </div>
      {/* as tall as the dock and what is under it, so the newest card's buttons can scroll above the dock */}
      <div ref={endRef} data-testid="assistant-dock-spacer" className={dockHeight === null ? "h-56" : undefined} style={dockHeight === null ? undefined : { height: `calc(${dockHeight}px + ${inset > 0 ? `${inset}px` : TAB_BAR})` }} aria-hidden="true" />

      <div ref={dockRef} className="fixed inset-x-0 z-10 border-t border-border bg-bg-elevated/95 backdrop-blur px-4 pt-3 pb-3" style={{ bottom: inset > 0 ? `${inset}px` : TAB_BAR }}>
        <div className="mx-auto max-w-lg space-y-2">
          {problem ? (
            <div className="text-sm text-text-muted" role="status">
              <p>{PROBLEM_TEXT[problem.kind]}</p>
              {problem.kind === "denied" ? (
                <details className="mt-1">
                  <summary className="cursor-pointer font-semibold text-accent">How to allow it</summary>
                  <ol className="mt-1 list-decimal pl-5 space-y-1">
                    <li>Open the iPhone's Settings → Privacy &amp; Security → Microphone, and allow Safari (or DoMe, for the Home Screen app).</li>
                    <li>Settings → Privacy &amp; Security → Speech Recognition: allow it the same way.</li>
                    <li>Come back and tap Try voice again.</li>
                  </ol>
                </details>
              ) : null}
            </div>
          ) : null}
          {dictateMode ? (
            <p className="text-xs text-text-muted">
              Dictate: tap the field, then the microphone key on the keyboard. No microphone key? iPhone Settings → General → Keyboard → Enable Dictation.{" "}
              <button type="button" className="tap font-semibold text-accent underline" onClick={tryVoiceAgain}>
                Try voice again
              </button>
            </p>
          ) : null}
          <div className="flex items-end justify-between gap-3">
            {dictateMode ? (
              // Dictate always shows the field: a Type instead / Hide keyboard toggle would do nothing here
              <span className="w-[104px]" aria-hidden="true" />
            ) : (
              <Button size="md" variant="ghost" onClick={() => setTyping((t) => !t)} aria-expanded={typing}>
                {typing ? "Hide keyboard" : "Type instead"}
              </Button>
            )}
            <TalkButton state={talkState} dictate={dictateMode} onTap={tap} onCancel={() => getAssistant().cancelTurn()} onStop={() => getAssistant().stop()} />
            <span className="w-[104px]" aria-hidden="true" />
          </div>
          {typing || dictateMode ? (
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                send();
              }}
            >
              <input ref={inputRef} aria-label={`Ask ${prefs.butlerName}`} className={inputClass} value={text} maxLength={MAX_TEXT} enterKeyHint="send" autoComplete="off" autoCorrect="on" placeholder={dictateMode ? "Tap here, then the keyboard's microphone" : "Type a request"} onChange={(e) => setText(e.currentTarget.value)} />
              <Button type="submit" variant="primary" disabled={text.trim() === "" || phase !== "idle"}>
                Send
              </Button>
            </form>
          ) : null}
        </div>
      </div>

      <VoiceNoticeSheet
        open={noticeOpen}
        onClose={() => setNoticeOpen(false)}
        onDictate={() => {
          setNoticeOpen(false);
          openDictation();
        }}
        onContinue={() => {
          setNoticeOpen(false);
          setPrefs({ voiceNoticeSeen: true });
          startListening(); // this tap is the gesture that starts the microphone
        }}
      />
    </div>
  );
}

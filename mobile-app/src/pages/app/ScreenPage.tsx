/**
 * Screen view (beta), protocol 1.4 (docs/design/screen-view.md §2.2, §7, §8, §16; ADR-0002). The page shows exactly
 * one §16 state at a time (`lib/screenStatus.ts`, which holds the copy) and, while a session runs, the full-viewport
 * stage: a thin top strip (PC name · "Encrypted between your PC and this phone" once the PC-signed answer verified ·
 * Free time left · Touchpad mode · Keyboard · Stop) over the live picture.
 *
 * Nothing starts by itself on arrival: the owner taps "Show screen", which is also the user gesture iOS wants before a
 * video may play. A session is stopped when the page is left (Phase 1 has no mini view), when the app is hidden (the
 * runtime sends `bye`; on return one fresh session starts automatically unless the PC asked someone to allow the last
 * one, which offers Resume instead), on a PC switch and on sign-out. The input session for taps starts once the
 * picture is live, only when the grant includes the touchpad or keyboard, and ends with the picture.
 *
 * The picture is a `<video>` fed by `srcObject` only; this page never reads a pixel, records nothing and logs no SDP,
 * address or credential (rules.screen_content; test/no-pixel-access.test.ts).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { useInputSession, useMyGrant, usePcKeyPin, useScreenSession, useScreenSwitch, useSelectedPc } from "../../app/hooks.ts";
import { FailureLinks } from "../../components/FailureLinks.tsx";
import { Icon } from "../../components/icons.tsx";
import { KeyboardPanel } from "../../components/KeyboardPanel.tsx";
import { PcSwitcher } from "../../components/PcSwitcher.tsx";
import { ScreenSurface, type SurfaceMode } from "../../components/ScreenSurface.tsx";
import { Button, Card, Notice, PageHeader, Pill, Spinner, Steps, type Tone } from "../../components/ui.tsx";
import { FREE_SCREEN_VIEW_MINUTES } from "../../content/plans.ts";
import { DIRECT_GESTURES } from "../../lib/directGestures.ts";
import type { InputEvent } from "../../lib/input.ts";
import { recoverySteps, SCREEN_SCOPE_EXPLANATION } from "../../lib/labels.ts";
import { EMPTY_SCREEN_STATE } from "../../lib/screen.ts";
import { allowanceLow, allowanceText, COPY, inputNotice, remainingSeconds, SCREEN_BETA_LABEL, screenStatus, type ScreenStatus } from "../../lib/screenStatus.ts";
import { WakeLockHolder } from "../../lib/wakeLock.ts";
import { useDevicesStore } from "../../store/devices.ts";
import { useLiveStore } from "../../store/live.ts";
import { useScreenStore } from "../../store/screen.ts";

const IN_SESSION = new Set(["ice", "offering", "starting", "connecting", "live", "paused"]);
const FREE_SECONDS = FREE_SCREEN_VIEW_MINUTES * 60;
/** How long the "View only" line stays after a tap a view-only grant could not send. */
const VIEW_ONLY_FLASH_MS = 2500;

const STRIP_BUTTON = "tap dome-press inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-white/25 bg-white/10 px-3 text-xs font-semibold text-white disabled:opacity-40";

/** The Free allowance as a thin bar plus its line ("About 7 min left"); nothing for unlimited plans. */
function AllowanceMeter({ remaining, text, low }: { remaining: number | null; text: string | null; low: boolean }) {
  if (remaining === null || text === null) return null;
  const percent = Math.max(0, Math.min(100, Math.round((remaining / FREE_SECONDS) * 100)));
  return (
    <div className="space-y-1" data-testid="screen-allowance">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium">Free Screen view</span>
        <span className={`tabular-nums ${low ? "text-warning" : "text-text-muted"}`}>{text}</span>
      </div>
      <div role="progressbar" aria-label="Free Screen view left in this window" aria-valuemin={0} aria-valuemax={FREE_SECONDS} aria-valuenow={remaining} className="h-2 overflow-hidden rounded-full bg-bg-sunken">
        <div className={`h-full rounded-full ${low ? "bg-warning" : "bg-success"}`} style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

/** The one next step a state offers (§16: one next action per state). */
function StatusActions({ status, busy, onStart, onTakeover, onResume }: { status: ScreenStatus; busy: boolean; onStart: () => void; onTakeover: () => void; onResume: () => void }) {
  switch (status.action) {
    case "start":
      return (
        <Button variant="primary" size="lg" full onClick={onStart} disabled={busy}>
          <Icon name="pc" size={20} />
          Show screen
        </Button>
      );
    case "retry":
      return (
        <Button variant="primary" size="lg" full onClick={onStart} disabled={busy}>
          Try again
        </Button>
      );
    case "takeover":
      return (
        <Button variant="primary" size="lg" full onClick={onTakeover} disabled={busy}>
          Take over
        </Button>
      );
    case "resume":
      return (
        <Button variant="primary" size="lg" full onClick={onResume} disabled={busy}>
          Resume
        </Button>
      );
    case "pair":
      return (
        <Link to="/app/devices/pair" className="dome-press dome-primary inline-flex min-h-[52px] w-full items-center justify-center rounded-control px-5 font-semibold">
          Pair again
        </Link>
      );
    default:
      return null;
  }
}

const TONE_CLASS: Record<Tone, string> = { neutral: "text-white", info: "text-white", success: "text-white", warning: "text-amber-200", danger: "text-red-200" };

export function ScreenPage() {
  const { pc, pcId, pcName, live } = useSelectedPc();
  const { session, prefs, setPrefs, client } = useScreenSession();
  const { session: input, prefs: inputPrefs, client: inputClient } = useInputSession();
  const switchOn = useScreenSwitch();
  const grant = useMyGrant(pcId);
  const loadGrants = useDevicesStore((s) => s.loadGrants);
  const relayStatus = useLiveStore((s) => s.relayStatus);
  const pin = usePcKeyPin(switchOn === "on" ? pcId : null);
  const lastAllowance = useScreenStore((s) => (pcId ? (s.lastAllowanceByPc[pcId] ?? null) : null));
  const [now, setNow] = useState(() => Date.now());
  const [mode, setMode] = useState<SurfaceMode>(prefs.touchpadMode ? "touchpad" : "direct");
  const [dragMode, setDragMode] = useState(false);
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const [playBlocked, setPlayBlocked] = useState(false);
  const [playEpoch, setPlayEpoch] = useState(0);
  const [viewOnlyAt, setViewOnlyAt] = useState<number | null>(null);
  const [wake] = useState(() => new WakeLockHolder());

  // a session on another PC is not this page's (the runtime stops it on a PC switch)
  const state = session.pcId === null || session.pcId === pcId ? session : EMPTY_SCREEN_STATE;
  const inSession = IN_SESSION.has(state.phase);
  const pointerGranted = grant?.capabilities.includes("pointer") === true;
  const keyboardGranted = grant?.capabilities.includes("keyboard") === true;
  const inputLive = input.phase === "live" && input.pcId === pcId;
  const canSend = inputLive && input.pointer;

  useEffect(() => {
    if (switchOn === "on" && pcId && grant === undefined) void loadGrants(pcId);
  }, [switchOn, pcId, grant, loadGrants]);
  useEffect(() => {
    const h = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(h);
  }, []);

  // leaving the page ends the picture and the input it started (Phase 1: no mini view)
  useEffect(
    () => () => {
      void client.stop("leave");
      void inputClient.stop("leave");
      void wake.want(false);
    },
    [client, inputClient, wake],
  );

  // a screen wake lock while a session runs; re-requested when the page is visible again
  useEffect(() => {
    void wake.want(inSession);
  }, [wake, inSession]);

  // back in front after the app was hidden: one fresh session automatically, or the Resume button (§2.2 step 5)
  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState !== "visible") return;
      void wake.revisit();
      if (!pcId) return;
      const snap = client.snapshot;
      if (snap.pcId === pcId && snap.phase === "idle" && snap.resume === "auto") {
        client.takeResume();
        void client.start(pcId);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [client, pcId, wake]);

  // taps need the signed input stream: start it once the picture is live, end it with the picture
  const inputFor = useRef<string | null>(null);
  useEffect(() => {
    if (state.phase !== "live" || !pcId || !state.sessionId) return;
    if (!pointerGranted && !keyboardGranted) return;
    if (inputFor.current === state.sessionId) return;
    inputFor.current = state.sessionId;
    if ((input.phase === "live" || input.phase === "starting") && input.pcId === pcId) return;
    void inputClient.start(pcId);
  }, [state.phase, state.sessionId, pcId, pointerGranted, keyboardGranted, input.phase, input.pcId, inputClient]);
  useEffect(() => {
    if (inSession || inputFor.current === null) return;
    inputFor.current = null;
    setKeyboardOpen(false);
    setDragMode(false);
    void inputClient.stop("stopped");
  }, [inSession, inputClient]);

  const start = useCallback(
    (takeover = false) => {
      if (!pcId) return;
      setPlayBlocked(false);
      if (!prefs.explainerSeen) setPrefs({ explainerSeen: true });
      void client.start(pcId, { takeover });
    },
    [pcId, client, prefs.explainerSeen, setPrefs],
  );
  const stop = useCallback(() => {
    setKeyboardOpen(false);
    setDragMode(false);
    void client.stop("stopped");
  }, [client]);

  // a click goes out at once (no tap delay); scroll and touchpad motion ride the per-frame flush, coalesced
  const sendInput = useCallback(
    (events: InputEvent[]) => {
      let queued = false;
      for (const ev of events) queued = inputClient.enqueue(ev) || queued;
      if (queued && events.some((ev) => ev.type === "pointer_button")) void inputClient.flush();
    },
    [inputClient],
  );

  const toggleMode = () => {
    const next: SurfaceMode = mode === "direct" ? "touchpad" : "direct";
    setMode(next);
    setDragMode(false);
    setPrefs({ touchpadMode: next === "touchpad" });
  };

  const status = screenStatus({ state, switchOn, grant, pin, pcName, pcOnline: live.connection === "online", remoteEnabled: live.state?.remote_enabled ?? null, relayUp: relayStatus === "open", now });
  const counting = state.phase === "live";
  const allowance = state.allowance ?? lastAllowance;
  const remaining = remainingSeconds(allowance, now, counting);
  const leftText = allowanceText(allowance, now, counting);
  const low = allowanceLow(allowance, now, counting);
  const notice = inSession ? inputNotice(input, state.restricted) : null;
  const viewOnly = state.phase === "live" && !pointerGranted;
  const flashViewOnly = viewOnlyAt !== null && now - viewOnlyAt < VIEW_ONLY_FLASH_MS;

  if (inSession) {
    const overlay: { title: string; text: string | null; tone: Tone; spinner: boolean } | null =
      state.phase === "live" && !state.stalled && !state.relayDown
        ? null
        : { title: status.title, text: status.kind === "live_relay_down" ? status.text : status.kind === "consent" || status.kind === "paused" ? status.text : null, tone: status.tone, spinner: state.phase !== "live" && state.phase !== "paused" };
    return (
      <div role="region" aria-label={`Screen view of ${pcName ?? "the PC"}`} data-testid="screen-stage" className="fixed inset-0 z-50 flex flex-col bg-black text-white" style={{ paddingTop: "var(--safe-top)", paddingBottom: "var(--safe-bottom)", paddingLeft: "var(--safe-left)", paddingRight: "var(--safe-right)" }}>
        <header className="flex items-center gap-2 px-3 py-2">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">{pcName ?? "Your PC"}</p>
            {state.verified ? (
              <p className="flex items-center gap-1 truncate text-[11px] text-white/75" data-testid="screen-encrypted">
                <Icon name="lock" size={12} />
                {COPY.encrypted}
              </p>
            ) : (
              <p className="truncate text-[11px] text-white/60">{SCREEN_BETA_LABEL}</p>
            )}
          </div>
          {leftText && !allowance?.unlimited ? (
            <span className={`shrink-0 text-[11px] font-semibold tabular-nums ${low ? "text-amber-200" : "text-white/80"}`} data-testid="screen-time-left">
              {leftText}
            </span>
          ) : null}
          <button type="button" className={STRIP_BUTTON} aria-pressed={mode === "touchpad"} onClick={toggleMode} disabled={!pointerGranted}>
            <Icon name="cursor" size={14} />
            Touchpad
          </button>
          <button type="button" className={STRIP_BUTTON} aria-expanded={keyboardOpen} onClick={() => setKeyboardOpen((v) => !v)} disabled={!keyboardGranted || !inputLive} aria-label="Keyboard">
            <Icon name="keyboard" size={14} />
          </button>
          <button type="button" onClick={stop} aria-label="Stop Screen view" className="tap dome-press inline-flex h-9 shrink-0 items-center gap-1 rounded-full bg-red-600 px-3 text-xs font-semibold text-white">
            <Icon name="stop" size={14} />
            Stop
          </button>
        </header>
        {state.path === "relayed" || state.dataSaver || state.encoder === "vp8" ? (
          <div className="flex flex-wrap gap-1.5 px-3 pb-1.5 text-[11px]" data-testid="screen-badges">
            {state.path === "relayed" ? <span className="rounded-full bg-white/12 px-2 py-0.5">{COPY.viaRelay}</span> : null}
            {state.dataSaver ? <span className="rounded-full bg-white/12 px-2 py-0.5">{COPY.dataSaver}</span> : null}
            {state.encoder === "vp8" ? <span className="rounded-full bg-white/12 px-2 py-0.5">{COPY.basicPicture}</span> : null}
          </div>
        ) : null}

        <div className="relative min-h-0 flex-1">
          {state.stream ? (
            <ScreenSurface
              stream={state.stream}
              mode={mode}
              canSend={canSend}
              scrollDirection={inputPrefs.scrollDirection}
              sensitivity={inputPrefs.sensitivity}
              dragMode={dragMode}
              geometry={() => client.tapGeometry()}
              onInput={sendInput}
              onViewOnlyTap={() => setViewOnlyAt(Date.now())}
              onFrame={() => client.onVideoFrame()}
              onResize={() => client.onVideoResize()}
              onPlayBlocked={setPlayBlocked}
              playEpoch={playEpoch}
              label={`Picture of ${pcName ?? "the PC"}'s screen. Tap to click.`}
            />
          ) : null}
          {overlay ? (
            <div className={`pointer-events-none absolute inset-x-0 ${state.stream ? "top-3" : "top-1/3"} z-10 flex flex-col items-center px-6 text-center`} role="status" data-testid="screen-overlay">
              <div className="rounded-2xl bg-black/70 px-4 py-3">
                {overlay.spinner ? <Spinner small label={overlay.title} /> : null}
                <p className={`text-sm font-semibold ${TONE_CLASS[overlay.tone]}`}>{overlay.title}</p>
                {overlay.text ? <p className="mt-1 text-xs text-white/75">{overlay.text}</p> : null}
              </div>
            </div>
          ) : null}
          {playBlocked && state.stream ? (
            <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/60">
              <Button variant="primary" size="lg" onClick={() => setPlayEpoch((n) => n + 1)}>
                {COPY.tapToPlay}
              </Button>
            </div>
          ) : null}
          {notice || viewOnly || flashViewOnly ? (
            <p role="status" className="pointer-events-none absolute inset-x-3 bottom-3 z-10 rounded-2xl bg-black/75 px-4 py-2 text-center text-xs text-white" data-testid="screen-input-notice">
              {notice ? notice.text : COPY.viewOnly}
            </p>
          ) : null}
        </div>

        {mode === "touchpad" && state.phase === "live" ? (
          <div className="flex justify-end gap-2 px-3 py-2">
            <button type="button" className={STRIP_BUTTON} aria-pressed={dragMode} onClick={() => setDragMode((v) => !v)} disabled={!canSend}>
              {dragMode ? "End Drag" : "Drag"}
            </button>
          </div>
        ) : null}
        {keyboardOpen ? (
          <div className="max-h-[50dvh] overflow-y-auto bg-bg p-3 text-text">
            <KeyboardPanel session={input} enqueue={(ev) => inputClient.enqueue(ev)} flush={() => inputClient.flush()} awaitAck={(seq) => inputClient.awaitAck(seq)} onClose={() => setKeyboardOpen(false)} />
          </div>
        ) : null}
      </div>
    );
  }

  const busy = relayStatus !== "open";
  const failedKind = status.kind === "failed";
  return (
    <div className="space-y-4">
      <PageHeader title={SCREEN_BETA_LABEL} back={{ to: "/app/remote", label: "Remote", ariaLabel: "Back to the remote" }} />
      {switchOn === "on" ? <PcSwitcher pc={pc} live={live} /> : null}

      <Card data-testid="screen-status" data-kind={status.kind}>
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-[17px] font-semibold leading-snug">{status.title}</h2>
          {status.kind === "ready" ? <Pill tone="neutral">Beta</Pill> : null}
        </div>
        {status.text ? <p className="mt-2 text-sm text-text-muted">{status.text}</p> : null}
        {failedKind && status.code ? <Steps steps={recoverySteps(status.code, { action: "screen.session_start" })} /> : null}
        {status.action !== "none" ? (
          <div className="mt-4">
            <StatusActions status={status} busy={busy} onStart={() => start(false)} onTakeover={() => start(true)} onResume={() => start(false)} />
          </div>
        ) : null}
        {status.code && status.kind !== "ready" && status.kind !== "stopped" ? <FailureLinks code={status.code} className="mt-3" /> : null}
      </Card>

      {switchOn === "on" ? <AllowanceMeter remaining={remaining} text={leftText} low={low} /> : null}

      {switchOn === "on" && status.kind === "ready" && !prefs.explainerSeen ? (
        <Notice tone="info" title="Before you start">
          <p>{COPY.protectedContent}</p>
          <p className="mt-1">Pinch to zoom on the phone; press and hold for a magnifier; switch on Touchpad for drags.</p>
        </Notice>
      ) : null}

      {switchOn === "on" ? (
        <details className="dome-card p-4 text-sm">
          <summary className="cursor-pointer font-semibold">Gestures on the picture</summary>
          <ul className="mt-3 space-y-2">
            {DIRECT_GESTURES.map((g) => (
              <li key={g.phone} className="flex justify-between gap-4">
                <span className="font-medium">{g.phone}</span>
                <span className="text-right text-text-muted">{g.pc}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-text-faint">{COPY.protectedContent}</p>
          <p className="mt-2 text-xs text-text-faint">{SCREEN_SCOPE_EXPLANATION}</p>
        </details>
      ) : (
        <Link to="/app/remote" className="text-sm font-semibold text-accent underline">
          Back to the remote
        </Link>
      )}
    </div>
  );
}

/**
 * Touchpad and keyboard (spec §10A, Free). Distinct from the text-command panel: everything here is
 * literal human input into whatever the PC has in front. The page starts the manual-input session
 * when it opens (takeover prompt when another phone owns it), stops it when it is left, when Stop
 * Input is tapped or when the runtime decides (hidden, PC switch, sign-out, lost socket). The live
 * indicator reflects the PC's `input_ack` — Windows accepted the events — never an application effect.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { useInputSession, useMyGrant, useSelectedPc } from "../../app/hooks.ts";
import { FailureLinks } from "../../components/FailureLinks.tsx";
import { GestureGuide } from "../../components/GestureGuide.tsx";
import { KeyboardPanel } from "../../components/KeyboardPanel.tsx";
import { PcSwitcher } from "../../components/PcSwitcher.tsx";
import { TouchpadSurface } from "../../components/TouchpadSurface.tsx";
import { Button, Notice, Pill, Steps } from "../../components/ui.tsx";
import { GestureMachine, type GestureOutput } from "../../lib/gestures.ts";
import type { InputEvent } from "../../lib/input.ts";
import { foregroundLabel, sessionStatus } from "../../lib/inputStatus.ts";
import { errorMessage, INPUT_SCOPE_EXPLANATION, recoverySteps } from "../../lib/labels.ts";
import { useDevicesStore } from "../../store/devices.ts";

function toEvent(o: GestureOutput): InputEvent {
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

export function TouchpadPage() {
  const { pc, pcId, pcName, live } = useSelectedPc();
  const { session, prefs, setPrefs, client } = useInputSession();
  const grant = useMyGrant(pcId);
  const loadGrants = useDevicesStore((s) => s.loadGrants);
  const [keyboardOpen, setKeyboardOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [prefsOpen, setPrefsOpen] = useState(false);
  const [dragMode, setDragMode] = useState(false);
  const [leftHeld, setLeftHeld] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [machine] = useState(() => new GestureMachine({ sensitivity: prefs.sensitivity, scrollDirection: prefs.scrollDirection })); // one machine per page instance; options follow below
  const sessionLive = session.phase === "live" && session.pcId === pcId;
  const pointerOk = sessionLive && session.pointer;
  const relayUp = live.connection === "online";
  const grantLacksInput = grant === null || (grant !== undefined && !grant.capabilities.includes("pointer") && !grant.capabilities.includes("keyboard"));

  useEffect(() => machine.setOptions({ sensitivity: prefs.sensitivity, scrollDirection: prefs.scrollDirection }), [machine, prefs]);
  useEffect(() => {
    if (pcId && grant === undefined) void loadGrants(pcId);
  }, [pcId, grant, loadGrants]);
  useEffect(() => {
    const h = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(h);
  }, []);

  // Enter: start the session (the PC decides lock/remote/permission); leave: stop it and release.
  const started = useRef<string | null>(null);
  useEffect(() => {
    if (!pcId || !relayUp || grantLacksInput) return;
    if (started.current === pcId) return;
    started.current = pcId;
    void client.start(pcId);
  }, [pcId, relayUp, grantLacksInput, client]);
  useEffect(
    () => () => {
      machine.cancel();
      started.current = null;
      void client.stop("leave");
    },
    [client, machine],
  );
  // A session that ended for any reason lets the page start again deliberately, not automatically.
  // Drag/held indicators are reset during render (previous-render comparison); the machine itself is
  // an external system and is reset in the effect.
  const sessionOver = session.phase === "ended" || session.phase === "failed" || session.phase === "suspended";
  const [seenPhase, setSeenPhase] = useState(session.phase);
  if (session.phase !== seenPhase) {
    setSeenPhase(session.phase);
    if (sessionOver) {
      setDragMode(false);
      setLeftHeld(false);
    }
  }
  useEffect(() => {
    if (sessionOver) {
      started.current = null;
      machine.cancel();
      machine.setDragMode(false);
      machine.take();
    }
  }, [sessionOver, machine]);

  const drain = useCallback(() => {
    for (const o of machine.take()) client.enqueue(toEvent(o));
    setLeftHeld(machine.isLeftHeld);
  }, [machine, client]);

  const toggleDrag = () => {
    const next = !dragMode;
    setDragMode(next);
    machine.setDragMode(next);
    drain();
  };
  const endDrag = () => {
    setDragMode(false);
    machine.setDragMode(false);
    drain();
  };
  const stopInput = () => {
    machine.cancel();
    machine.setDragMode(false);
    setDragMode(false);
    drain();
    started.current = null;
    void client.flush().then(() => client.stop("stopped"));
  };
  const startAgain = (takeover = false) => {
    if (!pcId) return;
    started.current = pcId;
    void client.start(pcId, { takeover });
  };
  const click = (button: "left" | "right" | "double") => {
    if (button === "double") client.enqueue({ type: "pointer_button", button: "left", action: "double_click" });
    else client.enqueue({ type: "pointer_button", button, action: "click" });
  };

  const status = sessionStatus(session, now);
  const problem = session.problem;
  const showProblem = problem !== null;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-2xl font-bold tracking-tight">Touchpad</h1>
        <Button size="md" variant="danger" onClick={stopInput} disabled={session.phase === "idle"} aria-label="Stop Input">
          Stop Input
        </Button>
      </div>
      <PcSwitcher pc={pc} live={live} />

      <div className="flex items-center justify-between gap-2 text-sm">
        <div className="min-w-0">
          <p className="truncate">
            <span className="text-text-muted">In front on {pcName ?? "the PC"}: </span>
            <span className="font-medium" title={session.foregroundApp?.window_title}>
              {foregroundLabel(session.foregroundApp)}
            </span>
          </p>
          {session.heldButtons.length > 0 || session.heldKeys.length > 0 ? <p className="text-xs text-warning">Held on the PC: {[...session.heldButtons.map((b) => `${b} button`), ...session.heldKeys].join(", ")}</p> : null}
        </div>
        <Link to="/app/health" className="shrink-0">
          <Pill tone={status.tone} pulse={status.pulse}>
            {status.label}
          </Pill>
        </Link>
      </div>

      {grantLacksInput ? (
        <Notice tone="warning" title="This phone has no touchpad or keyboard permission on this PC">
          <p>{INPUT_SCOPE_EXPLANATION}</p>
          <Steps steps={recoverySteps("INPUT_NOT_PERMITTED")} />
          <FailureLinks code="INPUT_NOT_PERMITTED" />
        </Notice>
      ) : null}

      {!relayUp && !grantLacksInput ? (
        <Notice tone="warning" title={`${pcName ?? "The PC"} is not online`}>
          <p>The touchpad needs the PC connected to DoMe. Nothing is queued for later.</p>
          <FailureLinks code="PC_OFFLINE" />
        </Notice>
      ) : null}

      {showProblem && problem && session.phase !== "live" ? (
        <Notice tone={session.owned ? "info" : "warning"} title={errorMessage(problem)}>
          <Steps steps={recoverySteps(problem.code)} />
          {session.endReason === "takeover" ? <p>Another phone took over this PC’s touchpad.</p> : null}
          {session.holdsReleased > 0 ? <p>The PC released {session.holdsReleased} held button(s)/key(s).</p> : null}
          <div className="flex flex-wrap gap-2 mt-2">
            {session.owned ? (
              <Button size="md" variant="primary" onClick={() => startAgain(true)} disabled={!relayUp}>
                Take over
              </Button>
            ) : null}
            <Button size="md" onClick={() => startAgain(false)} disabled={!relayUp || grantLacksInput}>
              {session.owned ? "Wait, try again" : "Start again"}
            </Button>
          </div>
          <FailureLinks code={problem.code} />
        </Notice>
      ) : null}
      {showProblem && problem && session.phase === "live" ? (
        <Notice tone="warning" title={errorMessage(problem)}>
          <Steps steps={recoverySteps(problem.code)} />
          <Button size="md" variant="ghost" onClick={() => client.clearProblem()}>
            Dismiss
          </Button>
        </Notice>
      ) : null}
      {session.phase === "idle" && relayUp && !grantLacksInput ? (
        <Button variant="primary" onClick={() => startAgain(false)} full>
          Start touchpad on {pcName ?? "the PC"}
        </Button>
      ) : null}
      {session.phase === "starting" ? <p className="text-xs text-text-muted">Asking {pcName ?? "the PC"} for a touchpad session — this times out after a few seconds if the PC does not answer.</p> : null}

      <TouchpadSurface machine={machine} onOutput={drain} disabled={!pointerOk} dragActive={dragMode || leftHeld} label={`Touchpad for ${pcName ?? "the PC"}`} compact={keyboardOpen} />

      <div className="grid grid-cols-4 gap-2" role="group" aria-label="Pointer buttons">
        <Button size="lg" disabled={!pointerOk} onClick={() => click("left")}>
          Left
        </Button>
        <Button size="lg" disabled={!pointerOk} onClick={() => click("right")}>
          Right
        </Button>
        <Button size="lg" disabled={!pointerOk} onClick={() => click("double")} aria-label="Double click">
          Double
        </Button>
        {dragMode || leftHeld ? (
          <Button size="lg" variant="danger" onClick={endDrag} aria-pressed="true" aria-label="End Drag">
            End Drag
          </Button>
        ) : (
          <Button size="lg" disabled={!pointerOk} onClick={toggleDrag} aria-pressed="false" aria-label="Drag mode">
            Drag
          </Button>
        )}
      </div>
      <div className="grid grid-cols-3 gap-2">
        <Button size="md" variant={keyboardOpen ? "primary" : "secondary"} onClick={() => setKeyboardOpen((v) => !v)} aria-expanded={keyboardOpen} disabled={!sessionLive && !keyboardOpen}>
          Keyboard
        </Button>
        <Button size="md" onClick={() => setGuideOpen(true)}>
          Gestures
        </Button>
        <Button size="md" onClick={() => setPrefsOpen((v) => !v)} aria-expanded={prefsOpen}>
          Settings
        </Button>
      </div>

      {prefsOpen ? (
        <section className="rounded-card bg-bg-elevated border border-border p-3 space-y-3" aria-label="Touchpad settings">
          <div>
            <label htmlFor="sensitivity" className="text-sm font-medium flex justify-between">
              <span>Sensitivity</span>
              <span className="text-text-muted">{prefs.sensitivity.toFixed(1)}×</span>
            </label>
            <input id="sensitivity" type="range" className="slider w-full" min={0.5} max={3} step={0.1} value={prefs.sensitivity} onChange={(e) => setPrefs({ sensitivity: Number(e.currentTarget.value) })} />
          </div>
          <fieldset>
            <legend className="text-sm font-medium mb-1">Scroll direction</legend>
            <div className="grid grid-cols-2 gap-2">
              {(["natural", "standard"] as const).map((d) => (
                <label key={d} className={`flex items-center gap-2 rounded-control border px-3 py-2 text-sm ${prefs.scrollDirection === d ? "border-accent bg-accent/10" : "border-border"}`}>
                  <input type="radio" name="scroll-direction" value={d} checked={prefs.scrollDirection === d} onChange={() => setPrefs({ scrollDirection: d })} />
                  {d === "natural" ? "Natural (content follows fingers)" : "Standard (wheel-like)"}
                </label>
              ))}
            </div>
          </fieldset>
          <p className="text-[11px] text-text-faint">Saved on this phone only. Cursor movement is relative, so these never depend on the PC’s screen size or monitor layout.</p>
        </section>
      ) : null}

      {keyboardOpen ? <KeyboardPanel session={session} enqueue={(ev) => client.enqueue(ev)} flush={() => client.flush()} awaitAck={(seq) => client.awaitAck(seq)} onClose={() => setKeyboardOpen(false)} /> : null}

      <GestureGuide open={guideOpen} onClose={() => setGuideOpen(false)} />
    </div>
  );
}

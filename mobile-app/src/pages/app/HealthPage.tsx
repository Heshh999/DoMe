/**
 * Connection health (spec §11A). Layered states from `lib/health.ts`, one next action each, details
 * behind a control, bounded retries with a support path, the V1 requirements stated plainly, and the
 * first-use walkthrough that returns to the step that failed. No spinner here runs indefinitely:
 * every waiting state shows what it waits for and offers Retry.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { PROTOCOL_VERSION } from "@dome/protocol";

import { useInputSession, useLabels, useMyGrant, useSelectedPc } from "../../app/hooks.ts";
import { getRuntime } from "../../app/runtime.ts";
import { useOnline } from "../../app/useOnline.ts";
import { CommandOutcome } from "../../components/CommandOutcome.tsx";
import { supportLink } from "../../components/FailureLinks.tsx";
import { Button, Card, Notice, Pill, type Tone } from "../../components/ui.tsx";
import { APP_VERSION } from "../../lib/diagnostics.ts";
import { relativeTime } from "../../lib/format.ts";
import { assessHealth, lastVerifiedResult, onboardingSteps, type HealthLayer, type LayerStatus } from "../../lib/health.ts";
import { useDevicesStore } from "../../store/devices.ts";
import { useLiveStore } from "../../store/live.ts";
import { useSessionStore } from "../../store/session.ts";

export const MAX_RETRIES = 3;

const TONE: Record<LayerStatus, Tone> = { ok: "success", problem: "danger", unknown: "neutral", waiting: "info" };
const LABEL: Record<LayerStatus, string> = { ok: "OK", problem: "Needs attention", unknown: "Unknown", waiting: "Waiting" };

function ActionButton({ layer }: { layer: HealthLayer }) {
  const a = layer.action;
  if (a.kind === "none") return null;
  if (a.to) {
    return (
      <Link to={a.to} className="tap inline-flex items-center rounded-control bg-accent text-on-accent text-sm font-semibold px-4 mt-2">
        {a.label}
      </Link>
    );
  }
  if (a.kind === "sign_in") {
    return (
      <Button size="md" variant="primary" className="mt-2" onClick={() => window.location.reload()}>
        {a.label}
      </Button>
    );
  }
  if (a.kind === "reconnect_phone") {
    return (
      <Button size="md" variant="primary" className="mt-2" onClick={() => getRuntime().relay.nudge()}>
        {a.label}
      </Button>
    );
  }
  // Actions that happen on the PC: a plain instruction, not a button that pretends to do it from here.
  return <p className="mt-2 text-sm font-semibold">{a.label}</p>;
}

export function HealthPage() {
  const online = useOnline();
  const sessionStatus = useSessionStore((s) => s.status);
  const relayStatus = useLiveStore((s) => s.relayStatus);
  const controllerId = useLiveStore((s) => s.controllerId);
  const commands = useLiveStore((s) => s.commands);
  const now = useLiveStore((s) => s.now);
  const { pc, pcId, pcName, live } = useSelectedPc();
  const grant = useMyGrant(pcId);
  const loadGrants = useDevicesStore((s) => s.loadGrants);
  const refreshDevices = useDevicesStore((s) => s.refresh);
  const { session: input } = useInputSession();
  const selectedTab = useLiveStore((s) => (pcId ? s.selectedTab[pcId] : undefined));
  const selectedSession = useLiveStore((s) => (pcId ? s.selectedSession[pcId] : undefined));
  const labels = useLabels(pcId);
  const [retries, setRetries] = useState(0);
  const [retryAt, setRetryAt] = useState<number | null>(null);
  const [showDetails, setShowDetails] = useState(false);

  useEffect(() => {
    if (pcId && grant === undefined) void loadGrants(pcId);
  }, [pcId, grant, loadGrants]);

  const layers = assessHealth({ online, sessionStatus, relayStatus, controllerBound: controllerId !== null, pc, live, grant, input, selectedTab, selectedSession, commands, appVersion: APP_VERSION, protocolVersion: PROTOCOL_VERSION, now: Math.max(now, Date.now()) });
  const steps = onboardingSteps({ sessionStatus, pc, live, controllerBound: controllerId !== null, grant, selectedTab, selectedSession, commands });
  const firstUndone = steps.find((s) => !s.done) ?? null;
  const problems = layers.filter((l) => l.status === "problem");
  const last = lastVerifiedResult(commands, pcId);
  const retriesLeft = MAX_RETRIES - retries;

  // A retry re-checks the connection only. It never resends a command: an uncertain PC action stays
  // uncertain until the PC reports (spec §11A "a connection retry must not retry an uncertain PC action").
  const retry = () => {
    if (retriesLeft <= 0) return;
    setRetries((r) => r + 1);
    setRetryAt(Date.now());
    useLiveStore.getState().markAllStale();
    getRuntime().relay.nudge();
    void refreshDevices();
    if (pcId) void loadGrants(pcId);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">Connection health</h1>
      <Card>
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="font-semibold text-lg">{pcName ?? "No PC selected"}</p>
            <p className="text-sm text-text-muted">Last seen {relativeTime(live.lastSeen ?? pc?.last_seen)}</p>
          </div>
          <Pill tone={problems.length === 0 ? (layers.some((l) => l.status === "waiting" || l.status === "unknown") ? "info" : "success") : "warning"}>{problems.length === 0 ? "No known problem" : `${problems.length} item${problems.length === 1 ? "" : "s"} need attention`}</Pill>
        </div>
        <p className="text-xs text-text-faint mt-2">Each row below is checked separately. A green row never implies the others work.</p>
      </Card>

      <ul className="space-y-2" aria-label="Health checks">
        {layers.map((l) => (
          <Card key={l.id} as="li" aria-label={l.title}>
            <div className="flex items-start justify-between gap-2">
              <p className="font-semibold">{l.title}</p>
              <Pill tone={TONE[l.status]} pulse={l.status === "waiting"}>
                {LABEL[l.status]}
              </Pill>
            </div>
            <p className="text-sm text-text-muted mt-1">{l.summary}</p>
            <ActionButton layer={l} />
            {showDetails && l.details.length > 0 ? (
              <ul className="mt-2 text-xs font-mono text-text-faint space-y-0.5" aria-label={`${l.title} details`}>
                {l.details.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            ) : null}
          </Card>
        ))}
      </ul>
      <button type="button" className="text-sm text-accent underline" onClick={() => setShowDetails((v) => !v)} aria-expanded={showDetails}>
        {showDetails ? "Hide technical details" : "Show technical details (codes, versions)"}
      </button>

      <Card>
        <h2 className="font-semibold">Retry</h2>
        <p className="text-sm text-text-muted mt-1">Re-checks this phone’s connection and asks the PC for its current state. It never re-sends a command: an action whose outcome is unknown stays unknown until the PC reports.</p>
        <div className="mt-2 flex items-center gap-3">
          <Button variant="primary" onClick={retry} disabled={retriesLeft <= 0}>
            Retry{retriesLeft > 0 && retries > 0 ? ` (${retriesLeft} left)` : ""}
          </Button>
          {retryAt ? <span className="text-xs text-text-faint">Last retry {relativeTime(new Date(retryAt).toISOString())}</span> : null}
        </div>
        {retriesLeft <= 0 ? (
          <Notice tone="warning" title="Still not working after several retries">
            <p>Retrying further will not change anything by itself. Check the PC directly, then contact support with your redacted diagnostics.</p>
            <Link to={supportLink(problems[0]?.details.find((d) => /^[A-Z_]+$/.test(d)) ?? null)} className="text-accent font-semibold underline">
              Contact support
            </Link>
          </Notice>
        ) : null}
      </Card>

      <Card>
        <h2 className="font-semibold">Last verified result</h2>
        {last ? <CommandOutcome record={last} pcName={pcName} labels={labels} compact /> : <p className="text-sm text-text-muted mt-1">No command has completed on {pcName ?? "this PC"} in this session.</p>}
      </Card>

      <Card>
        <h2 className="font-semibold">First-use walkthrough</h2>
        <p className="text-sm text-text-muted mt-1">{firstUndone ? `Continue at step ${steps.indexOf(firstUndone) + 1}: ${firstUndone.title}.` : "Every step is done."}</p>
        <ol className="mt-2 space-y-2" aria-label="Setup steps">
          {steps.map((s, idx) => (
            <li key={s.id} className={`flex items-start gap-3 rounded-control border px-3 py-2 ${s === firstUndone ? "border-accent bg-accent/10" : "border-border"}`}>
              <span aria-hidden="true" className={`mt-0.5 h-5 w-5 shrink-0 rounded-full text-xs flex items-center justify-center ${s.done ? "bg-success text-white" : "bg-bg-sunken text-text-muted"}`}>
                {s.done ? "✓" : idx + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block font-medium text-sm">{s.title}</span>
                <span className="block text-xs text-text-muted">{s.hint}</span>
              </span>
              {!s.done ? (
                <Link to={s.to} className="text-sm text-accent font-semibold shrink-0">
                  {s === firstUndone ? "Go" : "Open"}
                </Link>
              ) : null}
            </li>
          ))}
        </ol>
      </Card>

      <Card>
        <h2 className="font-semibold">What DoMe needs (V1)</h2>
        <ul className="list-disc pl-5 text-sm text-text-muted mt-1 space-y-1">
          <li>Internet access on this phone and on the PC. Both connect out to the DoMe service; no port forwarding or VPN.</li>
          <li>The DoMe Windows app running in a signed-in Windows user session (tray icon visible), linked to your account.</li>
          <li>Remote control switched on locally on the PC. Only the PC can switch it on.</li>
          <li>Touchpad and keyboard permission granted to this phone on the PC; it reaches every app of the unlocked session.</li>
          <li>Chrome or Edge with the DoMe extension for YouTube controls. Windows media, volume, apps and the touchpad work without it.</li>
          <li>Windows unlocked: the lock screen, sign-in and administrator prompts cannot be controlled, by design. Media while locked is a local PC setting.</li>
        </ul>
        <p className="text-xs text-text-faint mt-2">DoMe cannot wake or power on a PC remotely in this version.</p>
      </Card>
    </div>
  );
}

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
import { Icon, type IconName } from "../../components/icons.tsx";
import { supportLink } from "../../lib/support.ts";
import { Button, Card, IconWell, Notice, PageHeader, Pill, type Tone, type WellTone } from "../../components/ui.tsx";
import { APP_VERSION } from "../../lib/diagnostics.ts";
import { relativeTime } from "../../lib/format.ts";
import { assessHealth, lastVerifiedResult, onboardingSteps, type HealthLayer, type LayerStatus } from "../../lib/health.ts";
import { useDevicesStore } from "../../store/devices.ts";
import { useLiveStore } from "../../store/live.ts";
import { useSessionStore } from "../../store/session.ts";

export const MAX_RETRIES = 3;

const TONE: Record<LayerStatus, Tone> = { ok: "success", problem: "danger", unknown: "neutral", waiting: "info" };
const LABEL: Record<LayerStatus, string> = { ok: "OK", problem: "Needs attention", unknown: "Unknown", waiting: "Waiting" };
const WELL_TONE: Record<LayerStatus, WellTone> = { ok: "success", problem: "danger", unknown: "muted", waiting: "accent" };
const STATUS_ICON: Record<LayerStatus, IconName> = { ok: "check", problem: "warning", unknown: "help", waiting: "clock" };
const LAYER_ICON: Record<HealthLayer["id"], IconName> = { phone: "phone", account: "user", pc: "pc", remote: "remote", input: "touchpad", extension: "youtube", target: "play" };

function ActionButton({ layer }: { layer: HealthLayer }) {
  const a = layer.action;
  if (a.kind === "none") return null;
  if (a.to) {
    return (
      <Link to={a.to} className="tap dome-primary dome-press mt-3 inline-flex min-h-[44px] items-center rounded-control px-4 text-sm font-semibold">
        {a.label}
      </Link>
    );
  }
  if (a.kind === "sign_in") {
    return (
      <Button size="md" variant="primary" className="mt-3" onClick={() => window.location.reload()}>
        {a.label}
      </Button>
    );
  }
  if (a.kind === "reconnect_phone") {
    return (
      <Button size="md" variant="primary" className="mt-3" onClick={() => getRuntime().relay.nudge()}>
        {a.label}
      </Button>
    );
  }
  // Actions that happen on the PC: a plain instruction, not a button that pretends to do it from here.
  return (
    <p className="mt-3 flex items-start gap-2 text-sm font-semibold">
      <Icon name="pc" size={18} className="mt-px shrink-0 text-text-faint" />
      {a.label}
    </p>
  );
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

  const layers = assessHealth({ online, sessionStatus, relayStatus, controllerBound: controllerId !== null, controllerId, pc, live, grant, input, selectedTab, selectedSession, commands, appVersion: APP_VERSION, protocolVersion: PROTOCOL_VERSION, now });
  const steps = onboardingSteps({ sessionStatus, pc, live, controllerBound: controllerId !== null, grant, selectedTab, selectedSession, commands });
  const firstUndone = steps.find((s) => !s.done) ?? null;
  const problems = layers.filter((l) => l.status === "problem");
  const last = lastVerifiedResult(commands, pcId);
  const retriesLeft = MAX_RETRIES - retries;
  const overall: { tone: Tone; well: WellTone; label: string } = problems.length === 0 ? (layers.some((l) => l.status === "waiting" || l.status === "unknown") ? { tone: "info", well: "accent", label: "No known problem" } : { tone: "success", well: "success", label: "No known problem" }) : { tone: "warning", well: "warning", label: `${problems.length} item${problems.length === 1 ? "" : "s"} need attention` };

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
      <PageHeader title="Connection health" />
      <Card>
        <div className="flex items-center gap-3.5">
          <IconWell icon="wifi" tone={overall.well} size={44} radius={14} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-[17px] font-semibold leading-snug tracking-[-0.01em]">{pcName ?? "No PC selected"}</p>
            <p className="text-xs text-text-muted">Last seen {relativeTime(live.lastSeen ?? pc?.last_seen)}</p>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
          <Pill tone={overall.tone}>{overall.label}</Pill>
          <p className="text-xs text-text-faint">Each row below is checked separately. A green row never implies the others work.</p>
        </div>
      </Card>

      <ul className="space-y-2" aria-label="Health checks">
        {layers.map((l) => (
          <Card key={l.id} as="li" aria-label={l.title} className="p-4">
            <div className="flex items-start gap-3.5">
              <IconWell icon={LAYER_ICON[l.id]} tone={WELL_TONE[l.status]} size={40} />
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-2">
                  <p className="pt-0.5 font-semibold leading-snug">{l.title}</p>
                  <Pill tone={TONE[l.status]} pulse={l.status === "waiting"}>
                    <Icon name={STATUS_ICON[l.status]} size={12} className="-ml-0.5" />
                    {LABEL[l.status]}
                  </Pill>
                </div>
                <p className="mt-1 text-sm text-text-muted">{l.summary}</p>
                <ActionButton layer={l} />
                {showDetails && l.details.length > 0 ? (
                  <ul className="mt-2 space-y-0.5 rounded-control bg-bg-sunken px-3 py-2 font-mono text-xs text-text-faint" aria-label={`${l.title} details`}>
                    {l.details.map((d) => (
                      <li key={d} className="break-all">
                        {d}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            </div>
          </Card>
        ))}
      </ul>
      <div className="px-1">
        <Button variant="quiet" size="sm" className="-ml-3" onClick={() => setShowDetails((v) => !v)} aria-expanded={showDetails}>
          <Icon name="chevron" size={16} className={showDetails ? "-rotate-90" : "rotate-90"} />
          {showDetails ? "Hide technical details" : "Show technical details (codes, versions)"}
        </Button>
      </div>

      <Card>
        <div className="flex items-start gap-3.5">
          <IconWell icon="refresh" size={40} />
          <div className="min-w-0 flex-1 pt-0.5">
            <h2 className="font-semibold leading-snug">Retry</h2>
            <p className="mt-1 text-sm text-text-muted">Re-checks this phone’s connection and asks the PC for its current state. It never re-sends a command: an action whose outcome is unknown stays unknown until the PC reports.</p>
          </div>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button variant="primary" onClick={retry} disabled={retriesLeft <= 0}>
            Retry{retriesLeft > 0 && retries > 0 ? ` (${retriesLeft} left)` : ""}
          </Button>
          {retryAt ? <span className="text-xs text-text-faint">Last retry {relativeTime(new Date(retryAt).toISOString())}</span> : null}
        </div>
        {retriesLeft <= 0 ? (
          <div className="mt-4">
            <Notice tone="warning" title="Still not working after several retries">
              <p>Retrying further will not change anything by itself. Check the PC directly, then contact support with your redacted diagnostics.</p>
              <Link to={supportLink(problems[0]?.details.find((d) => /^[A-Z_]+$/.test(d)) ?? null)} className="font-semibold text-accent underline">
                Contact support
              </Link>
            </Notice>
          </div>
        ) : null}
      </Card>

      <Card>
        <h2 className="font-semibold">Last verified result</h2>
        {last ? (
          <div className="mt-2">
            <CommandOutcome record={last} pcName={pcName} labels={labels} compact />
          </div>
        ) : (
          <p className="mt-1 text-sm text-text-muted">No command has completed on {pcName ?? "this PC"} in this session.</p>
        )}
      </Card>

      <Card>
        <h2 className="font-semibold">First-use walkthrough</h2>
        <p className="mt-1 text-sm text-text-muted">{firstUndone ? `Continue at step ${steps.indexOf(firstUndone) + 1}: ${firstUndone.title}.` : "Every step is done."}</p>
        <ol className="mt-3 space-y-2" aria-label="Setup steps">
          {steps.map((s, idx) => (
            <li key={s.id} className={`flex items-start gap-3 rounded-control border px-3 py-2.5 ${s === firstUndone ? "border-accent/60 bg-accent/8" : "border-border"}`}>
              <span aria-hidden="true" className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${s.done ? "bg-success text-white" : s === firstUndone ? "bg-primary text-on-primary" : "bg-bg-sunken text-text-muted"}`}>
                {s.done ? <Icon name="check" size={14} strokeWidth={2.5} /> : idx + 1}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{s.title}</span>
                <span className="block text-xs text-text-muted">{s.hint}</span>
              </span>
              {!s.done ? (
                <Link to={s.to} className="tap -my-1.5 inline-flex shrink-0 items-center rounded-full px-2 text-sm font-semibold text-accent">
                  {s === firstUndone ? "Go" : "Open"}
                </Link>
              ) : null}
            </li>
          ))}
        </ol>
      </Card>

      <Card>
        <h2 className="font-semibold">What DoMe needs (V1)</h2>
        <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-text-muted marker:text-text-faint">
          <li>Internet access on this phone and on the PC. Both connect out to the DoMe service; no port forwarding or VPN.</li>
          <li>The DoMe Windows app running in a signed-in Windows user session (tray icon visible), linked to your account.</li>
          <li>Remote control switched on locally on the PC. Only the PC can switch it on.</li>
          <li>Touchpad and keyboard permission granted to this phone on the PC; it reaches every app of the unlocked session.</li>
          <li>Chrome or Edge with the DoMe extension for YouTube controls. Windows media, volume, apps and the touchpad work without it.</li>
          <li>Windows unlocked: the lock screen, sign-in and administrator prompts cannot be controlled, by design. Media while locked is a local PC setting.</li>
        </ul>
        <p className="mt-3 text-xs text-text-faint">DoMe cannot wake or power on a PC remotely in this version.</p>
      </Card>
    </div>
  );
}

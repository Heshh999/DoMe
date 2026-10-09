/**
 * Dashboard: selected PC with truthful connection state, active media with play/pause, Windows
 * volume, quick actions and the last command result. Consequential controls are disabled until the
 * PC's state is fresh.
 */
import { Link } from "react-router";

import { CommandOutcome } from "../../components/CommandOutcome.tsx";
import { FailureLinks } from "../../components/FailureLinks.tsx";
import { NowPlaying } from "../../components/NowPlaying.tsx";
import { PcSwitcher } from "../../components/PcSwitcher.tsx";
import { Button, Card, Notice, Pill } from "../../components/ui.tsx";
import { VolumeSlider } from "../../components/VolumeSlider.tsx";
import { useLabels, useLastCommandFor, useSelectedPc, useSend } from "../../app/hooks.ts";
import { errorMessage, recoverySteps } from "../../lib/labels.ts";
import { relativeTime } from "../../lib/format.ts";
import { powerRequestEvidence, powerRequestNotice } from "../../lib/power.ts";
import { resolveMediaTarget, resolveYoutubeTarget } from "../../lib/targets.ts";
import { useLiveStore } from "../../store/live.ts";
import { Steps } from "../../components/ui.tsx";

export function DashboardPage() {
  const { pc, pcId, pcName, live, fresh, canControl } = useSelectedPc();
  const { send, sendError, clearError } = useSend(pcId);
  const last = useLastCommandFor(pcId);
  const labels = useLabels(pcId);
  const selectedTab = useLiveStore((s) => (pcId ? s.selectedTab[pcId] : undefined));
  const selectedSession = useLiveStore((s) => (pcId ? s.selectedSession[pcId] : undefined));
  const commands = useLiveStore((s) => s.commands);
  const state = live.state;
  const yt = resolveYoutubeTarget(state, selectedTab);
  const media = resolveMediaTarget(state, selectedSession);
  const volume = state?.volume ?? null;
  const remoteOff = state?.remote_enabled === false;
  const locked = state?.session_locked === true;

  return (
    <div className="space-y-4">
      <header className="flex items-end justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Home</h1>
        <Link to="/app/devices" className="tap inline-flex items-center text-sm text-accent font-semibold px-2">
          Devices
        </Link>
      </header>
      <PcSwitcher pc={pc} live={live} />

      {pc && !fresh && live.connection === "online" && !live.stale && state === null ? <Notice tone="info">Waiting for the PC to report its state…</Notice> : null}
      {remoteOff ? (
        <Notice tone="warning" title="Remote control is switched off on the PC">
          <Steps steps={recoverySteps("PC_REMOTE_DISABLED")} />
          <FailureLinks code="PC_REMOTE_DISABLED" />
        </Notice>
      ) : null}
      {locked && !remoteOff ? <Notice tone="info">The PC is locked. {state?.media_while_locked ? "Media controls are allowed while locked." : "Only Lock and status are available until it is unlocked."}</Notice> : null}
      {state?.pending_power_action ? (
        <Notice tone="warning" title={`${state.pending_power_action.action.replace("power.", "").replace(/^\w/, (c) => c.toUpperCase())} countdown running on the PC`}>
          <p>Runs at {new Date(state.pending_power_action.fires_at).toLocaleTimeString()} unless cancelled.</p>
          <Button variant="danger" size="md" className="mt-2" disabled={!canControl} onClick={() => void send("power.cancel", {}, null, "button")}>
            Cancel countdown
          </Button>
        </Notice>
      ) : null}
      {pcId && live.lastPowerRequest && live.connection !== "online" ? (
        // The relay notes the request when it forwards it (state `created`); only this phone's own
        // command record can show that Windows actually accepted it. Never claim more than is known.
        <Notice tone="info" title="Power request">
          <p>{powerRequestNotice(live.lastPowerRequest, powerRequestEvidence(commands, pcId, live.lastPowerRequest))}</p>
        </Notice>
      ) : null}

      <NowPlaying pcName={pcName} state={state} fresh={fresh} canControl={canControl} selectedTab={selectedTab} selectedSession={selectedSession} onSend={(action, params, target) => void send(action, params, target, "button")} />

      <VolumeSlider label="Windows volume" scopeHint="The PC’s system volume — affects every app." value={fresh && typeof volume?.value === "number" ? volume.value : null} muted={fresh ? (volume?.muted ?? null) : null} disabled={!canControl} onChange={(v) => void send("windows.set_volume", { value: v }, null, "slider")} onToggleMute={() => void send("windows.set_muted", { muted: !(volume?.muted ?? false) }, null, "button")} />

      <Card>
        <h2 className="font-semibold mb-3">Quick actions</h2>
        <div className="grid grid-cols-2 gap-2">
          <Button size="lg" disabled={!canControl || !yt.ok || yt.tab.has_next === false} onClick={() => yt.ok && void send("youtube.next", {}, yt.target, "button")}>
            Next video
          </Button>
          <Button size="lg" disabled={!canControl || !yt.ok} onClick={() => yt.ok && void send("youtube.seek_relative", { seconds: -10 }, yt.target, "button")}>
            Back 10 s
          </Button>
          <Button size="lg" disabled={!canControl || locked} onClick={() => void send("windows.lock", {}, null, "button")}>
            Lock Windows
          </Button>
          <Button size="lg" disabled={!canControl} onClick={() => void send("power.sleep", {}, null, "button")}>
            Sleep…
          </Button>
        </div>
        {yt.ok && yt.tab.has_next === false ? <p className="text-xs text-text-muted mt-2">This player reports no next video, so Next is unavailable.</p> : null}
        {!yt.ok && media.ok ? <p className="text-xs text-text-muted mt-2">Next video and Back 10 s are YouTube controls; they stay off while the target is a Windows media player and are never turned into a generic media key.</p> : null}
        <p className="text-xs text-text-faint mt-2">Sleep, restart and shut down always ask you to confirm on this phone first.</p>
      </Card>

      {sendError ? (
        <Notice tone="danger" title="Not sent">
          <p>{errorMessage(sendError)}</p>
          <Steps steps={recoverySteps(sendError.code)} />
          <FailureLinks code={sendError.code} />
          <Button size="md" variant="ghost" onClick={clearError}>
            Dismiss
          </Button>
        </Notice>
      ) : null}

      <Card>
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-semibold">Last command</h2>
          {last ? <Pill tone="neutral">{relativeTime(new Date(last.createdAt).toISOString())}</Pill> : null}
        </div>
        {last ? <CommandOutcome record={last} pcName={pcName} labels={labels} /> : <p className="text-sm text-text-muted">Nothing sent to {pcName ?? "this PC"} yet in this session.</p>}
      </Card>
    </div>
  );
}

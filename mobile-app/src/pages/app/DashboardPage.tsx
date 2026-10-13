/**
 * Home: the selected PC with its truthful connection state, what is playing (with play/pause), the
 * Windows volume, the most-used actions and the last command result. Consequential controls are
 * disabled until the PC's state is fresh.
 */
import { Link } from "react-router";

import { CommandOutcome } from "../../components/CommandOutcome.tsx";
import { FailureLinks } from "../../components/FailureLinks.tsx";
import { Wordmark } from "../../components/brand.tsx";
import { Icon, type IconName } from "../../components/icons.tsx";
import { NowPlaying } from "../../components/NowPlaying.tsx";
import { PcOfflineNotice } from "../../components/PcOfflineNotice.tsx";
import { PcSwitcher } from "../../components/PcSwitcher.tsx";
import { Button, HeaderPill, Notice, Pill, SectionLabel } from "../../components/ui.tsx";
import { VolumeSlider } from "../../components/VolumeSlider.tsx";
import { useLabels, useLastCommandFor, useSelectedPc, useSend } from "../../app/hooks.ts";
import { errorMessage, recoverySteps } from "../../lib/labels.ts";
import { relativeTime } from "../../lib/format.ts";
import { powerRequestEvidence, powerRequestNotice } from "../../lib/power.ts";
import { resolveMediaTarget, resolveYoutubeTarget } from "../../lib/targets.ts";
import { useLiveStore } from "../../store/live.ts";
import { Steps } from "../../components/ui.tsx";

/** One of the four quick actions: a tile with an icon and its name (the name is the button's label). */
function QuickAction({ icon, label, disabled, onClick }: { icon: IconName; label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick} className="dome-card dome-press flex min-h-[64px] items-center gap-3 px-3.5 py-3 text-left text-[15px] font-semibold leading-tight tracking-[-0.01em] hover:bg-surface disabled:cursor-not-allowed disabled:opacity-45">
      <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent/12 text-accent">
        <Icon name={icon} size={20} />
      </span>
      {label}
    </button>
  );
}

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
      <header className="flex items-center justify-between pb-1">
        <h1 className="sr-only">Home</h1>
        <Wordmark compact title="DoMe" className="h-[22px] w-auto" />
        <HeaderPill to="/app/devices" icon="devices">
          Devices
        </HeaderPill>
      </header>
      <PcSwitcher pc={pc} live={live} hero />
      <PcOfflineNotice pc={pc} live={live} />

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

      <VolumeSlider label="Windows volume" scopeHint="Affects every app on the PC." value={fresh && typeof volume?.value === "number" ? volume.value : null} muted={fresh ? (volume?.muted ?? null) : null} disabled={!canControl} onChange={(v) => void send("windows.set_volume", { value: v }, null, "slider")} onToggleMute={() => void send("windows.set_muted", { muted: !(volume?.muted ?? false) }, null, "button")} />

      <section aria-labelledby="quick-actions" className="pt-2">
        <SectionLabel>
          <span id="quick-actions">Quick actions</span>
        </SectionLabel>
        <div className="grid grid-cols-2 gap-3">
          <QuickAction icon="next" label="Next video" disabled={!canControl || !yt.ok || yt.tab.has_next === false} onClick={() => yt.ok && void send("youtube.next", {}, yt.target, "button")} />
          <QuickAction icon="back10" label="Back 10 s" disabled={!canControl || !yt.ok} onClick={() => yt.ok && void send("youtube.seek_relative", { seconds: -10 }, yt.target, "button")} />
          <QuickAction icon="lock" label="Lock Windows" disabled={!canControl || locked} onClick={() => void send("windows.lock", {}, null, "button")} />
          <QuickAction icon="sleep" label="Sleep…" disabled={!canControl} onClick={() => void send("power.sleep", {}, null, "button")} />
        </div>
        <div className="mt-3 space-y-1.5 px-1">
          {yt.ok && yt.tab.has_next === false ? <p className="text-xs text-text-muted">This player reports no next video, so Next is unavailable.</p> : null}
          {!yt.ok && media.ok ? <p className="text-xs text-text-muted">Next video and Back 10 s are YouTube controls; they stay off while the target is a Windows media player and are never turned into a generic media key.</p> : null}
          <p className="text-xs text-text-faint">Sleep, restart and shut down always ask you to confirm on this phone first.</p>
        </div>
      </section>

      <Link to="/app/apps" className="dome-card dome-press flex items-center gap-4 p-4 hover:bg-surface">
        <span aria-hidden="true" className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/12 text-accent">
          <Icon name="apps" size={21} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-semibold">Apps</span>
          <span className="block truncate text-xs text-text-muted">Open and switch the apps you approved on the PC</span>
        </span>
        <Icon name="chevron" size={18} className="text-text-faint" />
      </Link>

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

      <section aria-labelledby="last-command" className="pt-2">
        <SectionLabel action={last ? <Pill tone="neutral">{relativeTime(new Date(last.createdAt).toISOString())}</Pill> : undefined}>
          <span id="last-command">Last command</span>
        </SectionLabel>
        <div className="dome-card p-4">{last ? <CommandOutcome record={last} pcName={pcName} labels={labels} /> : <p className="text-sm text-text-muted">Nothing sent to {pcName ?? "this PC"} yet in this session.</p>}</div>
      </section>
    </div>
  );
}

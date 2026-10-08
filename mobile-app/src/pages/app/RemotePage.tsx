/**
 * Media remote. Two sources side by side — YouTube (through the PC's browser extension) and
 * Windows media sessions — each with its own picker, and two clearly labelled volume controls:
 * the YouTube player volume and the Windows system volume.
 */
import { useState } from "react";

import { useLabels, useSelectedPc, useSend } from "../../app/hooks.ts";
import { CommandOutcome } from "../../components/CommandOutcome.tsx";
import { PcSwitcher } from "../../components/PcSwitcher.tsx";
import { Button, Card, IconButton, Notice, Steps } from "../../components/ui.tsx";
import { VolumeSlider } from "../../components/VolumeSlider.tsx";
import { mmss } from "../../lib/format.ts";
import { errorMessage, recoverySteps } from "../../lib/labels.ts";
import { browserLabel, controllableTabs, resolveMediaTarget, resolveYoutubeTarget, tabKey } from "../../lib/targets.ts";
import { useLiveStore } from "../../store/live.ts";

type Source = "youtube" | "media";

export function RemotePage() {
  const { pc, pcId, pcName, live, fresh, canControl } = useSelectedPc();
  const { send, record, sendError, clearError } = useSend(pcId);
  const labels = useLabels(pcId);
  const selectedTab = useLiveStore((s) => (pcId ? s.selectedTab[pcId] : undefined));
  const selectedSession = useLiveStore((s) => (pcId ? s.selectedSession[pcId] : undefined));
  const selectTab = useLiveStore((s) => s.selectTab);
  const selectSession = useLiveStore((s) => s.selectSession);
  const [source, setSource] = useState<Source>("youtube");
  const [scrub, setScrub] = useState<number | null>(null);
  const state = live.state;
  const yt = resolveYoutubeTarget(state, selectedTab);
  const tabs = controllableTabs(state);
  const allTabs = state?.youtube_tabs ?? [];
  const media = resolveMediaTarget(state, selectedSession);
  const sessions = (state?.media_sessions ?? []).filter((s) => s.status !== "closed");
  const volume = state?.volume ?? null;
  const ytOk = canControl && yt.ok;
  const tab = yt.ok ? yt.tab : null;
  const position = scrub ?? tab?.position_seconds ?? null;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">Remote</h1>
      <PcSwitcher pc={pc} live={live} />

      <div role="tablist" aria-label="What to control" className="grid grid-cols-2 gap-1 rounded-control bg-bg-sunken p-1">
        {(["youtube", "media"] as Source[]).map((s) => (
          <button key={s} role="tab" type="button" aria-selected={source === s} onClick={() => setSource(s)} className={`rounded-[0.7rem] py-2.5 text-sm font-semibold ${source === s ? "bg-bg-elevated text-text shadow" : "text-text-muted"}`}>
            {s === "youtube" ? "YouTube" : "Windows media"}
          </button>
        ))}
      </div>

      {source === "youtube" ? (
        <>
          {allTabs.length > 1 || (allTabs.length === 1 && !yt.ok) ? (
            <Card>
              <h2 className="font-semibold mb-2">Which tab?</h2>
              <ul className="space-y-1">
                {allTabs.map((t) => {
                  const key = tabKey(t);
                  const controllable = t.script_attached && !!t.tab_token;
                  const chosen = yt.ok && tabKey(yt.tab) === key;
                  return (
                    <li key={`${t.browser_instance_id}:${t.tab_id}`}>
                      <button type="button" disabled={!controllable} aria-pressed={chosen} onClick={() => pcId && selectTab(pcId, key)} className={`w-full text-left rounded-control border px-3 py-2.5 ${chosen ? "border-accent bg-accent/10" : "border-border bg-surface"} disabled:opacity-50`}>
                        <span className="block text-xs text-text-faint">
                          {browserLabel(state, t.browser_instance_id)}
                          {t.paused === false ? " · playing" : ""}
                          {!controllable ? " · reload this tab on the PC" : ""}
                        </span>
                        <span className="block truncate font-medium">{t.title ?? "YouTube"}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
              {tabs.length > 1 && !yt.ok ? <p className="text-sm text-text-muted mt-2">More than one video is playing. Pick the tab to control.</p> : null}
            </Card>
          ) : null}

          <Card className="text-center">
            {tab ? (
              <>
                <p className="text-xs text-text-faint">
                  {browserLabel(state, tab.browser_instance_id)}
                  {tab.ad_showing ? " · ad playing" : ""}
                  {tab.is_live ? " · live" : ""}
                  {tab.context === "shorts" ? " · Shorts" : tab.context === "music" ? " · YouTube Music" : ""}
                </p>
                <p className="font-semibold text-lg leading-snug mt-1 line-clamp-2" title={tab.title ?? undefined}>
                  {tab.title ?? "YouTube video"}
                </p>
              </>
            ) : (
              <p className="text-sm text-text-muted">
                {!fresh ? "The PC’s state is not available right now." : !yt.ok && yt.reason === "no_extension" ? "The DoMe browser extension is not connected on the PC." : !yt.ok && yt.reason === "no_tabs" ? "No YouTube tab is open on the PC." : !yt.ok && yt.reason === "none_attached" ? "Reload the YouTube tab on the PC so DoMe can control it." : "Choose a tab above."}
              </p>
            )}
            <div className="mt-4 flex items-center justify-center gap-3">
              <IconButton label="Previous video" size={56} disabled={!ytOk || tab?.has_previous === false} onClick={() => yt.ok && void send("youtube.previous", {}, yt.target, "button")}>
                <span aria-hidden="true">⏮</span>
              </IconButton>
              <IconButton label="Back 10 seconds" size={56} disabled={!ytOk} onClick={() => yt.ok && void send("youtube.seek_relative", { seconds: -10 }, yt.target, "button")}>
                <span aria-hidden="true" className="text-sm font-bold">
                  −10
                </span>
              </IconButton>
              <button type="button" aria-label={tab?.paused ? "Play" : "Pause"} disabled={!ytOk || tab?.paused === undefined} onClick={() => yt.ok && void send("youtube.set_paused", { paused: !yt.tab.paused }, yt.target, "button")} className="h-20 w-20 rounded-full bg-accent text-on-accent text-3xl font-bold flex items-center justify-center disabled:opacity-40 active:bg-accent-strong">
                <span aria-hidden="true">{tab?.paused === false ? "❚❚" : "▶"}</span>
              </button>
              <IconButton label="Forward 10 seconds" size={56} disabled={!ytOk} onClick={() => yt.ok && void send("youtube.seek_relative", { seconds: 10 }, yt.target, "button")}>
                <span aria-hidden="true" className="text-sm font-bold">
                  +10
                </span>
              </IconButton>
              <IconButton label="Next video" size={56} disabled={!ytOk || tab?.has_next === false} onClick={() => yt.ok && void send("youtube.next", {}, yt.target, "button")}>
                <span aria-hidden="true">⏭</span>
              </IconButton>
            </div>
            {tab && typeof tab.duration_seconds === "number" && tab.duration_seconds > 0 ? (
              <div className="mt-4">
                <label htmlFor="scrub" className="sr-only">
                  Position
                </label>
                <input id="scrub" type="range" className="slider" min={0} max={Math.floor(tab.duration_seconds)} step={1} value={Math.min(Math.floor(position ?? 0), Math.floor(tab.duration_seconds))} disabled={!ytOk || tab.is_live} aria-valuetext={`${mmss(position)} of ${mmss(tab.duration_seconds)}`} onChange={(e) => setScrub(Number(e.currentTarget.value))} onPointerUp={() => scrub !== null && yt.ok && (void send("youtube.seek_to", { position_seconds: scrub }, yt.target, "slider"), setScrub(null))} onKeyUp={() => scrub !== null && yt.ok && (void send("youtube.seek_to", { position_seconds: scrub }, yt.target, "slider"), setScrub(null))} />
                <div className="flex justify-between text-xs font-mono tabular-nums text-text-muted">
                  <span>{mmss(position)}</span>
                  <span>{mmss(tab.duration_seconds)}</span>
                </div>
              </div>
            ) : null}
            <div className="mt-4 grid grid-cols-2 gap-2">
              <Button disabled={!ytOk} aria-pressed={tab?.theater === true} onClick={() => yt.ok && void send("youtube.set_theater", { enabled: !(yt.tab.theater ?? false) }, yt.target, "button")}>
                Theater mode {tab?.theater ? "off" : "on"}
              </Button>
              <Button disabled={!ytOk} onClick={() => yt.ok && void send("youtube.request_fullscreen", {}, yt.target, "button")}>
                Fullscreen
              </Button>
            </div>
            <p className="text-xs text-text-faint mt-2 text-left">Browsers usually block fullscreen started from another device; DoMe reports honestly when that happens. Theater mode works without that restriction.</p>
          </Card>

          <VolumeSlider label="YouTube player volume" scopeHint="Only the video in this tab. Windows volume is separate, below." value={tab && typeof tab.volume === "number" ? tab.volume : null} muted={tab?.muted ?? null} disabled={!ytOk} onChange={(v) => yt.ok && void send("youtube.set_volume", { value: v }, yt.target, "slider")} onToggleMute={() => yt.ok && void send("youtube.set_muted", { muted: !(yt.tab.muted ?? false) }, yt.target, "button")} />
        </>
      ) : (
        <>
          {sessions.length > 1 ? (
            <Card>
              <h2 className="font-semibold mb-2">Which player?</h2>
              <ul className="space-y-1">
                {sessions.map((s) => {
                  const chosen = media.ok && media.session.session_id === s.session_id;
                  return (
                    <li key={s.session_id}>
                      <button type="button" aria-pressed={chosen} onClick={() => pcId && selectSession(pcId, s.session_id)} className={`w-full text-left rounded-control border px-3 py-2.5 ${chosen ? "border-accent bg-accent/10" : "border-border bg-surface"}`}>
                        <span className="block text-xs text-text-faint">
                          {s.app_label ?? "Media player"} · {s.status}
                        </span>
                        <span className="block truncate font-medium">{s.title ?? "Untitled"}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </Card>
          ) : null}
          <Card className="text-center">
            {media.ok ? (
              <>
                <p className="text-xs text-text-faint">{media.session.app_label ?? "Windows media"}</p>
                <p className="font-semibold text-lg leading-snug mt-1 line-clamp-2">{media.session.title ?? "Playing"}</p>
                {media.session.artist ? <p className="text-sm text-text-muted truncate">{media.session.artist}</p> : null}
              </>
            ) : (
              <p className="text-sm text-text-muted">{!fresh ? "The PC’s state is not available right now." : media.reason === "ambiguous" ? "Choose a player above." : "No Windows media player is active. Apps that play through Windows (Spotify, Groove, browsers) appear here."}</p>
            )}
            <div className="mt-4 flex items-center justify-center gap-3">
              <IconButton label="Previous track" size={56} disabled={!canControl || !media.ok || !media.session.controls.includes("previous")} onClick={() => media.ok && void send("media.previous", {}, media.target, "button")}>
                <span aria-hidden="true">⏮</span>
              </IconButton>
              <button type="button" aria-label={media.ok && media.session.status === "playing" ? "Pause" : "Play"} disabled={!canControl || !media.ok || !(media.session.controls.includes("pause") || media.session.controls.includes("play"))} onClick={() => media.ok && void send("media.set_paused", { paused: media.session.status === "playing" }, media.target, "button")} className="h-20 w-20 rounded-full bg-accent text-on-accent text-3xl font-bold flex items-center justify-center disabled:opacity-40">
                <span aria-hidden="true">{media.ok && media.session.status === "playing" ? "❚❚" : "▶"}</span>
              </button>
              <IconButton label="Next track" size={56} disabled={!canControl || !media.ok || !media.session.controls.includes("next")} onClick={() => media.ok && void send("media.next", {}, media.target, "button")}>
                <span aria-hidden="true">⏭</span>
              </IconButton>
            </div>
            {media.ok ? <p className="text-xs text-text-faint mt-3">Only the controls this player advertises are enabled.</p> : null}
          </Card>
        </>
      )}

      <VolumeSlider label="Windows volume" scopeHint="The PC’s system volume — affects every app on the PC." value={fresh && typeof volume?.value === "number" ? volume.value : null} muted={fresh ? (volume?.muted ?? null) : null} disabled={!canControl} onChange={(v) => void send("windows.set_volume", { value: v }, null, "slider")} onToggleMute={() => void send("windows.set_muted", { muted: !(volume?.muted ?? false) }, null, "button")} />

      {sendError ? (
        <Notice tone="danger" title="Not sent">
          <p>{errorMessage(sendError)}</p>
          <Steps steps={recoverySteps(sendError.code)} />
          <Button size="md" variant="ghost" onClick={clearError}>
            Dismiss
          </Button>
        </Notice>
      ) : null}
      {record ? (
        <Card>
          <CommandOutcome record={record} pcName={pcName} labels={labels} />
        </Card>
      ) : null}
    </div>
  );
}

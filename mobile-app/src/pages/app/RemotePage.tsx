/**
 * Media remote. Two sources side by side — YouTube (through the PC's browser extension) and
 * Windows media sessions — each with its own picker, and two clearly labelled volume controls:
 * the YouTube player volume and the Windows system volume. YouTube also has a search box whose
 * results open on the PC. A tab without a player (search results) is never controlled implicitly;
 * when picked by hand, its player buttons stay off.
 */
import { useState, type CSSProperties } from "react";

import { useLabels, useSelectedPc, useSend } from "../../app/hooks.ts";
import { CommandOutcome } from "../../components/CommandOutcome.tsx";
import { FailureLinks } from "../../components/FailureLinks.tsx";
import { Icon } from "../../components/icons.tsx";
import { Artwork, PlayButton } from "../../components/NowPlaying.tsx";
import { PcSwitcher } from "../../components/PcSwitcher.tsx";
import { Button, Card, IconButton, Notice, PageHeader, Segmented, Steps } from "../../components/ui.tsx";
import { VolumeSlider } from "../../components/VolumeSlider.tsx";
import { YoutubeSearch } from "../../components/YoutubeSearch.tsx";
import { mmss } from "../../lib/format.ts";
import { errorMessage, recoverySteps } from "../../lib/labels.ts";
import { browserLabel, controllableTabs, resolveMediaTarget, resolveYoutubeTarget, tabKey } from "../../lib/targets.ts";
import { useLiveStore } from "../../store/live.ts";

type Source = "youtube" | "media";

/** A picker row (which tab, which player). */
const PICK = "dome-press w-full text-left rounded-control border px-4 py-3";
const PICKED = "border-accent/60 bg-accent/10";
const UNPICKED = "border-border bg-surface hover:bg-surface-hover";

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
  const tab = yt.ok ? yt.tab : null;
  // Search results and other pages without a player: every player control would be refused there.
  const noPlayer = tab?.context === "other";
  const ytOk = canControl && yt.ok && !noPlayer;
  const position = scrub ?? tab?.position_seconds ?? null;
  const duration = tab && typeof tab.duration_seconds === "number" && tab.duration_seconds > 0 ? tab.duration_seconds : null;

  return (
    <div className="space-y-4">
      <PageHeader title="Remote" />
      <PcSwitcher pc={pc} live={live} />

      <Segmented<Source>
        label="What to control"
        value={source}
        onChange={setSource}
        options={[
          { value: "youtube", label: "YouTube", icon: "youtube" },
          { value: "media", label: "Windows media", icon: "music" },
        ]}
      />

      {source === "youtube" ? (
        <>
          {allTabs.length > 1 || (allTabs.length === 1 && !yt.ok) ? (
            <Card>
              <h2 className="mb-3 font-semibold">Which tab?</h2>
              <ul className="space-y-2">
                {allTabs.map((t) => {
                  const key = tabKey(t);
                  const controllable = t.script_attached && !!t.tab_token;
                  const chosen = yt.ok && tabKey(yt.tab) === key;
                  return (
                    <li key={`${t.browser_instance_id}:${t.tab_id}`}>
                      <button type="button" disabled={!controllable} aria-pressed={chosen} onClick={() => pcId && selectTab(pcId, key)} className={`${PICK} ${chosen ? PICKED : UNPICKED} disabled:opacity-50`}>
                        <span className="block text-xs text-text-faint">
                          {browserLabel(state, t.browser_instance_id)}
                          {t.paused === false ? " · playing" : ""}
                          {!controllable ? " · reload this tab on the PC" : ""}
                        </span>
                        <span className="block truncate font-medium">{t.title ?? (t.context === "other" ? "YouTube search results or another page" : "YouTube")}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
              {tabs.length > 1 && !yt.ok ? <p className="text-sm text-text-muted mt-3">More than one video is playing. Pick the tab to control.</p> : null}
            </Card>
          ) : null}

          <Card className="p-5">
            <div className="flex items-center gap-4">
              <Artwork kind={tab && !noPlayer ? "youtube" : "none"} size={72} />
              <div className="min-w-0 flex-1">
                {tab && noPlayer ? (
                  <>
                    <p className="text-xs text-text-faint">{browserLabel(state, tab.browser_instance_id)}</p>
                    <p className="mt-0.5 text-[17px] font-semibold leading-snug">YouTube search results: pick a video below</p>
                  </>
                ) : tab ? (
                  <>
                    <p className="truncate text-xs text-text-faint">
                      {browserLabel(state, tab.browser_instance_id)}
                      {tab.ad_showing ? " · ad playing" : ""}
                      {tab.is_live ? " · live" : ""}
                      {tab.context === "shorts" ? " · Shorts" : tab.context === "music" ? " · YouTube Music" : ""}
                    </p>
                    <p className="mt-0.5 line-clamp-2 text-[19px] font-semibold leading-snug tracking-[-0.015em]" title={tab.title ?? undefined}>
                      {tab.title ?? "YouTube video"}
                    </p>
                  </>
                ) : (
                  <p className="text-sm text-text-muted">
                    {!fresh ? "The PC’s state is not available right now." : !yt.ok && yt.reason === "no_extension" ? "The DoMe browser extension is not connected on the PC." : !yt.ok && yt.reason === "no_tabs" ? "No YouTube tab is open on the PC." : !yt.ok && yt.reason === "none_attached" ? "Reload the YouTube tab on the PC so DoMe can control it." : !yt.ok && yt.reason === "no_player" ? "No YouTube video is playing: the YouTube tab shows a list. Pick a video below or on the PC." : "Choose a tab above."}
                  </p>
                )}
              </div>
            </div>
            {tab && noPlayer ? <p className="mt-3 text-sm text-text-muted">This tab shows a list, not a player, so the player buttons are off until a video plays in it.</p> : null}

            {duration !== null && tab ? (
              <div className="mt-4">
                <label htmlFor="scrub" className="sr-only">
                  Position
                </label>
                <input id="scrub" type="range" className="slider" style={{ "--fill": `${Math.min(100, ((position ?? 0) / duration) * 100)}%` } as CSSProperties} min={0} max={Math.floor(duration)} step={1} value={Math.min(Math.floor(position ?? 0), Math.floor(duration))} disabled={!ytOk || tab.is_live} aria-valuetext={`${mmss(position)} of ${mmss(duration)}`} onChange={(e) => setScrub(Number(e.currentTarget.value))} onPointerUp={() => scrub !== null && yt.ok && (void send("youtube.seek_to", { position_seconds: scrub }, yt.target, "slider"), setScrub(null))} onKeyUp={() => scrub !== null && yt.ok && (void send("youtube.seek_to", { position_seconds: scrub }, yt.target, "slider"), setScrub(null))} />
                <div className="-mt-1 flex justify-between text-xs font-medium tabular-nums text-text-faint">
                  <span>{mmss(position)}</span>
                  <span>{mmss(duration)}</span>
                </div>
              </div>
            ) : null}

            <div className={`flex items-center justify-between px-1 ${duration !== null ? "mt-3" : "mt-5"}`}>
              <IconButton plain label="Previous video" size={52} disabled={!ytOk || tab?.has_previous === false} onClick={() => yt.ok && void send("youtube.previous", {}, yt.target, "button")}>
                <Icon name="prev" size={26} />
              </IconButton>
              <IconButton plain label="Back 10 seconds" size={52} disabled={!ytOk} onClick={() => yt.ok && void send("youtube.seek_relative", { seconds: -10 }, yt.target, "button")}>
                <Icon name="back10" size={28} strokeWidth={1.6} />
              </IconButton>
              <PlayButton size={76} playing={tab?.paused === false} label={tab?.paused ? "Play" : "Pause"} disabled={!ytOk || tab?.paused === undefined} onClick={() => yt.ok && void send("youtube.set_paused", { paused: !yt.tab.paused }, yt.target, "button")} />
              <IconButton plain label="Forward 10 seconds" size={52} disabled={!ytOk} onClick={() => yt.ok && void send("youtube.seek_relative", { seconds: 10 }, yt.target, "button")}>
                <Icon name="fwd10" size={28} strokeWidth={1.6} />
              </IconButton>
              <IconButton plain label="Next video" size={52} disabled={!ytOk || tab?.has_next === false} onClick={() => yt.ok && void send("youtube.next", {}, yt.target, "button")}>
                <Icon name="next" size={26} />
              </IconButton>
            </div>

            <div className="mt-5 grid grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] gap-2">
              <Button size="sm" disabled={!ytOk} aria-pressed={tab?.theater === true} onClick={() => yt.ok && void send("youtube.set_theater", { enabled: !(yt.tab.theater ?? false) }, yt.target, "button")}>
                <Icon name="theater" size={18} />
                Theater mode {tab?.theater ? "off" : "on"}
              </Button>
              <Button size="sm" disabled={!ytOk} onClick={() => yt.ok && void send("youtube.request_fullscreen", {}, yt.target, "button")}>
                <Icon name="fullscreen" size={18} />
                Fullscreen
              </Button>
            </div>
            <div className="mt-3 space-y-1.5">
              {tab?.has_previous === false ? <p className="text-xs text-text-muted">No previous video in this player, so Previous is unavailable.</p> : null}
              {tab?.has_next === false ? <p className="text-xs text-text-muted">This player reports no next video, so Next is unavailable.</p> : null}
              {tab?.ad_showing ? <p className="text-xs text-text-muted">An ad is playing: seeking and skipping apply to the ad context and may be refused. DoMe does not skip ads.</p> : null}
              <p className="text-xs text-text-faint">Most browsers block remote fullscreen; Theater mode always works.</p>
            </div>
          </Card>

          <VolumeSlider label="YouTube player volume" scopeHint="Only the video in this tab." value={tab && typeof tab.volume === "number" ? tab.volume : null} muted={tab?.muted ?? null} disabled={!ytOk} onChange={(v) => yt.ok && void send("youtube.set_volume", { value: v }, yt.target, "slider")} onToggleMute={() => yt.ok && void send("youtube.set_muted", { muted: !(yt.tab.muted ?? false) }, yt.target, "button")} />

          <YoutubeSearch pcId={pcId} state={state} canControl={canControl} send={send} />
        </>
      ) : (
        <>
          {sessions.length > 1 ? (
            <Card>
              <h2 className="mb-3 font-semibold">Which player?</h2>
              <ul className="space-y-2">
                {sessions.map((s) => {
                  const chosen = media.ok && media.session.session_id === s.session_id;
                  return (
                    <li key={s.session_id}>
                      <button type="button" aria-pressed={chosen} onClick={() => pcId && selectSession(pcId, s.session_id)} className={`${PICK} ${chosen ? PICKED : UNPICKED}`}>
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
          <Card className="p-5">
            <div className="flex items-center gap-4">
              <Artwork kind={media.ok ? "media" : "none"} size={72} />
              <div className="min-w-0 flex-1">
                {media.ok ? (
                  <>
                    <p className="truncate text-xs text-text-faint">{media.session.app_label ?? "Windows media"}</p>
                    <p className="mt-0.5 line-clamp-2 text-[19px] font-semibold leading-snug tracking-[-0.015em]">{media.session.title ?? "Playing"}</p>
                    {media.session.artist ? <p className="truncate text-sm text-text-muted">{media.session.artist}</p> : null}
                  </>
                ) : (
                  <p className="text-sm text-text-muted">{!fresh ? "The PC’s state is not available right now." : media.reason === "ambiguous" ? "Choose a player above." : "No Windows media player is active. Apps that play through Windows (Spotify, Groove, browsers) appear here."}</p>
                )}
              </div>
            </div>
            <div className="mt-5 flex items-center justify-center gap-8">
              <IconButton plain label="Previous track" size={52} disabled={!canControl || !media.ok || !media.session.controls.includes("previous")} onClick={() => media.ok && void send("media.previous", {}, media.target, "button")}>
                <Icon name="prev" size={26} />
              </IconButton>
              <PlayButton size={76} playing={media.ok && media.session.status === "playing"} label={media.ok && media.session.status === "playing" ? "Pause" : "Play"} disabled={!canControl || !media.ok || !(media.session.controls.includes("pause") || media.session.controls.includes("play"))} onClick={() => media.ok && void send("media.set_paused", { paused: media.session.status === "playing" }, media.target, "button")} />
              <IconButton plain label="Next track" size={52} disabled={!canControl || !media.ok || !media.session.controls.includes("next")} onClick={() => media.ok && void send("media.next", {}, media.target, "button")}>
                <Icon name="next" size={26} />
              </IconButton>
            </div>
            {media.ok ? <p className="mt-3 text-center text-xs text-text-faint">Only the controls this player advertises are enabled.</p> : null}
          </Card>
        </>
      )}

      <VolumeSlider label="Windows volume" scopeHint="Affects every app on the PC." value={fresh && typeof volume?.value === "number" ? volume.value : null} muted={fresh ? (volume?.muted ?? null) : null} disabled={!canControl} onChange={(v) => void send("windows.set_volume", { value: v }, null, "slider")} onToggleMute={() => void send("windows.set_muted", { muted: !(volume?.muted ?? false) }, null, "button")} />

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
      {record ? (
        <Card className="p-4">
          <CommandOutcome record={record} pcName={pcName} labels={labels} />
        </Card>
      ) : null}
    </div>
  );
}

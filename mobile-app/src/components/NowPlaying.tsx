/**
 * Compact Now Playing panel (spec §9 "Media target clarity"): PC name, app/browser, title where
 * available, and state. The target shown here is exactly the target the controls will address —
 * a YouTube control never falls back to a generic media key, and an ambiguous or vanished target
 * asks for a deliberate choice instead of guessing.
 */
import { Link } from "react-router";

import type { JsonValue } from "@dome/protocol";

import { browserLabel, resolveMediaTarget, resolveYoutubeTarget, type MediaResolution, type YoutubeResolution } from "../lib/targets.ts";
import type { PcState } from "../store/live.ts";
import { Button, Pill, type Tone } from "./ui.tsx";

export interface NowPlayingProps {
  pcName: string | null;
  state: PcState | null;
  fresh: boolean;
  canControl: boolean;
  selectedTab: string | undefined;
  selectedSession: string | undefined;
  onSend: (action: string, params: Record<string, JsonValue>, target: Record<string, JsonValue>) => void;
}

export interface NowPlayingView {
  kind: "youtube" | "media" | "none" | "ambiguous" | "stale";
  source: string;
  title: string;
  stateLabel: string;
  tone: Tone;
  explanation: string | null;
}

export function describeNowPlaying(pcName: string | null, state: PcState | null, fresh: boolean, yt: YoutubeResolution, media: MediaResolution): NowPlayingView {
  const pc = pcName ?? "the PC";
  if (!fresh) return { kind: "stale", source: pc, title: "Media state is not available", stateLabel: "Unknown", tone: "neutral", explanation: "Shown again once the PC reports its current state." };
  if (yt.ok) {
    const t = yt.tab;
    const variant = t.ad_showing ? " · ad playing" : t.is_live ? " · live" : t.context === "shorts" ? " · Shorts" : t.context === "music" ? " · YouTube Music" : "";
    return { kind: "youtube", source: `${pc} · YouTube in ${browserLabel(state, t.browser_instance_id)}${variant}`, title: t.title ?? "YouTube video", stateLabel: t.paused === undefined ? "State unknown" : t.paused ? "Paused" : "Playing", tone: t.paused === false ? "success" : "neutral", explanation: yt.explicit ? null : state && (state.youtube_tabs?.length ?? 0) > 1 ? "Chosen automatically: it is the only tab playing. Pick another in the remote if that is wrong." : null };
  }
  if (yt.reason === "ambiguous") return { kind: "ambiguous", source: pc, title: "Several YouTube tabs are playing", stateLabel: "Choose one", tone: "warning", explanation: "DoMe does not guess which tab you mean and never sends a YouTube control to another player instead." };
  if (media.ok) {
    const s = media.session;
    return { kind: "media", source: `${pc} · ${s.app_label ?? "Windows media player"}`, title: s.title ?? "Playing", stateLabel: s.status === "playing" ? "Playing" : s.status === "paused" ? "Paused" : s.status, tone: s.status === "playing" ? "success" : "neutral", explanation: yt.reason === "no_extension" && (state?.youtube_tabs?.length ?? 0) > 0 ? "A YouTube tab is open too, but the browser extension is not connected, so YouTube controls are unavailable; this player is controlled through Windows media controls." : yt.reason === "none_attached" ? "A YouTube tab is open but needs a reload before DoMe can control it; this player is controlled through Windows media controls." : null };
  }
  if (media.reason === "ambiguous") return { kind: "ambiguous", source: pc, title: "Several media players are active", stateLabel: "Choose one", tone: "warning", explanation: "Pick the player in the remote. DoMe does not switch to another player on its own." };
  if (yt.reason === "none_attached") return { kind: "none", source: pc, title: "YouTube tab needs a reload", stateLabel: "Not controllable", tone: "warning", explanation: "The tab is open but the DoMe extension is not attached to it. Reload the tab on the PC." };
  if (state?.extension_connected === false) return { kind: "none", source: pc, title: "Nothing is playing", stateLabel: "Idle", tone: "neutral", explanation: "To control YouTube, install the DoMe browser extension on the PC. Windows media players appear here without it." };
  return { kind: "none", source: pc, title: "Nothing is playing", stateLabel: "Idle", tone: "neutral", explanation: null };
}

export function NowPlaying({ pcName, state, fresh, canControl, selectedTab, selectedSession, onSend }: NowPlayingProps) {
  const yt = resolveYoutubeTarget(state, selectedTab);
  const media = resolveMediaTarget(state, selectedSession);
  const view = describeNowPlaying(pcName, state, fresh, yt, media);
  return (
    <section className="rounded-card bg-bg-elevated border border-border p-4" aria-label="Now playing" data-testid="now-playing">
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-semibold">Now playing</h2>
        <Link to="/app/remote" className="tap inline-flex items-center text-sm text-accent font-semibold px-1">
          Open remote
        </Link>
      </div>
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-text-faint truncate">{view.source}</p>
          <p className="font-medium truncate" title={view.title}>
            {view.title}
          </p>
          <Pill tone={view.tone} className="mt-1">
            {view.stateLabel}
          </Pill>
        </div>
        {view.kind === "youtube" && yt.ok ? (
          <Button size="lg" variant="primary" disabled={!canControl || yt.tab.paused === undefined} aria-label={yt.tab.paused ? "Play" : "Pause"} onClick={() => onSend("youtube.set_paused", { paused: !yt.tab.paused }, yt.target)}>
            {yt.tab.paused ? "Play" : "Pause"}
          </Button>
        ) : view.kind === "media" && media.ok ? (
          <Button size="lg" variant="primary" disabled={!canControl || !(media.session.controls.includes("pause") || media.session.controls.includes("play"))} aria-label={media.session.status === "playing" ? "Pause player" : "Play player"} onClick={() => onSend("media.set_paused", { paused: media.session.status === "playing" }, media.target)}>
            {media.session.status === "playing" ? "Pause" : "Play"}
          </Button>
        ) : view.kind === "ambiguous" ? (
          <Link to="/app/remote" className="tap inline-flex items-center rounded-control bg-accent text-on-accent font-semibold px-4 text-sm">
            Choose
          </Link>
        ) : null}
      </div>
      {view.explanation ? <p className="text-xs text-text-muted mt-2">{view.explanation}</p> : null}
    </section>
  );
}

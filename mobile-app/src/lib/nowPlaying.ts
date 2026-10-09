/**
 * Now Playing copy (spec §9 "Media target clarity"): what the panel shows is exactly the target the
 * controls will address. A YouTube control never falls back to a generic media key; ambiguity asks.
 */
import type { Tone } from "../components/ui.tsx";
import type { PcState } from "../store/live.ts";
import { browserLabel, type MediaResolution, type YoutubeResolution } from "./targets.ts";

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


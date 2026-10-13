/**
 * Compact Now Playing panel (spec §9 "Media target clarity"): PC name, app/browser, title where
 * available, and state. The target shown here is exactly the target the controls will address —
 * a YouTube control never falls back to a generic media key, and an ambiguous or vanished target
 * asks for a deliberate choice instead of guessing.
 */
import { Link } from "react-router";

import type { JsonValue } from "@dome/protocol";

import { describeNowPlaying, type NowPlayingView } from "../lib/nowPlaying.ts";
import { resolveMediaTarget, resolveYoutubeTarget } from "../lib/targets.ts";
import type { PcState } from "../store/live.ts";
import { Icon } from "./icons.tsx";
import { Pill } from "./ui.tsx";

export interface NowPlayingProps {
  pcName: string | null;
  state: PcState | null;
  fresh: boolean;
  canControl: boolean;
  selectedTab: string | undefined;
  selectedSession: string | undefined;
  onSend: (action: string, params: Record<string, JsonValue>, target: Record<string, JsonValue>) => void;
}

/** The square picture beside the title: YouTube or a music note on DoMe Blue, a quiet tile when idle. */
export function Artwork({ kind, size = 64 }: { kind: NowPlayingView["kind"]; size?: number }) {
  const live = kind === "youtube" || kind === "media";
  return (
    <span aria-hidden="true" style={{ width: size, height: size }} className={`flex shrink-0 items-center justify-center rounded-[18px] ${live ? "bg-[linear-gradient(140deg,#3a72ff_0%,#0142fb_45%,#001441_100%)] text-white shadow-[0_14px_30px_-14px_rgb(1_66_251/0.8)]" : "bg-surface text-text-faint"}`}>
      <Icon name={kind === "media" ? "music" : "youtube"} size={Math.round(size * 0.42)} />
    </span>
  );
}

/** The large round play/pause button. */
export function PlayButton({ playing, label, size = 56, disabled, onClick }: { playing: boolean; label: string; size?: number; disabled: boolean; onClick: () => void }) {
  return (
    <button type="button" aria-label={label} disabled={disabled} onClick={onClick} style={{ width: size, height: size }} className="dome-primary dome-press flex shrink-0 items-center justify-center rounded-full disabled:shadow-none">
      <Icon name={playing ? "pause" : "play"} size={Math.round(size * 0.4)} />
    </button>
  );
}

export function NowPlaying({ pcName, state, fresh, canControl, selectedTab, selectedSession, onSend }: NowPlayingProps) {
  const yt = resolveYoutubeTarget(state, selectedTab);
  const media = resolveMediaTarget(state, selectedSession);
  const view = describeNowPlaying(pcName, state, fresh, yt, media);
  return (
    <section className="dome-card p-5" aria-label="Now playing" data-testid="now-playing">
      <div className="-mt-1.5 mb-3 flex items-center justify-between gap-3">
        <h2 className="sr-only">Now playing</h2>
        <p className="min-w-0 truncate text-xs font-medium text-text-faint">{view.source}</p>
        <Link to="/app/remote" aria-label="Open remote" className="tap -my-1 -mr-1 inline-flex shrink-0 items-center gap-0.5 text-sm font-semibold text-accent">
          Remote
          <Icon name="chevron" size={16} />
        </Link>
      </div>
      <div className="flex items-center gap-4">
        <Artwork kind={view.kind} />
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-[17px] font-semibold leading-snug tracking-[-0.01em]" title={view.title}>
            {view.title}
          </p>
          <Pill tone={view.tone} className="mt-2">
            {view.stateLabel}
          </Pill>
        </div>
        {view.kind === "youtube" && yt.ok ? (
          <PlayButton playing={yt.tab.paused === false} label={yt.tab.paused ? "Play" : "Pause"} disabled={!canControl || yt.tab.paused === undefined} onClick={() => onSend("youtube.set_paused", { paused: !yt.tab.paused }, yt.target)} />
        ) : view.kind === "media" && media.ok ? (
          <PlayButton playing={media.session.status === "playing"} label={media.session.status === "playing" ? "Pause player" : "Play player"} disabled={!canControl || !(media.session.controls.includes("pause") || media.session.controls.includes("play"))} onClick={() => onSend("media.set_paused", { paused: media.session.status === "playing" }, media.target)} />
        ) : view.kind === "ambiguous" ? (
          <Link to="/app/remote" className="tap dome-primary dome-press inline-flex items-center rounded-full px-4 text-sm font-semibold">
            Choose
          </Link>
        ) : null}
      </div>
      {view.explanation ? <p className="mt-3 text-xs text-text-muted">{view.explanation}</p> : null}
    </section>
  );
}

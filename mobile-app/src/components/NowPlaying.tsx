/**
 * Compact Now Playing panel (spec §9 "Media target clarity"): PC name, app/browser, title where
 * available, and state. The target shown here is exactly the target the controls will address —
 * a YouTube control never falls back to a generic media key, and an ambiguous or vanished target
 * asks for a deliberate choice instead of guessing.
 */
import { Link } from "react-router";

import type { JsonValue } from "@dome/protocol";

import { describeNowPlaying } from "../lib/nowPlaying.ts";
import { resolveMediaTarget, resolveYoutubeTarget } from "../lib/targets.ts";
import type { PcState } from "../store/live.ts";
import { Button, Pill } from "./ui.tsx";

export interface NowPlayingProps {
  pcName: string | null;
  state: PcState | null;
  fresh: boolean;
  canControl: boolean;
  selectedTab: string | undefined;
  selectedSession: string | undefined;
  onSend: (action: string, params: Record<string, JsonValue>, target: Record<string, JsonValue>) => void;
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

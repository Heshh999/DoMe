import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { NowPlaying } from "../../src/components/NowPlaying.tsx";
import { describeNowPlaying } from "../../src/lib/nowPlaying.ts";
import { resolveMediaTarget, resolveYoutubeTarget } from "../../src/lib/targets.ts";
import type { PcState } from "../../src/store/live.ts";

const tab = { browser_instance_id: "b", tab_id: 1, tab_token: "dGFiLXRva2VuLTAwMDAwMD", script_attached: true, context: "watch" as const, ad_showing: false, is_live: false, in_playlist: false, paused: false, title: "Lo-fi beats" };
const chrome = { browser_instance_id: "b", browser: "chrome" as const, profile_label: "Work" };
const spotify = { session_id: "s1", status: "playing" as const, controls: ["pause", "next"] as Array<"play" | "pause" | "next" | "previous">, app_label: "Spotify", title: "Track" };

function view(state: PcState | null, fresh = true, selectedTab?: string, selectedSession?: string) {
  return describeNowPlaying("Office PC", state, fresh, resolveYoutubeTarget(state, selectedTab), resolveMediaTarget(state, selectedSession));
}

describe("Now Playing (spec §9 media target clarity)", () => {
  it("names the PC, the browser/profile, the title and the state for a single YouTube tab", () => {
    const v = view({ remote_enabled: true, session_locked: false, extension_connected: true, browser_instances: [chrome], youtube_tabs: [tab] });
    expect(v).toMatchObject({ kind: "youtube", source: "Office PC · YouTube in Chrome · Work", title: "Lo-fi beats", stateLabel: "Playing" });
  });
  it("two playing tabs: asks for a deliberate choice and never substitutes the Windows media session", () => {
    const state: PcState = { remote_enabled: true, session_locked: false, extension_connected: true, browser_instances: [chrome], youtube_tabs: [tab, { ...tab, tab_id: 2, tab_token: "dGFiLXRva2VuLTAwMDAwMX", title: "Other" }], media_sessions: [spotify] };
    const v = view(state);
    expect(v.kind).toBe("ambiguous");
    expect(v.stateLabel).toBe("Choose one");
    expect(v.explanation).toMatch(/never sends a YouTube control to another player/);
    // an explicit choice is honoured and labelled as such
    expect(view(state, true, "b:2:dGFiLXRva2VuLTAwMDAwMX")).toMatchObject({ kind: "youtube", title: "Other" });
  });
  it("a separate music app is shown as a Windows media target with its app name; a closed selected tab falls back only with an explanation", () => {
    const v = view({ remote_enabled: true, session_locked: false, extension_connected: true, media_sessions: [spotify] });
    expect(v).toMatchObject({ kind: "media", source: "Office PC · Spotify", title: "Track", stateLabel: "Playing" });
    // the chosen tab disappeared (key no longer exists) and no other tab is open: not silently another tab, nothing to control
    const gone = view({ remote_enabled: true, session_locked: false, extension_connected: true, youtube_tabs: [] }, true, "b:1:dGFiLXRva2VuLTAwMDAwMD");
    expect(gone.kind).toBe("none");
    // extension gone while a tab is open and Spotify plays: the media target is used, with the explanation
    const noExt = view({ remote_enabled: true, session_locked: false, extension_connected: false, youtube_tabs: [tab], media_sessions: [spotify] });
    expect(noExt.kind).toBe("media");
    expect(noExt.explanation).toMatch(/extension is not connected/);
  });
  it("stale state never shows controls as live", () => {
    expect(view({ remote_enabled: true, session_locked: false, extension_connected: true, youtube_tabs: [tab] }, false)).toMatchObject({ kind: "stale", stateLabel: "Unknown" });
  });
  it("renders and sends the YouTube pause with the tab target, not a media.* action", async () => {
    const onSend = vi.fn();
    const state: PcState = { remote_enabled: true, session_locked: false, extension_connected: true, browser_instances: [chrome], youtube_tabs: [tab], media_sessions: [spotify] };
    render(
      <MemoryRouter>
        <NowPlaying pcName="Office PC" state={state} fresh canControl selectedTab={undefined} selectedSession={undefined} onSend={onSend} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Office PC · YouTube in Chrome · Work")).toBeInTheDocument();
    expect(screen.getByText("Lo-fi beats")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(onSend).toHaveBeenCalledWith("youtube.set_paused", { paused: true }, expect.objectContaining({ tab_token: tab.tab_token, tab_id: 1 }));
    expect(onSend.mock.calls.some((c) => String(c[0]).startsWith("media."))).toBe(false);
  });
});

/**
 * Target selection (spec §9): explicit customer selection first; otherwise a single unambiguous
 * playing tab; otherwise ask. A selection is only honoured while the tab (same attachment token)
 * still exists. Targets always carry `tab_token`, so a command can never re-bind to another tab.
 */
import type { JsonValue } from "@dome/protocol";

import type { MediaSession, PcState, YoutubeTab } from "../store/live.ts";

export function tabKey(tab: YoutubeTab): string {
  return `${tab.browser_instance_id}:${tab.tab_id}:${tab.tab_token ?? ""}`;
}

export type YoutubeResolution = { ok: true; tab: YoutubeTab; target: Record<string, JsonValue>; explicit: boolean } | { ok: false; reason: "no_tabs" | "none_attached" | "ambiguous" | "no_extension" };

export function controllableTabs(state: PcState | null): YoutubeTab[] {
  return (state?.youtube_tabs ?? []).filter((t) => t.script_attached && typeof t.tab_token === "string");
}

export function youtubeTargetFor(tab: YoutubeTab): Record<string, JsonValue> {
  const target: Record<string, JsonValue> = { browser_instance_id: tab.browser_instance_id, tab_id: tab.tab_id, tab_token: tab.tab_token ?? "" };
  if (tab.video_id) target.expected_video_id = tab.video_id;
  return target;
}

export function resolveYoutubeTarget(state: PcState | null, selectedKey: string | null | undefined): YoutubeResolution {
  if (state && state.extension_connected === false) return { ok: false, reason: "no_extension" };
  const all = state?.youtube_tabs ?? [];
  if (all.length === 0) return { ok: false, reason: "no_tabs" };
  const tabs = controllableTabs(state);
  if (tabs.length === 0) return { ok: false, reason: "none_attached" };
  if (selectedKey) {
    const chosen = tabs.find((t) => tabKey(t) === selectedKey);
    if (chosen) return { ok: true, tab: chosen, target: youtubeTargetFor(chosen), explicit: true };
  }
  if (tabs.length === 1) return { ok: true, tab: tabs[0]!, target: youtubeTargetFor(tabs[0]!), explicit: false };
  const playing = tabs.filter((t) => t.paused === false);
  if (playing.length === 1) return { ok: true, tab: playing[0]!, target: youtubeTargetFor(playing[0]!), explicit: false };
  return { ok: false, reason: "ambiguous" };
}

export type MediaResolution = { ok: true; session: MediaSession; target: Record<string, JsonValue>; explicit: boolean } | { ok: false; reason: "no_sessions" | "ambiguous" };

export function resolveMediaTarget(state: PcState | null, selectedId: string | null | undefined): MediaResolution {
  const sessions = (state?.media_sessions ?? []).filter((s) => s.status !== "closed");
  if (sessions.length === 0) return { ok: false, reason: "no_sessions" };
  if (selectedId) {
    const chosen = sessions.find((s) => s.session_id === selectedId);
    if (chosen) return { ok: true, session: chosen, target: { session_id: chosen.session_id }, explicit: true };
  }
  if (sessions.length === 1) return { ok: true, session: sessions[0]!, target: { session_id: sessions[0]!.session_id }, explicit: false };
  const playing = sessions.filter((s) => s.status === "playing");
  if (playing.length === 1) return { ok: true, session: playing[0]!, target: { session_id: playing[0]!.session_id }, explicit: false };
  return { ok: false, reason: "ambiguous" };
}

export function browserLabel(state: PcState | null, browserInstanceId: string): string {
  const inst = state?.browser_instances?.find((b) => b.browser_instance_id === browserInstanceId);
  if (!inst) return "Browser";
  const name = inst.browser === "chrome" ? "Chrome" : inst.browser === "edge" ? "Edge" : "Browser";
  return inst.profile_label ? `${name} · ${inst.profile_label}` : name;
}

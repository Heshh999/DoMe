/**
 * Internal message shapes between the content script, the background service worker and the
 * popup. These never leave the extension; the bridge frames to the agent are built from them in
 * the background and validated against the frozen contract before they are sent.
 */

export type Context = "watch" | "shorts" | "music" | "other";

/** What the content script knows about the player. The background adds tab identity. */
export interface PlayerSnapshot {
  context: Context;
  ad_showing: boolean;
  is_live: boolean;
  in_playlist: boolean;
  title?: string;
  video_id?: string;
  paused?: boolean;
  muted?: boolean;
  volume?: number;
  position_seconds?: number;
  duration_seconds?: number;
  theater?: boolean;
  fullscreen?: boolean;
  has_next?: boolean;
  has_previous?: boolean;
}

export const TAB_OPS = [
  "get_state",
  "set_paused",
  "next",
  "previous",
  "seek_relative",
  "seek_to",
  "set_muted",
  "set_volume",
  "set_theater",
  "request_fullscreen",
] as const;
export type TabOp = (typeof TAB_OPS)[number];
export type BridgeOp = TabOp | "list_tabs";

export function isTabOp(op: string): op is TabOp {
  return (TAB_OPS as readonly string[]).includes(op);
}

/** Arguments of a tab op as they arrive in `bridge_request.args` (tab_id removed). */
export interface OpArgs {
  tab_token?: string;
  expected_video_id?: string;
  paused?: boolean;
  seconds?: number;
  position_seconds?: number;
  muted?: boolean;
  value?: number;
  enabled?: boolean;
}

export const CS_KIND = "dome_cs" as const;
export const BG_KIND = "dome_bg" as const;
export const POPUP_KIND = "dome_popup" as const;

export type ContentToBackground =
  | { kind: typeof CS_KIND; type: "attached"; token: string; state: PlayerSnapshot }
  | { kind: typeof CS_KIND; type: "player_state"; token: string; state: PlayerSnapshot }
  | { kind: typeof CS_KIND; type: "detached"; token: string };

export type BackgroundToContent =
  | { kind: typeof BG_KIND; type: "probe" }
  | { kind: typeof BG_KIND; type: "op"; op: TabOp; args: OpArgs; deadline_ms: number };

export type ContentReply =
  | { ok: true; token: string; state: PlayerSnapshot; previous_video_id?: string }
  | { ok: false; code: string; message: string; token?: string; state?: PlayerSnapshot };

export type ConnectionStateName =
  | "connecting"
  | "connected"
  | "disconnected"
  | "agent_not_running"
  | "host_missing"
  | "host_forbidden"
  | "incompatible"
  | "error";

export interface ConnectionState {
  state: ConnectionStateName;
  message?: string;
  agent_version?: string;
  protocol_version?: string;
  since: string;
  attempt: number;
}

export type PopupToBackground =
  | { kind: typeof POPUP_KIND; type: "status" }
  | { kind: typeof POPUP_KIND; type: "set_profile_label"; value: string }
  | { kind: typeof POPUP_KIND; type: "reconnect" };

export interface StatusReport {
  connection: ConnectionState;
  browser_instance_id: string;
  profile_label: string;
  extension_id: string;
  extension_version: string;
  browser: "chrome" | "edge" | "unknown";
  attached_tabs: number;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function isBackgroundToContent(v: unknown): v is BackgroundToContent {
  if (!isRecord(v) || v.kind !== BG_KIND) return false;
  if (v.type === "probe") return true;
  return v.type === "op" && typeof v.op === "string" && isTabOp(v.op) && isRecord(v.args) && typeof v.deadline_ms === "number";
}

export function isContentToBackground(v: unknown): v is ContentToBackground {
  if (!isRecord(v) || v.kind !== CS_KIND || typeof v.token !== "string") return false;
  if (v.type === "detached") return true;
  return (v.type === "attached" || v.type === "player_state") && isRecord(v.state);
}

export function isPopupToBackground(v: unknown): v is PopupToBackground {
  if (!isRecord(v) || v.kind !== POPUP_KIND) return false;
  if (v.type === "status" || v.type === "reconnect") return true;
  return v.type === "set_profile_label" && typeof v.value === "string";
}

/**
 * Connection health (spec §11A): layered, observable states only, one next action each. Nothing here
 * infers a cause it cannot see — an unreachable PC is "unknown cause" unless the relay said more.
 * Pure: the Health page and the onboarding walkthrough render what `assessHealth` returns.
 */
import type { rest } from "@dome/protocol";

import type { CommandRecord } from "./commands.ts";
import type { InputSessionState } from "./input.ts";
import type { RelayStatus } from "./relay.ts";
import { resolveMediaTarget, resolveYoutubeTarget } from "./targets.ts";
import type { LivePc } from "../store/live.ts";
import type { SessionStatus } from "../store/session.ts";

export type LayerStatus = "ok" | "problem" | "unknown" | "waiting";

export type NextActionKind = "reconnect_phone" | "sign_in" | "open_pc_app" | "enable_remote" | "grant_input" | "install_extension" | "open_youtube" | "choose_target" | "pair_phone" | "link_pc" | "unlock_pc" | "none";

export interface NextAction {
  kind: NextActionKind;
  label: string;
  /** In-app route when the action lives in the app. */
  to?: string;
}

export interface HealthLayer {
  id: "phone" | "account" | "pc" | "remote" | "input" | "extension" | "target";
  title: string;
  status: LayerStatus;
  summary: string;
  action: NextAction;
  /** Codes, versions and technical details for the details control. */
  details: string[];
}

export interface HealthInput {
  online: boolean;
  sessionStatus: SessionStatus;
  relayStatus: RelayStatus;
  controllerBound: boolean;
  pc: rest.Pc | undefined;
  live: LivePc | undefined;
  /** This phone's grant on the PC (undefined = unknown yet, null = none). */
  grant: rest.Grant | null | undefined;
  input: InputSessionState;
  selectedTab: string | undefined;
  selectedSession: string | undefined;
  commands: CommandRecord[];
  appVersion: string;
  protocolVersion: string;
  now: number;
}

const ACTIONS: Record<NextActionKind, NextAction> = {
  reconnect_phone: { kind: "reconnect_phone", label: "Reconnect this phone" },
  sign_in: { kind: "sign_in", label: "Sign in again" },
  open_pc_app: { kind: "open_pc_app", label: "Open DoMe on the PC" },
  enable_remote: { kind: "enable_remote", label: "Enable remote control on the PC" },
  grant_input: { kind: "grant_input", label: "Allow touchpad/keyboard on the PC", to: "/app/devices" },
  install_extension: { kind: "install_extension", label: "Install or update the extension on the PC" },
  open_youtube: { kind: "open_youtube", label: "Open a YouTube tab on the PC" },
  choose_target: { kind: "choose_target", label: "Choose a media target", to: "/app/remote" },
  pair_phone: { kind: "pair_phone", label: "Pair this phone with the PC", to: "/app/devices/pair" },
  link_pc: { kind: "link_pc", label: "Link a PC to your account", to: "/download" },
  unlock_pc: { kind: "unlock_pc", label: "Unlock Windows on the PC" },
  none: { kind: "none", label: "Nothing to do" },
};

export function assessHealth(i: HealthInput): HealthLayer[] {
  const layers: HealthLayer[] = [];
  const stateAge = i.live?.stateReceivedAt ? Math.round((i.now - i.live.stateReceivedAt) / 1000) : null;

  // 1. Phone connectivity
  if (!i.online) layers.push({ id: "phone", title: "This phone", status: "problem", summary: "Your phone reports no internet connection.", action: ACTIONS.reconnect_phone, details: ["navigator.onLine = false"] });
  else if (i.relayStatus === "open") layers.push({ id: "phone", title: "This phone", status: "ok", summary: "Connected to the DoMe service.", action: ACTIONS.none, details: [`relay: ${i.relayStatus}`, `app ${i.appVersion}`, `protocol ${i.protocolVersion}`] });
  else if (i.relayStatus === "connecting" || i.relayStatus === "reconnecting") layers.push({ id: "phone", title: "This phone", status: "waiting", summary: "Connecting to the DoMe service…", action: ACTIONS.reconnect_phone, details: [`relay: ${i.relayStatus}`] });
  else if (i.relayStatus === "revoked") layers.push({ id: "phone", title: "This phone", status: "problem", summary: "This phone’s pairing was revoked. Pair it again from the PC.", action: ACTIONS.pair_phone, details: ["relay: revoked (close 4003)"] });
  else if (i.relayStatus === "incompatible") layers.push({ id: "phone", title: "This phone", status: "problem", summary: "This app and the DoMe service speak different protocol versions. Reload the app; update the PC agent.", action: ACTIONS.reconnect_phone, details: ["PROTOCOL_INCOMPATIBLE", `protocol ${i.protocolVersion}`] });
  else layers.push({ id: "phone", title: "This phone", status: "problem", summary: "Not connected to the DoMe service.", action: ACTIONS.reconnect_phone, details: [`relay: ${i.relayStatus}`] });

  // 2. Account session
  if (i.sessionStatus === "signed_in") layers.push({ id: "account", title: "Your account", status: "ok", summary: "Signed in.", action: ACTIONS.none, details: ["session: signed_in"] });
  else if (i.sessionStatus === "loading" || i.sessionStatus === "unknown") layers.push({ id: "account", title: "Your account", status: "waiting", summary: "Checking your sign-in…", action: ACTIONS.none, details: [`session: ${i.sessionStatus}`] });
  else layers.push({ id: "account", title: "Your account", status: "problem", summary: "Your sign-in has ended or could not be verified. Paired PCs stay paired.", action: ACTIONS.sign_in, details: [`session: ${i.sessionStatus}`, "UNAUTHENTICATED"] });

  // 3. PC agent ↔ relay
  if (!i.pc) layers.push({ id: "pc", title: "Your PC", status: "problem", summary: "No PC is linked to your account.", action: ACTIONS.link_pc, details: [] });
  else if (i.live?.refused === "GRANT_MISSING") layers.push({ id: "pc", title: i.pc.name, status: "problem", summary: "This phone is not paired with this PC.", action: ACTIONS.pair_phone, details: ["GRANT_MISSING"] });
  else if (!i.controllerBound && i.relayStatus === "open") layers.push({ id: "pc", title: i.pc.name, status: "problem", summary: "This phone is not paired with any PC yet.", action: ACTIONS.pair_phone, details: ["hello_ack without controller_id"] });
  else if (!i.pc.enabled) layers.push({ id: "pc", title: i.pc.name, status: "problem", summary: "This PC is disabled on your plan.", action: { kind: "none", label: "Enable it under Devices", to: "/app/devices" }, details: ["PC_PLAN_DISABLED"] });
  else {
    const conn = i.live?.connection && i.live.connection !== "unknown" ? i.live.connection : i.pc.connection;
    const lastSeen = i.live?.lastSeen ?? i.pc.last_seen;
    const details = [`connection: ${conn}`, `last seen: ${lastSeen ?? "never"}`, ...(i.pc.agent_version ? [`agent ${i.pc.agent_version}`] : []), ...(stateAge !== null ? [`last state ${stateAge} s ago`] : [])];
    if (conn === "online" && i.live && !i.live.stale) layers.push({ id: "pc", title: i.pc.name, status: "ok", summary: "The DoMe agent on the PC is connected and reporting.", action: ACTIONS.none, details });
    else if (conn === "online") layers.push({ id: "pc", title: i.pc.name, status: "waiting", summary: "The PC is connected; waiting for its current state.", action: ACTIONS.none, details });
    else if (conn === "reconnecting") layers.push({ id: "pc", title: i.pc.name, status: "problem", summary: "The PC’s connection to DoMe dropped and has not come back yet. DoMe cannot tell whether the PC is asleep, off or without internet.", action: ACTIONS.open_pc_app, details });
    else layers.push({ id: "pc", title: i.pc.name, status: "problem", summary: "The PC is not connected to DoMe. The cause is unknown from here: it may be asleep, shut down, offline, or the DoMe agent may not be running.", action: ACTIONS.open_pc_app, details });
  }

  // 4. Local remote-control permission (+ lock state)
  const state = i.live?.state ?? null;
  if (!state) layers.push({ id: "remote", title: "Remote control on the PC", status: "unknown", summary: "Not known until the PC reports its state.", action: ACTIONS.none, details: i.pc?.remote_enabled_reported === undefined ? [] : [`last report: remote_enabled=${i.pc.remote_enabled_reported}`] });
  else if (!state.remote_enabled) layers.push({ id: "remote", title: "Remote control on the PC", status: "problem", summary: "Switched off on the PC. Remote commands can never switch it back on.", action: ACTIONS.enable_remote, details: ["PC_REMOTE_DISABLED"] });
  else if (state.session_locked) layers.push({ id: "remote", title: "Remote control on the PC", status: "problem", summary: `Windows is locked. ${state.media_while_locked ? "Media controls are allowed while locked; everything else waits." : "Only Lock and status work until it is unlocked."} Touchpad and keyboard never work on the lock screen.`, action: ACTIONS.unlock_pc, details: ["PC_SESSION_LOCKED"] });
  else layers.push({ id: "remote", title: "Remote control on the PC", status: "ok", summary: "Enabled, Windows unlocked.", action: ACTIONS.none, details: [`platform: ${state.platform ?? "unknown"}`] });

  // 5. Manual-input permission
  const caps = i.grant?.capabilities ?? [];
  const inputGranted = caps.includes("pointer") || caps.includes("keyboard");
  if (i.grant === undefined) layers.push({ id: "input", title: "Touchpad and keyboard permission", status: "unknown", summary: "Not loaded yet.", action: ACTIONS.none, details: [] });
  else if (!inputGranted) layers.push({ id: "input", title: "Touchpad and keyboard permission", status: "problem", summary: "Not granted for this phone. The PC owner allows it on the PC; existing pairings do not get it automatically.", action: ACTIONS.grant_input, details: ["INPUT_NOT_PERMITTED", `grant: ${caps.join(", ") || "none"}`] });
  else if (state?.input_restricted) layers.push({ id: "input", title: "Touchpad and keyboard permission", status: "problem", summary: "Granted, but Windows is showing a protected screen (lock, sign-in or an administrator prompt) where remote input is refused by design.", action: ACTIONS.unlock_pc, details: ["INPUT_RESTRICTED", `grant: ${caps.join(", ")}`] });
  else layers.push({ id: "input", title: "Touchpad and keyboard permission", status: "ok", summary: `${caps.includes("pointer") ? "Touchpad" : ""}${caps.includes("pointer") && caps.includes("keyboard") ? " and " : ""}${caps.includes("keyboard") ? "keyboard" : ""} allowed.${i.input.phase === "live" ? " A session is live." : ""}${state?.input_session && state.input_session.controller_id !== i.input.sessionId && i.input.phase !== "live" ? " Another phone currently holds the PC’s input session." : ""}`, action: ACTIONS.none, details: [`grant: ${caps.join(", ")}`, `session: ${i.input.phase}`, ...(i.input.problem ? [i.input.problem.code] : [])] });

  // 6. Browser extension
  if (!state) layers.push({ id: "extension", title: "Browser extension", status: "unknown", summary: "Not known until the PC reports its state.", action: ACTIONS.none, details: [] });
  else if (!state.extension_connected) layers.push({ id: "extension", title: "Browser extension", status: "problem", summary: "The DoMe extension is not connected on the PC. Needed only for YouTube controls; Windows media, volume, apps and the touchpad work without it.", action: ACTIONS.install_extension, details: ["EXTENSION_DISCONNECTED"] });
  else layers.push({ id: "extension", title: "Browser extension", status: "ok", summary: "Connected.", action: ACTIONS.none, details: [`browser instances: ${state.browser_instances?.length ?? 0}`] });

  // 7. Selected media target
  const yt = resolveYoutubeTarget(state, i.selectedTab);
  const media = resolveMediaTarget(state, i.selectedSession);
  if (!state) layers.push({ id: "target", title: "Media target", status: "unknown", summary: "Not known until the PC reports its state.", action: ACTIONS.none, details: [] });
  else if (yt.ok) layers.push({ id: "target", title: "Media target", status: "ok", summary: `YouTube tab${yt.explicit ? " you chose" : ""}: ${yt.tab.title ?? "untitled"}.`, action: ACTIONS.none, details: [`tabs: ${state.youtube_tabs?.length ?? 0}`, `explicit: ${yt.explicit}`] });
  else if (media.ok) layers.push({ id: "target", title: "Media target", status: "ok", summary: `Windows media session: ${media.session.app_label ?? "player"}${yt.reason === "ambiguous" ? " (several YouTube tabs are playing: choose one for YouTube controls)" : ""}.`, action: yt.reason === "ambiguous" ? ACTIONS.choose_target : ACTIONS.none, details: [`youtube: ${yt.reason}`, `sessions: ${state.media_sessions?.length ?? 0}`] });
  else if (yt.reason === "ambiguous" || media.reason === "ambiguous") layers.push({ id: "target", title: "Media target", status: "problem", summary: "More than one player is active; DoMe does not guess which one you mean.", action: ACTIONS.choose_target, details: ["TARGET_AMBIGUOUS"] });
  else if (yt.reason === "none_attached") layers.push({ id: "target", title: "Media target", status: "problem", summary: "A YouTube tab is open but the extension is not attached to it; reload that tab on the PC.", action: ACTIONS.open_youtube, details: ["TAB_NOT_CONTROLLABLE"] });
  else layers.push({ id: "target", title: "Media target", status: "problem", summary: state.extension_connected ? "No YouTube tab or media player is active on the PC." : "No media player is active; YouTube needs the extension.", action: state.extension_connected ? ACTIONS.open_youtube : ACTIONS.install_extension, details: [`youtube: ${yt.reason}`, `media: ${media.reason}`] });

  return layers;
}

/** Last command this phone saw a verified terminal result for on `pcId`. */
export function lastVerifiedResult(commands: CommandRecord[], pcId: string | null): CommandRecord | null {
  if (!pcId) return null;
  return commands.find((c) => c.pcId === pcId && c.terminal !== null) ?? null;
}

export interface OnboardingStep {
  id: "sign_in" | "link_pc" | "pair" | "online" | "target" | "first_action";
  title: string;
  done: boolean;
  /** Where to go to complete this step. */
  to: string;
  hint: string;
}

/** First-use walkthrough (spec §11A): the first undone step is where the customer returns to. */
export function onboardingSteps(i: Pick<HealthInput, "sessionStatus" | "pc" | "live" | "controllerBound" | "grant" | "selectedTab" | "selectedSession" | "commands">): OnboardingStep[] {
  const signedIn = i.sessionStatus === "signed_in";
  const linked = !!i.pc;
  const paired = linked && i.controllerBound && i.live?.refused !== "GRANT_MISSING" && i.grant !== null;
  const online = paired && i.live?.connection === "online" && !!i.live.state && i.live.state.remote_enabled;
  const state = i.live?.state ?? null;
  const target = online && (resolveYoutubeTarget(state, i.selectedTab).ok || resolveMediaTarget(state, i.selectedSession).ok);
  const firstAction = i.commands.some((c) => c.pcId === i.pc?.id && c.terminal?.state === "succeeded" && (c.action.startsWith("youtube.") || c.action.startsWith("media.") || c.action.startsWith("windows.")));
  return [
    { id: "sign_in", title: "Sign in", done: signedIn, to: "/app", hint: "Your account links phones and PCs." },
    { id: "link_pc", title: "Install DoMe on the PC and link it", done: linked, to: "/download", hint: "The Windows app signs in to the same account." },
    { id: "pair", title: "Pair this phone", done: paired, to: "/app/devices/pair", hint: "Scan the code the PC shows; approve on the PC." },
    { id: "online", title: "PC online with remote control enabled", done: online, to: "/app/health", hint: "The tray icon on the PC shows Connected and remote control on." },
    { id: "target", title: "Choose what to control", done: target, to: "/app/remote", hint: "A YouTube tab (with the extension) or a Windows media player." },
    { id: "first_action", title: "Do one action and see its result", done: firstAction, to: "/app/remote", hint: "Play/pause, next or volume — the result card shows what the PC reported." },
  ];
}

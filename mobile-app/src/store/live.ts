/**
 * Live state from the relay socket: connection status, per-PC status/state with staleness, the
 * command records and the pending confirmation. Everything here is derived from frames that were
 * validated in `relay.ts`/`commands.ts`; the UI only reads this store.
 */
import { create } from "zustand";

import type { relayFrames, results } from "@dome/protocol";

import type { CommandRecord } from "../lib/commands.ts";
import type { RelayStatus } from "../lib/relay.ts";
import { tabKey } from "../lib/targets.ts";

export type PcState = relayFrames.PcState;
export type YoutubeTab = relayFrames.YoutubeTab;
export type MediaSession = relayFrames.MediaSession;
export type YoutubeSearchItem = results.YoutubeSearchItem;
export interface AppEntry {
  app_id: string;
  display_name: string;
  running: boolean;
  windows: Array<{ window_id: string; title: string; minimized?: boolean; foreground?: boolean }>;
}

export interface LivePc {
  connection: relayFrames.PcConnectionState | "unknown";
  lastSeen: string | null;
  enabled: boolean | null;
  lastPowerRequest: { action: string; at: string } | null;
  state: PcState | null;
  /** Wall-clock ms when the current `state` frame was received by this phone. */
  stateReceivedAt: number | null;
  /** State frames received for this PC so far: orders a state report against other events (a search's answer). */
  stateSeq: number;
  stateAt: string | null;
  /** Set on every (re)connect/resume until a fresh `state` frame arrives. */
  stale: boolean;
  /** Error code when the relay refused our subscription (e.g. GRANT_MISSING: this phone is not paired). */
  refused: string | null;
}

/**
 * The last successful `youtube.search` on a PC: the results tab (with the NEW tab_token the results page attached
 * with) and its results (untrusted display text). In memory only and never the query itself (rules.search_queries);
 * dropped on PC switch and sign-out.
 */
export interface LastSearch {
  commandId: string;
  /** createdAt of the search command, so a slower, older search never replaces a newer one. */
  createdAt: number;
  /**
   * The PC's `stateSeq` when the answer arrived. The answer comes before the PC's next (debounced) state report, so
   * until a later report arrives, a new results tab missing from `pc_state` is not yet listed rather than closed.
   */
  stateSeq: number;
  tab: YoutubeTab;
  results: YoutubeSearchItem[];
}

export const EMPTY_LIVE_PC: LivePc = { connection: "unknown", lastSeen: null, enabled: null, lastPowerRequest: null, state: null, stateReceivedAt: null, stateSeq: 0, stateAt: null, stale: true, refused: null };

/** The agent re-sends state every 30 s; older than this and we no longer trust it. */
export const STATE_FRESH_MS = 75_000;

export interface LiveState {
  /** Wall-clock ms, advanced by `Runtime` every few seconds so time-based freshness is re-evaluated. */
  now: number;
  relayStatus: RelayStatus;
  relayError: relayFrames.Error | null;
  controllerId: string | null;
  pcs: Record<string, LivePc>;
  commands: CommandRecord[];
  /** Per-PC YouTube tab the customer picked explicitly (key = browser_instance_id:tab_id:tab_token). */
  selectedTab: Record<string, string>;
  selectedSession: Record<string, string>;
  /**
   * Approved apps per PC from the last successful `app.list` (untrusted display names), whichever page asked for it.
   * `sourceCreatedAt` is the createdAt of the command that produced the list, so an older answer never overwrites a newer one.
   */
  appsByPc: Record<string, { apps: AppEntry[]; at: number; sourceCreatedAt?: number }>;
  /** Per PC: the last YouTube search's results tab and results (memory only, never the query). */
  lastSearchByPc: Record<string, LastSearch>;
  setApps(pcId: string, apps: AppEntry[]): void;
  /** Forget the last YouTube search (one PC, or all of them on a PC switch). */
  clearLastSearch(pcId?: string): void;
  setRelay(status: RelayStatus, error?: relayFrames.Error | null): void;
  setControllerId(id: string | null): void;
  onPcStatus(frame: relayFrames.PcStatusEvent): void;
  onState(frame: relayFrames.AgentState): void;
  onSubscribeRefused(pcId: string, code: string): void;
  markAllStale(): void;
  tick(now?: number): void;
  upsertCommand(record: CommandRecord): void;
  setCommands(records: CommandRecord[]): void;
  selectTab(pcId: string, key: string | null): void;
  selectSession(pcId: string, sessionId: string | null): void;
  reset(): void;
}

export const useLiveStore = create<LiveState>()((set) => ({
  now: Date.now(),
  relayStatus: "idle",
  relayError: null,
  controllerId: null,
  pcs: {},
  commands: [],
  selectedTab: {},
  selectedSession: {},
  appsByPc: {},
  lastSearchByPc: {},
  setApps(pcId, apps) {
    set((s) => ({ appsByPc: { ...s.appsByPc, [pcId]: { apps, at: Date.now() } } }));
  },
  clearLastSearch(pcId) {
    set((s) => {
      if (pcId === undefined) return { lastSearchByPc: {} };
      const next = { ...s.lastSearchByPc };
      delete next[pcId];
      return { lastSearchByPc: next };
    });
  },
  setRelay(status, error) {
    set({ relayStatus: status, relayError: error ?? null });
  },
  setControllerId(id) {
    set({ controllerId: id });
  },
  onPcStatus(frame) {
    set((s) => {
      const prev = s.pcs[frame.pc_id] ?? EMPTY_LIVE_PC;
      const offline = frame.connection !== "online";
      return {
        pcs: {
          ...s.pcs,
          [frame.pc_id]: {
            ...prev,
            connection: frame.connection,
            lastSeen: frame.last_seen,
            enabled: frame.enabled ?? prev.enabled,
            lastPowerRequest: frame.last_power_request === undefined ? prev.lastPowerRequest : frame.last_power_request,
            // An offline PC has no current state; keep the last one for display but mark it stale.
            stale: offline ? true : prev.stale,
            refused: null,
          },
        },
      };
    });
  },
  onState(frame) {
    set((s) => {
      const prev = s.pcs[frame.pc_id] ?? EMPTY_LIVE_PC;
      return { pcs: { ...s.pcs, [frame.pc_id]: { ...prev, state: frame.state, stateAt: frame.at, stateReceivedAt: Date.now(), stateSeq: prev.stateSeq + 1, stale: false, refused: null } } };
    });
  },
  onSubscribeRefused(pcId, code) {
    set((s) => ({ pcs: { ...s.pcs, [pcId]: { ...(s.pcs[pcId] ?? EMPTY_LIVE_PC), refused: code, stale: true } } }));
  },
  markAllStale() {
    set((s) => {
      const pcs: Record<string, LivePc> = {};
      for (const [id, pc] of Object.entries(s.pcs)) pcs[id] = { ...pc, stale: true, connection: pc.connection === "online" ? "reconnecting" : pc.connection };
      return { pcs };
    });
  },
  tick(now = Date.now()) {
    set({ now });
  },
  upsertCommand(record) {
    set((s) => {
      const rest = s.commands.filter((c) => c.commandId !== record.commandId);
      const before = s.commands.find((c) => c.commandId === record.commandId);
      return { commands: [record, ...rest].sort((a, b) => b.createdAt - a.createdAt).slice(0, 200), ...fromResult(s, before, record) };
    });
  },
  setCommands(records) {
    set({ commands: [...records].sort((a, b) => b.createdAt - a.createdAt) });
  },
  selectTab(pcId, key) {
    set((s) => {
      const next = { ...s.selectedTab };
      if (key === null) delete next[pcId];
      else next[pcId] = key;
      return { selectedTab: next };
    });
  },
  selectSession(pcId, sessionId) {
    set((s) => {
      const next = { ...s.selectedSession };
      if (sessionId === null) delete next[pcId];
      else next[pcId] = sessionId;
      return { selectedSession: next };
    });
  },
  reset() {
    set({ now: Date.now(), relayStatus: "idle", relayError: null, controllerId: null, pcs: {}, commands: [], selectedTab: {}, selectedSession: {}, appsByPc: {}, lastSearchByPc: {} });
  },
}));

/**
 * State that follows from a validated success result, whichever page sent the command:
 * - `app.list` refreshes the PC's approved apps (the Apps and Type pages both read them);
 * - `youtube.search` records the results tab (with the attachment token its results page got) and the results. It
 *   does not select that tab: a list has no player, so Pause, volume and seek keep reaching the video they reached;
 * - `youtube.open_result` selects the tab the video opened in, so the remote now controls that video (also when the
 *   page reloaded and its attachment token changed).
 * Applied once per command, so a later re-upsert never overrides a selection the customer made since.
 */
function fromResult(s: LiveState, prevRecord: CommandRecord | undefined, record: CommandRecord): Partial<LiveState> {
  const result = record.terminal?.state === "succeeded" ? record.terminal.result : null;
  if (!result) return {};
  if (record.action === "app.list") {
    const prev = s.appsByPc[record.pcId];
    if (prev && (prev.sourceCreatedAt ?? 0) > record.createdAt) return {};
    return { appsByPc: { ...s.appsByPc, [record.pcId]: { apps: (result as { apps: AppEntry[] }).apps, at: Date.now(), sourceCreatedAt: record.createdAt } } };
  }
  if (record.action === "youtube.search") {
    const prev = s.lastSearchByPc[record.pcId];
    if (prev && (prev.commandId === record.commandId || prev.createdAt > record.createdAt)) return {};
    const found = result as unknown as results.YoutubeSearchResult;
    const stateSeq = s.pcs[record.pcId]?.stateSeq ?? 0;
    return { lastSearchByPc: { ...s.lastSearchByPc, [record.pcId]: { commandId: record.commandId, createdAt: record.createdAt, stateSeq, tab: found.tab, results: [...found.results] } } };
  }
  if (record.action === "youtube.open_result") {
    if (prevRecord?.terminal?.state === "succeeded") return {}; // already applied once
    const opened = (result as unknown as results.YoutubeStateResult).tab;
    return { selectedTab: { ...s.selectedTab, [record.pcId]: tabKey(opened) } };
  }
  return {};
}

/** True when the last `state` frame is older than STATE_FRESH_MS (the agent re-sends every 30 s). */
export function stateExpired(pc: LivePc | undefined, now = Date.now()): boolean {
  return !!pc && pc.state !== null && pc.stateReceivedAt !== null && now - pc.stateReceivedAt > STATE_FRESH_MS;
}

/**
 * The PC as the UI should see it at `now`: an online PC whose state has gone quiet for over 75 s is
 * shown exactly like one that just reconnected ("Online · refreshing", controls disabled).
 */
export function withTimeStaleness(pc: LivePc, now = Date.now()): LivePc {
  return !pc.stale && stateExpired(pc, now) ? { ...pc, stale: true } : pc;
}

/** True when the PC's state can be trusted for consequential controls. */
export function isFresh(pc: LivePc | undefined, now = Date.now()): boolean {
  if (!pc || pc.stale || pc.state === null || pc.connection !== "online") return false;
  if (pc.stateReceivedAt !== null && now - pc.stateReceivedAt > STATE_FRESH_MS) return false;
  return true;
}

/** The confirmation the modal must show: unanswered, not dismissed locally, command not finished. */
export function pendingConfirmation(commands: CommandRecord[]): CommandRecord | null {
  return commands.find((c) => c.confirmation && c.confirmation.decision === null && !c.terminal) ?? null;
}

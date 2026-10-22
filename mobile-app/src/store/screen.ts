/**
 * React-visible mirror of the ScreenSessionClient state (`lib/screen.ts`), the operator switch as the server states
 * it, and the two Screen view preferences (not secrets: localStorage). Nothing about a session is persisted; the
 * stream object lives in memory only and is never serialised.
 */
import { create } from "zustand";

import { EMPTY_SCREEN_STATE, type ScreenAllowance, type ScreenSessionState } from "../lib/screen.ts";

export type ScreenSwitch = "on" | "off" | "unknown";

export interface ScreenPrefs {
  /** The relative touchpad over the picture instead of tap-to-click (Phase 1's way to drag). */
  touchpadMode: boolean;
  /** The first-run explainer and the protected-content hint were shown once. */
  explainerSeen: boolean;
}

export const DEFAULT_SCREEN_PREFS: ScreenPrefs = { touchpadMode: false, explainerSeen: false };
const PREFS_KEY = "dome.screen.prefs";

function loadPrefs(): ScreenPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return DEFAULT_SCREEN_PREFS;
    const p = JSON.parse(raw) as Partial<ScreenPrefs>;
    return { touchpadMode: p.touchpadMode === true, explainerSeen: p.explainerSeen === true };
  } catch {
    return DEFAULT_SCREEN_PREFS;
  }
}

export interface ScreenStoreState {
  session: ScreenSessionState;
  /** GET /healthz `screen_view`: the Screen tile exists only while the server says "on". */
  switch: ScreenSwitch;
  switchChecked: boolean;
  /** The last allowance learned per PC, so the Remote tile can say "10 min left" before a session starts. */
  lastAllowanceByPc: Record<string, ScreenAllowance>;
  /** The last start problem per PC (e.g. PROTOCOL_INCOMPATIBLE → "Update DoMe on your PC" on the tile). */
  lastProblemByPc: Record<string, string>;
  prefs: ScreenPrefs;
  setSession(state: ScreenSessionState): void;
  setSwitch(value: ScreenSwitch): void;
  setPrefs(patch: Partial<ScreenPrefs>): void;
  reset(): void;
}

export const useScreenStore = create<ScreenStoreState>()((set) => ({
  session: EMPTY_SCREEN_STATE,
  switch: "unknown",
  switchChecked: false,
  lastAllowanceByPc: {},
  lastProblemByPc: {},
  prefs: loadPrefs(),
  setSession(session) {
    set((s) => {
      const patch: Partial<ScreenStoreState> = { session };
      if (session.pcId && session.allowance) patch.lastAllowanceByPc = { ...s.lastAllowanceByPc, [session.pcId]: session.allowance };
      if (session.pcId) {
        const next = { ...s.lastProblemByPc };
        if (session.phase === "failed" && session.problem) next[session.pcId] = session.problem.code;
        else if (session.phase === "live") delete next[session.pcId];
        patch.lastProblemByPc = next;
      }
      return patch;
    });
  },
  setSwitch(value) {
    set({ switch: value, switchChecked: true });
  },
  setPrefs(patch) {
    set((s) => {
      const prefs = { ...s.prefs, ...patch };
      try {
        localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
      } catch {
        /* storage unavailable: in memory only */
      }
      return { prefs };
    });
  },
  reset() {
    set({ session: EMPTY_SCREEN_STATE, lastAllowanceByPc: {}, lastProblemByPc: {} });
  },
}));

/**
 * React-visible mirror of the InputSessionClient state (`lib/input.ts`) plus the two Free touchpad
 * preferences. Preferences are not secrets, so they live in localStorage; nothing typed ever does.
 */
import { create } from "zustand";

import { EMPTY_INPUT_STATE, type InputSessionState } from "../lib/input.ts";

export interface TouchpadPrefs {
  /** 0.5 … 3 cursor pixels per touch pixel. */
  sensitivity: number;
  scrollDirection: "natural" | "standard";
}

export const DEFAULT_PREFS: TouchpadPrefs = { sensitivity: 1.4, scrollDirection: "natural" };
const PREFS_KEY = "dome.touchpad.prefs";

function loadPrefs(): TouchpadPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return DEFAULT_PREFS;
    const p = JSON.parse(raw) as Partial<TouchpadPrefs>;
    const sensitivity = typeof p.sensitivity === "number" && p.sensitivity >= 0.5 && p.sensitivity <= 3 ? p.sensitivity : DEFAULT_PREFS.sensitivity;
    const scrollDirection = p.scrollDirection === "standard" ? "standard" : "natural";
    return { sensitivity, scrollDirection };
  } catch {
    return DEFAULT_PREFS;
  }
}

export interface InputStoreState {
  session: InputSessionState;
  prefs: TouchpadPrefs;
  setSession(state: InputSessionState): void;
  setPrefs(patch: Partial<TouchpadPrefs>): void;
}

export const useInputStore = create<InputStoreState>()((set) => ({
  session: EMPTY_INPUT_STATE,
  prefs: loadPrefs(),
  setSession(session) {
    set({ session });
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
}));

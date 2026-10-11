/**
 * The Assistant's state (docs/design/assistant.md §4.10, §4.11). Phone preferences live in localStorage
 * (`dome.assistant.prefs`, validated on load like `dome.touchpad.prefs`): flags, a name, a form of address, a voice and a
 * rate, never anything said or typed. The conversation, the short `recent` memory for a later AI turn and the last
 * app are in memory only: cleared on sign-out, on switching PC, after 10 minutes without a turn, and on reload.
 */
import { create } from "zustand";

import type { rest } from "@dome/protocol";

import { BUTLER_NAME, DEFAULT_BUTLER_NAME, FORM_OF_ADDRESS } from "../lib/assistantContext.ts";
import type { PlanRun, StepPreview } from "../lib/commandPlan.ts";
import type { PlanStep } from "../lib/intents.ts";
import { clampRate } from "../lib/speech.ts";

export interface AssistantPrefs {
  /** The fourth tab is "Ask" (the Assistant) instead of "Type". */
  showAssistant: boolean;
  /** Requests DoMe cannot work out by itself go to the AI assistant (needs the account's consent too). */
  useAi: boolean;
  butlerName: string;
  /** "" = none (no sir, madam or name). */
  address: string;
  /** Ask before every action: every plan waits for Run. */
  confirmEveryAction: boolean;
  voiceURI: string | null;
  rate: number;
  /** Shown inverted as "Read replies aloud". */
  muted: boolean;
  /** The one-time "Talk to DoMe" notice was acknowledged. */
  voiceNoticeSeen: boolean;
}

export const DEFAULT_ASSISTANT_PREFS: AssistantPrefs = { showAssistant: false, useAi: false, butlerName: DEFAULT_BUTLER_NAME, address: "", confirmEveryAction: false, voiceURI: null, rate: 1, muted: false, voiceNoticeSeen: false };
export const PREFS_KEY = "dome.assistant.prefs";

/** Saved preferences, each field checked; anything unexpected falls back to its default. */
export function loadAssistantPrefs(storage: Pick<Storage, "getItem"> | null = safeLocalStorage()): AssistantPrefs {
  try {
    const raw = storage?.getItem(PREFS_KEY);
    if (!raw) return DEFAULT_ASSISTANT_PREFS;
    const p = JSON.parse(raw) as Partial<Record<keyof AssistantPrefs, unknown>>;
    const bool = (k: keyof AssistantPrefs) => (typeof p[k] === "boolean" ? (p[k] as boolean) : (DEFAULT_ASSISTANT_PREFS[k] as boolean));
    return {
      showAssistant: bool("showAssistant"),
      useAi: bool("useAi"),
      butlerName: typeof p.butlerName === "string" && BUTLER_NAME.test(p.butlerName) ? p.butlerName : DEFAULT_BUTLER_NAME,
      address: typeof p.address === "string" && (p.address === "" || FORM_OF_ADDRESS.test(p.address)) ? p.address : "",
      confirmEveryAction: bool("confirmEveryAction"),
      voiceURI: typeof p.voiceURI === "string" && p.voiceURI.length <= 256 ? p.voiceURI : null,
      rate: typeof p.rate === "number" ? clampRate(p.rate) : 1,
      muted: bool("muted"),
      voiceNoticeSeen: bool("voiceNoticeSeen"),
    };
  } catch {
    return DEFAULT_ASSISTANT_PREFS;
  }
}

function safeLocalStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/** A tap target in the conversation. `choice`: the AI's suggested answer (a new turn); `local`: handled on the phone only; `ask_ai`: the owner's own words, sent to the AI on purpose. */
export interface ChatChip {
  label: string;
  text: string;
  kind: "choice" | "local" | "ask_ai";
  /** For `ask_ai`: how the words first arrived. */
  source?: "speech" | "typed" | "dictated";
}

export type PlanCardStatus = "waiting_run" | "running" | "done" | "dismissed" | "blocked";

/** The card's Cancel shut-down / restart / sleep: `sending` until power.cancel has its result; `failed` keeps the button. */
export interface PowerCancelState {
  status: "sending" | "done" | "failed";
  commandId: string | null;
  /** Why it could not be cancelled (shown on the card). */
  error: string | null;
}

export type ChatItem =
  | { id: number; kind: "owner"; text: string }
  | {
      id: number;
      kind: "butler";
      /** Shown on screen (React text only, never HTML). */
      text: string;
      /** What was spoken (the voice copy may differ from the screen text). "" = nothing to say. */
      spoken: string;
      /** From an AI turn: whether it counted towards the allowance. null = handled on the phone. */
      ai: { charged: boolean } | null;
      chips: ChatChip[];
      touchpad: boolean;
      /** Speech did not start in time: the bubble offers "Tap to hear". */
      tapToHear: boolean;
      tone?: "warning";
    }
  | {
      id: number;
      kind: "plan";
      pcId: string;
      pcName: string | null;
      steps: PlanStep[];
      previews: StepPreview[];
      status: PlanCardStatus;
      run: PlanRun | null;
      /** "Other ways to say it" chips from the plan (local turns). */
      chips: ChatChip[];
      /** After a search: the results, as local "play the Nth video" chips. */
      results: ChatChip[];
      /** The plan came from an AI turn. */
      ai: { charged: boolean } | null;
      /** After a power step: the owner's tap on its Cancel button. */
      cancel?: PowerCancelState;
    };

type NewItem = ChatItem extends infer T ? (T extends ChatItem ? Omit<T, "id"> : never) : never;

export interface RecentLine {
  who: "owner" | "butler";
  text: string;
  at: number;
}

/** Why AI turns stop until the status changes (an error code, with the limit's reason and renewal when known). */
export interface AiBlock {
  code: string;
  reason?: string;
  resetsAt?: string;
}

/** `checking`: waiting for the PC's approved apps before a request naming an app can be judged. */
export type AssistantPhase = "idle" | "connecting" | "checking" | "thinking" | "working";

export const MAX_ITEMS = 30;
export const IDLE_CLEAR_MS = 10 * 60_000;

export interface AssistantState {
  prefs: AssistantPrefs;
  status: rest.AssistantStatusResponse | null;
  /** The status could not be read (offline, server without the assistant): AI is not used. */
  statusError: string | null;
  allowance: rest.AssistantAllowance | null;
  aiBlocked: AiBlock | null;
  items: ChatItem[];
  recent: RecentLine[];
  lastAppId: string | null;
  lastActivity: number | null;
  /** Counts conversations: a turn that started in an earlier one (before a PC switch, sign-out or idle clear) is dropped. */
  conversation: number;
  phase: AssistantPhase;
  /** A line is being spoken (the talk button then offers "Tap to interrupt"). */
  speaking: boolean;
  /** "Connecting to Office PC…", "Finishing your last request…": a passing line under the conversation. */
  notice: string | null;
  setPrefs(patch: Partial<AssistantPrefs>): void;
  setStatus(status: rest.AssistantStatusResponse | null, error?: string | null): void;
  setAllowance(allowance: rest.AssistantAllowance): void;
  blockAi(block: AiBlock | null): void;
  addItem(item: NewItem): number;
  updateItem(id: number, patch: Partial<NewItem>): void;
  pushRecent(lines: Array<Omit<RecentLine, "at">>, at?: number): void;
  setLastAppId(id: string | null): void;
  setPhase(phase: AssistantPhase): void;
  setSpeaking(speaking: boolean): void;
  setNotice(notice: string | null): void;
  touch(now?: number): void;
  clearConversation(): void;
  /** Sign-out: the conversation and everything the account's status said. Phone preferences stay. */
  resetAccount(): void;
}

let nextId = 1;

export const useAssistantStore = create<AssistantState>()((set) => ({
  prefs: loadAssistantPrefs(),
  status: null,
  statusError: null,
  allowance: null,
  aiBlocked: null,
  items: [],
  recent: [],
  lastAppId: null,
  lastActivity: null,
  conversation: 0,
  phase: "idle",
  speaking: false,
  notice: null,
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
  setStatus(status, error = null) {
    // a new status is the "status changed" that lets AI turns start again after a limit or a consent error
    set({ status, statusError: error, aiBlocked: null, ...(status ? { allowance: status.allowance } : {}) });
  },
  setAllowance(allowance) {
    set({ allowance });
  },
  blockAi(block) {
    set({ aiBlocked: block });
  },
  addItem(item) {
    const id = nextId++;
    set((s) => ({ items: [...s.items, { ...item, id } as ChatItem].slice(-MAX_ITEMS) }));
    return id;
  },
  updateItem(id, patch) {
    set((s) => ({ items: s.items.map((it) => (it.id === id ? ({ ...it, ...patch } as ChatItem) : it)) }));
  },
  pushRecent(lines, at = Date.now()) {
    set((s) => ({ recent: [...s.recent, ...lines.map((l) => ({ ...l, at }))].slice(-12) }));
  },
  setLastAppId(id) {
    set({ lastAppId: id });
  },
  setPhase(phase) {
    set({ phase });
  },
  setSpeaking(speaking) {
    set({ speaking });
  },
  setNotice(notice) {
    set({ notice });
  },
  touch(now = Date.now()) {
    set({ lastActivity: now });
  },
  clearConversation() {
    set((s) => ({ items: [], recent: [], lastAppId: null, lastActivity: null, notice: null, conversation: s.conversation + 1 }));
  },
  resetAccount() {
    set((s) => ({ items: [], recent: [], lastAppId: null, lastActivity: null, notice: null, conversation: s.conversation + 1, status: null, statusError: null, allowance: null, aiBlocked: null, phase: "idle" }));
  },
}));

/**
 * The one "busy" signal (DECISIONS 92): what keeps the app from reloading itself for a newer version right now. Most of
 * it is state the app already has, read where it lives and never copied:
 *   - screen: a Screen view session starting, live or paused (store/screen.ts), and the Screen page itself while it
 *     is open (its hold: the page keeps "Stopped · 3 min" and the resume after the app was hidden, and the notice says
 *     "when you leave Screen view");
 *   - input: a touchpad or keyboard session starting, live or suspended (store/input.ts), and the Touchpad page while
 *     it is open (its hold: the page starts a session by itself when it opens, so a reload would start one again);
 *   - confirmation: a PC confirmation waiting for an answer (store/live.ts `pendingConfirmation`: shut down, restart,
 *     sleep, closing an app);
 *   - assistant: a request in progress or a plan running (store/assistant.ts), and the microphone listening (the
 *     Assistant page's hold: that state lives in the page);
 *   - pairing: the pairing page before the PC's answer (its hold: the code lives only in the page);
 *   - checkout: the Billing page on its way to Stripe or confirming a payment after the return (its hold).
 * Pages report what only they know with `useBusyHold`; nothing else is stored here.
 */
import { useEffect, useId } from "react";
import { create } from "zustand";

import type { InputPhase } from "../lib/input.ts";
import type { ScreenPhase } from "../lib/screen.ts";
import { useAssistantStore } from "./assistant.ts";
import { useInputStore } from "./input.ts";
import { pendingConfirmation, useLiveStore } from "./live.ts";
import { useScreenStore } from "./screen.ts";

export type BusyKind = "screen" | "input" | "pairing" | "checkout" | "confirmation" | "assistant";

/** The order the notice names them in when several are busy at once (the one the person is most likely looking at first). */
export const BUSY_ORDER: readonly BusyKind[] = ["screen", "input", "pairing", "checkout", "confirmation", "assistant"];

const SCREEN_BUSY: ReadonlySet<ScreenPhase> = new Set<ScreenPhase>(["ice", "offering", "starting", "connecting", "live", "paused"]);
const INPUT_BUSY: ReadonlySet<InputPhase> = new Set<InputPhase>(["starting", "live", "suspended"]);

interface BusyStoreState {
  /** What pages reported, by an id of their own. */
  holds: Record<string, BusyKind>;
}

export const useBusyStore = create<BusyStoreState>()(() => ({ holds: {} }));

export function holdBusy(id: string, kind: BusyKind): void {
  useBusyStore.setState((s) => (s.holds[id] === kind ? s : { holds: { ...s.holds, [id]: kind } }));
}

export function releaseBusy(id: string): void {
  useBusyStore.setState((s) => {
    if (!(id in s.holds)) return s;
    const holds = { ...s.holds };
    delete holds[id];
    return { holds };
  });
}

/** A page reports `kind` while `active` (and while it is mounted). */
export function useBusyHold(kind: BusyKind, active = true): void {
  const id = useId();
  useEffect(() => {
    if (!active) return;
    holdBusy(id, kind);
    return () => releaseBusy(id);
  }, [id, kind, active]);
}

/** Everything busy now, in BUSY_ORDER. */
export function busyKinds(): BusyKind[] {
  const on = new Set<BusyKind>(Object.values(useBusyStore.getState().holds));
  if (SCREEN_BUSY.has(useScreenStore.getState().session.phase)) on.add("screen");
  if (INPUT_BUSY.has(useInputStore.getState().session.phase)) on.add("input");
  if (pendingConfirmation(useLiveStore.getState().commands)) on.add("confirmation");
  const assistant = useAssistantStore.getState();
  if (assistant.phase !== "idle" || assistant.items.some((it) => it.kind === "plan" && it.status === "running")) on.add("assistant");
  return BUSY_ORDER.filter((k) => on.has(k));
}

/** The first busy kind, or null at a safe moment. */
export function currentBusy(): BusyKind | null {
  return busyKinds()[0] ?? null;
}

/** Calls `listener` whenever anything the busy signal reads may have changed. Returns the unsubscribe. */
export function subscribeBusy(listener: () => void): () => void {
  const offs = [useBusyStore.subscribe(listener), useScreenStore.subscribe(listener), useInputStore.subscribe(listener), useLiveStore.subscribe(listener), useAssistantStore.subscribe(listener)];
  return () => {
    for (const off of offs) off();
  };
}

/** Tests only: forget every hold. */
export function resetBusyForTests(): void {
  useBusyStore.setState({ holds: {} });
}

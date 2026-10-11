/**
 * Running a plan on the selected PC, shared by the Type page and the Assistant (docs/design/assistant.md §4.5,
 * §4.14): the live view each step is resolved against, signing and sending through the one runtime (so every step is
 * this phone's own signed command, whoever built the plan), Stop and a dropped connection waking a waiting step, and
 * re-reading the PC's app list after an app was opened or closed. Also the small waits both pages need before they
 * trust a plan: current PC state, and an app list young enough to say an app is not approved.
 */
import { refreshApprovedApps } from "../app/hooks.ts";
import { describeSendError, getRuntime } from "../app/runtime.ts";
import { useDevicesStore } from "../store/devices.ts";
import { isFresh, useLiveStore } from "../store/live.ts";
import { runPlan, type LiveView, type PlanRun, type StepRun } from "./commandPlan.ts";
import type { CommandRecord } from "./commands.ts";
import type { PlanStep } from "./intents.ts";

/** How long typed text must stay unchanged before an unknown app name makes the Type page re-read the PC's app list. */
export const APP_CHECK_DELAY_MS = 400;
/** An app list younger than this is current enough to say "not approved" without asking the PC again. */
export const APPS_FRESH_MS = 3_000;
/** Longest wait for the PC's answer to an app-list refresh before a plan is judged on the list it has. */
export const APPS_ANSWER_WAIT_MS = 5_000;

export const STATUS_LABEL: Record<StepRun["status"], string> = { waiting: "Waiting", sending: "Sending", running: "Running", succeeded: "Done", skipped: "Not needed", failed: "Failed", not_sent: "Not sent", unknown: "Result unknown" };

/** Stop for a running plan: set once, and wakes every step waiting for its answer (runPlan's onInterrupt). */
export interface PlanStopper {
  readonly requested: boolean;
  stop(): void;
  /** Ready for the next plan (a new run starts un-stopped). */
  reset(): void;
  /** Registers a waiting step's wake-up; returns the unsubscribe. */
  onStop(wake: () => void): () => void;
}

export function createStopper(): PlanStopper {
  let requested = false;
  const wakes = new Set<() => void>();
  return {
    get requested() {
      return requested;
    },
    stop() {
      requested = true;
      for (const wake of [...wakes]) wake();
    },
    reset() {
      requested = false;
    },
    onStop(wake) {
      wakes.add(wake);
      return () => wakes.delete(wake);
    },
  };
}

/** The selected PC's live facts as `runPlan` resolves each step against them (read again before every step). */
export function liveView(pcId: string): LiveView {
  const s = useLiveStore.getState();
  return { state: s.pcs[pcId]?.state ?? null, stateSeq: s.pcs[pcId]?.stateSeq, selectedTab: s.selectedTab[pcId], selectedSession: s.selectedSession[pcId], lastSearch: s.lastSearchByPc[pcId] ?? null };
}

export interface RunOnPcOptions {
  source: CommandRecord["source"];
  stopper: PlanStopper;
  onUpdate(run: PlanRun): void;
  /** Called once, when the first step has been signed and sent. */
  onFirstSend?(): void;
}

/**
 * Run `steps` on `pcId` (commandPlan.ts `runPlan`): each step signed and sent by this phone, the next only after the
 * previous one succeeded. Opening or closing an app changes which ones are running, so the app list is re-read
 * afterwards ("maximise it" next then sees the app running).
 */
export async function runPlanOnPc(pcId: string, steps: readonly PlanStep[], options: RunOnPcOptions): Promise<PlanRun> {
  let first = true;
  const result = await runPlan(steps, {
    view: () => liveView(pcId),
    send: async (action, params, target) => {
      const record = await getRuntime().send({ pcId, action, params, target, source: options.source });
      if (first) {
        first = false;
        options.onFirstSend?.();
      }
      return record;
    },
    settled: (commandId) => getRuntime().commands.onceSettled(commandId),
    describeError: describeSendError,
    stopRequested: () => options.stopper.requested,
    connected: () => useLiveStore.getState().relayStatus === "open",
    onInterrupt: (wake) => {
      const offStop = options.stopper.onStop(wake);
      const unsubscribe = useLiveStore.subscribe((now, before) => {
        if (now.relayStatus !== before.relayStatus) wake();
      });
      return () => {
        offStop();
        unsubscribe();
      };
    },
    update: options.onUpdate,
  });
  if (result.steps.some((s, i) => s.status === "succeeded" && (steps[i]!.action === "app.launch" || steps[i]!.action === "app.close"))) void refreshApprovedApps(pcId);
  return result;
}

/** Why the selected PC cannot take commands now (null: it can), in the Dashboard's terms. */
export type ControlProblem = "no_pc" | "offline" | "disabled" | "remote_off" | "not_current";

/** Whether `pcId` can be controlled now (the same rule as `useSelectedPc().canControl`), and if not, why. */
export function controlProblem(pcId: string | null, now = Date.now()): ControlProblem | null {
  if (!pcId) return "no_pc";
  const pc = useDevicesStore.getState().pcs.find((p) => p.id === pcId);
  if (!pc) return "no_pc";
  if (pc.enabled === false) return "disabled";
  const live = useLiveStore.getState().pcs[pcId];
  if (live?.state?.remote_enabled === false) return "remote_off";
  if (isFresh(live, Math.max(now, live?.stateReceivedAt ?? 0))) return null;
  return live?.connection === "offline" || (!live && pc.connection === "offline") ? "offline" : "not_current";
}

/** Resolves true as soon as `test()` holds (checked on every live-store change), or false after `ms`. */
export function waitForLive(test: () => boolean, ms: number): Promise<boolean> {
  if (test()) return Promise.resolve(true);
  return new Promise((resolve) => {
    let done = false;
    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      unsubscribe();
      resolve(ok);
    };
    const timer = setTimeout(() => finish(test()), ms);
    const unsubscribe = useLiveStore.subscribe(() => {
      if (test()) finish(true);
    });
  });
}

/**
 * Before trusting "not an approved app": ask the PC for its app list once (it may have been added a moment ago, DoMe
 * tray → Add an app…) unless the list is younger than APPS_FRESH_MS, and wait for the answer (at most `ms`).
 */
export async function refreshAppsAndWait(pcId: string, ms = APPS_ANSWER_WAIT_MS): Promise<void> {
  const before = useLiveStore.getState().appsByPc[pcId]?.at ?? null;
  if (before !== null && Date.now() - before < APPS_FRESH_MS) return;
  if (!(await refreshApprovedApps(pcId))) return;
  const listing = () => useLiveStore.getState().commands.find((c) => c.pcId === pcId && c.action === "app.list") ?? null;
  await waitForLive(() => {
    const at = useLiveStore.getState().appsByPc[pcId]?.at ?? null;
    const l = listing();
    return (at !== null && at !== before) || (!!l && (!!l.terminal || l.noAnswer));
  }, ms);
}

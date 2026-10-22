/** React hooks over the stores and runtime used by every app page. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { JsonValue, rest } from "@dome/protocol";

import { screenViewSwitch } from "../lib/api.ts";
import type { CommandRecord } from "../lib/commands.ts";
import type { PcKeyPin } from "../lib/controllerKey.ts";
import type { LabelContext } from "../lib/labels.ts";
import { getPin } from "../lib/pcKeyPins.ts";
import { useDevicesStore } from "../store/devices.ts";
import { useInputStore } from "../store/input.ts";
import { EMPTY_LIVE_PC, isFresh, useLiveStore, withTimeStaleness, type AppEntry, type LivePc } from "../store/live.ts";
import { useScreenStore, type ScreenSwitch } from "../store/screen.ts";
import { describeSendError, getRuntime } from "./runtime.ts";

export function useSelectedPc() {
  const selectedPcId = useDevicesStore((s) => s.selectedPcId);
  return usePcView(selectedPcId);
}

/** One PC as the pages see it: inventory entry, live view with the 75 s rule applied, and whether controls may be sent. */
function usePcView(pcId: string | null) {
  const pcs = useDevicesStore((s) => s.pcs);
  const rawLive = useLiveStore((s) => (pcId ? s.pcs[pcId] : undefined));
  // The store clock advances every few seconds (Runtime.start) so the 75 s rule is re-evaluated even
  // when no frame arrives; `Math.max` keeps a state frame received after the last tick fresh.
  const now = useLiveStore((s) => s.now);
  const clock = Math.max(now, rawLive?.stateReceivedAt ?? 0);
  const pc = pcs.find((p) => p.id === pcId);
  const livePc: LivePc = rawLive ? withTimeStaleness(rawLive, clock) : EMPTY_LIVE_PC;
  const fresh = isFresh(rawLive, clock);
  const canControl = fresh && pc?.enabled === true && livePc.state?.remote_enabled !== false;
  return { pc, pcId: pc?.id ?? null, pcName: pc?.name ?? null, live: livePc, fresh, canControl };
}

/** Every PC's live view with the 75 s rule applied (for lists and pickers). */
export function useLivePcs(): Record<string, LivePc> {
  const pcs = useLiveStore((s) => s.pcs);
  const now = useLiveStore((s) => s.now);
  return useMemo(() => {
    const out: Record<string, LivePc> = {};
    for (const [id, pc] of Object.entries(pcs)) out[id] = withTimeStaleness(pc, Math.max(now, pc.stateReceivedAt ?? 0));
    return out;
  }, [pcs, now]);
}

export function useLabels(pcId: string | null): LabelContext {
  const apps = useLiveStore((s) => (pcId ? s.appsByPc[pcId]?.apps : undefined));
  return useMemo(() => {
    const appNames: Record<string, string> = {};
    for (const a of apps ?? []) appNames[a.app_id] = a.display_name;
    return { appNames };
  }, [apps]);
}

export interface SendState {
  lastCommandId: string | null;
  sending: boolean;
  sendError: { code: string; message: string } | null;
}

/** Sends a command and tracks the latest one for the page's "last result" area. */
export function useSend(pcId: string | null) {
  const [state, setState] = useState<SendState>({ lastCommandId: null, sending: false, sendError: null });
  const record = useLiveStore((s) => (state.lastCommandId ? s.commands.find((c) => c.commandId === state.lastCommandId) : undefined));
  const send = useCallback(
    async (action: string, params?: Record<string, JsonValue>, target?: Record<string, JsonValue> | null, source?: CommandRecord["source"]) => {
      if (!pcId) return null;
      setState((s) => ({ ...s, sending: true, sendError: null }));
      try {
        const rec = await getRuntime().send({ pcId, action, ...(params ? { params } : {}), target: target ?? null, ...(source ? { source } : {}) });
        setState({ lastCommandId: rec.commandId, sending: false, sendError: null });
        return rec;
      } catch (e) {
        setState((s) => ({ ...s, sending: false, sendError: describeSendError(e) }));
        return null;
      }
    },
    [pcId],
  );
  return { send, record: record ?? null, sending: state.sending, sendError: state.sendError, clearError: () => setState((s) => ({ ...s, sendError: null })) };
}

/** PCs with an `app.list` being signed right now (before its record exists), so two triggers never send two. */
const appListSigning = new Set<string>();

/**
 * Ask the PC for its approved apps, unless that question is already on its way. The answer lands in
 * `appsByPc` by itself (live store), whichever page asked. Resolves false when nothing could be sent
 * (not connected, not paired); callers retry on their next trigger.
 */
export async function refreshApprovedApps(pcId: string): Promise<boolean> {
  const inFlight = useLiveStore.getState().commands.find((c) => c.pcId === pcId && c.action === "app.list" && !c.terminal && !c.noAnswer);
  if (inFlight || appListSigning.has(pcId)) return true;
  appListSigning.add(pcId);
  try {
    await getRuntime().send({ pcId, action: "app.list", source: "system" });
    return true;
  } catch {
    return false;
  } finally {
    appListSigning.delete(pcId);
  }
}

export interface ApprovedApps {
  /** null until the PC answered once (this session). */
  apps: AppEntry[] | null;
  /** When the list was received (ms). */
  at: number | null;
  /** The most recent `app.list` command for this PC (in flight or finished), for loading and error copy. */
  listing: CommandRecord | null;
}

/**
 * The PC's approved apps, re-read from the PC when the calling page mounts and every time the app comes back
 * to the foreground (visibilitychange / window focus), once the PC's state is fresh again. Apps the owner adds
 * on the PC (DoMe tray → Add an app…) therefore appear without a manual Refresh. pc_state carries no app list,
 * so asking is the only way to learn about them.
 */
export function useApprovedApps(pcId: string | null): ApprovedApps {
  const { canControl } = usePcView(pcId);
  const cached = useLiveStore((s) => (pcId ? s.appsByPc[pcId] : undefined));
  const listing = useLiveStore((s) => (pcId ? (s.commands.find((c) => c.pcId === pcId && c.action === "app.list") ?? null) : null));
  const [wanted, setWanted] = useState(0);
  const asked = useRef<string | null>(null);
  useEffect(() => {
    const again = () => {
      if (document.visibilityState === "visible") setWanted((n) => n + 1);
    };
    document.addEventListener("visibilitychange", again);
    window.addEventListener("focus", again);
    return () => {
      document.removeEventListener("visibilitychange", again);
      window.removeEventListener("focus", again);
    };
  }, []);
  useEffect(() => {
    // One request per trigger, sent as soon as controls are available (after a resume the state is stale until the
    // PC's next state frame; the request waits for it instead of failing on a reconnecting socket).
    const key = `${pcId}:${wanted}`;
    if (!pcId || !canControl || asked.current === key) return;
    asked.current = key;
    void refreshApprovedApps(pcId).then((sent) => {
      if (!sent && asked.current === key) asked.current = null;
    });
  }, [pcId, canControl, wanted]);
  return { apps: cached?.apps ?? null, at: cached?.at ?? null, listing };
}

export function useLastCommandFor(pcId: string | null): CommandRecord | null {
  return useLiveStore((s) => (pcId ? (s.commands.find((c) => c.pcId === pcId) ?? null) : null));
}

/** The live manual-input session (store mirror) plus the grant this phone holds on the selected PC. */
export function useInputSession() {
  const session = useInputStore((s) => s.session);
  const prefs = useInputStore((s) => s.prefs);
  const setPrefs = useInputStore((s) => s.setPrefs);
  return { session, prefs, setPrefs, client: getRuntime().input };
}

/** This phone's grant on `pcId` from the REST inventory (null while unknown or when not paired with that PC). */
export function useMyGrant(pcId: string | null): rest.Grant | null | undefined {
  const grants = useDevicesStore((s) => (pcId ? s.grantsByPc[pcId] : undefined));
  const controllers = useDevicesStore((s) => s.controllers);
  const controllerId = useLiveStore((s) => s.controllerId);
  if (!pcId || grants === undefined) return undefined;
  if (!controllerId) return controllers.length === 0 ? undefined : null;
  return grants.find((g) => g.controller_id === controllerId) ?? null;
}

/** The live Screen view session (store mirror), its preferences and the client (protocol 1.4). */
export function useScreenSession() {
  const session = useScreenStore((s) => s.session);
  const prefs = useScreenStore((s) => s.prefs);
  const setPrefs = useScreenStore((s) => s.setPrefs);
  return { session, prefs, setPrefs, client: getRuntime().screen };
}

/** Asked at most once per app load (`switchChecked`); until the server answers the value is "unknown" and the app keeps its "Coming" card. */
let switchRequest: Promise<void> | null = null;

/**
 * The Screen view operator switch as the server states it (GET /healthz `screen_view`). "unknown" until it answers or
 * when it cannot be read: the Screen tile then does not exist and the honest "Coming" card stays (DECISIONS 63).
 */
export function useScreenSwitch(): ScreenSwitch {
  const value = useScreenStore((s) => s.switch);
  const checked = useScreenStore((s) => s.switchChecked);
  useEffect(() => {
    if (checked || switchRequest) return;
    switchRequest = screenViewSwitch()
      .then((v) => useScreenStore.getState().setSwitch(v))
      .catch(() => useScreenStore.getState().setSwitch("unknown"))
      .finally(() => {
        switchRequest = null;
      });
  }, [checked]);
  return value;
}

/** Tests only: forget the one-shot switch request so the next mount asks again. */
export function resetScreenSwitchForTests(): void {
  switchRequest = null;
  useScreenStore.setState({ switch: "unknown", switchChecked: false });
}

/** The PC key pinned at pairing for `pcId` (undefined while reading, null when none: Screen view says "pair again"). */
export function usePcKeyPin(pcId: string | null, epoch = 0): PcKeyPin | null | undefined {
  const key = `${pcId ?? ""}|${epoch}`;
  const [read, setRead] = useState<{ key: string; pin: PcKeyPin | null } | null>(null);
  useEffect(() => {
    if (!pcId) return;
    let cancelled = false;
    void getPin(pcId).then((p) => {
      if (!cancelled) setRead({ key, pin: p });
    });
    return () => {
      cancelled = true;
    };
  }, [pcId, key]);
  if (!pcId) return null;
  // a read for another PC (or an older epoch) is not this one's answer yet
  return read && read.key === key ? read.pin : undefined;
}

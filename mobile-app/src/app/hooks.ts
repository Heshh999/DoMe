/** React hooks over the stores and runtime used by every app page. */
import { useCallback, useMemo, useState } from "react";

import type { JsonValue, rest } from "@dome/protocol";

import type { CommandRecord } from "../lib/commands.ts";
import type { LabelContext } from "../lib/labels.ts";
import { useDevicesStore } from "../store/devices.ts";
import { useInputStore } from "../store/input.ts";
import { EMPTY_LIVE_PC, isFresh, useLiveStore, withTimeStaleness, type LivePc } from "../store/live.ts";
import { describeSendError, getRuntime } from "./runtime.ts";

export function useSelectedPc() {
  const pcs = useDevicesStore((s) => s.pcs);
  const selectedPcId = useDevicesStore((s) => s.selectedPcId);
  const rawLive = useLiveStore((s) => (selectedPcId ? s.pcs[selectedPcId] : undefined));
  // The store clock advances every few seconds (Runtime.start) so the 75 s rule is re-evaluated even
  // when no frame arrives; `Math.max` keeps a state frame received after the last tick fresh.
  const now = useLiveStore((s) => s.now);
  const clock = Math.max(now, rawLive?.stateReceivedAt ?? 0);
  const pc = pcs.find((p) => p.id === selectedPcId);
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

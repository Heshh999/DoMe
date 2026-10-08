/** React hooks over the stores and runtime used by every app page. */
import { useCallback, useMemo, useState } from "react";

import type { JsonValue } from "@dome/protocol";

import type { CommandRecord } from "../lib/commands.ts";
import type { LabelContext } from "../lib/labels.ts";
import { useDevicesStore } from "../store/devices.ts";
import { EMPTY_LIVE_PC, isFresh, useLiveStore, type LivePc } from "../store/live.ts";
import { describeSendError, getRuntime } from "./runtime.ts";

export function useSelectedPc() {
  const pcs = useDevicesStore((s) => s.pcs);
  const selectedPcId = useDevicesStore((s) => s.selectedPcId);
  const live = useLiveStore((s) => (selectedPcId ? s.pcs[selectedPcId] : undefined));
  const pc = pcs.find((p) => p.id === selectedPcId);
  const livePc: LivePc = live ?? EMPTY_LIVE_PC;
  const fresh = isFresh(live);
  const canControl = fresh && pc?.enabled === true && livePc.state?.remote_enabled !== false;
  return { pc, pcId: pc?.id ?? null, pcName: pc?.name ?? null, live: livePc, fresh, canControl };
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

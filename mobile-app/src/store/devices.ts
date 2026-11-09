/**
 * Device inventory from REST: PCs, controller installations, grants per PC and the selected PC. The
 * selection is persisted in IndexedDB (`state` store) so a resumed PWA opens on the same PC; it is
 * cleared on sign-out together with the rest of the account state.
 *
 * Protocol 1.5: `pc.protocol_versions` says which MINORs a PC's agent announced (a phone sends a newer optional param
 * only to a PC that speaks it). A REST field the app does not know (an open app that still runs the bundle from before
 * a deploy) makes a SUCCESSFUL `/v1/pcs` answer fail this bundle's schema (MALFORMED_MESSAGE with a 2xx status): the
 * list on screen is kept, the controllers are still taken from their own answer, `updateReady` says a newer DoMe is
 * ready, and the newer app is asked for (design screen-view-2a D28). A refusal whose body is not DoMe's (an HTML 403 or
 * 404 from a proxy: MALFORMED_MESSAGE with a 4xx status) is an error like any other.
 */
import { create } from "zustand";

import { protocolCompatible, type rest } from "@dome/protocol";

import { api, ApiError } from "../lib/api.ts";
import { getState, setState, STATE_SELECTED_PC } from "../lib/controllerKey.ts";
import { errorSummary, log } from "../lib/log.ts";
import { requestAppUpdate } from "../pwa.ts";

export interface DevicesState {
  loaded: boolean;
  loading: boolean;
  error: ApiError | null;
  pcs: rest.Pc[];
  controllers: rest.Controller[];
  grantsByPc: Record<string, rest.Grant[]>;
  selectedPcId: string | null;
  /** `/v1/pcs` answered with something this app version does not understand: a newer DoMe is ready (reopen the app). */
  updateReady: boolean;
  refresh(): Promise<void>;
  /** The protocol versions `pcId`'s agent announced at its last relay hello ([] when unknown). */
  pcProtocolVersions(pcId: string): string[];
  /** Whether `pcId`'s agent announced protocol `minor` (e.g. "1.5"). */
  pcSpeaks(pcId: string, minor: string): boolean;
  loadGrants(pcId: string): Promise<void>;
  select(pcId: string | null): Promise<void>;
  /** Restore the persisted selection (called once after sign-in). */
  restoreSelection(): Promise<void>;
  reset(): void;
}

function pickDefault(pcs: rest.Pc[], current: string | null): string | null {
  if (current && pcs.some((p) => p.id === current)) return current;
  const enabled = pcs.filter((p) => p.enabled);
  const online = enabled.find((p) => p.connection === "online");
  return (online ?? enabled[0] ?? pcs[0])?.id ?? null;
}

export const useDevicesStore = create<DevicesState>()((set, get) => ({
  loaded: false,
  loading: false,
  error: null,
  pcs: [],
  controllers: [],
  grantsByPc: {},
  selectedPcId: null,
  updateReady: false,
  async refresh() {
    set({ loading: true });
    const pcsCall = api.pcs();
    const controllersCall = api.controllers();
    try {
      let pcsRes: Awaited<typeof pcsCall>;
      try {
        pcsRes = await pcsCall;
      } catch (e) {
        if (e instanceof ApiError && e.code === "MALFORMED_MESSAGE" && e.status >= 200 && e.status < 300) {
          // a field this bundle does not know: the deploy is newer than this app. Keep what is shown, say so, update.
          log.info("devices.update_ready", { status: e.status });
          requestAppUpdate();
          // the controllers still come from their own answer (the Devices page names this phone from them)
          let controllers = get().controllers;
          try {
            controllers = (await controllersCall).controllers;
          } catch (ce) {
            log.info("devices.controllers_failed", errorSummary(ce));
          }
          set({ loading: false, updateReady: true, controllers });
          return;
        }
        throw e;
      }
      const controllersRes = await controllersCall;
      const selected = pickDefault(pcsRes.pcs, get().selectedPcId);
      set({ pcs: pcsRes.pcs, controllers: controllersRes.controllers, loaded: true, loading: false, error: null, selectedPcId: selected });
      if (selected !== get().selectedPcId) void setState(STATE_SELECTED_PC, selected).catch(() => undefined);
    } catch (e) {
      void controllersCall.catch(() => undefined);
      log.warn("devices.refresh_failed", errorSummary(e));
      set({ loading: false, error: e instanceof ApiError ? e : new ApiError(0, "INTERNAL", "Could not load your devices.", true) });
    }
  },
  pcProtocolVersions(pcId) {
    return [...(get().pcs.find((p) => p.id === pcId)?.protocol_versions ?? [])];
  },
  pcSpeaks(pcId, minor) {
    return protocolCompatible(minor, get().pcProtocolVersions(pcId));
  },
  async loadGrants(pcId) {
    try {
      const res = await api.pcGrants(pcId);
      set((s) => ({ grantsByPc: { ...s.grantsByPc, [pcId]: res.grants } }));
    } catch (e) {
      log.warn("devices.grants_failed", errorSummary(e));
    }
  },
  async select(pcId) {
    set({ selectedPcId: pcId });
    try {
      await setState(STATE_SELECTED_PC, pcId);
    } catch {
      /* storage unavailable: selection is in memory only */
    }
  },
  async restoreSelection() {
    try {
      const stored = await getState(STATE_SELECTED_PC);
      if (stored && get().selectedPcId === null) set({ selectedPcId: stored });
    } catch {
      /* storage unavailable */
    }
  },
  reset() {
    set({ loaded: false, loading: false, error: null, pcs: [], controllers: [], grantsByPc: {}, selectedPcId: null, updateReady: false });
  },
}));

export function pcName(pcs: rest.Pc[], pcId: string | null): string | null {
  return pcs.find((p) => p.id === pcId)?.name ?? null;
}

/**
 * Device inventory from REST: PCs, controller installations, grants per PC and the selected PC. The
 * selection is persisted in IndexedDB (`state` store) so a resumed PWA opens on the same PC; it is
 * cleared on sign-out together with the rest of the account state.
 */
import { create } from "zustand";

import type { rest } from "@dome/protocol";

import { api, ApiError } from "../lib/api.ts";
import { getState, setState, STATE_SELECTED_PC } from "../lib/controllerKey.ts";
import { errorSummary, log } from "../lib/log.ts";

export interface DevicesState {
  loaded: boolean;
  loading: boolean;
  error: ApiError | null;
  pcs: rest.Pc[];
  controllers: rest.Controller[];
  grantsByPc: Record<string, rest.Grant[]>;
  selectedPcId: string | null;
  refresh(): Promise<void>;
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
  async refresh() {
    set({ loading: true });
    try {
      const [pcsRes, controllersRes] = await Promise.all([api.pcs(), api.controllers()]);
      const selected = pickDefault(pcsRes.pcs, get().selectedPcId);
      set({ pcs: pcsRes.pcs, controllers: controllersRes.controllers, loaded: true, loading: false, error: null, selectedPcId: selected });
      if (selected !== get().selectedPcId) void setState(STATE_SELECTED_PC, selected).catch(() => undefined);
    } catch (e) {
      log.warn("devices.refresh_failed", errorSummary(e));
      set({ loading: false, error: e instanceof ApiError ? e : new ApiError(0, "INTERNAL", "Could not load your devices.", true) });
    }
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
    set({ loaded: false, loading: false, error: null, pcs: [], controllers: [], grantsByPc: {}, selectedPcId: null });
  },
}));

export function pcName(pcs: rest.Pc[], pcId: string | null): string | null {
  return pcs.find((p) => p.id === pcId)?.name ?? null;
}

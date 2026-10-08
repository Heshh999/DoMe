/**
 * Customer-initiated, redacted diagnostics (Settings → Diagnostics). Contains versions, platform
 * capabilities, connection states and command lifecycle rows (action/state/error code only). It never
 * contains tokens, codes, pairing material, challenge text, parameters, results or media titles.
 */
import { PROTOCOL_VERSION, REGISTRY_VERSION } from "@dome/protocol";

import type { CommandRecord } from "./commands.ts";
import { recentLogs, type LogEntry } from "./log.ts";
import { detectQrSupport } from "./qr.ts";
import type { RelayStatus } from "./relay.ts";

export const APP_VERSION: string = __APP_VERSION__;

export interface DiagnosticsInput {
  relayStatus: RelayStatus;
  controllerBound: boolean;
  keyAvailable: boolean;
  selectedPcId: string | null;
  pcs: Array<{ id: string; connection: string; enabled: boolean; lastSeen: string | null; stale: boolean; remoteEnabled: boolean | null; extensionConnected: boolean | null; sessionLocked: boolean | null }>;
  commands: CommandRecord[];
  sessionPresent: boolean;
  plan: string | null;
}

export interface DiagnosticsReport {
  generated_at: string;
  app: { version: string; protocol_version: string; registry_version: string; release_channel: string };
  device: { user_agent: string; standalone: boolean; online: boolean; language: string; qr_support: string; indexeddb: boolean; webcrypto: boolean; viewport: string };
  session: { present: boolean; plan: string | null };
  relay: { status: string; controller_bound: boolean; key_available: boolean };
  pcs: DiagnosticsInput["pcs"];
  selected_pc_id: string | null;
  commands: Array<{ command_id: string; pc_id: string; action: string; state: string; origin: string | null; error_code: string | null; duration_ms: number | null; created_at: string; no_answer: boolean }>;
  log: LogEntry[];
}

export function isStandalone(): boolean {
  const nav = navigator as Navigator & { standalone?: boolean };
  return nav.standalone === true || (typeof matchMedia === "function" && matchMedia("(display-mode: standalone)").matches);
}

export function buildDiagnostics(input: DiagnosticsInput): DiagnosticsReport {
  return {
    generated_at: new Date().toISOString(),
    app: { version: APP_VERSION, protocol_version: PROTOCOL_VERSION, registry_version: REGISTRY_VERSION, release_channel: import.meta.env.VITE_DOME_RELEASE_CHANNEL ?? "dev" },
    device: {
      user_agent: navigator.userAgent,
      standalone: isStandalone(),
      online: navigator.onLine,
      language: navigator.language,
      qr_support: detectQrSupport(),
      indexeddb: typeof indexedDB !== "undefined",
      webcrypto: typeof crypto !== "undefined" && !!crypto.subtle,
      viewport: `${window.innerWidth}x${window.innerHeight}`,
    },
    session: { present: input.sessionPresent, plan: input.plan },
    relay: { status: input.relayStatus, controller_bound: input.controllerBound, key_available: input.keyAvailable },
    pcs: input.pcs,
    selected_pc_id: input.selectedPcId,
    commands: input.commands.slice(0, 30).map((c) => ({
      command_id: c.commandId,
      pc_id: c.pcId,
      action: c.action,
      state: c.state,
      origin: c.terminal?.origin ?? null,
      error_code: c.terminal?.error?.code ?? null,
      duration_ms: c.terminal?.durationMs ?? null,
      created_at: new Date(c.createdAt).toISOString(),
      no_answer: c.noAnswer,
    })),
    log: recentLogs(),
  };
}

export function downloadJson(filename: string, value: unknown): void {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

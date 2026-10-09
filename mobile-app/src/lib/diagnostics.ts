/**
 * Customer-initiated, redacted diagnostics (Settings → Diagnostics). Contains versions, platform
 * capabilities, connection states and command lifecycle rows (action/state/error code only). It never
 * contains tokens, codes, pairing material, challenge text, parameters, results or media titles.
 */
import { PROTOCOL_VERSION, REGISTRY_VERSION } from "@dome/protocol";

import type { CommandRecord } from "./commands.ts";
import type { InputSessionState } from "./input.ts";
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
  /** Manual-input session summary (phase, counts, codes — never events or text). */
  input?: InputSessionState;
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
  input: { phase: string; pc_id: string | null; pointer: boolean; keyboard: boolean; seq: number; batches_sent: number; last_ack_seq: number | null; accepted_events: number | null; dropped_events: number | null; problem_code: string | null; end_reason: string | null } | null;
  log: LogEntry[];
}

/** Support-ticket size limit for the diagnostics text (rest.schema.json support_ticket_request.diagnostics). */
export const DIAGNOSTICS_TEXT_MAX = 32768;

/** The report as JSON text within the ticket limit: log entries are dropped oldest-first until it fits. */
export function diagnosticsText(report: DiagnosticsReport): string {
  let r: DiagnosticsReport = report;
  let text = JSON.stringify(r, null, 1);
  while (text.length > DIAGNOSTICS_TEXT_MAX && r.log.length > 0) {
    r = { ...r, log: r.log.slice(Math.ceil(r.log.length / 4)) };
    text = JSON.stringify(r, null, 1);
  }
  if (text.length > DIAGNOSTICS_TEXT_MAX) {
    r = { ...r, commands: r.commands.slice(0, 5), log: [] };
    text = JSON.stringify(r, null, 1);
  }
  return text.slice(0, DIAGNOSTICS_TEXT_MAX);
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
    input: input.input
      ? {
          phase: input.input.phase,
          pc_id: input.input.pcId,
          pointer: input.input.pointer,
          keyboard: input.input.keyboard,
          seq: input.input.seq,
          batches_sent: input.input.batchesSent,
          last_ack_seq: input.input.lastAck?.lastSeq ?? null,
          accepted_events: input.input.lastAck?.acceptedEvents ?? null,
          dropped_events: input.input.lastAck?.droppedEvents ?? null,
          problem_code: input.input.problem?.code ?? null,
          end_reason: input.input.endReason,
        }
      : null,
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

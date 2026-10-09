/**
 * REST client for cloud-api. Same origin by default; cookies carry the session; every state-changing
 * request carries `X-DoMe-CSRF` (from GET /v1/session). Every response body is strict-parsed and
 * validated against `rest.schema.json` before use; error bodies are validated the same way.
 * Nothing is cached (`cache: "no-store"`), nothing is logged beyond method/path/status/code.
 */
import { loadsStrict, ProtocolError, schemas, LIMITS, type rest } from "@dome/protocol";

import { log } from "./log.ts";

export const API_ORIGIN: string = (import.meta.env.VITE_DOME_API_ORIGIN ?? "").replace(/\/+$/, "");

/** REST bodies may legitimately exceed the 16 KiB signed-payload limit (inventories, event lists). */
const MAX_REST_BYTES = 512 * 1024;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  readonly detail: Record<string, unknown> | undefined;
  constructor(status: number, code: string, message: string, retryable: boolean, detail?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.detail = detail;
  }
}

export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

interface ClientState {
  csrfToken: string | null;
  fetchImpl: FetchLike;
  onUnauthenticated: (() => void) | null;
}

const state: ClientState = {
  csrfToken: null,
  fetchImpl: (input, init) => fetch(input, init),
  onUnauthenticated: null,
};

export function configureApi(options: { csrfToken?: string | null; fetchImpl?: FetchLike; onUnauthenticated?: (() => void) | null }): void {
  if ("csrfToken" in options) state.csrfToken = options.csrfToken ?? null;
  if (options.fetchImpl) state.fetchImpl = options.fetchImpl;
  if ("onUnauthenticated" in options) state.onUnauthenticated = options.onUnauthenticated ?? null;
}

export function apiUrl(path: string): string {
  return `${API_ORIGIN}${path}`;
}

/**
 * Where the browser must navigate to sign in; cloud-api redirects to the OIDC provider. The
 * `return_to` value is a relative path + query only: any fragment is dropped here as a last line of
 * defence, because the pairing deep link carries the pairing code in its fragment and the backend
 * must never receive it (version.json rules.pairing_secret). Callers scrub the fragment themselves
 * before calling this (see `app/navigation.ts`).
 */
export function loginUrl(returnTo: string): string {
  const withoutFragment = returnTo.split("#", 1)[0] ?? "";
  const safe = withoutFragment.startsWith("/") && !withoutFragment.startsWith("//") ? withoutFragment : "/app";
  return apiUrl(`/v1/auth/login?return_to=${encodeURIComponent(safe)}`);
}

function parseBody(text: string): Record<string, unknown> {
  return loadsStrict(text, { maxBytes: MAX_REST_BYTES, maxDepth: LIMITS.max_json_depth, requireObject: true }) as Record<string, unknown>;
}

async function toApiError(response: Response, method: string, path: string): Promise<ApiError> {
  let text = "";
  try {
    text = await response.text();
  } catch {
    /* body unreadable */
  }
  let err: ApiError;
  try {
    const body = parseBody(text);
    schemas.validateRest("error_body", body);
    const e = (body as unknown as rest.ErrorBody).error;
    err = new ApiError(response.status, e.code, e.message, e.retryable, e.detail as Record<string, unknown> | undefined);
  } catch {
    err = new ApiError(response.status, response.status >= 500 ? "INTERNAL" : "MALFORMED_MESSAGE", response.status >= 500 ? "DoMe is having trouble right now. Try again shortly." : "The server's answer was not understood.", response.status >= 500);
  }
  log.info("api.error", { method, path, status: response.status, code: err.code });
  if (response.status === 401) state.onUnauthenticated?.();
  return err;
}

export interface RequestOptions {
  /** `rest.schema.json#/$defs/<name>` the response must satisfy; `null` for 204 responses. */
  responseBody: string | null;
  /** `rest.schema.json#/$defs/<name>` the request body is checked against before sending. */
  requestBody?: string;
  body?: unknown;
  signal?: AbortSignal;
}

export async function apiRequest<T>(method: "GET" | "POST" | "PATCH" | "DELETE", path: string, options: RequestOptions): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = { method, headers, credentials: "include", cache: "no-store", redirect: "error", mode: "same-origin" };
  if (API_ORIGIN) init.mode = "cors";
  if (options.signal) init.signal = options.signal;
  if (method !== "GET") {
    if (!state.csrfToken) throw new ApiError(0, "UNAUTHENTICATED", "Sign in to continue.", false);
    headers["X-DoMe-CSRF"] = state.csrfToken;
    if (options.body !== undefined) {
      if (options.requestBody) schemas.validateRest(options.requestBody, options.body);
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
  }
  let response: Response;
  try {
    response = await state.fetchImpl(apiUrl(path), init);
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    log.info("api.network_error", { method, path });
    throw new ApiError(0, "NETWORK", "You appear to be offline, or DoMe could not be reached.", true);
  }
  if (!response.ok) throw await toApiError(response, method, path);
  if (response.status === 204 || options.responseBody === null) return undefined as T;
  const text = await response.text();
  let parsed: Record<string, unknown>;
  try {
    parsed = parseBody(text);
    schemas.validateRest(options.responseBody, parsed);
  } catch (e) {
    log.warn("api.invalid_response", { method, path, code: e instanceof ProtocolError ? e.code : "parse" });
    throw new ApiError(response.status, "MALFORMED_MESSAGE", "The server's answer was not understood. Try updating DoMe.", false);
  }
  return parsed as T;
}

// ---- typed endpoints ------------------------------------------------------------------------------

export const api = {
  session: () => apiRequest<rest.SessionResponse>("GET", "/v1/session", { responseBody: "session_response" }),
  logout: () => apiRequest<void>("POST", "/v1/auth/logout", { responseBody: null }),
  plans: () => apiRequest<rest.PlansResponse>("GET", "/v1/plans", { responseBody: "plans_response" }),

  pcs: () => apiRequest<rest.PcsResponse>("GET", "/v1/pcs", { responseBody: "pcs_response" }),
  patchPc: (pcId: string, body: rest.PcPatchRequest) => apiRequest<rest.Pc>("PATCH", `/v1/pcs/${encodeURIComponent(pcId)}`, { responseBody: "pc", requestBody: "pc_patch_request", body }),
  unlinkPc: (pcId: string) => apiRequest<void>("DELETE", `/v1/pcs/${encodeURIComponent(pcId)}`, { responseBody: null }),
  pcGrants: (pcId: string) => apiRequest<rest.GrantsResponse>("GET", `/v1/pcs/${encodeURIComponent(pcId)}/grants`, { responseBody: "grants_response" }),
  revokeGrant: (grantId: string) => apiRequest<void>("DELETE", `/v1/grants/${encodeURIComponent(grantId)}`, { responseBody: null }),

  controllers: () => apiRequest<rest.ControllersResponse>("GET", "/v1/controllers", { responseBody: "controllers_response" }),
  renameController: (id: string, body: rest.ControllerPatchRequest) => apiRequest<rest.Controller>("PATCH", `/v1/controllers/${encodeURIComponent(id)}`, { responseBody: "controller", requestBody: "controller_patch_request", body }),
  revokeController: (id: string) => apiRequest<void>("DELETE", `/v1/controllers/${encodeURIComponent(id)}`, { responseBody: null }),

  pairingClaim: (body: rest.PairingClaimRequest) => apiRequest<rest.PairingStatusResponse>("POST", "/v1/pairing/claim", { responseBody: "pairing_status_response", requestBody: "pairing_claim_request", body }),
  pairingStatus: (pairingId: string, signal?: AbortSignal) => apiRequest<rest.PairingStatusResponse>("GET", `/v1/pairing/${encodeURIComponent(pairingId)}`, { responseBody: "pairing_status_response", ...(signal ? { signal } : {}) }),

  linkPreview: (userCode: string) => apiRequest<rest.AgentLinkPreviewResponse>("GET", `/v1/agent-link/${encodeURIComponent(userCode)}`, { responseBody: "agent_link_preview_response" }),
  linkApprove: (userCode: string, body: rest.AgentLinkApproveRequest) => apiRequest<rest.AgentLinkApproveResponse>("POST", `/v1/agent-link/${encodeURIComponent(userCode)}/approve`, { responseBody: "agent_link_approve_response", requestBody: "agent_link_approve_request", body }),
  linkDeny: (userCode: string) => apiRequest<void>("POST", `/v1/agent-link/${encodeURIComponent(userCode)}/deny`, { responseBody: null }),

  commands: () => apiRequest<rest.CommandsResponse>("GET", "/v1/commands", { responseBody: "commands_response" }),

  // Support (spec §11A): the only path by which anything leaves the phone for support staff, and only
  // what the customer typed plus the diagnostics bundle they reviewed.
  supportTicketCreate: (body: rest.SupportTicketRequest) => apiRequest<rest.SupportTicket>("POST", "/v1/support/tickets", { responseBody: "support_ticket_response", requestBody: "support_ticket_request", body }),
  supportTickets: () => apiRequest<rest.SupportTicketsResponse>("GET", "/v1/support/tickets", { responseBody: "support_tickets_response" }),
  supportTicket: (id: string) => apiRequest<rest.SupportTicket>("GET", `/v1/support/tickets/${encodeURIComponent(id)}`, { responseBody: "support_ticket_response" }),
  securityEvents: () => apiRequest<rest.SecurityEventsResponse>("GET", "/v1/account/security-events", { responseBody: "security_events_response" }),
};

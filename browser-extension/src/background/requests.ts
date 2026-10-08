/**
 * bridge_request routing: list_tabs is answered from the tab registry; tab ops are forwarded to the
 * content script of the addressed tab with a deadline. Every failure maps to a stable error code.
 */
import { OpError, toErrorObject } from "../shared/errors.ts";
import { log } from "../shared/log.ts";
import { BG_KIND, isTabOp, type OpArgs } from "../shared/messages.ts";
import { validateResult } from "../shared/validate.ts";
import { isYoutubeUrl } from "../shared/youtubeUrl.ts";
import { isMutatingOp } from "../content/adapter.ts";
import type { ExtensionApi } from "./api.ts";
import { responseError, responseOk, type BridgeRequest, type BridgeResponse, type YoutubeStateResult } from "./frames.ts";
import { NoReceiverError, TabTimeoutError, type TabRegistry } from "./tabs.ts";

export const DEFAULT_TIMEOUT_MS = 8000;
/** Margin so the content script answers before the agent's own deadline. */
const DEADLINE_MARGIN_MS = 700;
/** Margin so the background's reply reaches the agent before the agent's own timeout. */
const REPLY_MARGIN_MS = 200;
/** The schema allows timeout_ms down to 100; below this the margins would invert, so the budget is floored. */
export const MIN_BUDGET_MS = 1000;

export interface Budget {
  /** How long the background waits for the content script's reply. */
  bgWaitMs: number;
  /** The deadline handed to the content script; always strictly shorter than bgWaitMs. */
  deadlineMs: number;
}

/** Time budget for one tab op from the agent's timeout_ms; the invariant deadlineMs < bgWaitMs holds for every schema value. */
export function computeBudget(timeoutMs: number | undefined): Budget {
  const total = Math.min(60000, Math.max(MIN_BUDGET_MS, timeoutMs ?? DEFAULT_TIMEOUT_MS));
  return { bgWaitMs: total - REPLY_MARGIN_MS, deadlineMs: total - DEADLINE_MARGIN_MS };
}

export interface RequestContext {
  api: ExtensionApi;
  registry: TabRegistry;
  /** Called after a tab op changed the cached state so the service can emit player_state. */
  onTabState?: (tabId: number) => void;
}

export async function handleRequest(request: BridgeRequest, ctx: RequestContext): Promise<BridgeResponse> {
  const started = Date.now();
  try {
    const response = await route(request, ctx);
    log.info("request handled", { op: request.op, request_id: request.request_id, ok: response.ok, code: response.ok ? null : (response.error?.code ?? null), ms: Date.now() - started });
    return response;
  } catch (err) {
    const e = toErrorObject(err);
    log.warn("request failed", { op: request.op, request_id: request.request_id, code: e.code, ms: Date.now() - started });
    return responseError(request.request_id, e);
  }
}

async function route(request: BridgeRequest, ctx: RequestContext): Promise<BridgeResponse> {
  const { bgWaitMs, deadlineMs } = computeBudget(request.timeout_ms);
  if (request.op === "list_tabs") {
    const tabs = await ctx.registry.list({ fresh: true });
    const result = { tabs };
    validateResult("tabs_result", result);
    return responseOk(request.request_id, result);
  }
  if (!isTabOp(request.op)) throw new OpError("UNKNOWN_ACTION", `unsupported op ${request.op}`);
  const { tab_id: tabId, ...rest } = request.args;
  if (typeof tabId !== "number" || !Number.isInteger(tabId) || tabId < 0) throw new OpError("INVALID_PARAMETERS", "tab_id is required for tab operations");
  const args: OpArgs = rest;

  let tab: chrome.tabs.Tab;
  try {
    tab = await ctx.api.tabs.get(tabId);
  } catch {
    throw new OpError("TARGET_GONE", "That tab is no longer open");
  }
  if (!isYoutubeUrl(tab.url)) throw new OpError("TARGET_GONE", "That tab is no longer on YouTube");
  if (tab.discarded) throw new OpError("TAB_NOT_CONTROLLABLE", "The tab was unloaded by the browser; reload the YouTube tab on the PC");

  let reply;
  try {
    reply = await ctx.registry.sendToTab(tabId, { kind: BG_KIND, type: "op", op: request.op, args, deadline_ms: deadlineMs }, bgWaitMs);
  } catch (err) {
    if (err instanceof NoReceiverError) throw new OpError("TAB_NOT_CONTROLLABLE", "DoMe is not running in that tab yet; reload the YouTube tab on the PC");
    if (err instanceof TabTimeoutError) {
      if (isMutatingOp(request.op)) throw new OpError("OUTCOME_UNKNOWN", "The YouTube tab did not report back in time; the action may or may not have happened");
      throw new OpError("TAB_NOT_CONTROLLABLE", "The YouTube tab did not respond; reload it on the PC");
    }
    throw err;
  }

  const recorded = reply.token !== undefined && reply.state !== undefined ? ctx.registry.record(tabId, reply.token, reply.state) : null;
  if (recorded) ctx.onTabState?.(tabId);
  if (!reply.ok) {
    const code = typeof reply.code === "string" && /^[A-Z_]+$/.test(reply.code) ? reply.code : "INTERNAL";
    const message = typeof reply.message === "string" ? reply.message : "the YouTube tab reported a failure";
    throw new OpError(code, message);
  }
  if (!recorded) throw new OpError("INTERNAL", "the YouTube tab reported success without a usable state");
  // Compose from the attachment just recorded: a tabs.onUpdated event caused by this very action
  // (e.g. the URL change of `next`) may be dispatched between the reply and here.
  const composed = await ctx.registry.composeById(tabId, recorded);
  if (!composed) throw new OpError("TARGET_GONE", "The tab closed or left YouTube while the action ran");
  const result: YoutubeStateResult = { tab: composed };
  if ((request.op === "next" || request.op === "previous") && typeof reply.previous_video_id === "string" && /^[A-Za-z0-9_-]{1,32}$/.test(reply.previous_video_id)) {
    result.previous_video_id = reply.previous_video_id;
  }
  validateResult("youtube_state_result", result);
  return responseOk(request.request_id, result);
}

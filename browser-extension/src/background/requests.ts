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
  const timeoutMs = Math.min(60000, Math.max(100, request.timeout_ms ?? DEFAULT_TIMEOUT_MS));
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

  const deadlineMs = Math.max(500, timeoutMs - DEADLINE_MARGIN_MS);
  let reply;
  try {
    reply = await ctx.registry.sendToTab(tabId, { kind: BG_KIND, type: "op", op: request.op, args, deadline_ms: deadlineMs }, timeoutMs - 200);
  } catch (err) {
    if (err instanceof NoReceiverError) throw new OpError("TAB_NOT_CONTROLLABLE", "DoMe is not running in that tab yet; reload the YouTube tab on the PC");
    if (err instanceof TabTimeoutError) {
      if (isMutatingOp(request.op)) throw new OpError("OUTCOME_UNKNOWN", "The YouTube tab did not report back in time; the action may or may not have happened");
      throw new OpError("TAB_NOT_CONTROLLABLE", "The YouTube tab did not respond; reload it on the PC");
    }
    throw err;
  }

  if (reply.token !== undefined && reply.state !== undefined && ctx.registry.record(tabId, reply.token, reply.state)) ctx.onTabState?.(tabId);
  if (!reply.ok) {
    const code = typeof reply.code === "string" && /^[A-Z_]+$/.test(reply.code) ? reply.code : "INTERNAL";
    const message = typeof reply.message === "string" ? reply.message : "the YouTube tab reported a failure";
    throw new OpError(code, message);
  }
  const composed = await ctx.registry.composeById(tabId);
  if (!composed || !composed.script_attached) throw new OpError("TARGET_GONE", "The tab changed while the action ran");
  const result: YoutubeStateResult = { tab: composed };
  if ((request.op === "next" || request.op === "previous") && typeof reply.previous_video_id === "string" && /^[A-Za-z0-9_-]{1,32}$/.test(reply.previous_video_id)) {
    result.previous_video_id = reply.previous_video_id;
  }
  validateResult("youtube_state_result", result);
  return responseOk(request.request_id, result);
}

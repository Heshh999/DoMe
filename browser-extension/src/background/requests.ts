/**
 * bridge_request routing: list_tabs is answered from the tab registry; search navigates or opens a
 * tab itself (search.ts); tab ops are forwarded to the content script of the addressed tab with a
 * deadline. Every failure maps to a stable error code. Logs carry the op, request id, code and
 * duration only: never arguments (a search query), URLs or titles.
 */
import { OpError, toErrorObject } from "../shared/errors.ts";
import { log } from "../shared/log.ts";
import { BG_KIND, isTabOp, type OpArgs } from "../shared/messages.ts";
import { realTimers, type Timers } from "../shared/throttle.ts";
import { validateResult } from "../shared/validate.ts";
import { isYoutubeUrl, parseYoutubeUrl } from "../shared/youtubeUrl.ts";
import { isMutatingOp } from "../content/adapter.ts";
import type { ExtensionApi } from "./api.ts";
import { responseError, responseOk, type BridgeRequest, type BridgeResponse, type YoutubeStateResult } from "./frames.ts";
import { search } from "./search.ts";
import { NoReceiverError, ReceiverGoneError, replyFailure, TabTimeoutError, type TabRegistry } from "./tabs.ts";

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
  /** Clock for ops that wait in several steps (search, open_result after a page load); real time when absent. */
  timers?: Timers;
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
  const budget = computeBudget(request.timeout_ms);
  const { bgWaitMs, deadlineMs } = budget;
  const timers = ctx.timers ?? realTimers;
  const startedAt = timers.now();
  if (request.op === "list_tabs") {
    const tabs = await ctx.registry.list({ fresh: true });
    const result = { tabs };
    validateResult("tabs_result", result);
    return responseOk(request.request_id, result);
  }
  if (request.op === "search") return search(request, ctx, budget);
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
    if (err instanceof ReceiverGoneError && request.op === "open_result") return openedByPageLoad(request.request_id, tabId, args, ctx, bgWaitMs - (timers.now() - startedAt));
    if (err instanceof NoReceiverError) throw new OpError("TAB_NOT_CONTROLLABLE", "DoMe is not running in that tab yet; reload the YouTube tab on the PC");
    if (err instanceof TabTimeoutError) {
      if (isMutatingOp(request.op)) throw new OpError("OUTCOME_UNKNOWN", "The YouTube tab did not report back in time; the action may or may not have happened");
      throw new OpError("TAB_NOT_CONTROLLABLE", "The YouTube tab did not respond; reload it on the PC");
    }
    throw err;
  }

  const recorded = reply.token !== undefined && reply.state !== undefined ? ctx.registry.record(tabId, reply.token, reply.state) : null;
  if (recorded) ctx.onTabState?.(tabId);
  if (!reply.ok) throw replyFailure(reply);
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

const OPEN_RESULT_UNKNOWN = "The search result was clicked, but the YouTube tab loaded a new page before it reported which video opened; check the PC";

/**
 * open_result whose click made the browser load a whole new page instead of YouTube navigating in
 * place: the content script that clicked is gone before it could reply. Success still means seeing the
 * chosen video, so wait for the next attachment in the tab and accept it only on that video's watch
 * page. Without a video_id to look for, the outcome is unknown.
 */
async function openedByPageLoad(requestId: string, tabId: number, args: OpArgs, ctx: RequestContext, remainingMs: number): Promise<BridgeResponse> {
  const videoId = args.video_id;
  if (videoId === undefined) throw new OpError("OUTCOME_UNKNOWN", OPEN_RESULT_UNKNOWN);
  const accept = (url: string): boolean => {
    const facts = parseYoutubeUrl(url);
    return facts.context === "watch" && facts.video_id === videoId;
  };
  const outcome = await ctx.registry.waitForAttachment(tabId, { notToken: args.tab_token, accept }, remainingMs);
  if (outcome === "gone") throw new OpError("TARGET_GONE", "The tab closed while the video was opening");
  if (outcome === null) throw new OpError("OUTCOME_UNKNOWN", OPEN_RESULT_UNKNOWN);
  ctx.onTabState?.(tabId);
  const composed = await ctx.registry.composeById(tabId, outcome);
  if (!composed) throw new OpError("TARGET_GONE", "The tab closed or left YouTube while the video was opening");
  const result: YoutubeStateResult = { tab: composed };
  validateResult("youtube_state_result", result);
  return responseOk(requestId, result);
}

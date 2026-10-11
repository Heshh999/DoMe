/**
 * The `search` bridge op (youtube.search, protocol 1.2). Opens YouTube's results page for a
 * customer-typed query in the targeted tab (token-checked like a tab op) or in a new tab, waits for
 * the content script of THAT results page to attach (a new tab_token: the page loaded again), reads
 * its video results and answers youtube_search_result. The query and the URL built from it are never
 * logged, stored or echoed (rules.search_queries): they live only in this function's locals and in
 * the navigation itself, every error message here is fixed text, and the results tab is composed
 * without a title.
 */
import { OpError } from "../shared/errors.ts";
import { BG_KIND, type ContentReply } from "../shared/messages.ts";
import { sanitizeSearchItems } from "../shared/sanitize.ts";
import { realTimers } from "../shared/throttle.ts";
import { validateResult } from "../shared/validate.ts";
import { isResultsUrlFor, isYoutubeUrl, searchResultsUrl } from "../shared/youtubeUrl.ts";
import { responseOk, type BridgeRequest, type BridgeResponse, type YoutubeSearchResult } from "./frames.ts";
import type { Budget, RequestContext } from "./requests.ts";
import { NoReceiverError, replyFailure, TabTimeoutError } from "./tabs.ts";

/** Part of the budget kept for reading the results once the page attached (at most half of the budget). */
const READ_RESERVE_MS = 2500;
/** The content script's read deadline ends this long before the worker stops waiting for its reply. */
const READ_MARGIN_MS = 300;

/** The targeted tab must be a controllable YouTube tab whose content script still holds `tabToken`; returns that token. */
async function checkSearchTab(ctx: RequestContext, tabId: number, tabToken: string): Promise<string> {
  if (!Number.isInteger(tabId) || tabId < 0) throw new OpError("INVALID_PARAMETERS", "tab_id must be a non-negative integer");
  let tab: chrome.tabs.Tab;
  try {
    tab = await ctx.api.tabs.get(tabId);
  } catch {
    throw new OpError("TARGET_GONE", "That tab is no longer open");
  }
  if (!isYoutubeUrl(tab.url)) throw new OpError("TARGET_GONE", "That tab is no longer on YouTube");
  if (tab.discarded) throw new OpError("TAB_NOT_CONTROLLABLE", "The tab was unloaded by the browser; reload the YouTube tab on the PC");
  // The content script is the authority for its token; the cache can lag a reload.
  const current = await ctx.registry.probe(tabId).catch(() => null);
  if (!current) throw new OpError("TAB_NOT_CONTROLLABLE", "DoMe is not running in that tab yet; reload the YouTube tab on the PC");
  if (current.token !== tabToken) throw new OpError("TARGET_CHANGED", "The tab was reloaded since it was selected; choose it again");
  return current.token;
}

/** Navigate the targeted tab, or open a new one, to `url`; returns the tab id and the token its old page had. */
async function openResultsPage(ctx: RequestContext, url: string, tabId: number | undefined, tabToken: string | undefined): Promise<{ tabId: number; oldToken: string | undefined }> {
  if (tabId !== undefined && tabToken !== undefined) {
    const oldToken = await checkSearchTab(ctx, tabId, tabToken);
    try {
      await ctx.api.tabs.update(tabId, { url });
    } catch {
      throw new OpError("TARGET_GONE", "That tab is no longer open");
    }
    return { tabId, oldToken };
  }
  let created: chrome.tabs.Tab;
  try {
    created = await ctx.api.tabs.create({ url, active: true });
  } catch {
    throw new OpError("ACTION_UNAVAILABLE", "The browser did not open a new tab");
  }
  if (typeof created.id !== "number") throw new OpError("ACTION_UNAVAILABLE", "The browser did not open a new tab");
  return { tabId: created.id, oldToken: undefined };
}

export async function search(request: BridgeRequest, ctx: RequestContext, budget: Budget): Promise<BridgeResponse> {
  const timers = ctx.timers ?? realTimers;
  const startedAt = timers.now();
  const left = (): number => budget.bgWaitMs - (timers.now() - startedAt);
  const { query: raw, tab_id: requestedTab, tab_token: requestedToken } = request.args;
  const query = typeof raw === "string" ? raw.trim() : "";
  if (!query) throw new OpError("INVALID_PARAMETERS", "query must not be empty");
  if ((requestedTab === undefined) !== (requestedToken === undefined)) throw new OpError("INVALID_PARAMETERS", "tab_id and tab_token must be given together");

  const { tabId, oldToken } = await openResultsPage(ctx, searchResultsUrl(query), requestedTab, requestedToken);
  // From here on the browser was asked to navigate: a failure no longer means that nothing happened.
  const reserve = Math.min(READ_RESERVE_MS, left() / 2);
  const attached = await ctx.registry.waitForAttachment(tabId, { notToken: oldToken, accept: (url) => isResultsUrlFor(url, query) }, left() - reserve);
  if (attached === "gone") throw new OpError("TARGET_GONE", "The tab closed before the search results appeared");
  if (attached === null) throw new OpError("OUTCOME_UNKNOWN", "YouTube was asked to search, but the results page did not report back in time; check the PC");

  const waitMs = Math.max(0, left());
  let reply: ContentReply;
  try {
    reply = await ctx.registry.sendToTab(tabId, { kind: BG_KIND, type: "op", op: "read_results", args: { tab_token: attached.token }, deadline_ms: Math.max(0, waitMs - READ_MARGIN_MS) }, waitMs);
  } catch (err) {
    if (err instanceof NoReceiverError || err instanceof TabTimeoutError) throw new OpError("ACTION_UNAVAILABLE", "The search opened on the PC, but its results could not be read in time");
    throw err;
  }
  const recorded = reply.token !== undefined && reply.state !== undefined ? ctx.registry.record(tabId, reply.token, reply.state) : null;
  if (!reply.ok) throw replyFailure(reply);
  if (!recorded) throw new OpError("INTERNAL", "the YouTube tab reported results without a usable state");
  const composed = await ctx.registry.composeById(tabId, recorded);
  if (!composed) throw new OpError("TARGET_GONE", "The tab closed or left YouTube while the results were read");
  const result: YoutubeSearchResult = { tab: composed, results: sanitizeSearchItems(reply.results) as YoutubeSearchResult["results"] };
  validateResult("youtube_search_result", result);
  return responseOk(request.request_id, result);
}

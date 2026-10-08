/**
 * Per-tab attachment registry. The in-memory map is a cache: it is rebuilt from a probe of every
 * YouTube tab when the worker (re)starts, and the content script is the authority for its own
 * tab_token. Tabs without a responding content script are reported with script_attached:false.
 */
import { OpError } from "../shared/errors.ts";
import { log } from "../shared/log.ts";
import { BG_KIND, type BackgroundToContent, type ContentReply, type PlayerSnapshot } from "../shared/messages.ts";
import { sanitizeSnapshot, cleanText } from "../shared/sanitize.ts";
import { realTimers, withTimeout, type Timers } from "../shared/throttle.ts";
import { TOKEN_RE } from "../shared/token.ts";
import { validateYoutubeTab } from "../shared/validate.ts";
import { isYoutubeUrl, parseYoutubeUrl, YT_URL_PATTERN } from "../shared/youtubeUrl.ts";
import type { ExtensionApi } from "./api.ts";
import type { YoutubeTab } from "./frames.ts";

export interface Attachment {
  token: string;
  snapshot: PlayerSnapshot;
  updatedAt: number;
}

export class NoReceiverError extends Error {
  constructor() {
    super("no content script in tab");
    this.name = "NoReceiverError";
  }
}

export class TabTimeoutError extends Error {
  constructor() {
    super("content script did not reply in time");
    this.name = "TabTimeoutError";
  }
}

const NO_RECEIVER_RE = /Receiving end does not exist|Could not establish connection|No tab with id|message port closed/i;

export interface TabRegistryDeps {
  api: ExtensionApi;
  browserInstanceId: () => Promise<string>;
  onChanged: () => void;
  timers?: Timers;
  probeTimeoutMs?: number;
}

export class TabRegistry {
  private readonly attachments = new Map<number, Attachment>();
  private readonly timers: Timers;
  private readonly probeTimeoutMs: number;

  constructor(private readonly deps: TabRegistryDeps) {
    this.timers = deps.timers ?? realTimers;
    this.probeTimeoutMs = deps.probeTimeoutMs ?? 2000;
  }

  installListeners(): void {
    const { tabs } = this.deps.api;
    tabs.onRemoved.addListener((tabId) => {
      const had = this.attachments.delete(tabId);
      if (had) log.info("tab removed", { tab_id: tabId });
      this.deps.onChanged();
    });
    tabs.onReplaced.addListener((_added, removed) => {
      this.attachments.delete(removed);
      this.deps.onChanged();
    });
    tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
      const relevant = this.attachments.has(tabId) || isYoutubeUrl(tab.url) || isYoutubeUrl(changeInfo.url);
      if (!relevant) return;
      if (changeInfo.discarded === true) {
        // A discard kills the content script; a new attach message will follow if the tab comes back.
        this.attachments.delete(tabId);
      } else if ((changeInfo.status === "loading" || changeInfo.status === "complete") && this.attachments.has(tabId)) {
        // Chrome reports status "loading" for history.pushState too, i.e. for every in-site YouTube
        // navigation, during which the content script stays alive with the same tab_token. The
        // content script is the authority (attached/detached messages); here we only verify with a
        // cheap probe and drop the attachment when nobody answers any more (full reload, lost detach).
        void this.verify(tabId);
      }
      this.deps.onChanged();
    });
    tabs.onActivated.addListener(() => this.deps.onChanged());
  }

  attachedCount(): number {
    return this.attachments.size;
  }

  get(tabId: number): Attachment | undefined {
    return this.attachments.get(tabId);
  }

  /** Record what the content script reported; returns the attachment, or null when the payload was unusable. */
  record(tabId: number, token: unknown, rawSnapshot: unknown): Attachment | null {
    if (typeof token !== "string" || !TOKEN_RE.test(token)) return null;
    const snapshot = sanitizeSnapshot(rawSnapshot);
    if (!snapshot) return null;
    const attachment: Attachment = { token, snapshot, updatedAt: this.timers.now() };
    this.attachments.set(tabId, attachment);
    return attachment;
  }

  detach(tabId: number, token?: unknown): boolean {
    const current = this.attachments.get(tabId);
    if (!current) return false;
    if (token !== undefined && current.token !== token) return false;
    this.attachments.delete(tabId);
    return true;
  }

  /** Message the top frame of a tab with a timeout; distinguishes "no content script" from "slow". */
  async sendToTab(tabId: number, message: BackgroundToContent, timeoutMs: number): Promise<ContentReply> {
    const request = this.deps.api.tabs.sendMessage(tabId, message, { frameId: 0 }).then(
      (reply) => reply,
      (err: unknown) => {
        const text = err instanceof Error ? err.message : String(err);
        throw NO_RECEIVER_RE.test(text) ? new NoReceiverError() : new OpError("INTERNAL", `tab message failed: ${text.slice(0, 120)}`);
      },
    );
    const reply = await withTimeout(request, timeoutMs, () => new TabTimeoutError(), this.timers);
    if (typeof reply !== "object" || reply === null || typeof (reply as { ok?: unknown }).ok !== "boolean") {
      throw new OpError("INTERNAL", "unexpected reply from the YouTube tab");
    }
    return reply as ContentReply;
  }

  async probe(tabId: number): Promise<Attachment | null> {
    try {
      const reply = await this.sendToTab(tabId, { kind: BG_KIND, type: "probe" }, this.probeTimeoutMs);
      if (reply.ok) return this.record(tabId, reply.token, reply.state);
      return null;
    } catch (err) {
      if (err instanceof NoReceiverError || err instanceof TabTimeoutError) {
        this.attachments.delete(tabId);
        return null;
      }
      throw err;
    }
  }

  /** Re-probe a tab after a navigation event and announce a change only when the attachment actually changed. */
  private async verify(tabId: number): Promise<void> {
    const before = this.attachments.get(tabId);
    const after = await this.probe(tabId).catch(() => this.attachments.get(tabId) ?? null);
    if (before?.token !== after?.token) {
      log.info("tab attachment changed after navigation", { tab_id: tabId, attached: after !== null });
      this.deps.onChanged();
    }
  }

  async youtubeTabs(): Promise<chrome.tabs.Tab[]> {
    const tabs = await this.deps.api.tabs.query({ url: YT_URL_PATTERN });
    return tabs.filter((t) => typeof t.id === "number" && isYoutubeUrl(t.url)).slice(0, 32);
  }

  /**
   * Probe every YouTube tab (worker start) so the cache reflects reality before anything is reported.
   * Announces a change when done so a tabs_changed built from a partially rebuilt cache (the agent
   * acknowledged hello while probes were still outstanding) is corrected.
   */
  async rebuild(): Promise<void> {
    const tabs = await this.youtubeTabs();
    const live = new Set<number>();
    await Promise.all(
      tabs.map(async (t) => {
        live.add(t.id!);
        await this.probe(t.id!).catch(() => null);
      }),
    );
    for (const id of [...this.attachments.keys()]) if (!live.has(id)) this.attachments.delete(id);
    this.deps.onChanged();
  }

  /** Compose the contract's youtube_tab for one browser tab from tab metadata plus the attachment. */
  async compose(tab: chrome.tabs.Tab, attachment: Attachment | undefined): Promise<YoutubeTab> {
    const browserInstanceId = await this.deps.browserInstanceId();
    const base: YoutubeTab = {
      browser_instance_id: browserInstanceId,
      tab_id: tab.id!,
      script_attached: false,
      context: "other",
      ad_showing: false,
      is_live: false,
      in_playlist: false,
      active: tab.active === true,
    };
    if (tab.discarded) attachment = undefined;
    if (attachment) {
      const out: YoutubeTab = { ...base, ...attachment.snapshot, script_attached: true, tab_token: attachment.token };
      validateYoutubeTab(out);
      return out;
    }
    // Not attached: only what the URL and tab metadata tell us (untrusted display data).
    const facts = parseYoutubeUrl(tab.url ?? "");
    const out: YoutubeTab = { ...base, context: facts.context, in_playlist: facts.in_playlist };
    if (facts.video_id) out.video_id = facts.video_id;
    const title = cleanText(tab.title, 200);
    if (title) out.title = title;
    validateYoutubeTab(out);
    return out;
  }

  /** All YouTube tabs; `fresh` probes tabs for current state, otherwise the cache is used. */
  async list(options: { fresh: boolean }): Promise<YoutubeTab[]> {
    const tabs = await this.youtubeTabs();
    const out: YoutubeTab[] = [];
    await Promise.all(
      tabs.map(async (tab) => {
        const id = tab.id!;
        let attachment = this.attachments.get(id);
        if (options.fresh && !tab.discarded) {
          const probed = await this.probe(id).catch(() => null);
          attachment = probed ?? undefined;
        }
        try {
          out.push(await this.compose(tab, attachment));
        } catch (err) {
          log.warn("tab omitted from list", { tab_id: id, code: err instanceof OpError ? err.code : "INTERNAL" });
        }
      }),
    );
    out.sort((a, b) => a.tab_id - b.tab_id);
    return out;
  }

  /**
   * Compose one tab from current tab metadata. `attachment` lets a caller use the attachment it just
   * recorded from a reply instead of re-reading the cache after an await (a concurrent tab event
   * must never turn an observed success into TARGET_GONE).
   */
  async composeById(tabId: number, attachment?: Attachment): Promise<YoutubeTab | null> {
    let tab: chrome.tabs.Tab;
    try {
      tab = await this.deps.api.tabs.get(tabId);
    } catch {
      return null;
    }
    if (!isYoutubeUrl(tab.url)) return null;
    return this.compose(tab, attachment ?? this.attachments.get(tabId));
  }
}

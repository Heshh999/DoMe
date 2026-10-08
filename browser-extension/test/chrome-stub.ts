/**
 * TEST DOUBLE: an in-memory implementation of the chrome.* subset in src/background/api.ts.
 * Only ever imported by tests; the shipped code receives the real `chrome` object.
 */
import type { ChromeEvent, ExtensionApi, NativePort } from "../src/background/api.ts";

export class FakeEvent<F extends (...args: never[]) => unknown> implements ChromeEvent<F> {
  readonly listeners = new Set<F>();
  addListener(cb: F): void {
    this.listeners.add(cb);
  }
  removeListener(cb: F): void {
    this.listeners.delete(cb);
  }
  dispatch(...args: Parameters<F>): ReturnType<F>[] {
    return [...this.listeners].map((l) => l(...args) as ReturnType<F>);
  }
}

export class FakePort implements NativePort {
  readonly sent: unknown[] = [];
  readonly onMessage = new FakeEvent<(message: unknown) => void>();
  readonly onDisconnect = new FakeEvent<() => void>();
  disconnected = false;

  constructor(
    readonly name: string,
    private readonly chrome: FakeChrome,
  ) {}

  postMessage(message: unknown): void {
    if (this.disconnected) throw new Error("Attempting to use a disconnected port object");
    this.sent.push(structuredClone(message));
  }

  disconnect(): void {
    this.disconnected = true;
  }

  /** Agent → extension frame. */
  receive(frame: unknown): void {
    this.onMessage.dispatch(structuredClone(frame));
  }

  /** Host process ended; `lastErrorMessage` mirrors chrome.runtime.lastError during the callback. */
  fail(lastErrorMessage?: string): void {
    this.disconnected = true;
    this.chrome.runtime.lastError = lastErrorMessage ? { message: lastErrorMessage } : undefined;
    try {
      this.onDisconnect.dispatch();
    } finally {
      this.chrome.runtime.lastError = undefined;
    }
  }

  framesOfType<T = Record<string, unknown>>(type: string): T[] {
    return this.sent.filter((f) => (f as { type?: string }).type === type) as T[];
  }
}

export interface FakeTabSpec {
  id: number;
  url: string;
  title?: string;
  active?: boolean;
  discarded?: boolean;
}

export type ContentHandler = (message: unknown) => Promise<unknown> | unknown;

export class FakeChrome implements ExtensionApi {
  readonly ports: FakePort[] = [];
  readonly alarmsCreated: Array<{ name: string; info: { delayInMinutes?: number; periodInMinutes?: number } }> = [];
  readonly tabList: chrome.tabs.Tab[] = [];
  readonly contentHandlers = new Map<number, ContentHandler>();
  connectNativeError: Error | null = null;

  readonly runtime = {
    id: "abcdefghijklmnopabcdefghijklmnop",
    lastError: undefined as { message?: string } | undefined,
    getManifest: () => ({ version: "0.1.0-test" }),
    connectNative: (application: string): NativePort => {
      if (this.connectNativeError) throw this.connectNativeError;
      const port = new FakePort(application, this);
      this.ports.push(port);
      return port;
    },
    onMessage: new FakeEvent<(message: unknown, sender: chrome.runtime.MessageSender, sendResponse: (response: unknown) => void) => boolean | undefined | void>(),
    onStartup: new FakeEvent<() => void>(),
    onInstalled: new FakeEvent<(details: { reason: string }) => void>(),
  };

  readonly storage = {
    local: {
      get: async (keys: string | string[]): Promise<Record<string, unknown>> => {
        const list = Array.isArray(keys) ? keys : [keys];
        const out: Record<string, unknown> = {};
        for (const k of list) if (this.store.has(k)) out[k] = structuredClone(this.store.get(k));
        return out;
      },
      set: async (items: Record<string, unknown>): Promise<void> => {
        for (const [k, v] of Object.entries(items)) this.store.set(k, structuredClone(v));
      },
    },
  };

  readonly alarms = {
    create: async (name: string, info: { delayInMinutes?: number; periodInMinutes?: number }): Promise<void> => {
      this.alarmsCreated.push({ name, info });
    },
    clear: async (_name: string): Promise<boolean> => true,
    onAlarm: new FakeEvent<(alarm: { name: string }) => void>(),
  };

  readonly tabs = {
    query: async (info: { url?: string }): Promise<chrome.tabs.Tab[]> => {
      const pattern = info.url;
      return this.tabList.filter((t) => !pattern || matchPattern(pattern, t.url ?? "")).map((t) => ({ ...t }));
    },
    get: async (tabId: number): Promise<chrome.tabs.Tab> => {
      const tab = this.tabList.find((t) => t.id === tabId);
      if (!tab) throw new Error(`No tab with id: ${tabId}.`);
      return { ...tab };
    },
    sendMessage: async (tabId: number, message: unknown, _options?: { frameId?: number }): Promise<unknown> => {
      const handler = this.contentHandlers.get(tabId);
      if (!handler || !this.tabList.some((t) => t.id === tabId)) throw new Error("Could not establish connection. Receiving end does not exist.");
      return handler(structuredClone(message));
    },
    onRemoved: new FakeEvent<(tabId: number) => void>(),
    onUpdated: new FakeEvent<(tabId: number, changeInfo: chrome.tabs.OnUpdatedInfo, tab: chrome.tabs.Tab) => void>(),
    onActivated: new FakeEvent<(info: { tabId: number; windowId: number }) => void>(),
    onReplaced: new FakeEvent<(addedTabId: number, removedTabId: number) => void>(),
  };

  constructor(readonly store: Map<string, unknown> = new Map()) {}

  /** A second worker lifetime sharing the same storage (simulates service-worker termination + restart). */
  restartWorker(): FakeChrome {
    const next = new FakeChrome(this.store);
    next.tabList.push(...this.tabList.map((t) => ({ ...t })));
    for (const [id, h] of this.contentHandlers) next.contentHandlers.set(id, h);
    return next;
  }

  addTab(spec: FakeTabSpec, contentHandler?: ContentHandler): chrome.tabs.Tab {
    const tab = { id: spec.id, index: this.tabList.length, windowId: 1, url: spec.url, title: spec.title, active: spec.active ?? false, discarded: spec.discarded ?? false, pinned: false, highlighted: false, incognito: false, selected: false, autoDiscardable: true, groupId: -1, frozen: false } as unknown as chrome.tabs.Tab;
    this.tabList.push(tab);
    if (contentHandler) this.contentHandlers.set(spec.id, contentHandler);
    return tab;
  }

  removeTab(tabId: number): void {
    const idx = this.tabList.findIndex((t) => t.id === tabId);
    if (idx >= 0) this.tabList.splice(idx, 1);
    this.contentHandlers.delete(tabId);
    this.tabs.onRemoved.dispatch(tabId);
  }

  /**
   * tabs.onUpdated as Chrome fires it. Note that Chrome reports status "loading"/"complete" for
   * history.pushState navigations too, during which the content script survives; use reloadTab()
   * for a full (re)load that kills it.
   */
  updateTab(tabId: number, changeInfo: chrome.tabs.OnUpdatedInfo): void {
    const tab = this.tabList.find((t) => t.id === tabId);
    if (!tab) throw new Error(`no tab ${tabId}`);
    if (changeInfo.url !== undefined) tab.url = changeInfo.url;
    if (changeInfo.title !== undefined) tab.title = changeInfo.title;
    if (changeInfo.discarded !== undefined) tab.discarded = changeInfo.discarded;
    if (changeInfo.discarded === true) this.contentHandlers.delete(tabId);
    this.tabs.onUpdated.dispatch(tabId, changeInfo, { ...tab });
  }

  /** A full page (re)load: the old content script is gone before Chrome reports status "loading". */
  reloadTab(tabId: number, url?: string): void {
    this.contentHandlers.delete(tabId);
    this.updateTab(tabId, url === undefined ? { status: "loading" } : { status: "loading", url });
  }

  fireAlarm(name: string): void {
    this.alarms.onAlarm.dispatch({ name });
  }

  get lastPort(): FakePort {
    const port = this.ports[this.ports.length - 1];
    if (!port) throw new Error("no native port was opened");
    return port;
  }

  /** Deliver a message as if a content script in `tabId` (top frame) sent chrome.runtime.sendMessage. */
  async messageFromContent(tabId: number, message: unknown): Promise<unknown[]> {
    const tab = this.tabList.find((t) => t.id === tabId);
    if (!tab) throw new Error(`no tab ${tabId}`);
    const sender = { id: this.runtime.id, tab, frameId: 0, url: tab.url } as chrome.runtime.MessageSender;
    return this.dispatchRuntimeMessage(message, sender);
  }

  /** Deliver a message as if the popup sent it; resolves with the response. */
  messageFromPopup(message: unknown): Promise<unknown> {
    const sender = { id: this.runtime.id, url: `chrome-extension://${this.runtime.id}/popup.html` } as chrome.runtime.MessageSender;
    return new Promise((resolve) => {
      const results = this.runtime.onMessage.dispatch(message, sender, resolve);
      if (!results.includes(true)) resolve(undefined);
    });
  }

  private dispatchRuntimeMessage(message: unknown, sender: chrome.runtime.MessageSender): Promise<unknown[]> {
    const responses: unknown[] = [];
    this.runtime.onMessage.dispatch(message, sender, (r) => responses.push(r));
    return Promise.resolve(responses);
  }
}

function matchPattern(pattern: string, url: string): boolean {
  if (pattern.endsWith("/*")) return url.startsWith(pattern.slice(0, -1));
  return pattern === url;
}

/** Flush pending promise callbacks (microtasks) a few times. */
export async function flush(times = 5): Promise<void> {
  for (let i = 0; i < times; i++) await new Promise<void>((r) => queueMicrotask(r));
}

/**
 * The exact subset of the chrome.* API the service worker uses, as an interface so tests can
 * provide a stub (test/chrome-stub.ts) and so the manifest's permission set stays visibly minimal:
 * runtime, storage (local, and session for the reconnect pace), alarms, scripting (only to run the
 * extension's own content.js in YouTube tabs that have none), and the tabs methods that need no
 * "tabs" permission for hosts covered by host_permissions.
 */
export interface ChromeEvent<F extends (...args: never[]) => unknown> {
  addListener(callback: F): void;
  removeListener?(callback: F): void;
}

export interface NativePort {
  postMessage(message: unknown): void;
  disconnect(): void;
  onMessage: ChromeEvent<(message: unknown) => void>;
  onDisconnect: ChromeEvent<() => void>;
}

export interface StorageArea {
  get(keys: string | string[]): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
}

export interface ExtensionApi {
  runtime: {
    id: string;
    lastError?: { message?: string } | undefined;
    getManifest(): { version: string };
    connectNative(application: string): NativePort;
    onMessage: ChromeEvent<(message: unknown, sender: chrome.runtime.MessageSender, sendResponse: (response: unknown) => void) => boolean | undefined | void>;
    onStartup: ChromeEvent<() => void>;
    onInstalled: ChromeEvent<(details: { reason: string }) => void>;
  };
  storage: {
    local: StorageArea;
    /**
     * In memory while the extension is loaded: survives service-worker termination, and is cleared
     * when the browser restarts and when the extension is disabled, reloaded or updated.
     */
    session: StorageArea;
  };
  alarms: {
    create(name: string, info: { delayInMinutes?: number; periodInMinutes?: number }): Promise<void> | void;
    clear(name: string): Promise<boolean> | void;
    onAlarm: ChromeEvent<(alarm: { name: string }) => void>;
  };
  scripting: {
    executeScript(injection: { target: { tabId: number }; files: string[] }): Promise<unknown>;
  };
  tabs: {
    query(info: { url?: string }): Promise<chrome.tabs.Tab[]>;
    get(tabId: number): Promise<chrome.tabs.Tab>;
    sendMessage(tabId: number, message: unknown, options?: { frameId?: number }): Promise<unknown>;
    onRemoved: ChromeEvent<(tabId: number) => void>;
    onUpdated: ChromeEvent<(tabId: number, changeInfo: chrome.tabs.OnUpdatedInfo, tab: chrome.tabs.Tab) => void>;
    onActivated: ChromeEvent<(info: { tabId: number; windowId: number }) => void>;
    onReplaced: ChromeEvent<(addedTabId: number, removedTabId: number) => void>;
  };
}

export type BrowserKind = "chrome" | "edge" | "unknown";

/** Browser detection from the User-Agent Client Hints brands (navigator.userAgentData). */
export function detectBrowser(nav: { userAgentData?: { brands?: Array<{ brand: string }> } } | undefined): BrowserKind {
  const brands = nav?.userAgentData?.brands ?? [];
  if (brands.some((b) => b.brand === "Microsoft Edge")) return "edge";
  if (brands.some((b) => b.brand === "Google Chrome")) return "chrome";
  return "unknown";
}

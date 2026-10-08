/**
 * Service-worker composition root. Listener registration happens synchronously in createBackground()
 * (required by MV3); start() performs the asynchronous boot: load/create browser_instance_id,
 * connect the native port, probe YouTube tabs, then announce them once the agent acknowledged hello.
 */
import { log } from "../shared/log.ts";
import { isContentToBackground, isPopupToBackground, type ConnectionState, type StatusReport } from "../shared/messages.ts";
import { createDebounce, createThrottle, realTimers, type Throttled, type Timers } from "../shared/throttle.ts";
import { SUPPORTED_PROTOCOL_VERSIONS } from "../shared/version.ts";
import { isYoutubeUrl } from "../shared/youtubeUrl.ts";
import { detectBrowser, type BrowserKind, type ExtensionApi } from "./api.ts";
import { NativeConnection } from "./connection.ts";
import { helloFrame, playerStateEvent, tabsChangedEvent, type BridgeRequest } from "./frames.ts";
import { handleRequest } from "./requests.ts";
import { ExtensionStorage } from "./storage.ts";
import { TabRegistry } from "./tabs.ts";

export interface BackgroundOptions {
  timers?: Timers;
  browser?: BrowserKind;
  hostName?: string;
  tabsChangedDebounceMs?: number;
  playerStateMinIntervalMs?: number;
}

export interface Background {
  start(): Promise<void>;
  readonly connection: NativeConnection;
  readonly registry: TabRegistry;
  status(): Promise<StatusReport>;
}

export function createBackground(api: ExtensionApi, options: BackgroundOptions = {}): Background {
  const timers = options.timers ?? realTimers;
  const storage = new ExtensionStorage(api);
  const browser = options.browser ?? detectBrowser(typeof navigator === "undefined" ? undefined : (navigator as unknown as { userAgentData?: { brands?: Array<{ brand: string }> } }));
  const extensionVersion = api.runtime.getManifest().version;

  const playerStateThrottles = new Map<number, Throttled>();

  const registry = new TabRegistry({
    api,
    browserInstanceId: () => storage.browserInstanceId(),
    onChanged: () => tabsChanged.trigger(),
    timers,
  });

  const connection = new NativeConnection({
    api,
    timers,
    ...(options.hostName ? { hostName: options.hostName } : {}),
    buildHello: async () =>
      helloFrame({
        browserInstanceId: await storage.browserInstanceId(),
        browser,
        extensionVersion,
        protocolVersions: SUPPORTED_PROTOCOL_VERSIONS,
        profileLabel: await storage.profileLabel(),
      }),
    onRequest: (request: BridgeRequest) => {
      void handleRequest(request, { api, registry, onTabState: (tabId) => emitPlayerState(tabId) }).then((response) => connection.post(response));
    },
    onConnected: () => {
      void sendTabsChanged();
    },
    onStateChange: (state: ConnectionState) => {
      void storage.setConnectionState(state);
    },
  });

  async function sendTabsChanged(): Promise<void> {
    if (!connection.connected) return;
    try {
      const tabs = await registry.list({ fresh: false });
      connection.post(tabsChangedEvent(tabs));
    } catch (err) {
      log.warn("tabs_changed not sent", { error: err instanceof Error ? err.name : "unknown" });
    }
  }

  const tabsChanged = createDebounce(options.tabsChangedDebounceMs ?? 300, () => void sendTabsChanged(), timers);

  function emitPlayerState(tabId: number): void {
    let throttle = playerStateThrottles.get(tabId);
    if (!throttle) {
      throttle = createThrottle(
        options.playerStateMinIntervalMs ?? 500,
        () => {
          void (async () => {
            if (!connection.connected) return;
            const tab = await registry.composeById(tabId).catch(() => null);
            if (!tab || !tab.script_attached) {
              playerStateThrottles.delete(tabId);
              return;
            }
            connection.post(playerStateEvent(tab));
          })();
        },
        timers,
      );
      playerStateThrottles.set(tabId, throttle);
    }
    throttle.trigger();
  }

  async function status(): Promise<StatusReport> {
    return {
      connection: connection.current(),
      browser_instance_id: await storage.browserInstanceId(),
      profile_label: await storage.profileLabel(),
      extension_id: api.runtime.id,
      extension_version: extensionVersion,
      browser,
      attached_tabs: registry.attachedCount(),
    };
  }

  // ----- synchronous listener registration -----------------------------------------------------
  registry.installListeners();
  connection.installListeners();
  api.runtime.onInstalled.addListener(() => void connection.connect());
  api.runtime.onStartup.addListener(() => void connection.connect());
  api.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (sender.id !== api.runtime.id) return undefined;
    if (isContentToBackground(message)) {
      const tabId = sender.tab?.id;
      if (typeof tabId !== "number" || sender.frameId !== 0 || !isYoutubeUrl(sender.url ?? sender.tab?.url)) return undefined;
      if (message.type === "detached") {
        if (registry.detach(tabId, message.token)) {
          playerStateThrottles.get(tabId)?.cancel();
          playerStateThrottles.delete(tabId);
          tabsChanged.trigger();
        }
        return undefined;
      }
      const previous = registry.get(tabId);
      if (!registry.record(tabId, message.token, message.state)) {
        log.warn("content message rejected", { tab_id: tabId, type: message.type });
        return undefined;
      }
      if (message.type === "attached" || !previous || previous.token !== message.token) tabsChanged.trigger();
      else emitPlayerState(tabId);
      return undefined;
    }
    if (isPopupToBackground(message) && sender.tab === undefined) {
      void (async () => {
        if (message.type === "status") return status();
        if (message.type === "set_profile_label") {
          await storage.setProfileLabel(message.value);
          if (connection.connected) await connection.sendHello();
          return status();
        }
        if (!connection.connected) connection.disconnect();
        await connection.connect();
        return status();
      })().then(sendResponse, () => sendResponse(null));
      return true;
    }
    return undefined;
  });

  return {
    connection,
    registry,
    status,
    async start() {
      const id = await storage.browserInstanceId();
      log.info("worker started", { browser_instance_id: id, browser, version: extensionVersion });
      await Promise.all([connection.connect(), registry.rebuild()]);
      await sendTabsChanged();
    },
  };
}

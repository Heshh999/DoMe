/**
 * Service-worker registration (vite-plugin-pwa, `registerType: "autoUpdate"`). Registered from a module instead of an
 * inline script because cloud-api serves the app with `script-src 'self'`. The worker precaches the built shell only;
 * nothing under /v1 or /ws is ever cached (vite.config.ts).
 *
 * Keeping an open app current (DECISIONS 92, lib/appUpdate.ts): the app looks for a newer version when it starts, on
 * every return to the front (at most once a minute) and every 30 minutes while in front. A newer worker installs and
 * takes control by itself (`autoUpdate`), but the page reloads only when the app says so (`onNeedReload`, and the
 * browser's own `controllerchange`, which also covers the updates Workbox no longer watches): at once at a safe moment, otherwise when Screen view, the touchpad, pairing, a payment return, a PC confirmation or the Assistant
 * is done (store/busy.ts), with a small notice meanwhile (components/AppUpdateNotice.tsx).
 *
 * `requestAppUpdate()` asks for the newer app at once (design screen-view-2a D28): after a deploy, an open app still
 * runs the old bundle until it is reloaded, and that bundle refuses a REST answer with a field it does not know. The
 * devices store calls it when that happens and says "A newer DoMe is ready" instead of an error.
 */
import { registerSW } from "virtual:pwa-register";

import { AppUpdater, NO_UPDATE } from "./lib/appUpdate.ts";
import { log } from "./lib/log.ts";
import { useAppUpdateStore } from "./store/appUpdate.ts";
import { currentBusy, subscribeBusy } from "./store/busy.ts";

/**
 * What `registerSW()` returned. In prompt mode it activates a waiting worker; with `autoUpdate` (this app) it does
 * nothing at all (vite-plugin-pwa's register.js), so it is never the only thing `requestAppUpdate()` does.
 */
let updateSW: ((reloadPage?: boolean) => Promise<void>) | null = null;
let updater: AppUpdater | null = null;
let registration: ServiceWorkerRegistration | null = null;
let stopWatchingController: (() => void) | null = null;

export interface RegisterOptions {
  /** Tests: what loading the newer version does (default `location.reload()`). */
  reload?: () => void;
}

/**
 * The browser's own signal that a newer worker took control of this page (DECISIONS 92 (3)). vite-plugin-pwa calls
 * `onNeedReload` only from Workbox's "activated" event, and Workbox sees that only for a worker whose install it
 * watched: it stops watching after the first update it labels external (any update found more than a minute after
 * registering, the usual case for an app iOS keeps alive), and it starts watching only once `register()` has
 * finished (a cold start's own update check can be quicker). A second newer version, after the first one's install
 * was cut short, or one found that early, would take control with no `onNeedReload`, and the page would keep the
 * old bundle. `controllerchange` fires for every change of controller. The first one on a page that had no
 * controller is the first install claiming it (the page already runs that bundle), so it does not count.
 */
function watchController(container: ServiceWorkerContainer, onNewVersion: () => void): () => void {
  let controlled = Boolean(container.controller);
  const onChange = () => {
    if (controlled) onNewVersion();
    controlled = true;
  };
  container.addEventListener("controllerchange", onChange);
  return () => container.removeEventListener("controllerchange", onChange);
}

export function registerServiceWorker(options: RegisterOptions = {}): void {
  if (!("serviceWorker" in navigator)) return;
  if (import.meta.env.DEV) return; // devOptions.enabled is false; nothing to register in development
  try {
    const app = new AppUpdater({
      check: async () => {
        const r = registration ?? (await navigator.serviceWorker.getRegistration()) ?? null;
        await r?.update();
      },
      reload: options.reload ?? (() => window.location.reload()),
      busy: currentBusy,
      subscribeBusy,
      onChange: (view) => useAppUpdateStore.setState(view),
    });
    updater = app;
    app.start();
    stopWatchingController = watchController(navigator.serviceWorker, () => app.onNewVersion());
    updateSW = registerSW({
      immediate: true,
      onRegisteredSW(_url: string, r: ServiceWorkerRegistration | undefined) {
        registration = r ?? null;
        // the start check: the page may be one Safari brought back without loading it again
        if (r) app.check("start");
      },
      // the newer worker controls the page: the app, not the plugin, decides when to reload into it (the same arrival
      // also comes through watchController above; the second signal changes nothing)
      onNeedReload() {
        app.onNewVersion();
      },
      onRegisterError(error: unknown) {
        log.warn("pwa.register_failed", { name: error instanceof Error ? error.name : "error" });
      },
      onOfflineReady() {
        log.info("pwa.offline_ready");
      },
    });
  } catch (e) {
    log.warn("pwa.register_threw", { name: e instanceof Error ? e.name : "error" });
  }
}

/**
 * Fetch the newer app now: the registration checks for a newer worker (`registration.update()`); with `autoUpdate` a
 * newer one installs and activates, and the page reloads at the next safe moment (above). The registration's own
 * update function is called as well (it matters only in prompt mode). A no-op in development and where service
 * workers do not exist. Never throws.
 */
export function requestAppUpdate(): void {
  if (import.meta.env.DEV) return;
  if (!("serviceWorker" in navigator)) return;
  const failed = (e: unknown) => log.info("pwa.update_failed", { name: e instanceof Error ? e.name : "error" });
  try {
    void navigator.serviceWorker
      .getRegistration()
      .then((r) => r?.update())
      .catch(failed);
    if (updateSW) void updateSW(true).catch(failed);
    log.info("pwa.update_requested", { registered: updateSW !== null });
  } catch (e) {
    failed(e);
  }
}

/** "Update now" on the notice, after the person confirmed it ends what is running. */
export function applyAppUpdateNow(): void {
  updater?.applyNow();
}

/** Tests only: forget the registration, its update function and the update checks. */
export function resetPwaForTests(): void {
  updater?.stop();
  updater = null;
  stopWatchingController?.();
  stopWatchingController = null;
  updateSW = null;
  registration = null;
  useAppUpdateStore.setState(NO_UPDATE);
}

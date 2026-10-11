/**
 * Approved applications: the list comes from the PC (`app.list`); the phone only ever sends app ids
 * and window ids back. Close goes through the confirmation flow on the PC. Window controls (front,
 * minimise, maximise, restore, full screen) act only on an app that shows a window and never start
 * one: an app can run with none (Edge's Startup boost, a player closed to the tray). Opening or
 * closing an app re-reads the list, so the buttons follow what the PC shows now.
 */
import { useEffect, useMemo, useRef } from "react";

import type { results } from "@dome/protocol";

import { refreshApprovedApps, useApprovedApps, useLabels, useSelectedPc, useSend } from "../../app/hooks.ts";
import { CommandOutcome } from "../../components/CommandOutcome.tsx";
import { FailureLinks } from "../../components/FailureLinks.tsx";
import { PcSwitcher } from "../../components/PcSwitcher.tsx";
import { Button, Card, EmptyState, Notice, Pill, Steps } from "../../components/ui.tsx";
import type { CommandRecord } from "../../lib/commands.ts";
import { errorMessage, recoverySteps } from "../../lib/labels.ts";
import { relativeTime } from "../../lib/format.ts";
import { useLiveStore } from "../../store/live.ts";

type WindowState = results.AppWindowStateResult;

const WINDOW_STATE_ACTIONS: ReadonlySet<string> = new Set(["app.maximize", "app.restore", "app.set_fullscreen"]);
/** After Open or Close: read the app list again now and then twice more, as the app's first window can take a while. */
const APP_LIST_REREAD_MS: readonly number[] = [1_500, 5_000];

/**
 * The last window state the PC read back (app_window_state_result, from a success or a failure that carried the
 * best-known state), per app and per window. pc_state and app.list do not report full screen, so this is the only
 * basis for the Full screen toggle; unknown means "not full screen".
 */
function lastWindowStates(commands: CommandRecord[], pcId: string | null): { byApp: Record<string, WindowState>; byWindow: Record<string, WindowState> } {
  const byApp: Record<string, WindowState> = {};
  const byWindow: Record<string, WindowState> = {};
  for (const c of commands) {
    // commands are newest first, so the first state seen for an app or window is the latest one
    if (c.pcId !== pcId || !WINDOW_STATE_ACTIONS.has(c.action)) continue;
    const r = c.terminal?.result as WindowState | null | undefined;
    if (!r) continue;
    byApp[r.app_id] ??= r;
    byWindow[`${r.app_id}:${r.window_id}`] ??= r;
  }
  return { byApp, byWindow };
}

export function AppsPage() {
  const { pc, pcId, pcName, live, canControl, fresh } = useSelectedPc();
  const { send, record, sendError, clearError } = useSend(pcId);
  const labels = useLabels(pcId);
  // Loads on entry and again whenever the app returns to the foreground, so apps added on the PC appear by themselves.
  const { apps: cachedApps, at, listing } = useApprovedApps(pcId);
  const commands = useLiveStore((s) => s.commands);
  const states = useMemo(() => lastWindowStates(commands, pcId), [commands, pcId]);
  // The newest app.launch / app.close that succeeded on this PC (from this page or any other).
  const opened = useMemo(() => commands.find((c) => c.pcId === pcId && (c.action === "app.launch" || c.action === "app.close") && c.terminal?.state === "succeeded")?.commandId ?? null, [commands, pcId]);
  const rereadFor = useRef<string | null | undefined>(undefined);
  const rereadTimers = useRef<Array<ReturnType<typeof setTimeout>>>([]);
  useEffect(() => () => rereadTimers.current.forEach(clearTimeout), []);
  useEffect(() => {
    // An app was opened or closed: which apps run, and with which windows, changed on the PC. (One that finished
    // before this page opened is already in the list the page reads on entry.)
    const first = rereadFor.current === undefined;
    if (rereadFor.current === opened) return;
    rereadFor.current = opened;
    if (first || !pcId || !opened) return;
    void refreshApprovedApps(pcId);
    rereadTimers.current.push(...APP_LIST_REREAD_MS.map((ms) => setTimeout(() => void refreshApprovedApps(pcId), ms)));
  }, [pcId, opened]);

  const apps = cachedApps ?? [];
  const locked = live.state?.session_locked === true;
  const appsBlocked = !canControl || locked;
  const fullscreenUnsupported = record?.action === "app.set_fullscreen" && record.terminal?.error?.code === "FULLSCREEN_UNSUPPORTED" && record.target ? record.target : null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Apps</h1>
        <Button size="md" variant="ghost" disabled={!canControl} onClick={() => void send("app.list", {}, null, "system")}>
          Refresh
        </Button>
      </div>
      <PcSwitcher pc={pc} live={live} />
      {locked ? <Notice tone="info">The PC is locked. App controls resume when it is unlocked.</Notice> : null}
      {!fresh && apps.length === 0 ? <Notice tone="info">The list of approved apps comes from the PC and is available while it is online.</Notice> : null}
      {listing && !listing.terminal && cachedApps === null ? <p className="text-sm text-text-muted">Asking the PC for its approved apps…</p> : null}
      {listing?.terminal && listing.terminal.state !== "succeeded" ? (
        <Notice tone="warning" title="Could not load the app list">
          <p>{errorMessage(listing.terminal.error)}</p>
        </Notice>
      ) : null}
      {cachedApps !== null && apps.length === 0 ? (
        <EmptyState title="No approved apps yet">
          <p>Apps are approved on the PC itself, never from the phone: on the PC, open the DoMe tray menu, choose Add an app…, and pick the program you want to open from here.</p>
          <p>It shows up on this page by itself when you come back to it.</p>
        </EmptyState>
      ) : null}
      <ul className="space-y-3">
        {apps.map((app) => {
          const fullscreen = states.byApp[app.app_id]?.fullscreen === true;
          // Running with no window: window commands would only wait and fail on the PC, so Open is the way forward.
          const windowless = app.running && app.windows.length === 0;
          const windowBlocked = appsBlocked || !app.running || windowless;
          return (
            <Card key={app.app_id} as="li">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-semibold truncate">{app.display_name}</p>
                  <p className="text-xs text-text-muted">{windowless ? "Running in the background · no open window" : app.running ? `Running · ${app.windows.length} window${app.windows.length === 1 ? "" : "s"}` : "Not running"}</p>
                </div>
                <Pill tone={windowless ? "neutral" : app.running ? "success" : "neutral"}>{windowless ? "Background" : app.running ? "Running" : "Closed"}</Pill>
              </div>
              {windowless ? <p className="mt-2 text-sm text-text-muted">It has no window to control right now (for example it is only in the system tray). Tap Open to show it; the window buttons work once it has one.</p> : null}
              <div className="mt-3 grid grid-cols-2 gap-2">
                <Button disabled={appsBlocked} onClick={() => void send("app.launch", { app_id: app.app_id }, null, "button")}>
                  {app.running && !windowless ? "Open again" : "Open"}
                </Button>
                <Button disabled={windowBlocked} onClick={() => void send("app.focus", {}, { app_id: app.app_id }, "button")}>
                  Bring to front
                </Button>
                <Button disabled={windowBlocked} onClick={() => void send("app.minimize", {}, { app_id: app.app_id }, "button")}>
                  Minimise
                </Button>
                <Button disabled={windowBlocked} onClick={() => void send("app.maximize", {}, { app_id: app.app_id }, "button")}>
                  Maximise
                </Button>
                <Button disabled={windowBlocked} title="Show the window at normal size" onClick={() => void send("app.restore", {}, { app_id: app.app_id }, "button")}>
                  Restore
                </Button>
                <Button disabled={windowBlocked} aria-pressed={fullscreen} title="Presses F11 in the app on the PC" onClick={() => void send("app.set_fullscreen", { enabled: !fullscreen }, { app_id: app.app_id }, "button")}>
                  {fullscreen ? "Leave full screen" : "Full screen"}
                </Button>
                <Button variant="danger" className="col-span-2" disabled={windowBlocked} onClick={() => void send("app.close", {}, { app_id: app.app_id }, "button")}>
                  Close…
                </Button>
              </div>
              {app.windows.length > 1 ? (
                <details className="mt-3 text-sm">
                  <summary className="cursor-pointer text-text-muted">Windows</summary>
                  <ul className="mt-2 space-y-2">
                    {app.windows.map((w) => {
                      const target = { app_id: app.app_id, window_id: w.window_id };
                      const windowFullscreen = states.byWindow[`${app.app_id}:${w.window_id}`]?.fullscreen === true;
                      return (
                        <li key={w.window_id} className="space-y-1">
                          <span className="block truncate">{w.title || "Untitled window"}</span>
                          <span className="flex flex-wrap gap-1">
                            <Button size="md" disabled={appsBlocked} onClick={() => void send("app.focus", {}, target, "button")}>
                              Front
                            </Button>
                            <Button size="md" disabled={appsBlocked} title="Maximise this window" onClick={() => void send("app.maximize", {}, target, "button")}>
                              Max
                            </Button>
                            <Button size="md" disabled={appsBlocked} title="Show this window at normal size" onClick={() => void send("app.restore", {}, target, "button")}>
                              Restore
                            </Button>
                            <Button size="md" disabled={appsBlocked} aria-pressed={windowFullscreen} title="Presses F11 in this window on the PC" onClick={() => void send("app.set_fullscreen", { enabled: !windowFullscreen }, target, "button")}>
                              {windowFullscreen ? "Leave full" : "Full"}
                            </Button>
                            <Button size="md" variant="danger" disabled={appsBlocked} onClick={() => void send("app.close", {}, target, "button")}>
                              Close…
                            </Button>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </details>
              ) : null}
            </Card>
          );
        })}
      </ul>
      {at !== null ? <p className="text-xs text-text-faint">List from the PC {relativeTime(new Date(at).toISOString())}. Close asks you to confirm and never force-quits; unsaved work stays open on the PC. Full screen presses F11 in the app.</p> : null}
      {sendError ? (
        <Notice tone="danger" title="Not sent">
          <p>{errorMessage(sendError)}</p>
          <Steps steps={recoverySteps(sendError.code)} />
          <FailureLinks code={sendError.code} />
          <Button size="md" variant="ghost" onClick={clearError}>
            Dismiss
          </Button>
        </Notice>
      ) : null}
      {record && record.action !== "app.list" ? (
        <Card>
          <CommandOutcome record={record} pcName={pcName} labels={labels} />
          {fullscreenUnsupported ? (
            // A separate, visible command: DoMe never maximises in place of full screen by itself.
            <Button className="mt-3" full disabled={appsBlocked} onClick={() => void send("app.maximize", {}, fullscreenUnsupported, "button")}>
              Maximise instead
            </Button>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

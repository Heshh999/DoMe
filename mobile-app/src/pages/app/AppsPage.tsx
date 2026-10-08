/**
 * Approved applications: the list comes from the PC (`app.list`); the phone only ever sends app ids
 * and window ids back. Close goes through the confirmation flow on the PC.
 */
import { useEffect, useRef } from "react";

import { useLabels, useSelectedPc, useSend } from "../../app/hooks.ts";
import { getRuntime } from "../../app/runtime.ts";
import { CommandOutcome } from "../../components/CommandOutcome.tsx";
import { PcSwitcher } from "../../components/PcSwitcher.tsx";
import { Button, Card, EmptyState, Notice, Pill, Steps } from "../../components/ui.tsx";
import { errorMessage, recoverySteps } from "../../lib/labels.ts";
import { relativeTime } from "../../lib/format.ts";
import { useLiveStore, type AppEntry } from "../../store/live.ts";

export function AppsPage() {
  const { pc, pcId, pcName, live, canControl, fresh } = useSelectedPc();
  const { send, record, sendError, clearError } = useSend(pcId);
  const labels = useLabels(pcId);
  const cached = useLiveStore((s) => (pcId ? s.appsByPc[pcId] : undefined));
  const setApps = useLiveStore((s) => s.setApps);
  const listing = useLiveStore((s) => s.commands.find((c) => c.pcId === pcId && c.action === "app.list"));
  const requested = useRef<string | null>(null);

  // Refresh the list once per PC when controls are available (and whenever the user asks).
  useEffect(() => {
    if (!pcId || !canControl || requested.current === pcId) return;
    requested.current = pcId;
    void getRuntime()
      .send({ pcId, action: "app.list", source: "system" })
      .catch(() => {
        requested.current = null;
      });
  }, [pcId, canControl]);
  useEffect(() => {
    if (listing?.terminal?.state === "succeeded" && listing.terminal.result && pcId) {
      const apps = (listing.terminal.result as { apps: AppEntry[] }).apps;
      if (!cached || cached.at < listing.terminal.durationMs + listing.createdAt) setApps(pcId, apps);
    }
  }, [listing, pcId, setApps, cached]);

  const apps = cached?.apps ?? [];
  const locked = live.state?.session_locked === true;
  const appsBlocked = !canControl || locked;

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
      {listing && !listing.terminal && !cached ? <p className="text-sm text-text-muted">Asking the PC for its approved apps…</p> : null}
      {listing?.terminal && listing.terminal.state !== "succeeded" ? (
        <Notice tone="warning" title="Could not load the app list">
          <p>{errorMessage(listing.terminal.error)}</p>
        </Notice>
      ) : null}
      {cached && apps.length === 0 ? (
        <EmptyState title="No approved apps yet">
          <p>Apps are approved on the PC itself, never from the phone: open the DoMe tray menu on the PC, choose Approved apps, and add the programs you want to open from here.</p>
        </EmptyState>
      ) : null}
      <ul className="space-y-3">
        {apps.map((app) => (
          <Card key={app.app_id} as="li">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="font-semibold truncate">{app.display_name}</p>
                <p className="text-xs text-text-muted">{app.running ? `Running · ${app.windows.length} window${app.windows.length === 1 ? "" : "s"}` : "Not running"}</p>
              </div>
              <Pill tone={app.running ? "success" : "neutral"}>{app.running ? "Running" : "Closed"}</Pill>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <Button disabled={appsBlocked} onClick={() => void send("app.launch", { app_id: app.app_id }, null, "button")}>
                {app.running ? "Open again" : "Open"}
              </Button>
              <Button disabled={appsBlocked || !app.running} onClick={() => void send("app.focus", {}, { app_id: app.app_id }, "button")}>
                Bring to front
              </Button>
              <Button disabled={appsBlocked || !app.running} onClick={() => void send("app.minimize", {}, { app_id: app.app_id }, "button")}>
                Minimise
              </Button>
              <Button variant="danger" disabled={appsBlocked || !app.running} onClick={() => void send("app.close", {}, { app_id: app.app_id }, "button")}>
                Close…
              </Button>
            </div>
            {app.windows.length > 1 ? (
              <details className="mt-3 text-sm">
                <summary className="cursor-pointer text-text-muted">Windows</summary>
                <ul className="mt-2 space-y-1">
                  {app.windows.map((w) => (
                    <li key={w.window_id} className="flex items-center justify-between gap-2">
                      <span className="truncate">{w.title || "Untitled window"}</span>
                      <span className="flex gap-1 shrink-0">
                        <Button size="md" disabled={appsBlocked} onClick={() => void send("app.focus", {}, { app_id: app.app_id, window_id: w.window_id }, "button")}>
                          Front
                        </Button>
                        <Button size="md" variant="danger" disabled={appsBlocked} onClick={() => void send("app.close", {}, { app_id: app.app_id, window_id: w.window_id }, "button")}>
                          Close…
                        </Button>
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </Card>
        ))}
      </ul>
      {cached ? <p className="text-xs text-text-faint">List from the PC {relativeTime(new Date(cached.at).toISOString())}. Close asks you to confirm and never force-quits; unsaved work stays open on the PC.</p> : null}
      {sendError ? (
        <Notice tone="danger" title="Not sent">
          <p>{errorMessage(sendError)}</p>
          <Steps steps={recoverySteps(sendError.code)} />
          <Button size="md" variant="ghost" onClick={clearError}>
            Dismiss
          </Button>
        </Notice>
      ) : null}
      {record && record.action !== "app.list" ? (
        <Card>
          <CommandOutcome record={record} pcName={pcName} labels={labels} />
        </Card>
      ) : null}
    </div>
  );
}

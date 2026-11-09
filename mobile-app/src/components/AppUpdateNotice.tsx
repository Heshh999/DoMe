/**
 * "A newer DoMe is ready." (DECISIONS 92). Shown only while a newer version waits for something busy to end (a safe
 * moment reloads at once and never shows this): a small card over the bottom of the screen, above the tab bar and over
 * the Screen view stage, that takes touches only on itself. It names what the update waits for; "Update now" first says
 * what it ends, and only "End it and update" reloads. "Hide" puts it away until something else is what it waits for;
 * the update still happens when the busy state ends.
 */
import { useState } from "react";

import { UPDATE_COPY } from "../lib/appUpdate.ts";
import { applyAppUpdateNow } from "../pwa.ts";
import { useAppUpdateStore } from "../store/appUpdate.ts";
import type { BusyKind } from "../store/busy.ts";
import { Button } from "./ui.tsx";

export function AppUpdateNotice({ onUpdateNow = applyAppUpdateNow }: { onUpdateNow?: () => void }) {
  const ready = useAppUpdateStore((s) => s.ready);
  const waitingFor = useAppUpdateStore((s) => s.waitingFor);
  const [hiddenFor, setHiddenFor] = useState<BusyKind | null>(null);
  if (!ready || waitingFor === null || hiddenFor === waitingFor) return null;
  // keyed by what it waits for: a new reason starts again without the confirmation step open
  return <Card key={waitingFor} kind={waitingFor} onUpdateNow={onUpdateNow} onHide={() => setHiddenFor(waitingFor)} />;
}

function Card({ kind, onUpdateNow, onHide }: { kind: BusyKind; onUpdateNow: () => void; onHide: () => void }) {
  const [confirming, setConfirming] = useState(false);
  return (
    <div className="pointer-events-none fixed inset-x-0 z-[60] flex justify-center px-4" style={{ bottom: "calc(var(--safe-bottom) + 72px)" }}>
      <div role="status" data-testid="app-update-notice" data-kind={kind} className="pointer-events-auto w-full max-w-sm rounded-card border border-border bg-bg-elevated px-4 py-3 text-sm text-text shadow-lg">
        <p>
          <span className="font-semibold">{UPDATE_COPY.ready}</span> {UPDATE_COPY.when[kind]}
        </p>
        {confirming ? (
          <>
            <p className="mt-1 text-text-muted" data-testid="app-update-ends">
              {UPDATE_COPY.ends[kind]}
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="primary" onClick={onUpdateNow}>
                {UPDATE_COPY.confirm}
              </Button>
              <Button size="sm" variant="quiet" onClick={() => setConfirming(false)}>
                {UPDATE_COPY.notNow}
              </Button>
            </div>
          </>
        ) : (
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={() => setConfirming(true)}>
              {UPDATE_COPY.updateNow}
            </Button>
            <Button size="sm" variant="quiet" onClick={onHide}>
              {UPDATE_COPY.hide}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

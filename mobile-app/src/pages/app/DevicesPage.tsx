/**
 * Devices and permissions: PCs (rename, enable/disable, unlink), controller installations (this
 * phone highlighted by its kid, rename, revoke), grants per PC, and the entry to pairing.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router";

import type { rest } from "@dome/protocol";

import { api, ApiError } from "../../lib/api.ts";
import { getControllerIdentity, KeyStorageError } from "../../lib/controllerKey.ts";
import { absoluteTime, relativeTime } from "../../lib/format.ts";
import { capabilityLabel, errorMessage } from "../../lib/labels.ts";
import { connectionOf } from "../../lib/connection.ts";
import { Button, Card, EmptyState, inputClass, Notice, Pill } from "../../components/ui.tsx";
import { useDevicesStore } from "../../store/devices.ts";
import { useLiveStore } from "../../store/live.ts";
import { useSessionStore } from "../../store/session.ts";

function useThisKid(): { kid: string | null; problem: string | null } {
  const [kid, setKid] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    getControllerIdentity()
      .then((i) => setKid(i.kid))
      .catch((e: unknown) => setProblem(e instanceof KeyStorageError ? e.message : "This browser cannot keep a signing key, so it cannot be paired."));
  }, []);
  return { kid, problem };
}

function RenameInline({ value, onSave, label }: { value: string; onSave: (v: string) => Promise<void>; label: string }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(value);
  const [busy, setBusy] = useState(false);
  if (!editing) {
    return (
      <Button size="md" variant="ghost" onClick={() => (setText(value), setEditing(true))} aria-label={`Rename ${label}`}>
        Rename
      </Button>
    );
  }
  return (
    <form
      className="flex gap-2 w-full"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!text.trim()) return;
        setBusy(true);
        try {
          await onSave(text.trim());
          setEditing(false);
        } finally {
          setBusy(false);
        }
      }}
    >
      <input className={inputClass} value={text} onChange={(e) => setText(e.currentTarget.value)} maxLength={64} aria-label={`New name for ${label}`} autoFocus />
      <Button type="submit" variant="primary" busy={busy}>
        Save
      </Button>
      <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
        Cancel
      </Button>
    </form>
  );
}

function ConfirmButton({ label, confirmLabel, onConfirm, variant = "danger" }: { label: string; confirmLabel: string; onConfirm: () => Promise<void>; variant?: "danger" | "secondary" }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!armed)
    return (
      <Button size="md" variant={variant} onClick={() => setArmed(true)}>
        {label}
      </Button>
    );
  return (
    <span className="inline-flex gap-2">
      <Button size="md" variant="ghost" onClick={() => setArmed(false)}>
        Keep
      </Button>
      <Button
        size="md"
        variant="danger"
        busy={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await onConfirm();
          } finally {
            setBusy(false);
            setArmed(false);
          }
        }}
      >
        {confirmLabel}
      </Button>
    </span>
  );
}

export function DevicesPage() {
  const { pcs, controllers, grantsByPc, loaded, loading, error, refresh, loadGrants, selectedPcId, select } = useDevicesStore();
  const livePcs = useLiveStore((s) => s.pcs);
  const limits = useSessionStore((s) => s.session?.limits);
  const { kid, problem } = useThisKid();
  const [actionError, setActionError] = useState<ApiError | null>(null);
  useEffect(() => {
    for (const pc of pcs) if (!grantsByPc[pc.id]) void loadGrants(pc.id);
  }, [pcs, grantsByPc, loadGrants]);

  const run = async (fn: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await fn();
      await refresh();
      for (const pc of useDevicesStore.getState().pcs) void loadGrants(pc.id);
    } catch (e) {
      setActionError(e instanceof ApiError ? e : new ApiError(0, "INTERNAL", "Something went wrong.", true));
    }
  };

  const thisController = controllers.find((c) => c.kid === kid) ?? null;
  const enabledCount = pcs.filter((p) => p.enabled).length;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Devices</h1>
        <Button size="md" variant="ghost" onClick={() => void refresh()} busy={loading}>
          Refresh
        </Button>
      </div>
      {error ? (
        <Notice tone="danger">
          <p>{errorMessage(error)}</p>
        </Notice>
      ) : null}
      {actionError ? (
        <Notice tone="danger">
          <p>{errorMessage(actionError)}</p>
        </Notice>
      ) : null}
      {problem ? (
        <Notice tone="warning" title="This installation has no signing key">
          <p>{problem}</p>
        </Notice>
      ) : null}

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-lg">This phone</h2>
          <Link to="/app/devices/pair" className="tap inline-flex items-center rounded-control bg-accent text-on-accent font-semibold px-4 text-sm">
            Pair with a PC
          </Link>
        </div>
        {thisController ? (
          <Card>
            <div className="flex items-center justify-between gap-2">
              <div>
                <p className="font-semibold">{thisController.display_name}</p>
                <p className="text-xs text-text-muted">Paired {absoluteTime(thisController.created_at)}</p>
              </div>
              <Pill tone="success">This phone</Pill>
            </div>
          </Card>
        ) : kid ? (
          <EmptyState title="Not paired yet">
            <p>This installation is not known to your PCs. On the PC open the DoMe tray menu, choose Pair a phone, then scan the code here.</p>
          </EmptyState>
        ) : null}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-lg">PCs</h2>
          {limits ? (
            <span className="text-xs text-text-muted">
              {enabledCount} of {limits.max_enabled_pcs} enabled
            </span>
          ) : null}
        </div>
        {loaded && pcs.length === 0 ? (
          <EmptyState title="No PC linked">
            <p>
              Install DoMe on your Windows PC (<Link to="/download" className="text-accent font-semibold">Download</Link>), sign in there, and approve the link on this phone.
            </p>
          </EmptyState>
        ) : null}
        {pcs.map((pc) => {
          const live = livePcs[pc.id];
          const c = connectionOf(pc, live);
          const grants = grantsByPc[pc.id] ?? [];
          const mine = grants.find((g) => g.controller_id === thisController?.id);
          return (
            <Card key={pc.id} as="article" aria-label={pc.name}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-semibold text-lg truncate">{pc.name}</p>
                  <p className="text-xs text-text-muted">
                    {c.detail} {pc.agent_version ? `· DoMe agent ${pc.agent_version}` : ""}
                  </p>
                </div>
                <Pill tone={c.tone} pulse={c.pulse}>
                  {c.label}
                </Pill>
              </div>
              <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                <dt className="text-text-muted">Last seen</dt>
                <dd>{relativeTime(live?.lastSeen ?? pc.last_seen)}</dd>
                <dt className="text-text-muted">Remote control on PC</dt>
                <dd>{live?.state ? (live.state.remote_enabled ? "On" : "Off (tray icon on the PC)") : pc.remote_enabled_reported === undefined ? "Unknown" : pc.remote_enabled_reported ? "On (last report)" : "Off (last report)"}</dd>
                <dt className="text-text-muted">This phone may</dt>
                <dd>{mine ? mine.capabilities.map(capabilityLabel).join(", ") : grants.length === 0 && !grantsByPc[pc.id] ? "…" : "Nothing — not paired with this PC"}</dd>
              </dl>
              {grants.length > 0 ? (
                <details className="mt-3 text-sm">
                  <summary className="cursor-pointer text-text-muted">
                    {grants.length} paired phone{grants.length === 1 ? "" : "s"}
                  </summary>
                  <ul className="mt-2 space-y-2">
                    {grants.map((g) => {
                      const ctrl = controllers.find((k) => k.id === g.controller_id);
                      return (
                        <li key={g.id} className="flex items-start justify-between gap-2">
                          <span>
                            <span className="font-medium">{ctrl?.display_name ?? "Phone"}</span>
                            {ctrl?.id === thisController?.id ? <span className="text-accent"> (this phone)</span> : null}
                            <span className="block text-xs text-text-muted">{g.capabilities.map(capabilityLabel).join(", ")}</span>
                          </span>
                          <ConfirmButton label="Revoke" confirmLabel="Revoke access" onConfirm={() => run(() => api.revokeGrant(g.id))} />
                        </li>
                      );
                    })}
                  </ul>
                </details>
              ) : null}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {selectedPcId !== pc.id ? (
                  <Button size="md" onClick={() => void select(pc.id)}>
                    Control this PC
                  </Button>
                ) : (
                  <Pill tone="info">Selected</Pill>
                )}
                <RenameInline value={pc.name} label={pc.name} onSave={(name) => run(() => api.patchPc(pc.id, { name }))} />
                <Button size="md" onClick={() => run(() => api.patchPc(pc.id, { enabled: !pc.enabled }))}>
                  {pc.enabled ? "Disable" : "Enable"}
                </Button>
                <ConfirmButton label="Unlink…" confirmLabel="Unlink PC" onConfirm={() => run(() => api.unlinkPc(pc.id))} />
              </div>
              {!pc.enabled ? <p className="text-xs text-text-muted mt-2">Disabled PCs cannot be controlled but keep their pairings and settings.</p> : null}
            </Card>
          );
        })}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-lg">Paired phones</h2>
          {limits ? (
            <span className="text-xs text-text-muted">
              {controllers.filter((c) => c.status !== "revoked").length} of {limits.max_controllers}
            </span>
          ) : null}
        </div>
        {loaded && controllers.length === 0 ? <p className="text-sm text-text-muted">No phone is paired on this account yet.</p> : null}
        {controllers.map((c: rest.Controller) => (
          <Card key={c.id} as="article" aria-label={c.display_name}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-semibold truncate">{c.display_name}</p>
                <p className="text-xs text-text-muted">
                  Added {absoluteTime(c.created_at)} · last seen {relativeTime(c.last_seen)}
                </p>
              </div>
              <Pill tone={c.status === "active" ? (c.id === thisController?.id ? "success" : "neutral") : c.status === "plan_disabled" ? "warning" : "danger"}>{c.id === thisController?.id ? "This phone" : c.status === "active" ? "Active" : c.status === "plan_disabled" ? "Disabled by plan" : "Revoked"}</Pill>
            </div>
            {c.status !== "revoked" ? (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <RenameInline value={c.display_name} label={c.display_name} onSave={(display_name) => run(() => api.renameController(c.id, { display_name }))} />
                <ConfirmButton label={c.id === thisController?.id ? "Revoke this phone…" : "Revoke…"} confirmLabel="Revoke" onConfirm={() => run(() => api.revokeController(c.id))} />
              </div>
            ) : null}
          </Card>
        ))}
        <p className="text-xs text-text-faint">Revoking takes effect immediately: open connections are closed and the PC refuses further commands from that phone. A revoked phone can only be re-paired on the PC.</p>
      </section>
    </div>
  );
}

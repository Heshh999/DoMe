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
import { capabilityLabel, errorMessage, INPUT_SCOPE_EXPLANATION, isInputCapability } from "../../lib/labels.ts";
import { connectionOf } from "../../lib/connection.ts";
import { Icon } from "../../components/icons.tsx";
import { Button, Card, EmptyState, HeaderPill, IconWell, inputClass, KeyValue, Notice, PageHeader, Pill, SectionLabel } from "../../components/ui.tsx";
import { useDevicesStore } from "../../store/devices.ts";
import { useLivePcs } from "../../app/hooks.ts";
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
      <Button size="sm" variant="quiet" onClick={() => (setText(value), setEditing(true))} aria-label={`Rename ${label}`}>
        <Icon name="pen" size={16} />
        Rename
      </Button>
    );
  }
  return (
    <form
      className="flex w-full gap-2"
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

/** A destructive action that stays quiet until tapped; then Keep / the real thing, in red. */
function ConfirmButton({ label, confirmLabel, onConfirm, variant = "danger-ghost", outlined = false }: { label: string; confirmLabel: string; onConfirm: () => Promise<void>; variant?: "danger-ghost" | "secondary"; outlined?: boolean }) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!armed)
    return (
      <Button size="sm" variant={variant} className={outlined ? "border border-danger/35" : ""} onClick={() => setArmed(true)}>
        {label}
      </Button>
    );
  return (
    <span className="inline-flex gap-2">
      <Button size="sm" variant="ghost" onClick={() => setArmed(false)}>
        Keep
      </Button>
      <Button
        size="sm"
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
  const livePcs = useLivePcs();
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
  // already paired with every PC the plan allows: pairing again is not this screen's main action
  const pairingQuiet = thisController !== null && limits !== undefined && enabledCount >= limits.max_enabled_pcs;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Devices"
        action={
          <HeaderPill icon="refresh" onClick={() => void refresh()} busy={loading}>
            Refresh
          </HeaderPill>
        }
      />
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
        <SectionLabel
          action={
            <Link to="/app/devices/pair" className={`tap dome-press inline-flex h-11 items-center gap-1.5 rounded-full pl-3.5 pr-4 text-sm font-semibold ${pairingQuiet ? "border border-border bg-surface text-text hover:bg-surface-hover" : "dome-primary"}`}>
              <Icon name="qr" size={18} className={pairingQuiet ? "text-accent" : ""} />
              Pair with a PC
            </Link>
          }
        >
          This phone
        </SectionLabel>
        {thisController ? (
          <Card className="flex items-center gap-3.5 p-4">
            <IconWell icon="phone" size={44} radius={14} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[17px] font-semibold leading-snug tracking-[-0.01em]">{thisController.display_name}</p>
              <p className="text-xs text-text-muted">Paired {absoluteTime(thisController.created_at)}</p>
            </div>
            <Pill tone="success">This phone</Pill>
          </Card>
        ) : kid ? (
          <EmptyState icon="phone" title="Not paired yet">
            <p>This installation is not known to your PCs. On the PC open the DoMe tray menu, choose Pair a phone, then scan the code here.</p>
          </EmptyState>
        ) : null}
      </section>

      <section className="space-y-3">
        <SectionLabel action={limits ? <span className="text-xs font-medium text-text-muted">{`${enabledCount} of ${limits.max_enabled_pcs} enabled`}</span> : undefined}>PCs</SectionLabel>
        {loaded && pcs.length === 0 ? (
          <EmptyState icon="pc" title="No PC linked">
            <p>
              Install DoMe on your Windows PC (
              <Link to="/download" className="font-semibold text-accent">
                Download
              </Link>
              ), sign in there, and approve the link on this phone.
            </p>
          </EmptyState>
        ) : null}
        {pcs.map((pc) => {
          const live = livePcs[pc.id];
          const c = connectionOf(pc, live);
          const grants = grantsByPc[pc.id] ?? [];
          const mine = grants.find((g) => g.controller_id === thisController?.id);
          return (
            <Card key={pc.id} as="article" aria-label={pc.name} className="p-4">
              <div className="flex items-center gap-3.5">
                <IconWell icon="pc" tone={c.tone === "success" ? "accent" : "muted"} size={44} radius={14} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[17px] font-semibold leading-snug tracking-[-0.01em]">{pc.name}</p>
                  <p className="truncate text-xs text-text-muted">{selectedPcId === pc.id ? <span className="font-semibold text-accent">Controlling now · </span> : null}{c.detail}</p>
                </div>
                <Pill tone={c.tone} pulse={c.pulse}>
                  {c.label}
                </Pill>
              </div>
              <KeyValue
                className="mt-3"
                rows={[
                  { label: "Last seen", value: relativeTime(live?.lastSeen ?? pc.last_seen) },
                  ...(pc.agent_version ? [{ label: "DoMe agent", value: pc.agent_version }] : []),
                  { label: "Remote control on PC", value: live?.state ? (live.state.remote_enabled ? "On" : "Off (tray icon on the PC)") : pc.remote_enabled_reported === undefined ? "Unknown" : pc.remote_enabled_reported ? "On (last report)" : "Off (last report)" },
                  {
                    label: "This phone may",
                    stack: true,
                    value: mine ? (
                      <ul className="mt-1.5 flex flex-wrap gap-1.5" aria-label={`What this phone may do on ${pc.name}`}>
                        {mine.capabilities.map((cap) => (
                          <li key={cap} className="rounded-full bg-surface px-2.5 py-1 text-xs font-medium text-text-muted">
                            {capabilityLabel(cap)}
                          </li>
                        ))}
                      </ul>
                    ) : grants.length === 0 && !grantsByPc[pc.id] ? (
                      "…"
                    ) : (
                      "Nothing — not paired with this PC"
                    ),
                  },
                  { label: "Touchpad / keyboard", value: mine ? (mine.capabilities.some(isInputCapability) ? `Allowed: ${mine.capabilities.filter(isInputCapability).map((c) => (c === "pointer" ? "touchpad" : "keyboard")).join(" and ")}` : "Not allowed — the PC owner grants it on the PC (tray → Paired phones)") : "—", testId: `input-perm-${pc.id}` },
                ]}
              />
              {grants.some((g) => g.capabilities.some(isInputCapability)) ? <p className="mt-2 text-xs text-text-muted">{INPUT_SCOPE_EXPLANATION}</p> : null}
              {grants.length > 0 ? (
                <details className="mt-3 text-sm">
                  <summary className="flex min-h-[44px] cursor-pointer items-center font-medium text-text-muted">
                    {grants.length} paired phone{grants.length === 1 ? "" : "s"}
                  </summary>
                  <ul className="mt-2 space-y-2">
                    {grants.map((g) => {
                      const ctrl = controllers.find((k) => k.id === g.controller_id);
                      return (
                        <li key={g.id} className="flex items-start justify-between gap-2 rounded-control border border-border bg-surface px-3 py-2.5">
                          <span className="min-w-0">
                            <span className="font-medium">{ctrl?.display_name ?? "Phone"}</span>
                            {ctrl?.id === thisController?.id ? <span className="text-accent"> (this phone)</span> : null}
                            <span className="block text-xs text-text-muted">{g.capabilities.map(capabilityLabel).join(", ")}</span>
                            {g.capabilities.some(isInputCapability) ? <span className="block text-xs text-warning">Manual input: reaches every app of the unlocked session</span> : null}
                          </span>
                          <ConfirmButton label="Revoke" confirmLabel="Revoke access" onConfirm={() => run(() => api.revokeGrant(g.id))} />
                        </li>
                      );
                    })}
                  </ul>
                </details>
              ) : null}
              <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
                {selectedPcId !== pc.id ? (
                  <Button size="sm" onClick={() => void select(pc.id)}>
                    Control this PC
                  </Button>
                ) : null}
                <RenameInline value={pc.name} label={pc.name} onSave={(name) => run(() => api.patchPc(pc.id, { name }))} />
                <Button size="sm" onClick={() => run(() => api.patchPc(pc.id, { enabled: !pc.enabled }))}>
                  {pc.enabled ? "Disable" : "Enable"}
                </Button>
                <span className="ml-auto">
                  <ConfirmButton label="Unlink…" confirmLabel="Unlink PC" outlined onConfirm={() => run(() => api.unlinkPc(pc.id))} />
                </span>
              </div>
              {!pc.enabled ? <p className="mt-2 text-xs text-text-muted">Disabled PCs cannot be controlled but keep their pairings and settings.</p> : null}
            </Card>
          );
        })}
      </section>

      <section className="space-y-3">
        <SectionLabel action={limits ? <span className="text-xs font-medium text-text-muted">{`${controllers.filter((c) => c.status !== "revoked").length} of ${limits.max_controllers}`}</span> : undefined}>Paired phones</SectionLabel>
        {loaded && controllers.length === 0 ? <p className="px-1 text-sm text-text-muted">No phone is paired on this account yet.</p> : null}
        {controllers.map((c: rest.Controller) => (
          <Card key={c.id} as="article" aria-label={c.display_name} className="p-4">
            <div className="flex items-center gap-3.5">
              <IconWell icon="phone" tone={c.status === "active" ? "accent" : "muted"} size={44} radius={14} />
              <div className="min-w-0 flex-1">
                <p className="truncate font-semibold leading-snug">{c.display_name}</p>
                <p className="text-xs text-text-muted">
                  Added {absoluteTime(c.created_at)} · last seen {relativeTime(c.last_seen)}
                </p>
              </div>
              <Pill tone={c.status === "active" ? (c.id === thisController?.id ? "success" : "neutral") : c.status === "plan_disabled" ? "warning" : "danger"}>{c.id === thisController?.id ? "This phone" : c.status === "active" ? "Active" : c.status === "plan_disabled" ? "Disabled by plan" : "Revoked"}</Pill>
            </div>
            {c.status !== "revoked" ? (
              <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
                <RenameInline value={c.display_name} label={c.display_name} onSave={(display_name) => run(() => api.renameController(c.id, { display_name }))} />
                <span className="ml-auto">
                  <ConfirmButton label={c.id === thisController?.id ? "Revoke this phone…" : "Revoke…"} confirmLabel="Revoke" onConfirm={() => run(() => api.revokeController(c.id))} />
                </span>
              </div>
            ) : null}
          </Card>
        ))}
        <p className="px-1 text-xs text-text-faint">Revoking takes effect immediately: open connections are closed and the PC refuses further commands from that phone. A revoked phone can only be re-paired on the PC.</p>
      </section>
    </div>
  );
}

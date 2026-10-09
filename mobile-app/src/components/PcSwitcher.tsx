/** Selected-PC header shown on every control surface: name, truthful status and a switcher when the account has several PCs. */
import { useId } from "react";
import { Link } from "react-router";

import type { rest } from "@dome/protocol";

import { useDevicesStore } from "../store/devices.ts";
import { useLivePcs } from "../app/hooks.ts";
import type { LivePc } from "../store/live.ts";
import { connectionOf } from "../lib/connection.ts";
import { Pill } from "./ui.tsx";

export function PcSwitcher({ pc, live }: { pc: rest.Pc | undefined; live: LivePc }) {
  const pcs = useDevicesStore((s) => s.pcs);
  const select = useDevicesStore((s) => s.select);
  const loaded = useDevicesStore((s) => s.loaded);
  const livePcs = useLivePcs();
  const id = useId();
  if (loaded && pcs.length === 0) {
    return (
      <div className="rounded-card border border-dashed border-border-strong p-4 text-sm text-text-muted">
        No PC is linked to your account yet.{" "}
        <Link to="/download" className="text-accent font-semibold">
          Install DoMe on your Windows PC
        </Link>{" "}
        and link it, then pair this phone under <Link to="/app/devices" className="text-accent font-semibold">Devices</Link>.
      </div>
    );
  }
  const c = connectionOf(pc, live);
  return (
    <div className="rounded-card bg-bg-elevated border border-border p-3 flex items-center gap-3">
      <div className="min-w-0 flex-1">
        {pcs.length > 1 ? (
          <>
            <label htmlFor={id} className="sr-only">
              PC to control
            </label>
            <select id={id} value={pc?.id ?? ""} onChange={(e) => void select(e.currentTarget.value)} className="w-full bg-transparent font-bold text-lg truncate pr-6 appearance-none">
              {pcs.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} — {connectionOf(p, livePcs[p.id]).label}
                </option>
              ))}
            </select>
          </>
        ) : (
          <p className="font-bold text-lg truncate">{pc?.name ?? "Loading…"}</p>
        )}
        <p className="text-xs text-text-muted truncate">{c.detail}</p>
      </div>
      <Link to="/app/health" aria-label={`Connection health: ${c.label}`} className="shrink-0">
        <Pill tone={c.tone} pulse={c.pulse}>
          {c.label}
        </Pill>
      </Link>
    </div>
  );
}

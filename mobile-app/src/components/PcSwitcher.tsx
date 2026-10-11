/**
 * Selected-PC header shown on every control surface: name, truthful status and a switcher when the
 * account has several PCs. `hero` is the large card at the top of Home; the default is one compact row.
 */
import { useId } from "react";
import { Link } from "react-router";

import type { rest } from "@dome/protocol";

import { useDevicesStore } from "../store/devices.ts";
import { useLivePcs } from "../app/hooks.ts";
import type { LivePc } from "../store/live.ts";
import { connectionOf } from "../lib/connection.ts";
import { Icon } from "./icons.tsx";
import { Pill } from "./ui.tsx";

export function PcSwitcher({ pc, live, hero = false }: { pc: rest.Pc | undefined; live: LivePc; hero?: boolean }) {
  const pcs = useDevicesStore((s) => s.pcs);
  const select = useDevicesStore((s) => s.select);
  const loaded = useDevicesStore((s) => s.loaded);
  const livePcs = useLivePcs();
  const id = useId();
  if (loaded && pcs.length === 0) {
    return (
      <div className="rounded-card border border-dashed border-border-strong p-5 text-sm text-text-muted">
        No PC is linked to your account yet.{" "}
        <Link to="/download" className="text-accent font-semibold">
          Install DoMe on your Windows PC
        </Link>{" "}
        and link it, then pair this phone under <Link to="/app/devices" className="text-accent font-semibold">Devices</Link>.
      </div>
    );
  }
  const c = connectionOf(pc, live);
  const online = c.tone === "success";
  const name =
    pcs.length > 1 ? (
      <>
        <label htmlFor={id} className="sr-only">
          PC to control
        </label>
        <span className="relative flex items-center">
          <select id={id} value={pc?.id ?? ""} onChange={(e) => void select(e.currentTarget.value)} className={`w-full min-w-0 appearance-none truncate bg-transparent pr-6 font-semibold tracking-[-0.02em] ${hero ? "text-[1.375rem]" : "text-[17px]"}`}>
            {pcs.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — {connectionOf(p, livePcs[p.id]).label}
              </option>
            ))}
          </select>
          <Icon name="chevron" size={16} className="pointer-events-none absolute right-0 rotate-90 text-text-faint" />
        </span>
      </>
    ) : (
      <p className={`truncate font-semibold tracking-[-0.02em] ${hero ? "text-[1.375rem] leading-tight" : "text-[17px]"}`}>{pc?.name ?? "Loading…"}</p>
    );
  const status = (
    <Link to="/app/health" aria-label={`Connection health: ${c.label}`} className={`tap inline-flex shrink-0 items-center ${hero ? "-my-2.5" : ""}`}>
      <Pill tone={c.tone} pulse={c.pulse}>
        {c.label}
      </Pill>
    </Link>
  );
  const tile = (
    <span aria-hidden="true" className={`relative flex shrink-0 items-center justify-center rounded-2xl ${hero ? "h-[60px] w-[60px]" : "h-10 w-10"} ${online ? "bg-[linear-gradient(145deg,#2a62ff,#0034c4)] text-white shadow-[0_10px_24px_-10px_rgb(1_66_251/0.7)]" : "bg-surface text-text-muted"}`}>
      <Icon name="pc" size={hero ? 28 : 20} />
    </span>
  );
  if (hero) {
    return (
      <div className="dome-card flex items-center gap-4 p-5">
        {tile}
        <div className="min-w-0 flex-1">
          {name}
          <div className="mt-2 flex min-w-0 items-center gap-2">
            {status}
            <p className="min-w-0 truncate text-xs text-text-muted">{c.detail}</p>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="dome-card flex items-center gap-3 px-4 py-3">
      {tile}
      <div className="min-w-0 flex-1">
        {name}
        <p className="mt-0.5 truncate text-xs text-text-muted">{c.detail}</p>
      </div>
      {status}
    </div>
  );
}

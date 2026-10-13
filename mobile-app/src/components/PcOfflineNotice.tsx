/**
 * What to do when the selected PC is offline (Home and Remote): the reason, two steps, and the way to Connection
 * health. Nothing is queued while it is offline, so the notice says that too: the PC never acts hours later.
 */
import { Link } from "react-router";

import type { rest } from "@dome/protocol";

import { isPcOffline } from "../lib/connection.ts";
import type { LivePc } from "../store/live.ts";
import { Icon } from "./icons.tsx";
import { Notice } from "./ui.tsx";

export function PcOfflineNotice({ pc, live }: { pc: rest.Pc | undefined; live: LivePc }) {
  if (!isPcOffline(pc, live)) return null;
  return (
    <Notice tone="warning" title={`${pc?.name ?? "Your PC"} is offline`}>
      <ol className="mt-1 list-decimal space-y-1 pl-5">
        <li>Turn the PC on and sign in to Windows.</li>
        <li>Check the DoMe tray icon on the PC says Connected.</li>
      </ol>
      <p className="mt-2 text-xs">Commands are not stored for later, so nothing runs on the PC by surprise when it comes back.</p>
      <Link to="/app/health" className="tap dome-press mt-3 inline-flex min-h-[44px] items-center gap-1.5 rounded-control border border-border bg-surface px-4 text-sm font-semibold text-text hover:bg-surface-hover">
        <Icon name="pulse" size={16} className="text-accent" />
        Check connection
      </Link>
    </Notice>
  );
}

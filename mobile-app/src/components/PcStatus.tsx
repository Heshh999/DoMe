/** Truthful connection state for a PC: online / reconnecting / offline since last seen, plus plan and remote-control flags. */
import type { rest } from "@dome/protocol";

import { connectionOf } from "../lib/connection.ts";
import type { LivePc } from "../store/live.ts";
import { Pill } from "./ui.tsx";

export function PcStatusPill({ pc, live }: { pc: rest.Pc | undefined; live: LivePc | undefined }) {
  const c = connectionOf(pc, live);
  return (
    <Pill tone={c.tone} pulse={c.pulse}>
      {c.label}
    </Pill>
  );
}

/**
 * The way into Screen view (docs/design/screen-view.md §2.2 step 1, §16): the Remote page's "Screen" tile and the Home
 * card. Behind the operator switch: while the server does not say `screen_view: "on"` (GET /healthz), the Remote page
 * keeps the honest "Screen view: Coming" card and Home shows nothing, so nothing reads as available that is not
 * (DECISIONS 63). With the switch on, one line says the one state that matters next ("Allow Screen view on your PC
 * first", "Pair again so your phone can check it is your PC", "Update DoMe on your PC", "Ready · 10 min left in this
 * window", "Free Screen view refreshes today at 4:12 pm"), and the tile opens the Screen page.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { useMyGrant, usePcKeyPin, useScreenSwitch } from "../app/hooks.ts";
import { entryLine, SCREEN_BETA_LABEL } from "../lib/screenStatus.ts";
import { useDevicesStore } from "../store/devices.ts";
import { useScreenStore } from "../store/screen.ts";
import { Icon } from "./icons.tsx";
import { ScreenViewComingCard } from "./ScreenViewComingCard.tsx";

export function ScreenViewTile({ pcId, variant }: { pcId: string | null; variant: "remote" | "home" }) {
  const switchOn = useScreenSwitch();
  const grant = useMyGrant(pcId);
  const loadGrants = useDevicesStore((s) => s.loadGrants);
  const pin = usePcKeyPin(switchOn === "on" ? pcId : null);
  const lastProblem = useScreenStore((s) => (pcId ? (s.lastProblemByPc[pcId] ?? null) : null));
  const allowance = useScreenStore((s) => (pcId ? (s.lastAllowanceByPc[pcId] ?? null) : null));
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (switchOn !== "on") return;
    const h = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(h);
  }, [switchOn]);
  useEffect(() => {
    if (switchOn === "on" && pcId && grant === undefined) void loadGrants(pcId);
  }, [switchOn, pcId, grant, loadGrants]);

  if (switchOn !== "on") return variant === "remote" ? <ScreenViewComingCard /> : null;
  if (!pcId) return null;
  // nothing is claimed ("Ready …") before the grant and the pin are known
  const line = grant === undefined || pin === undefined ? "Checking…" : entryLine({ grant, pin, lastProblem, allowance, now });
  return (
    <Link to="/app/screen" className="dome-card dome-press flex items-center gap-4 p-4 hover:bg-surface" data-testid={`screen-view-tile-${variant}`}>
      <span aria-hidden="true" className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/12 text-accent">
        <Icon name="pc" size={21} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold">{SCREEN_BETA_LABEL}</span>
        <span className="block text-xs text-text-muted" data-testid="screen-view-entry-line">
          {line}
        </span>
      </span>
      <Icon name="chevron" size={18} className="text-text-faint" />
    </Link>
  );
}

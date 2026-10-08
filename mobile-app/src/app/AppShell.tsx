/**
 * Authenticated shell: session guard, runtime start, bottom tab bar, relay banners, offline screen
 * and the global confirmation modal. Keeps developer concepts out of view: statuses are phrased for
 * customers and the only identifiers shown are PC and phone names.
 */
import { useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router";

import { loginUrl } from "../lib/api.ts";
import { errorMessage } from "../lib/labels.ts";
import { ConfirmationModal } from "../components/ConfirmationModal.tsx";
import { OfflineScreen } from "../components/OfflineScreen.tsx";
import { useOnline } from "./useOnline.ts";
import { Button, Notice, Spinner } from "../components/ui.tsx";
import { useDevicesStore } from "../store/devices.ts";
import { pendingConfirmation, useLiveStore } from "../store/live.ts";
import { useSessionStore } from "../store/session.ts";
import { useLabels } from "./hooks.ts";
import { navigation, signInRedirect } from "./navigation.ts";
import { getRuntime } from "./runtime.ts";

const TABS: Array<{ to: string; label: string; icon: string; end?: boolean }> = [
  { to: "/app", label: "Home", icon: "⌂", end: true },
  { to: "/app/remote", label: "Remote", icon: "▶" },
  { to: "/app/command", label: "Type", icon: "⌨" },
  { to: "/app/apps", label: "Apps", icon: "▦" },
  { to: "/app/more", label: "More", icon: "⋯" },
];

export function RequireSession({ children }: { children: React.ReactNode }) {
  const status = useSessionStore((s) => s.status);
  const error = useSessionStore((s) => s.error);
  const load = useSessionStore((s) => s.load);
  const location = useLocation();
  useEffect(() => {
    if (status === "unknown") void load();
  }, [status, load]);
  useEffect(() => {
    // Only the server's verdict (401 / relay 4008 → `signed_out`) redirects to sign-in. A sign-out the
    // customer started (`signing_out`) must not: that flow deletes local state first and navigates once.
    if (status !== "signed_out") return;
    // Never forward the fragment: on `/app/devices/pair#code=…` it is the pairing code, which must not
    // reach cloud-api (query string, oidc_flows.return_to) nor be kept on the phone. Scrub it from the
    // address bar first, then leave; the pairing page asks for the code again after sign-in.
    const target = signInRedirect(location);
    const nav = navigation();
    if (target.scrubTo !== null) nav.replaceState(target.scrubTo);
    nav.assign(loginUrl(target.returnTo));
  }, [status, location]);
  if (status === "signed_in") return <>{children}</>;
  if (status === "signing_out") {
    return (
      <main className="min-h-dvh flex items-center justify-center bg-bg text-text">
        <Spinner label="Signing out" />
      </main>
    );
  }
  if (status === "error") {
    return (
      <main className="min-h-dvh flex items-center justify-center px-6 bg-bg text-text">
        <div className="max-w-sm w-full space-y-4">
          <Notice tone="danger" title="DoMe could not load your account">
            <p>{errorMessage(error)}</p>
          </Notice>
          <Button full onClick={() => void load()}>
            Try again
          </Button>
        </div>
      </main>
    );
  }
  return (
    <main className="min-h-dvh flex items-center justify-center bg-bg text-text">
      <Spinner label={status === "signed_out" ? "Taking you to sign in" : "Loading your account"} />
    </main>
  );
}

function RelayBanner() {
  const status = useLiveStore((s) => s.relayStatus);
  const relayError = useLiveStore((s) => s.relayError);
  const controllerId = useLiveStore((s) => s.controllerId);
  if (status === "revoked") {
    return (
      <Notice tone="danger" title="This phone’s access was revoked">
        <p>Pair again from the PC (DoMe tray → Pair a phone) to restore control. Your PCs stay linked to your account.</p>
      </Notice>
    );
  }
  if (status === "incompatible") {
    return (
      <Notice tone="danger" title="Update needed">
        <p>{errorMessage(relayError ?? { code: "PROTOCOL_INCOMPATIBLE" })}</p>
      </Notice>
    );
  }
  if (status === "refused") {
    return (
      <Notice tone="danger" title="DoMe refused the connection">
        <p>Reload the app. If this keeps happening, sign out and back in.</p>
      </Notice>
    );
  }
  if (status === "reconnecting" || status === "connecting") {
    return (
      <Notice tone="warning">
        <p>{status === "connecting" ? "Connecting to DoMe…" : "Connection to DoMe lost — reconnecting. PC status is not current."}</p>
      </Notice>
    );
  }
  if (status === "open" && controllerId === null) {
    return (
      <Notice tone="info" title="This phone is not paired yet">
        <p>Pair it with your PC under Devices to start controlling it.</p>
      </Notice>
    );
  }
  return null;
}

/**
 * The one confirmation modal of the app. The PC named in the header is the device-inventory entry
 * matched by the *challenge's* `pc_id` (ADR-0001 D7), not the command record's: when the two differ
 * the binding check withholds Approve and the header must agree with that warning.
 */
export function GlobalConfirmation() {
  const commands = useLiveStore((s) => s.commands);
  const relayStatus = useLiveStore((s) => s.relayStatus);
  const pcs = useDevicesStore((s) => s.pcs);
  const pending = pendingConfirmation(commands);
  const labels = useLabels(pending?.pcId ?? null);
  if (!pending?.confirmation) return null;
  const pcName = pcs.find((p) => p.id === pending.confirmation!.parsed.challenge.pc_id)?.name ?? null;
  return <ConfirmationModal record={pending} pcName={pcName} labels={labels} connected={relayStatus === "open"} onRespond={(decision) => getRuntime().respond(pending.commandId, decision)} onDismiss={() => getRuntime().dismissConfirmation(pending.commandId)} />;
}

function AppRuntime() {
  const session = useSessionStore((s) => s.session);
  const pcs = useDevicesStore((s) => s.pcs);
  const loaded = useDevicesStore((s) => s.loaded);
  const refresh = useDevicesStore((s) => s.refresh);
  const restoreSelection = useDevicesStore((s) => s.restoreSelection);
  useEffect(() => {
    if (!session) return;
    const rt = getRuntime();
    void restoreSelection().then(() => refresh());
    rt.start();
  }, [session, refresh, restoreSelection]);
  useEffect(() => {
    if (loaded) getRuntime().syncSubscriptions(pcs.map((p) => p.id));
  }, [loaded, pcs]);
  return null;
}

export function AppShell() {
  const online = useOnline();
  if (!online) return <OfflineScreen />;
  return (
    <RequireSession>
      <AppRuntime />
      <div className="min-h-dvh bg-bg text-text flex flex-col">
        <div className="flex-1 w-full max-w-lg mx-auto px-4 pt-[calc(var(--safe-top)+0.75rem)] pb-[calc(84px+var(--safe-bottom))]">
          <div className="mb-3 empty:hidden">
            <RelayBanner />
          </div>
          <Outlet />
        </div>
        <nav aria-label="Main" className="fixed bottom-0 inset-x-0 border-t border-border bg-bg-elevated/95 backdrop-blur supports-[backdrop-filter]:bg-bg-elevated/80 pb-[var(--safe-bottom)]">
          <ul className="max-w-lg mx-auto grid grid-cols-5">
            {TABS.map((t) => (
              <li key={t.to}>
                <NavLink to={t.to} end={t.end ?? false} className={({ isActive }) => `tap flex flex-col items-center justify-center gap-0.5 py-2 min-h-[56px] text-[11px] font-semibold ${isActive ? "text-accent" : "text-text-muted"}`}>
                  <span aria-hidden="true" className="text-xl leading-none">
                    {t.icon}
                  </span>
                  {t.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <GlobalConfirmation />
      </div>
    </RequireSession>
  );
}

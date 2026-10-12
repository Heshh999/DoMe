/**
 * Authenticated shell: session guard, runtime start, bottom tab bar, relay banners, offline screen
 * and the global confirmation modal. Keeps developer concepts out of view: statuses are phrased for
 * customers and the only identifiers shown are PC and phone names.
 */
import { useEffect } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";

import { loginUrl } from "../lib/api.ts";
import { errorMessage } from "../lib/labels.ts";
import { Wordmark } from "../components/brand.tsx";
import { ConfirmationModal } from "../components/ConfirmationModal.tsx";
import { OfflineScreen } from "../components/OfflineScreen.tsx";
import { useOnline } from "./useOnline.ts";
import { Icon, type IconName } from "../components/icons.tsx";
import { Button, Notice, Spinner } from "../components/ui.tsx";
import { useDevicesStore } from "../store/devices.ts";
import { pendingConfirmation, useLiveStore } from "../store/live.ts";
import { useAssistantStore } from "../store/assistant.ts";
import { useSessionStore } from "../store/session.ts";
import { useLabels } from "./hooks.ts";
import { navigation, signInRedirect } from "./navigation.ts";
import { getRuntime } from "./runtime.ts";
import { getSpeaker } from "./voice.ts";

type Tab = { to: string; label: string; icon: IconName; end?: boolean };
const TYPE_TAB: Tab = { to: "/app/command", label: "Type", icon: "keyboard" };
const ASK_TAB: Tab = { to: "/app/assistant", label: "Ask", icon: "ask" };

/**
 * Five tabs, the most a phone tab bar carries comfortably: Home, Remote, Touchpad, then Type — or Ask
 * when this phone shows the Assistant tab — and More (Apps, Devices, Settings and the rest live there;
 * Type and Ask stay under More too).
 */
function tabs(showAssistant: boolean): Tab[] {
  return [
    { to: "/app", label: "Home", icon: "home", end: true },
    { to: "/app/remote", label: "Remote", icon: "remote" },
    { to: "/app/touchpad", label: "Touchpad", icon: "touchpad" },
    showAssistant ? ASK_TAB : TYPE_TAB,
    { to: "/app/more", label: "More", icon: "more" },
  ];
}

/**
 * iOS speaks only after a gesture: taps anywhere that are not the talk button (the tab that opens Ask, Send, a chip)
 * unlock speech, each one trying again until speech has really started once (WebKit may not count every event type
 * as a gesture, and may ignore a try). The talk tap itself never speaks, so the unlock never competes with listening.
 */
const UNLOCK_EVENTS = ["pointerup", "touchend", "click"] as const;

function SpeechUnlock() {
  useEffect(() => {
    const stop = () => {
      for (const type of UNLOCK_EVENTS) document.removeEventListener(type, unlock, true);
    };
    function unlock(e: Event) {
      const speaker = getSpeaker();
      if (speaker.unlocked) {
        stop();
        return;
      }
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest("[data-talk-button]")) return;
      speaker.unlock();
    }
    for (const type of UNLOCK_EVENTS) document.addEventListener(type, unlock, true);
    return stop;
  }, []);
  return null;
}

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
      <Splash>
        <Spinner label="Signing out" />
        <p className="mt-4 text-sm text-text-muted">Signing out…</p>
      </Splash>
    );
  }
  if (status === "error") {
    return (
      <Splash>
        <div className="w-full max-w-sm space-y-4 text-left">
          <Notice tone="danger" title="DoMe could not load your account">
            <p>{errorMessage(error)}</p>
          </Notice>
          <Button full variant="primary" size="lg" onClick={() => void load()}>
            Try again
          </Button>
        </div>
      </Splash>
    );
  }
  return (
    <Splash>
      <Spinner label={status === "signed_out" ? "Taking you to sign in" : "Loading your account"} />
      <p className="mt-4 text-sm text-text-muted">{status === "signed_out" ? "Taking you to sign in…" : "Loading your account…"}</p>
    </Splash>
  );
}

/** The screens before the app can show anything of yours: the wordmark on the brand backdrop, then what is happening. */
function Splash({ children }: { children: React.ReactNode }) {
  return (
    <main className="relative isolate flex min-h-dvh flex-col items-center justify-center px-6 pt-[var(--safe-top)] pb-[var(--safe-bottom)] text-center text-text">
      <div className="dome-backdrop" aria-hidden="true" />
      <Wordmark compact title="DoMe" className="mb-10 h-8 w-auto" />
      {children}
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
        <p>
          {status === "connecting" ? "Connecting to DoMe…" : "Connection to DoMe lost — reconnecting. PC status is not current."}{" "}
          <Link to="/app/health" className="text-accent font-semibold underline">
            Connection health
          </Link>
        </p>
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
  const showAssistant = useAssistantStore((s) => s.prefs.showAssistant);
  if (!online) return <OfflineScreen />;
  return (
    <RequireSession>
      <AppRuntime />
      <SpeechUnlock />
      <div className="relative isolate min-h-dvh text-text flex flex-col">
        <div className="dome-backdrop" aria-hidden="true" />
        <div className="flex-1 w-full max-w-lg mx-auto px-5 pt-[calc(var(--safe-top)+0.75rem)] pb-[calc(92px+var(--safe-bottom))]">
          <div className="mb-3 empty:hidden">
            <RelayBanner />
          </div>
          <Outlet />
        </div>
        <nav aria-label="Main" className="dome-bar fixed bottom-0 inset-x-0 z-20 border-t border-border pb-[var(--safe-bottom)]">
          <ul className="max-w-lg mx-auto grid grid-cols-5 px-1">
            {tabs(showAssistant).map((t) => (
              <li key={t.to}>
                <NavLink to={t.to} end={t.end ?? false} className={({ isActive }) => `tap dome-press relative flex h-14 flex-col items-center justify-center gap-[3px] text-[11px] font-semibold tracking-[-0.01em] ${isActive ? "text-text" : "text-text-faint hover:text-text-muted"}`}>
                  {({ isActive }) => (
                    <>
                      <Icon name={t.icon} size={24} strokeWidth={isActive ? 2 : 1.75} className={isActive ? "text-accent" : ""} />
                      {t.label}
                      <span aria-hidden="true" className={`absolute bottom-[3px] h-1 w-1 rounded-full transition-opacity duration-200 ${isActive ? "bg-coral opacity-100" : "opacity-0"}`} />
                    </>
                  )}
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

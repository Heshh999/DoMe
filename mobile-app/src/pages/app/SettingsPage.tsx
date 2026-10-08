/**
 * Settings & help: connection check (system.ping round trip), privacy facts, iOS Add-to-Home-Screen
 * steps, redacted diagnostics download, support, sign out and the local "forget this installation".
 */
import { useState } from "react";
import { Link } from "react-router";

import { useLabels, useSelectedPc, useSend } from "../../app/hooks.ts";
import { navigation } from "../../app/navigation.ts";
import { getRuntime } from "../../app/runtime.ts";
import { CommandOutcome } from "../../components/CommandOutcome.tsx";
import { Button, Card, Notice } from "../../components/ui.tsx";
import { APP_VERSION, buildDiagnostics, downloadJson, isStandalone } from "../../lib/diagnostics.ts";
import { useDevicesStore } from "../../store/devices.ts";
import { useLiveStore } from "../../store/live.ts";
import { useSessionStore } from "../../store/session.ts";

const SUPPORT_URL: string | undefined = import.meta.env.VITE_DOME_SUPPORT_URL;

export function SettingsPage() {
  const { pcId, pcName, live: livePc } = useSelectedPc();
  const { send, record, sending } = useSend(pcId);
  const labels = useLabels(pcId);
  const session = useSessionStore((s) => s.session);
  const pcs = useDevicesStore((s) => s.pcs);
  const live = useLiveStore();
  const [busy, setBusy] = useState<string | null>(null);
  const [forgetArmed, setForgetArmed] = useState(false);
  const ios = /iPhone|iPad/.test(navigator.userAgent);
  const standalone = isStandalone();

  const diagnostics = () => {
    const report = buildDiagnostics({
      relayStatus: live.relayStatus,
      controllerBound: live.controllerId !== null,
      keyAvailable: true,
      selectedPcId: pcId,
      pcs: pcs.map((p) => {
        const l = live.pcs[p.id];
        return { id: p.id, connection: l?.connection ?? p.connection, enabled: p.enabled, lastSeen: l?.lastSeen ?? p.last_seen, stale: l?.stale ?? true, remoteEnabled: l?.state?.remote_enabled ?? null, extensionConnected: l?.state?.extension_connected ?? null, sessionLocked: l?.state?.session_locked ?? null };
      }),
      commands: live.commands,
      sessionPresent: session !== null,
      plan: session?.plan ?? null,
    });
    downloadJson(`dome-diagnostics-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`, report);
  };

  // Both flows navigate exactly once, after the runtime has finished: `Runtime.signOut` puts the
  // session store into `signing_out` first so `RequireSession` never starts its own sign-in redirect
  // in parallel (which could unload the page before the key deletion commits, or land the customer on
  // the identity provider and silently sign them back in).
  const signOut = async () => {
    setBusy("signout");
    try {
      await getRuntime().signOut();
    } finally {
      navigation().assign("/");
    }
  };
  const forget = async () => {
    setBusy("forget");
    try {
      await getRuntime().signOut({ forgetInstallation: true });
    } finally {
      navigation().assign("/");
    }
  };
  // The ping is a non-consequential `status` action: it is exactly what a customer needs while the
  // state is stale ("Online · refreshing") or remote control is switched off on the PC, so it is
  // gated only on the socket being open and the relay reporting the PC online. The PC answers
  // PC_REMOTE_DISABLED / PC_RECONNECTING honestly and the outcome shows the recovery steps.
  const canPing = live.relayStatus === "open" && livePc.connection === "online";

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">Settings & help</h1>

      <Card>
        <h2 className="font-semibold">Connection check</h2>
        <p className="text-sm text-text-muted mt-1">Sends a harmless round-trip to {pcName ?? "the selected PC"} and shows how long it took.</p>
        <Button className="mt-3" disabled={!canPing || pcId === null} busy={sending} onClick={() => void send("system.ping", {}, null, "system")}>
          Check connection to {pcName ?? "PC"}
        </Button>
        {record ? (
          <div className="mt-3">
            <CommandOutcome record={record} pcName={pcName} labels={labels} />
          </div>
        ) : null}
      </Card>

      <Card>
        <h2 className="font-semibold">Install on your iPhone</h2>
        {standalone ? (
          <p className="text-sm text-text-muted mt-1">DoMe is installed on this device’s home screen.</p>
        ) : (
          <>
            <p className="text-sm text-text-muted mt-1">Adding DoMe to your home screen makes it open full-screen like an app.{ios ? "" : " These steps are for Safari on iPhone; on Android use Chrome’s “Install app” menu item."}</p>
            <ol className="mt-2 list-decimal pl-5 text-sm space-y-1">
              <li>Open this page in Safari (not inside another app).</li>
              <li>
                Tap the <strong>Share</strong> button (the square with an arrow).
              </li>
              <li>
                Scroll and tap <strong>Add to Home Screen</strong>, then <strong>Add</strong>.
              </li>
              <li>Open DoMe from the new icon and sign in once.</li>
            </ol>
            <p className="text-xs text-text-faint mt-2">The installed app is a separate installation: pair it with your PC once from Devices. Clearing Safari’s website data removes its pairing, and you pair again — there is no shortcut around that by design.</p>
          </>
        )}
      </Card>

      <Card>
        <h2 className="font-semibold">Privacy</h2>
        <ul className="text-sm text-text-muted mt-1 space-y-1 list-disc pl-5">
          <li>Commands are signed by a key that never leaves this phone; your PC only accepts phones you approved on the PC.</li>
          <li>The DoMe service routes commands and current PC state between your devices and keeps a lifecycle record per command (action, state, timing, error code) — not parameters, results, typed text or media titles.</li>
          <li>This app stores no analytics, advertising or session-replay scripts, and never caches your PC’s data.</li>
          <li>
            Full details: <Link to="/privacy" className="text-accent font-semibold">Privacy notice</Link>.
          </li>
        </ul>
      </Card>

      <Card>
        <h2 className="font-semibold">Diagnostics</h2>
        <p className="text-sm text-text-muted mt-1">Download a redacted report (versions, connection states, command outcomes without content) to attach to a support request. Nothing is sent automatically.</p>
        <Button className="mt-3" onClick={diagnostics}>
          Download diagnostics
        </Button>
        <p className="text-xs text-text-faint mt-2">
          DoMe app {APP_VERSION}
          {session ? ` · signed in as ${session.account.email}` : ""}
        </p>
      </Card>

      <Card>
        <h2 className="font-semibold">Support</h2>
        {SUPPORT_URL ? (
          <a href={SUPPORT_URL} className="tap mt-2 inline-flex items-center rounded-control border border-border px-4 text-sm font-medium" rel="noopener noreferrer" target="_blank">
            Contact support
          </a>
        ) : (
          <p className="text-sm text-text-muted mt-1">
            A support contact has not been configured for this deployment yet. See <Link to="/support" className="text-accent font-semibold">Support</Link> for self-help steps.
          </p>
        )}
      </Card>

      <Card>
        <h2 className="font-semibold">Account</h2>
        <p className="text-sm text-text-muted mt-1">Signing out closes the connection and forgets account data on this phone. Your pairing with the PC stays, so signing back in needs no new pairing.</p>
        <Button className="mt-3" full onClick={() => void signOut()} busy={busy === "signout"} disabled={busy !== null}>
          Sign out
        </Button>
      </Card>

      <Card className="border-danger/40">
        <h2 className="font-semibold">Forget this installation</h2>
        <p className="text-sm text-text-muted mt-1">Deletes this phone’s signing key. Your PCs will no longer accept it until you pair again on the PC. Use this before handing the phone to someone else; you can also revoke it from any other device under Devices.</p>
        {!forgetArmed ? (
          <Button className="mt-3" variant="danger" onClick={() => setForgetArmed(true)}>
            Forget this installation…
          </Button>
        ) : (
          <div className="mt-3 space-y-2">
            <Notice tone="danger">This cannot be undone on this phone.</Notice>
            <div className="grid grid-cols-2 gap-2">
              <Button onClick={() => setForgetArmed(false)}>Keep</Button>
              <Button variant="danger" onClick={() => void forget()} busy={busy === "forget"} disabled={busy !== null}>
                Delete key and sign out
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

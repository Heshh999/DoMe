/**
 * Settings & help: connection check (system.ping round trip), privacy facts, iOS Add-to-Home-Screen
 * steps, redacted diagnostics download, support, sign out and the local "forget this installation".
 */
import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import { useLabels, useSelectedPc, useSend } from "../../app/hooks.ts";
import { navigation } from "../../app/navigation.ts";
import { getRuntime } from "../../app/runtime.ts";
import { AssistantSettingsCard } from "../../components/AssistantSettingsCard.tsx";
import { CommandOutcome } from "../../components/CommandOutcome.tsx";
import { Icon, type IconName } from "../../components/icons.tsx";
import { Button, Card, IconWell, Notice, PageHeader, SectionLabel, type WellTone } from "../../components/ui.tsx";
import { APP_VERSION, buildDiagnostics, downloadJson, isStandalone } from "../../lib/diagnostics.ts";
import { useDevicesStore } from "../../store/devices.ts";
import { useInputStore } from "../../store/input.ts";
import { useScreenStore } from "../../store/screen.ts";
import { useLiveStore } from "../../store/live.ts";
import { useSessionStore } from "../../store/session.ts";

const SUPPORT_URL: string | undefined = import.meta.env.VITE_DOME_SUPPORT_URL;

/** A settings card: an icon well, a title, a line of explanation, then its content. */
export function SettingCard({ icon, tone = "accent", title, text, children, className = "" }: { icon: IconName; tone?: WellTone; title: string; text?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <Card className={className}>
      <div className="flex items-start gap-3.5">
        <IconWell icon={icon} tone={tone} size={40} />
        <div className="min-w-0 flex-1 pt-0.5">
          <h2 className="font-semibold leading-snug">{title}</h2>
          {text ? <p className="mt-1 text-sm text-text-muted">{text}</p> : null}
        </div>
      </div>
      {children}
    </Card>
  );
}

/** A setting that is mostly explanation: one row (icon, title, one line) that opens to show the details. */
function SettingDisclosure({ icon, title, line, children }: { icon: IconName; title: string; line: string; children: ReactNode }) {
  return (
    <details className="dome-card dome-more group overflow-hidden p-0">
      <summary className="flex min-h-[64px] cursor-pointer items-center gap-3.5 px-5 py-4">
        <IconWell icon={icon} size={40} />
        <span className="min-w-0 flex-1">
          <span className="block font-semibold leading-snug">{title}</span>
          <span className="mt-0.5 block text-sm text-text-muted">{line}</span>
        </span>
        <Icon name="chevron" size={18} className="dome-more-chevron shrink-0 text-text-faint" />
      </summary>
      <div className="px-5 pb-5">{children}</div>
    </details>
  );
}

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
      input: useInputStore.getState().session,
      screen: useScreenStore.getState().session,
      screenFacts: getRuntime().screen.facts,
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
    <div className="space-y-6">
      <PageHeader title="Settings & help" subtitle={session ? <>Signed in as <span className="text-text">{session.account.email}</span></> : undefined} />

      <section>
        <SectionLabel>Your PC</SectionLabel>
        <SettingCard icon="pulse" title="Connection check" text={`Sends a harmless round-trip to ${pcName ?? "the selected PC"} and shows how long it took.`}>
          <Button className="mt-4" full disabled={!canPing || pcId === null} busy={sending} onClick={() => void send("system.ping", {}, null, "system")}>
            Check connection to {pcName ?? "PC"}
          </Button>
          {record ? (
            <div className="mt-4 rounded-control border border-border bg-surface p-3">
              <CommandOutcome record={record} pcName={pcName} labels={labels} />
            </div>
          ) : null}
        </SettingCard>
      </section>

      <section>
        <SectionLabel>Ask (AI butler)</SectionLabel>
        <AssistantSettingsCard />
      </section>

      <section className="space-y-3">
        <SectionLabel>This phone</SectionLabel>
        <SettingDisclosure icon="download" title="Install on your iPhone" line={standalone ? "Installed on this phone’s home screen." : "Open DoMe full-screen, like an app."}>
          <p className="text-sm text-text-muted">{standalone ? "DoMe is installed on this device’s home screen." : `Adding DoMe to your home screen makes it open full-screen like an app.${ios ? "" : " These steps are for Safari on iPhone; on Android use Chrome’s “Install app” menu item."}`}</p>
          {standalone ? null : (
            <>
              <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm marker:text-text-faint">
                <li>Open this page in Safari (not inside another app).</li>
                <li>
                  Tap the <strong>Share</strong> button (the square with an arrow).
                </li>
                <li>
                  Scroll and tap <strong>Add to Home Screen</strong>, then <strong>Add</strong>.
                </li>
                <li>Open DoMe from the new icon and sign in once.</li>
              </ol>
              <p className="mt-3 text-xs text-text-faint">The installed app is a separate installation: pair it with your PC once from Devices. Clearing Safari’s website data removes its pairing, and you pair again — there is no shortcut around that by design.</p>
            </>
          )}
        </SettingDisclosure>

        <SettingDisclosure icon="shield" title="Privacy" line="Signed commands, no analytics, nothing cached.">
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-text-muted marker:text-text-faint">
            <li>Commands are signed by a key that never leaves this phone; your PC only accepts phones you approved on the PC.</li>
            <li>The DoMe service routes commands and current PC state between your devices and keeps a lifecycle record per command (action, state, timing, error code) — not parameters, results, typed text or media titles.</li>
            <li>This app stores no analytics, advertising or session-replay scripts, and never caches your PC’s data.</li>
            <li>Ask keeps the conversation in memory only. With the AI butler on, only requests DoMe can’t work out by itself go to Anthropic, through DoMe’s server; see the AI butler card above.</li>
            <li>
              Full details:{" "}
              <Link to="/privacy" className="font-semibold text-accent">
                Privacy notice
              </Link>
              .
            </li>
          </ul>
        </SettingDisclosure>

        <SettingDisclosure icon="info" title="Diagnostics" line="A redacted report for a support request.">
          <p className="text-sm text-text-muted">Download a redacted report (versions, connection states, command outcomes without content) to attach to a support request. Nothing is sent automatically.</p>
          <Button className="mt-4" full onClick={diagnostics}>
            Download diagnostics
          </Button>
          <p className="mt-3 text-xs text-text-faint">
            DoMe app {APP_VERSION}
            {session ? ` · signed in as ${session.account.email}` : ""}
          </p>
        </SettingDisclosure>

        <SettingCard
          icon="help"
          title="Support"
          text={
            <>
              Send a request from the{" "}
              <Link to="/support" className="font-semibold text-accent">
                Support page
              </Link>{" "}
              (you can attach the redacted diagnostics after reviewing them), or check{" "}
              <Link to="/app/health" className="font-semibold text-accent">
                Connection health
              </Link>{" "}
              first.
            </>
          }
        >
          {SUPPORT_URL ? (
            <a href={SUPPORT_URL} className="tap mt-4 inline-flex min-h-[44px] items-center rounded-control border border-border bg-surface px-4 text-sm font-semibold" rel="noopener noreferrer" target="_blank">
              Contact support directly
            </a>
          ) : null}
        </SettingCard>
      </section>

      <section className="space-y-3">
        <SectionLabel>Account</SectionLabel>
        <SettingCard icon="logout" tone="muted" title="Sign out" text="Signing out closes the connection and forgets account data on this phone. Your pairing with the PC stays, so signing back in needs no new pairing.">
          <Button className="mt-4" full onClick={() => void signOut()} busy={busy === "signout"} disabled={busy !== null}>
            Sign out
          </Button>
        </SettingCard>

        <SettingCard icon="key" tone="danger" title="Forget this installation" text="Deletes this phone’s signing key. Your PCs will no longer accept it until you pair again on the PC. Use this before handing the phone to someone else; you can also revoke it from any other device under Devices.">
          {!forgetArmed ? (
            <div className="mt-3 flex justify-end">
              <Button variant="danger-ghost" className="-mr-2" onClick={() => setForgetArmed(true)}>
                Forget this installation…
              </Button>
            </div>
          ) : (
            <div className="mt-4 space-y-3">
              <Notice tone="danger">This cannot be undone on this phone.</Notice>
              <div className="grid grid-cols-2 gap-2">
                <Button onClick={() => setForgetArmed(false)}>Keep</Button>
                <Button variant="danger" onClick={() => void forget()} busy={busy === "forget"} disabled={busy !== null}>
                  Delete key and sign out
                </Button>
              </div>
            </div>
          )}
        </SettingCard>
      </section>
    </div>
  );
}

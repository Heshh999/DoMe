/** Privacy notice — DRAFT for founder review. Describes what the software actually does today. */
import { DraftBanner } from "./DraftBanner.tsx";

export function PrivacyPage() {
  return (
    <article className="prose-dome space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Privacy notice</h1>
        <DraftBanner />
      </header>
      <section className="space-y-2 text-sm text-text-muted">
        <h2 className="text-lg font-semibold text-text">What DoMe is</h2>
        <p>DoMe lets you control your own Windows PC from your phone through a service we operate (the “DoMe service”). This notice describes what data moves where. It is written to match the software as built; it is not a statement of legal compliance until reviewed.</p>
      </section>
      <section className="space-y-2 text-sm text-text-muted">
        <h2 className="text-lg font-semibold text-text">Data we hold for your account</h2>
        <ul className="list-disc pl-5 space-y-1">
          <li>Account: the identifier, email and display name from your sign-in provider; session records (creation time, hashed IP, browser type).</li>
          <li>Devices: your PCs (name, platform, agent version, connection times), your paired phones (name, public key identifier, last seen) and the permissions you granted each phone for each PC.</li>
          <li>Pairing: a hash of each pairing code and its outcome. The code itself is never sent to us.</li>
          <li>Commands: a lifecycle record per command — which phone, which PC, the action name, state, timing and error code. Not the parameters, results, typed text or media titles.</li>
          <li>Security events: sign-ins, pairing attempts, revocations and refused requests, with hashed IP addresses.</li>
          <li>Billing (when Pro launches): subscription state and provider identifiers. Card details are handled by the payment provider, never by DoMe.</li>
        </ul>
      </section>
      <section className="space-y-2 text-sm text-text-muted">
        <h2 className="text-lg font-semibold text-text">Data that passes through the DoMe service</h2>
        <p>To route commands, the service receives signed commands from your phone and the current state your PC reports (playback state, open YouTube tab titles, media titles, volume, approved app names and window titles). It forwards them between your devices and keeps the latest PC state in memory only while the PC is connected. Connections are encrypted in transit (TLS). The service can read what it routes: DoMe is not end-to-end encrypted and we do not claim otherwise.</p>
      </section>
      <section className="space-y-2 text-sm text-text-muted">
        <h2 className="text-lg font-semibold text-text">On your phone</h2>
        <ul className="list-disc pl-5 space-y-1">
          <li>A signing key that cannot be exported, your selected PC and your controller identifier are stored in the browser’s storage. No passwords or long-lived tokens are stored.</li>
          <li>The app does not use analytics, advertising or session-replay scripts and does not cache your PC’s data for offline use.</li>
          <li>The camera is used only on the pairing page while you scan a code.</li>
        </ul>
      </section>
      <section className="space-y-2 text-sm text-text-muted">
        <h2 className="text-lg font-semibold text-text">On your PC</h2>
        <p>The DoMe app keeps its key material protected by Windows, the list of phones you approved, the apps you approved, and a short bounded journal of executed commands. Logs are redacted. The browser extension only runs on YouTube pages and does not collect browsing history.</p>
      </section>
      <section className="space-y-2 text-sm text-text-muted">
        <h2 className="text-lg font-semibold text-text">Your choices</h2>
        <ul className="list-disc pl-5 space-y-1">
          <li>Revoke any phone or unlink any PC at any time from the app; disable remote control locally on the PC.</li>
          <li>Diagnostics are only created when you ask for them, and you see the file before sharing it.</li>
          <li>Account deletion removes your devices and pairings and ends any subscription; records we must keep for business or legal reasons (such as billing records) are retained for the required period. [FOUNDER: confirm retention periods and legal basis.]</li>
        </ul>
      </section>
      <section className="space-y-2 text-sm text-text-muted">
        <h2 className="text-lg font-semibold text-text">Open items for founder review</h2>
        <ul className="list-disc pl-5 space-y-1">
          <li>Legal entity name, address and contact for privacy requests.</li>
          <li>Jurisdiction, applicable law and supervisory authority; whether GDPR/UK GDPR/CCPA terms apply to the launch regions.</li>
          <li>Sub-processors: hosting provider, identity provider, payment provider, email provider.</li>
          <li>Retention table per record type (see docs/DATA_RETENTION.md when written).</li>
        </ul>
      </section>
    </article>
  );
}

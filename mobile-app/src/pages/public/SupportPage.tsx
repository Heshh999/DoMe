import { Link } from "react-router";

const SUPPORT_URL: string | undefined = import.meta.env.VITE_DOME_SUPPORT_URL;

const TOPICS: Array<{ title: string; steps: string[] }> = [
  { title: "The PC shows Offline", steps: ["Check the PC is awake and connected to the internet.", "Click the DoMe tray icon on the PC; it should say Connected. Use Reconnect if not.", "If the tray icon is missing, start DoMe from the Start menu."] },
  { title: "YouTube controls say the extension is not connected", steps: ["Open Chrome or Edge on the PC and check the DoMe extension is installed and enabled.", "Reload the YouTube tab so the extension attaches to it.", "Restart the browser if the extension icon still shows it is disconnected."] },
  { title: "The pairing code is rejected", steps: ["Codes last 5 minutes and work once. Ask the PC for a new one (tray → Pair a phone).", "Make sure the phone is signed in to the same account the PC is linked to.", "Compare the six-digit verification code on both screens before approving on the PC."] },
  { title: "This phone was revoked", steps: ["Revocation is permanent for that installation. Pair again from the PC.", "If you did not revoke it yourself, review Devices and remove anything you do not recognise."] },
  { title: "An action shows “Outcome unknown”", steps: ["The PC lost its connection while running it. It may or may not have happened.", "Check the PC’s current state (it refreshes when the PC reconnects) before sending the same action again."] },
  { title: "Microphone or dictation does not work", steps: ["DoMe uses your keyboard’s dictation; check that dictation is enabled in iOS Settings → General → Keyboard.", "Typing a command works exactly the same way."] },
];

export function SupportPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Support</h1>
        <p className="text-text-muted mt-2">Most problems have a specific cause that DoMe reports directly in the app. The steps below cover the common ones.</p>
      </header>
      <section className="space-y-3">
        {TOPICS.map((t) => (
          <details key={t.title} className="rounded-card border border-border p-4">
            <summary className="cursor-pointer font-semibold">{t.title}</summary>
            <ol className="list-decimal pl-5 text-sm text-text-muted mt-2 space-y-1">
              {t.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
          </details>
        ))}
      </section>
      <section className="rounded-card bg-bg-elevated border border-border p-5 space-y-2">
        <h2 className="font-semibold text-lg">Contact</h2>
        {SUPPORT_URL ? (
          <a href={SUPPORT_URL} className="tap inline-flex items-center rounded-control bg-accent text-on-accent font-semibold px-5" rel="noopener noreferrer" target="_blank">
            Contact support
          </a>
        ) : (
          <p className="text-sm text-text-muted">A support contact has not been set up for this deployment yet. When it is, you will find it here and under Settings & help in the app.</p>
        )}
        <p className="text-sm text-text-muted">
          When you write in, attach the redacted diagnostics file from <Link to="/app/settings" className="text-accent font-semibold">Settings & help</Link>. It contains versions and connection states, never your commands’ content or media titles.
        </p>
      </section>
    </div>
  );
}

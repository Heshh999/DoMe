/** Product explanation. No testimonials, counts, certifications or platform claims that are not verified. */
import { Link } from "react-router";

const STEPS = [
  { title: "Install DoMe on your Windows PC", text: "Sign in with your DoMe account on the PC and name it. The PC connects out to the DoMe service; you never open ports or change your router." },
  { title: "Pair your phone", text: "The PC shows a QR code. Scan it here, compare the six-digit verification code on both screens, and approve on the PC." },
  { title: "Control from the couch", text: "Play, pause, skip and seek YouTube in the PC’s browser — even while another window has focus — plus volume, approved apps, lock and confirmed power actions." },
];

const CAPABILITIES = [
  ["YouTube controls", "Play/pause, next, previous, seek, player volume and mute for the right tab, through the DoMe browser extension on the PC."],
  ["Windows media and volume", "Pause or skip whatever Windows is playing, and set the exact system volume."],
  ["Approved apps", "Open, bring to front, minimise or close apps you approved on the PC — nothing else."],
  ["Lock and power", "Lock Windows in one tap. Sleep, restart and shut down always ask for a confirmation on your phone and run a cancellable countdown on the PC."],
  ["Typed commands", "“Skip this video”, “set volume to 35”, “open Discord” — understood on your phone without any AI service."],
  ["Honest results", "Every control shows what the PC actually did. “Sent” is never shown as “done”."],
];

export function LandingPage() {
  return (
    <div className="space-y-12">
      <section className="text-center pt-4">
        <p className="text-sm font-semibold text-accent uppercase tracking-wider">DoMe</p>
        <h1 className="text-4xl sm:text-5xl font-bold tracking-tight mt-2">Your phone is a simple, secure remote for your PC.</h1>
        <p className="text-lg text-text-muted mt-4 max-w-xl mx-auto">Control YouTube, media, volume and approved apps on your own Windows PC from your iPhone — at home or away. Free to use; no credit card.</p>
        <div className="mt-6 flex flex-wrap gap-3 justify-center">
          <Link to="/app" className="tap inline-flex items-center rounded-control bg-accent text-on-accent font-semibold px-6 text-base">
            Open the app
          </Link>
          <Link to="/download" className="tap inline-flex items-center rounded-control border border-border bg-surface font-semibold px-6 text-base">
            Get DoMe for Windows
          </Link>
        </div>
        <p className="text-xs text-text-faint mt-3">DoMe is in development. The Windows installer is not published yet — see Download for status.</p>
      </section>

      <section aria-labelledby="how">
        <h2 id="how" className="text-2xl font-bold tracking-tight mb-4">
          How it works
        </h2>
        <ol className="grid gap-4 sm:grid-cols-3">
          {STEPS.map((s, i) => (
            <li key={s.title} className="rounded-card bg-bg-elevated border border-border p-4">
              <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-accent text-on-accent font-bold">{i + 1}</span>
              <h3 className="font-semibold mt-3">{s.title}</h3>
              <p className="text-sm text-text-muted mt-1">{s.text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="what">
        <h2 id="what" className="text-2xl font-bold tracking-tight mb-4">
          What you can do
        </h2>
        <ul className="grid gap-3 sm:grid-cols-2">
          {CAPABILITIES.map(([title, text]) => (
            <li key={title} className="rounded-card border border-border p-4">
              <h3 className="font-semibold">{title}</h3>
              <p className="text-sm text-text-muted mt-1">{text}</p>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="security" className="rounded-card bg-bg-elevated border border-border p-5">
        <h2 id="security" className="text-2xl font-bold tracking-tight">
          Built to be trusted with your PC
        </h2>
        <ul className="mt-3 space-y-2 text-sm text-text-muted list-disc pl-5">
          <li>Your PC never listens on the internet. It opens one outbound, authenticated connection to the DoMe service.</li>
          <li>Each phone has its own signing key that never leaves the device. The PC accepts only phones you approved on the PC with a matching verification code.</li>
          <li>Phones send action names and checked settings — never file paths, arguments or scripts. Apps must be approved on the PC first.</li>
          <li>Disruptive actions (closing apps, sleep, restart, shutdown) need a fresh confirmation on your phone every time.</li>
          <li>Revoke a phone or disable remote control on the PC at any moment. Security controls are free for everyone.</li>
          <li>Transport is encrypted (TLS). The DoMe service can see routed commands and PC state; it is not end-to-end encrypted and we say so in the <Link to="/privacy" className="text-accent font-semibold">privacy notice</Link>.</li>
        </ul>
      </section>

      <section className="text-center">
        <h2 className="text-2xl font-bold tracking-tight">Free today. Pro when it is worth it.</h2>
        <p className="text-text-muted mt-2">Core remote control stays free. Pro adds more PCs, custom layouts and routines when those features ship.</p>
        <Link to="/pricing" className="tap inline-flex items-center mt-4 text-accent font-semibold">
          Compare Free and Pro ›
        </Link>
      </section>
    </div>
  );
}

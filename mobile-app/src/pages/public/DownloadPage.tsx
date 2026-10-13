/** Download page. Links stay disabled with honest copy until a signed installer exists. */
import { Link } from "react-router";

import { Icon, type IconName } from "../../components/icons.tsx";
import { PublicIcon } from "./publicIcons.tsx";

const NEEDS: Array<{ icon: IconName; title: string; text: string }> = [
  { icon: "pc", title: "Windows 10 or 11, 64-bit", text: "Windows 10 (version 1809 or later) or Windows 11, 64-bit, signed in to a normal user account (no administrator rights for everyday use). The agent runs inside your signed-in session; it does not run as a service." },
  { icon: "youtube", title: "Chrome or Edge, for YouTube control", text: "Chrome or Edge (current stable release) with the DoMe extension if you want YouTube control. The extension will be published through the browser stores; developer loading is only for development. Windows media, volume, apps, lock, power and the touchpad/keyboard work without it." },
  { icon: "shield", title: "Internet access, nothing else", text: "Internet access from the PC. No port forwarding, VPN or extra software." },
];

const IPHONE_STEPS = ["Open this site in Safari and sign in.", "Tap Share, then “Add to Home Screen”, then Add.", "Open DoMe from the home screen and pair it with your PC under Devices."];

export function DownloadPage() {
  return (
    <div>
      <header className="dome-page-intro animate-rise">
        <p className="dome-eyebrow">Download</p>
        <h1 className="mt-3 text-h1 font-bold">DoMe for Windows</h1>
        <p className="mt-4 max-w-2xl text-lg text-text-muted">The Windows app runs in your user session with a tray icon, links to your account, and lets you approve phones and apps.</p>
      </header>

      <section aria-labelledby="download-title" className="dome-card p-6 sm:p-8">
        <div className="flex items-start gap-4">
          <span className="dome-well">
            <PublicIcon name="download" size={22} />
          </span>
          <div className="min-w-0">
            <h2 id="download-title" className="text-title font-semibold">
              Windows 10 and 11
            </h2>
            <p className="mt-1 text-sm text-text-muted">One official download path: a signed installer with a checksum, listed here when it exists.</p>
          </div>
        </div>
        <button type="button" disabled className="mt-6 flex min-h-[52px] w-full cursor-not-allowed items-center justify-center gap-2 rounded-full border border-border bg-surface px-5 text-[15px] font-semibold text-text-faint" aria-describedby="download-status" data-testid="download-button">
          <PublicIcon name="clock" size={18} />
          Download for Windows 10/11 — not yet published
        </button>
        <p id="download-status" className="mt-5 text-[15px] leading-relaxed text-text-muted">
          <strong className="font-semibold text-text">Release status: not yet published.</strong> This is the one official download path and it will appear here, with a checksum and a signed installer, once the installer, update channel and uninstall have been verified. We will not publish an unsigned build, and there is no “beta” to download today. If a download or installation ever fails, the page will say what went wrong and what to do — never to disable antivirus or other protection.
        </p>
        <p className="mt-3 text-[15px] text-text-muted">
          Until then, see the{" "}
          <Link to="/release-notes" className="font-semibold text-accent underline decoration-accent/35 underline-offset-[3px] hover:decoration-current">
            release history and known issues
          </Link>
          .
        </p>
      </section>

      <section aria-labelledby="needs" className="mt-14">
        <h2 id="needs" className="text-title font-semibold">
          What the app will need
        </h2>
        <ul className="dome-hairlines mt-4">
          {NEEDS.map((n) => (
            <li key={n.title} className="flex gap-4 py-5">
              <span className="dome-well dome-well-sm">
                <Icon name={n.icon} size={18} />
              </span>
              <div className="min-w-0">
                <h3 className="font-semibold">{n.title}</h3>
                <p className="mt-1 text-[15px] leading-relaxed text-text-muted">{n.text}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="iphone" className="mt-14">
        <h2 id="iphone" className="text-title font-semibold">
          Install DoMe on your iPhone
        </h2>
        <p className="mt-2 text-[15px] text-text-muted">No App Store download: the app is this website, added to your home screen.</p>
        <ol className="mt-5 grid gap-3 sm:grid-cols-3">
          {IPHONE_STEPS.map((step, i) => (
            <li key={step} className="dome-card p-5">
              <span className="dome-step-badge" aria-hidden="true">
                {i + 1}
              </span>
              <p className="mt-3 text-[15px] leading-relaxed text-text-muted">{step}</p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

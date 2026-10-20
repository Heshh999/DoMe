/**
 * Download page. The button stays disabled with honest copy until a signed installer exists; then it is the
 * real download of the release named in src/content/windows-release.json (see windowsRelease.ts for where
 * that file comes from and the signature test that allows the button).
 */
import { Link } from "react-router";

import { Icon, type IconName } from "../../components/icons.tsx";
import { formatDate, formatSize, getWindowsRelease, type WindowsRelease } from "../../content/windowsRelease.ts";
import { PublicIcon } from "./publicIcons.tsx";

/**
 * Smart App Control (Windows 11) checks every file Windows loads, and the app's bundled libraries are not signed, only
 * DoMe.exe, the browser helper, Setup and the uninstaller are (docs/SECURITY.md T12). Not tested on such a PC yet.
 */
export const SMART_APP_CONTROL_NOTE =
  "Not yet verified on Windows 11 PCs with Smart App Control on; if it blocks DoMe, do not turn it off. See the release page for what to do.";

const NEEDS: Array<{ icon: IconName; title: string; text: string }> = [
  { icon: "pc", title: "Windows 10 or 11, 64-bit", text: "Windows 10 (version 1809 or later) or Windows 11, 64-bit, signed in to a normal user account (no administrator rights for everyday use). The agent runs inside your signed-in session; it does not run as a service." },
  { icon: "youtube", title: "Chrome or Edge, for YouTube control", text: "Chrome or Edge (current stable release) with the DoMe extension if you want YouTube control. The extension will be published through the browser stores; developer loading is only for development. Windows media, volume, apps, lock, power and the touchpad/keyboard work without it." },
  { icon: "shield", title: "Internet access, nothing else", text: "Internet access from the PC. No port forwarding, VPN or extra software." },
];

const IPHONE_STEPS = ["Open this site in Safari and sign in.", "Tap Share, then “Add to Home Screen”, then Add.", "Open DoMe from the home screen and pair it with your PC under Devices."];

const LINK_CLASS = "font-semibold text-accent underline decoration-accent/35 underline-offset-[3px] hover:decoration-current";

/** No signed release yet: the disabled control with its full name, drawn as a quiet status line. */
function NotPublished() {
  return (
    <>
      {/* still the (disabled) download control with its full name, but drawn as a quiet status line, never a button-shaped pill */}
      <button type="button" disabled aria-label="Download for Windows 10/11 — not yet published" className="mt-6 flex min-h-[44px] w-full cursor-not-allowed items-center justify-start gap-2 border-0 bg-transparent px-0 text-left text-[15px] font-semibold text-text-muted" aria-describedby="download-status" data-testid="download-button">
        <PublicIcon name="clock" size={18} />
        Windows app: not yet published
      </button>
      <p id="download-status" className="mt-3 text-[15px] leading-relaxed text-text-muted">
        <strong className="font-semibold text-text">Release status: not yet published.</strong> A signed installer with a checksum will appear here once install, update and uninstall have been verified — never an unsigned build, and if an install ever fails, this page will say what to do, never to disable antivirus or other protection.
      </p>
      <p className="mt-3 text-[15px] text-text-muted">
        Until then, see the{" "}
        <Link to="/release-notes" className="font-semibold text-accent underline decoration-accent/35 underline-offset-[3px] hover:decoration-current">
          release history and known issues
        </Link>
        .
      </p>
    </>
  );
}

/** A signed release: the real download, its checksum, the publisher Windows will show, honest caveats. */
function Published({ release }: { release: WindowsRelease }) {
  const file = `DoMe-Setup-${release.version}.exe`;
  return (
    <>
      <a href={release.url} download data-testid="download-button" aria-describedby="download-status" className="tap dome-primary dome-press mt-6 inline-flex min-h-[48px] w-full items-center justify-center gap-2 rounded-full px-6 text-center text-base font-semibold sm:w-auto">
        <PublicIcon name="download" size={18} />
        Download {file} ({formatSize(release.size)})
      </a>
      <p id="download-status" className="mt-3 text-[15px] leading-relaxed text-text-muted">
        <strong className="font-semibold text-text">
          Release status: {release.version}, published {formatDate(release.publishedAt)}.
        </strong>{" "}
        Signed for Windows by {release.publisher} through Azure Artifact Signing. SHA-256 <code className="break-all font-mono text-[13px]">{release.sha256}</code>
      </p>
      <details className="mt-3 text-[15px] text-text-muted">
        <summary className="cursor-pointer font-semibold text-text">Check the file before you run it</summary>
        <p className="mt-2">In PowerShell, in the folder you downloaded it to:</p>
        <pre className="mt-2 overflow-x-auto rounded-control border border-border bg-bg-sunken p-2 font-mono text-[13px]">
          <code>Get-FileHash .\{file} -Algorithm SHA256</code>
        </pre>
        <p className="mt-2">and compare the result with the SHA-256 above.</p>
      </details>
      <p className="mt-3 text-[15px] leading-relaxed text-text-muted">
        While a version is new, Windows may show “Windows protected your PC”. The dialog names the publisher: continue only if it says {release.publisher}. We never ask you to turn off antivirus or other protection.
      </p>
      <p className="mt-3 text-[15px] leading-relaxed text-text-muted" data-testid="smart-app-control">
        {SMART_APP_CONTROL_NOTE}
      </p>
      {release.browserAddon ? null : <p className="mt-3 text-[15px] leading-relaxed text-text-muted">This version does not include YouTube control in Chrome or Edge; everything else in DoMe is included.</p>}
      <p className="mt-3 text-[15px] text-text-muted">
        See the{" "}
        <a href={release.releasePage} className={LINK_CLASS} rel="noopener noreferrer">
          release page on GitHub
        </a>{" "}
        and the{" "}
        <Link to="/release-notes" className={LINK_CLASS}>
          release history and known issues
        </Link>
        .
      </p>
    </>
  );
}

export function DownloadPage() {
  const release = getWindowsRelease();
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
        {release ? <Published release={release} /> : <NotPublished />}
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

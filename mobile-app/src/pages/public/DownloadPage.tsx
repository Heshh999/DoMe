/** Download page. Links stay disabled with honest copy until a signed installer exists. */
export function DownloadPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Download DoMe for Windows</h1>
        <p className="text-text-muted mt-2">The Windows app runs in your user session with a tray icon, links to your account, and lets you approve phones and apps.</p>
      </header>
      <div className="rounded-card border border-border p-5 space-y-3">
        <button type="button" disabled className="w-full rounded-control border border-border py-3.5 font-semibold text-text-faint cursor-not-allowed" aria-describedby="download-status" data-testid="download-button">
          Download for Windows 10/11 — not yet published
        </button>
        <p id="download-status" className="text-sm text-text-muted">
          <strong className="text-text">Release status: not yet published.</strong> This is the one official download path and it will appear here, with a checksum and a signed installer, once the installer, update channel and uninstall have been verified. We will not publish an unsigned build, and there is no “beta” to download today. If a download or installation ever fails, the page will say what went wrong and what to do — never to disable antivirus or other protection.
        </p>
        <p className="text-sm text-text-muted">
          Until then, see the <a href="/release-notes" className="text-accent font-semibold">release history and known issues</a>.
        </p>
      </div>
      <section className="space-y-2">
        <h2 className="text-xl font-bold">What the app will need</h2>
        <ul className="list-disc pl-5 text-sm text-text-muted space-y-1">
          <li>Windows 10 (version 1809 or later) or Windows 11, 64-bit, signed in to a normal user account (no administrator rights for everyday use). The agent runs inside your signed-in session; it does not run as a service.</li>
          <li>Chrome or Edge (current stable release) with the DoMe extension if you want YouTube control. The extension will be published through the browser stores; developer loading is only for development. Windows media, volume, apps, lock, power and the touchpad/keyboard work without it.</li>
          <li>Internet access from the PC. No port forwarding, VPN or extra software.</li>
        </ul>
      </section>
      <section className="space-y-2">
        <h2 className="text-xl font-bold">Install DoMe on your iPhone</h2>
        <ol className="list-decimal pl-5 text-sm text-text-muted space-y-1">
          <li>Open this site in Safari and sign in.</li>
          <li>Tap Share, then “Add to Home Screen”, then Add.</li>
          <li>Open DoMe from the home screen and pair it with your PC under Devices.</li>
        </ol>
      </section>
    </div>
  );
}

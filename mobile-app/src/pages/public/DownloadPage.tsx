/** Download page. Links stay disabled with honest copy until a signed installer exists. */
export function DownloadPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">Download DoMe for Windows</h1>
        <p className="text-text-muted mt-2">The Windows app runs in your user session with a tray icon, links to your account, and lets you approve phones and apps.</p>
      </header>
      <div className="rounded-card border border-border p-5 space-y-3">
        <button type="button" disabled className="w-full rounded-control border border-border py-3.5 font-semibold text-text-faint cursor-not-allowed" aria-describedby="download-status">
          Download for Windows 10/11 — coming soon
        </button>
        <p id="download-status" className="text-sm text-text-muted">
          There is no public installer yet. A signed, verified installer and update channel are part of the public-release work; we will not publish an unsigned build. Nothing here is a “beta” you can download today.
        </p>
      </div>
      <section className="space-y-2">
        <h2 className="text-xl font-bold">What the app will need</h2>
        <ul className="list-disc pl-5 text-sm text-text-muted space-y-1">
          <li>Windows 10 or 11, a normal user account (no administrator rights for everyday use).</li>
          <li>Chrome or Edge with the DoMe extension if you want YouTube control. The extension will be published through the browser stores; developer loading is only for development.</li>
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

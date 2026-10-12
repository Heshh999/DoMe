/** Full-screen offline state. Deliberately looks disconnected: no controls, no PC status. */
import { Wordmark } from "./brand.tsx";
import { Button, IconWell } from "./ui.tsx";

export function OfflineScreen() {
  return (
    <main role="main" className="relative isolate flex min-h-dvh flex-col items-center justify-center px-6 pt-[var(--safe-top)] pb-[calc(4rem+var(--safe-bottom))] text-center text-text">
      <div className="dome-backdrop" aria-hidden="true" />
      <IconWell icon="offline" tone="muted" size={76} radius={24} />
      <h1 className="mt-6 text-[1.75rem] font-bold tracking-[-0.03em]">You’re offline</h1>
      <p className="mt-2 max-w-sm text-text-muted">DoMe needs an internet connection to reach your PC through the DoMe service. Nothing is sent while you are offline, and nothing will run later on its own.</p>
      <p className="mt-4 text-sm text-text-faint">Controls return as soon as your phone is back online.</p>
      <Button className="mt-7 min-w-[11rem]" variant="primary" size="lg" onClick={() => window.location.reload()}>
        Try again
      </Button>
      <Wordmark compact title={null} className="absolute bottom-[calc(1.75rem+var(--safe-bottom))] h-5 w-auto opacity-35" />
    </main>
  );
}

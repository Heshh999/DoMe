/** Full-screen offline state. Deliberately looks disconnected: no controls, no PC status. */
import { Button } from "./ui.tsx";

export function OfflineScreen() {
  return (
    <main role="main" className="min-h-dvh flex flex-col items-center justify-center text-center px-6 pt-[var(--safe-top)] pb-[var(--safe-bottom)] bg-bg text-text">
      <div aria-hidden="true" className="text-5xl mb-4">⌁</div>
      <h1 className="text-2xl font-bold">You’re offline</h1>
      <p className="text-text-muted mt-2 max-w-sm">DoMe needs an internet connection to reach your PC through the DoMe service. Nothing is sent while you are offline, and nothing will run later on its own.</p>
      <p className="text-text-faint text-sm mt-4">Controls return as soon as your phone is back online.</p>
      <Button className="mt-6" onClick={() => window.location.reload()}>
        Try again
      </Button>
    </main>
  );
}

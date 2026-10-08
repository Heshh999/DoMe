import { Link } from "react-router";

export function NotFoundPage() {
  return (
    <main className="min-h-dvh bg-bg text-text flex flex-col items-center justify-center text-center px-6">
      <h1 className="text-3xl font-bold">Page not found</h1>
      <p className="text-text-muted mt-2">That address does not exist in DoMe.</p>
      <div className="mt-6 flex gap-3">
        <Link to="/" className="tap inline-flex items-center rounded-control border border-border px-5 font-semibold">
          Website
        </Link>
        <Link to="/app" className="tap inline-flex items-center rounded-control bg-accent text-on-accent px-5 font-semibold">
          Open app
        </Link>
      </div>
    </main>
  );
}

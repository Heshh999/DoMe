/** Public website chrome: header with navigation and an "Open app" entry, footer with legal links. */
import { Link, NavLink, Outlet } from "react-router";

const NAV = [
  { to: "/pricing", label: "Pricing" },
  { to: "/faq", label: "FAQ" },
  { to: "/download", label: "Download" },
  { to: "/support", label: "Support" },
];

export function Logo() {
  return (
    <Link to="/" className="tap inline-flex items-center gap-2 font-bold text-lg tracking-tight">
      <span aria-hidden="true" className="inline-block h-7 w-7 rounded-lg bg-accent" style={{ clipPath: "polygon(0 100%, 0 55%, 50% 0, 100% 55%, 100% 100%)" }} />
      DoMe
    </Link>
  );
}

export function PublicLayout() {
  return (
    <div className="min-h-dvh bg-bg text-text flex flex-col">
      <header className="border-b border-border">
        <div className="max-w-3xl mx-auto px-4 pt-[calc(var(--safe-top)+0.5rem)] pb-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <Logo />
          <nav aria-label="Site" className="flex items-center gap-1 overflow-x-auto">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} className={({ isActive }) => `tap inline-flex items-center px-3 text-sm font-medium rounded-control ${isActive ? "text-accent" : "text-text-muted hover:text-text"}`}>
                {n.label}
              </NavLink>
            ))}
            <Link to="/app" className="tap inline-flex items-center px-3.5 rounded-control bg-accent text-on-accent text-sm font-semibold ml-1">
              Open app
            </Link>
          </nav>
        </div>
      </header>
      <main className="flex-1 w-full max-w-3xl mx-auto px-4 py-8">
        <Outlet />
      </main>
      <footer className="border-t border-border text-sm text-text-muted">
        <div className="max-w-3xl mx-auto px-4 py-6 pb-[calc(1.5rem+var(--safe-bottom))] flex flex-wrap gap-x-5 gap-y-2 items-center justify-between">
          <p>DoMe — your phone is a simple, secure remote for your PC.</p>
          <nav aria-label="Legal" className="flex gap-4">
            <Link to="/privacy" className="tap inline-flex items-center hover:text-text">
              Privacy
            </Link>
            <Link to="/terms" className="tap inline-flex items-center hover:text-text">
              Terms
            </Link>
            <Link to="/support" className="tap inline-flex items-center hover:text-text">
              Support
            </Link>
            <Link to="/release-notes" className="tap inline-flex items-center hover:text-text">
              Release notes
            </Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}

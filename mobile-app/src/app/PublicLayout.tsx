/**
 * Public website chrome: a translucent header (wordmark, site links with the current page underlined,
 * the Light / Auto / Dark switch, "Open app"; on phones the links and the switch sit in a menu under the
 * header), and a footer with the full DoMe REMOTE lockup, every page and the switch again. The home,
 * pricing, FAQ and demo pages lay out their own full-width sections; every other page gets a reading column.
 * Page styles live in src/styles/public.css, scoped to `.dome-public` (styles.css is the design system).
 */
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";

import "../styles/public.css";
import { Wordmark } from "../components/brand.tsx";
import { Icon } from "../components/icons.tsx";
import { ThemeSwitch } from "../components/ThemeSwitch.tsx";
import { FOOTER, NAV } from "../content/nav.ts";
import { publicTitle, useDocumentTitle } from "./documentTitle.ts";

/** Pages that draw their own full-width sections. */
const WIDE = new Set(["/", "/pricing", "/faq", "/demo"]);

export function Logo({ className = "h-[26px]" }: { className?: string }) {
  return (
    <Link to="/" aria-label="DoMe Remote home" className="tap inline-flex shrink-0 items-center rounded-lg">
      <Wordmark compact title={null} className={`w-auto ${className}`} />
    </Link>
  );
}

export function PublicLayout() {
  const { pathname } = useLocation();
  // the phone menu remembers the page it was opened on, so any navigation (a link in it, back/forward) closes it
  const [menuAt, setMenuAt] = useState<string | null>(null);
  const menu = menuAt === pathname;
  const close = () => setMenuAt(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  useDocumentTitle(publicTitle(pathname));
  // Escape closes the phone menu and puts focus back on the Menu button.
  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setMenuAt(null);
      menuButton.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menu]);
  return (
    <div className="dome-public relative isolate flex min-h-dvh flex-col text-text">
      <a href="#main" className="dome-skip">
        Skip to content
      </a>
      <div className="dome-backdrop" aria-hidden="true" />
      <header className="dome-bar sticky top-0 z-30 border-b border-border pt-[var(--safe-top)]">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-3 px-5 lg:px-8">
          <Logo />
          <nav aria-label="Site" className="ml-4 hidden items-center gap-0.5 md:flex lg:ml-8">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} className={({ isActive }) => `dome-nav-link tap inline-flex items-center rounded-full px-2.5 text-[14px] font-medium transition-colors hover:bg-surface lg:px-3 lg:text-[15px] ${isActive ? "text-text" : "text-text-muted hover:text-text"}`}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <div className="hidden md:block">
              <ThemeSwitch labels="lg" />
            </div>
            {/* below md the hero carries the page's one solid-blue primary, so this one is outlined there (public.css) */}
            <Link to="/app" className="dome-header-cta tap dome-primary dome-press inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-4 text-[15px] font-semibold max-[359px]:px-3 max-[359px]:text-[14px]">
              Open the app
            </Link>
            <button ref={menuButton} type="button" aria-label={menu ? "Close menu" : "Menu"} aria-expanded={menu} aria-controls={menu ? "site-menu" : undefined} onClick={() => setMenuAt(menu ? null : pathname)} className="dome-press -mr-2 inline-flex h-11 w-11 items-center justify-center rounded-full text-text hover:bg-surface md:hidden">
              <Icon name={menu ? "close" : "menu"} />
            </button>
          </div>
        </div>
        {menu ? (
          <div id="site-menu" className="dome-menu border-t border-border md:hidden">
            <nav aria-label="Site menu" className="mx-auto max-w-6xl px-3 py-2">
              {NAV.map((n) => (
                <NavLink key={n.to} to={n.to} onClick={close} className={({ isActive }) => `tap flex min-h-[52px] items-center justify-between rounded-control px-3 text-[17px] font-semibold ${isActive ? "text-accent" : "text-text"}`}>
                  {n.label}
                  <Icon name="chevron" size={18} className="text-text-faint" />
                </NavLink>
              ))}
            </nav>
            <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-3 gap-y-3 border-t border-border px-5 py-4 pb-[calc(1rem+var(--safe-bottom))]">
              {/* the switch is itself named "Appearance": on narrow phones the visible word gives way to it */}
              <span className="text-sm font-medium text-text-muted max-[399px]:sr-only">Appearance</span>
              <ThemeSwitch labels />
            </div>
          </div>
        ) : null}
      </header>

      <main id="main" tabIndex={-1} className="flex-1 outline-none">
        {WIDE.has(pathname) ? (
          <Outlet />
        ) : (
          // a reading column that starts at the logo's x (the header's max-w-6xl), not centred on its own
          <div className="mx-auto w-full max-w-6xl px-5 py-12 lg:px-8 lg:py-16">
            <div className="max-w-3xl">
              <Outlet />
            </div>
          </div>
        )}
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto grid max-w-6xl grid-cols-2 gap-x-6 gap-y-10 px-5 py-14 sm:grid-cols-3 lg:grid-cols-[1.7fr_1fr_1fr_1fr] lg:px-8 lg:py-16">
          <div className="col-span-2 sm:col-span-3 lg:col-span-1">
            <Link to="/" aria-label="DoMe Remote home" className="inline-flex rounded-lg">
              <Wordmark title={null} className="h-[64px] w-auto sm:h-[72px]" />
            </Link>
            <p className="mt-5 max-w-xs text-[15px] text-text-muted">Your phone is a simple, secure remote for your PC.</p>
            <div className="mt-6">
              <ThemeSwitch labels />
            </div>
          </div>
          {FOOTER.map((group) => (
            <nav key={group.title} aria-label={group.title}>
              <h2 className="text-caption font-semibold uppercase tracking-[0.12em] text-text-faint">{group.title}</h2>
              <ul className="mt-2">
                {group.links.map((l) => (
                  <li key={l.to}>
                    <Link to={l.to} className="tap inline-flex min-w-11 items-center text-[15px] text-text-muted transition-colors hover:text-text">
                      {l.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>
        <div className="border-t border-border">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-5 py-6 pb-[calc(1.5rem+var(--safe-bottom))] text-xs text-text-faint lg:px-8">
            <p>DoMe Remote · for Windows PCs and iPhone</p>
            <p className="inline-flex items-center gap-2">
              <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-coral" />
              In development: the Windows app is not published yet.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}

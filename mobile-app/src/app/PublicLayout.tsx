/**
 * Public website chrome: a translucent header (wordmark, site links, Light/Auto/Dark switch, "Open app";
 * on phones the links and the switch sit in a menu), and a footer with every page and the switch again.
 * The home and pricing pages lay out their own full-width sections; every other page gets a reading column.
 */
import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";

import { Wordmark } from "../components/brand.tsx";
import { Icon } from "../components/icons.tsx";
import { ThemeSwitch } from "../components/ThemeSwitch.tsx";

const NAV = [
  { to: "/pricing", label: "Pricing" },
  { to: "/faq", label: "FAQ" },
  { to: "/download", label: "Download" },
  { to: "/support", label: "Support" },
];

const FOOTER: Array<{ title: string; links: Array<{ to: string; label: string }> }> = [
  {
    title: "Product",
    links: [
      { to: "/pricing", label: "Pricing" },
      { to: "/download", label: "Download" },
      { to: "/app", label: "Open the app" },
    ],
  },
  {
    title: "Help",
    links: [
      { to: "/faq", label: "FAQ" },
      { to: "/support", label: "Support" },
      { to: "/release-notes", label: "Release notes" },
    ],
  },
  {
    title: "Legal",
    links: [
      { to: "/privacy", label: "Privacy" },
      { to: "/terms", label: "Terms" },
    ],
  },
];

/** Pages that draw their own full-width sections. */
const WIDE = new Set(["/", "/pricing"]);

export function Logo({ className = "h-[26px]" }: { className?: string }) {
  return (
    <Link to="/" aria-label="DoMe Remote home" className="tap inline-flex shrink-0 items-center">
      <Wordmark compact title={null} className={`w-auto ${className}`} />
    </Link>
  );
}

export function PublicLayout() {
  const { pathname } = useLocation();
  const [menu, setMenu] = useState(false);
  const close = () => setMenu(false);
  return (
    <div className="relative isolate min-h-dvh text-text flex flex-col">
      <div className="dome-backdrop" aria-hidden="true" />
      <header className="sticky top-0 z-30 dome-bar border-b border-border pt-[var(--safe-top)]">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-3 px-5 lg:px-8">
          <Logo />
          <nav aria-label="Site" className="ml-6 hidden items-center gap-1 md:flex">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} className={({ isActive }) => `tap inline-flex items-center rounded-full px-3.5 text-[15px] font-medium transition-colors ${isActive ? "text-text" : "text-text-muted hover:text-text"}`}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <div className="hidden md:block">
              <ThemeSwitch />
            </div>
            <Link to="/app" className="tap dome-primary dome-press inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-4 text-[15px] font-semibold">
              Open app
            </Link>
            <button type="button" aria-label={menu ? "Close menu" : "Menu"} aria-expanded={menu} aria-controls="site-menu" onClick={() => setMenu((m) => !m)} className="dome-press inline-flex h-11 w-11 items-center justify-center rounded-full text-text hover:bg-surface md:hidden">
              <Icon name={menu ? "close" : "menu"} />
            </button>
          </div>
        </div>
        {menu ? (
          <div id="site-menu" className="animate-fade border-t border-border md:hidden">
            <nav aria-label="Site menu" className="mx-auto max-w-6xl px-3 py-2">
              {NAV.map((n) => (
                <NavLink key={n.to} to={n.to} onClick={close} className={({ isActive }) => `tap flex items-center justify-between rounded-control px-3 text-lg font-semibold ${isActive ? "text-accent" : "text-text"}`}>
                  {n.label}
                  <Icon name="chevron" size={18} className="text-text-faint" />
                </NavLink>
              ))}
            </nav>
            <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 border-t border-border px-5 py-4">
              <span className="text-sm text-text-muted">Appearance</span>
              <ThemeSwitch />
            </div>
          </div>
        ) : null}
      </header>

      <main id="main" className="flex-1">
        {WIDE.has(pathname) ? (
          <Outlet />
        ) : (
          <div className="mx-auto w-full max-w-3xl px-5 py-12 lg:py-16">
            <Outlet />
          </div>
        )}
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto grid max-w-6xl grid-cols-3 gap-x-4 gap-y-10 px-5 py-14 lg:px-8 md:grid-cols-[1.6fr_1fr_1fr_1fr]">
          <div className="col-span-3 md:col-span-1">
            <Logo className="h-7" />
            <p className="mt-4 max-w-xs text-sm text-text-muted">Your phone is a simple, secure remote for your PC.</p>
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
                    <Link to={l.to} className="tap inline-flex items-center text-[15px] text-text-muted transition-colors hover:text-text">
                      {l.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>
        <div className="border-t border-border">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-5 py-6 pb-[calc(1.5rem+var(--safe-bottom))] text-xs text-text-faint lg:px-8">
            <p>DoMe Remote · for Windows PCs and iPhone</p>
            <p>In development: the Windows app is not published yet.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}

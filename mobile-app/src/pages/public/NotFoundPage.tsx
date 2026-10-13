import { Link } from "react-router";

import "../../styles/public.css";
import { BrandMark } from "../../components/brand.tsx";
import { Icon } from "../../components/icons.tsx";

export function NotFoundPage() {
  return (
    <main className="dome-public relative isolate flex min-h-dvh flex-col items-center justify-center px-6 py-16 text-center text-text">
      <div className="dome-backdrop" aria-hidden="true" />
      <div className="animate-rise flex flex-col items-center">
        <BrandMark className="h-14 w-auto text-text-faint" />
        <p className="dome-eyebrow mt-8">404</p>
        <h1 className="mt-3 text-h1 font-bold">Page not found</h1>
        <p className="mt-4 max-w-sm text-lg text-text-muted">That address does not exist in DoMe.</p>
        <div className="mt-9 flex w-full flex-col items-center gap-3 sm:w-auto sm:flex-row">
          <Link to="/" className="tap dome-press inline-flex w-full items-center justify-center gap-2 rounded-full border border-border-strong bg-bg-elevated px-6 font-semibold hover:bg-surface sm:w-auto sm:min-h-[52px]">
            Website
          </Link>
          <Link to="/app" className="tap dome-primary dome-press inline-flex w-full items-center justify-center gap-2 rounded-full px-6 font-semibold sm:w-auto sm:min-h-[52px]">
            Open app
            <Icon name="arrow" size={18} />
          </Link>
        </div>
      </div>
    </main>
  );
}

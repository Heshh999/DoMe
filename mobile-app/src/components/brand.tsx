/**
 * The DoMe logo, drawn inline from the brand's own paths (brandPaths.ts ← brand/*.svg):
 * - `Wordmark`: the full lockup (DoMe + REMOTE) or the compact one (DoMe). Letters and ring take the
 *   theme's logo ink (Navy on light, White on dark — the two lockup files of brand/), the ring tip is
 *   Sky and the cursor Coral, as in every brand file.
 * - `BrandMark`: the ringed o alone; the ring follows the text colour.
 * - `AppIcon`: the app icon tile (DoMe Blue, white ring, Sky light tip, Coral cursor).
 * Decorative uses pass `title={null}`; otherwise the SVG is an image with an accessible name.
 */
import { BRAND_PATHS } from "./brandPaths.ts";

const SKY = "#4281fa";
const SKY_LIGHT = "#63a1fe";
const CORAL = "#f96054";
const DOME_BLUE = "#0142fb";

type Labelled = { title?: string | null; className?: string };

function a11y(title: string | null) {
  return title ? { role: "img" as const, "aria-label": title } : { "aria-hidden": true as const, focusable: "false" as const };
}

export function Wordmark({ compact = false, title, className = "" }: Labelled & { compact?: boolean }) {
  const p = BRAND_PATHS.wordmark;
  const name = title === undefined ? (compact ? "DoMe" : "DoMe Remote") : title;
  return (
    <svg viewBox={compact ? "0 0 1345 328" : "0 0 1345 408"} className={`text-logo-ink ${className}`} {...a11y(name)}>
      <g fill="currentColor">
        <path d={p.d} />
        <path d={p.m} />
        <path d={p.e} />
        <path d={p.ring} />
        {compact ? null : <path d={p.remote} />}
      </g>
      <path fill={SKY} d={p.tip} />
      <path fill={CORAL} d={p.cursor} />
    </svg>
  );
}

export function BrandMark({ title = null, className = "" }: Labelled) {
  const p = BRAND_PATHS.mark;
  return (
    <svg viewBox="0 0 349 264" className={className} {...a11y(title)}>
      <path fill="currentColor" d={p.ring} />
      <path fill={SKY} d={p.tip} />
      <path fill={CORAL} d={p.cursor} />
    </svg>
  );
}

export function AppIcon({ title = null, className = "" }: Labelled) {
  const p = BRAND_PATHS.icon;
  return (
    <svg viewBox="0 0 512 512" className={className} {...a11y(title)}>
      <rect width="512" height="512" rx="108" fill={DOME_BLUE} />
      <path fill="#ffffff" d={p.ring} />
      <path fill={SKY_LIGHT} d={p.tip} />
      <path fill={CORAL} d={p.cursor} />
    </svg>
  );
}

/**
 * Icons the film needs that the app's set (icons.tsx) does not have: the same 24-unit grid, 1.75 stroke, round caps.
 * Decorative only (aria-hidden): every meaning they carry is also in text next to them.
 */
import type { ReactNode } from "react";

export type FilmIconName = "bell" | "expand" | "compress" | "speaker" | "speakerMute" | "dpad" | "ok" | "cast" | "hand" | "tv" | "grid" | "back" | "star";

const PATHS: Record<FilmIconName, ReactNode> = {
  bell: (
    <>
      <path d="M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 1.75H5z" />
      <path d="M10 20a2 2 0 0 0 4 0" />
    </>
  ),
  expand: <path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9M20 15v3.5a1.5 1.5 0 0 1-1.5 1.5H15M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15" />,
  compress: <path d="M9 4v3.5A1.5 1.5 0 0 1 7.5 9H4M15 4v3.5A1.5 1.5 0 0 0 16.5 9H20M20 15h-3.5a1.5 1.5 0 0 0-1.5 1.5V20M4 15h3.5A1.5 1.5 0 0 1 9 16.5V20" />,
  speaker: (
    <>
      <path d="M4.5 9.5h3l4.5-4v13l-4.5-4h-3a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1z" />
      <path d="M15.5 9.25a4 4 0 0 1 0 5.5M18.25 6.75a7.5 7.5 0 0 1 0 10.5" />
    </>
  ),
  speakerMute: (
    <>
      <path d="M4.5 9.5h3l4.5-4v13l-4.5-4h-3a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1z" />
      <path d="m16 9.5 5 5M21 9.5l-5 5" />
    </>
  ),
  dpad: (
    <>
      <path d="M9.25 3.5h5.5v5.75h5.75v5.5h-5.75v5.75h-5.5v-5.75H3.5v-5.5h5.75z" />
      <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
    </>
  ),
  ok: <circle cx="12" cy="12" r="7.5" />,
  cast: (
    <>
      <path d="M3 17.5a3.5 3.5 0 0 1 3.5 3.5M3 13.5a7.5 7.5 0 0 1 7.5 7.5M3 9.5A11.5 11.5 0 0 1 14.5 21" />
      <path d="M3 6.25V5a1.5 1.5 0 0 1 1.5-1.5h15A1.5 1.5 0 0 1 21 5v12.5a1.5 1.5 0 0 1-1.5 1.5H18" />
    </>
  ),
  hand: <path d="M8 13.5V6.75a1.75 1.75 0 0 1 3.5 0V12m0-6.5a1.75 1.75 0 0 1 3.5 0V12m0-4a1.75 1.75 0 0 1 3.5 0v7.25a5.75 5.75 0 0 1-5.75 5.75h-1.1a5.75 5.75 0 0 1-4.9-2.75l-2.7-4.35a1.6 1.6 0 0 1 2.6-1.85L8 13.5" />,
  tv: (
    <>
      <rect x="2.75" y="4.5" width="18.5" height="12.5" rx="2.5" />
      <path d="M8 20.5h8M12 17v3.5" />
    </>
  ),
  grid: (
    <>
      <rect x="4" y="4" width="6.5" height="6.5" rx="1.9" />
      <rect x="13.5" y="4" width="6.5" height="6.5" rx="1.9" />
      <rect x="4" y="13.5" width="6.5" height="6.5" rx="1.9" />
      <rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.9" />
    </>
  ),
  back: <path d="m14.5 5.5-6.5 6.5 6.5 6.5" />,
  star: <path d="m12 3.75 2.5 5.3 5.75.7-4.25 4 1.1 5.75L12 16.7l-5.1 2.8 1.1-5.75-4.25-4 5.75-.7z" />,
};

export function FilmIcon({ name, size = 24, className = "", strokeWidth = 1.75 }: { name: FilmIconName; size?: number; className?: string; strokeWidth?: number }) {
  return (
    <svg aria-hidden="true" focusable="false" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" className={`shrink-0 ${className}`}>
      {PATHS[name]}
    </svg>
  );
}

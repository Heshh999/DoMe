/**
 * Icons the public website needs beyond the shared set (src/components/icons.tsx is the app's; the public
 * pages keep their own additions here). Same grid and stroke as the shared set: 24 units, 1.75 stroke,
 * round caps and joins, `currentColor`, always decorative (every control has its own text or aria-label).
 */
import type { ReactNode } from "react";

export type PublicIconName = "clock" | "download" | "minus" | "screen" | "cursor" | "help" | "wallet" | "timer" | "hand" | "chat" | "list" | "info";

const PATHS: Record<PublicIconName, ReactNode> = {
  clock: (
    <>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  download: <path d="M12 3.75v11M7.5 10.25 12 14.75l4.5-4.5M4.5 17.25v1.5a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-1.5" />,
  minus: <path d="M6 12h12" />,
  screen: (
    <>
      <rect x="2.75" y="4" width="18.5" height="12.5" rx="2.5" />
      <path d="M8.5 20.25h7M12 16.5v3.75" />
      <path d="M8 10.25h4.5M8 13h8" />
    </>
  ),
  cursor: (
    <>
      <path d="m5.5 4.5 13 5.4-5.6 2.2-2.3 5.6z" />
      <path d="m12.9 12.1 5.6 5.6" />
    </>
  ),
  help: (
    <>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M9.6 9.6a2.5 2.5 0 1 1 3.3 2.4c-.6.3-.9.8-.9 1.4v.4M12 16.9v.01" />
    </>
  ),
  wallet: (
    <>
      <path d="M3.75 7.5A2.25 2.25 0 0 1 6 5.25h11.5A2.25 2.25 0 0 1 19.75 7.5v9A2.25 2.25 0 0 1 17.5 18.75H6a2.25 2.25 0 0 1-2.25-2.25z" />
      <path d="M3.75 10h16M15.5 14h1.75" />
    </>
  ),
  timer: (
    <>
      <circle cx="12" cy="13" r="7.25" />
      <path d="M12 9.5V13l2.5 1.5M9.5 3.25h5M12 3.25v2.5" />
    </>
  ),
  hand: (
    <>
      <path d="M8.5 12.5V5.75a1.5 1.5 0 0 1 3 0v6" />
      <path d="M11.5 11V4.5a1.5 1.5 0 0 1 3 0V11" />
      <path d="M14.5 11.5V6.25a1.5 1.5 0 0 1 3 0v7.25c0 3.6-2.5 6.5-6 6.5-2.7 0-4.3-1.3-5.5-3.4L4.4 14a1.4 1.4 0 0 1 2.3-1.5l1.8 2.3" />
    </>
  ),
  chat: (
    <>
      <path d="M4.5 6.5A2.75 2.75 0 0 1 7.25 3.75h9.5A2.75 2.75 0 0 1 19.5 6.5v7a2.75 2.75 0 0 1-2.75 2.75H11l-4.25 3.5v-3.5h.5A2.75 2.75 0 0 1 4.5 13.5z" />
      <path d="M8.5 8.75h7M8.5 11.75h4.5" />
    </>
  ),
  list: <path d="M8 6.5h12M8 12h12M8 17.5h12M4 6.5h.01M4 12h.01M4 17.5h.01" />,
  info: (
    <>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M12 11v5M12 7.9v.01" />
    </>
  ),
};

export function PublicIcon({ name, size = 24, className = "", strokeWidth = 1.75 }: { name: PublicIconName; size?: number; className?: string; strokeWidth?: number }) {
  return (
    <svg aria-hidden="true" focusable="false" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" className={`shrink-0 ${className}`}>
      {PATHS[name]}
    </svg>
  );
}

/**
 * One consistent icon set: 24-unit grid, 1.75 stroke, round caps and joins, `currentColor`. Icons are
 * always decorative here (aria-hidden): every control that shows one also has a text label or an
 * aria-label of its own.
 */
import type { ReactNode } from "react";

export type IconName =
  | "home"
  | "remote"
  | "touchpad"
  | "ask"
  | "keyboard"
  | "more"
  | "apps"
  | "play"
  | "pause"
  | "prev"
  | "next"
  | "back10"
  | "fwd10"
  | "volume"
  | "mute"
  | "lock"
  | "sleep"
  | "power"
  | "pc"
  | "sun"
  | "moon"
  | "system"
  | "check"
  | "chevron"
  | "arrow"
  | "menu"
  | "close"
  | "shield"
  | "mic"
  | "sparkle"
  | "theater"
  | "fullscreen"
  | "search"
  | "youtube"
  | "music"
  | "devices"
  | "settings"
  | "pulse"
  | "phone"
  | "key"
  | "eye"
  | "card";

const STROKE: Record<string, ReactNode> = {
  home: <path d="M4 10.2 12 3.8l8 6.4V19a1.5 1.5 0 0 1-1.5 1.5H15v-5.2a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v5.2H5.5A1.5 1.5 0 0 1 4 19z" />,
  remote: (
    <>
      <rect x="7" y="2.75" width="10" height="18.5" rx="5" />
      <circle cx="12" cy="8.5" r="2.1" />
      <path d="M10.25 14h3.5M10.25 17.25h3.5" />
    </>
  ),
  touchpad: (
    <>
      <rect x="3" y="4.5" width="18" height="15" rx="3.5" />
      <path d="M3 14.75h18M12 14.75v4.75" />
    </>
  ),
  ask: (
    <>
      <path d="M4.5 6.5A2.75 2.75 0 0 1 7.25 3.75h9.5A2.75 2.75 0 0 1 19.5 6.5v7a2.75 2.75 0 0 1-2.75 2.75H11l-4.25 3.5v-3.5h.5A2.75 2.75 0 0 1 4.5 13.5z" />
      <path d="M12 6.9v.01M12 7l.75 1.85 1.85.75-1.85.75L12 12.2l-.75-1.85-1.85-.75 1.85-.75z" fill="currentColor" stroke="none" />
    </>
  ),
  keyboard: (
    <>
      <rect x="2.75" y="6" width="18.5" height="12" rx="2.75" />
      <path d="M6.5 10h.01M9.5 10h.01M12.5 10h.01M15.5 10h.01M17.5 10h.01M6.5 13h.01M17.5 13h.01M9 15h6" />
    </>
  ),
  apps: (
    <>
      <rect x="4" y="4" width="6.5" height="6.5" rx="1.9" />
      <rect x="13.5" y="4" width="6.5" height="6.5" rx="1.9" />
      <rect x="4" y="13.5" width="6.5" height="6.5" rx="1.9" />
      <rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.9" />
    </>
  ),
  back10: (
    <>
      <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3" />
      <path d="M4.25 3.75v3.5h3.5" />
      <text x="12.1" y="14.6" textAnchor="middle" fontSize="7" fontWeight="700" fill="currentColor" stroke="none" fontFamily="system-ui, sans-serif">
        10
      </text>
    </>
  ),
  fwd10: (
    <>
      <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" />
      <path d="M19.75 3.75v3.5h-3.5" />
      <text x="11.9" y="14.6" textAnchor="middle" fontSize="7" fontWeight="700" fill="currentColor" stroke="none" fontFamily="system-ui, sans-serif">
        10
      </text>
    </>
  ),
  volume: (
    <>
      <path d="M4.5 9.5h3l4.5-4v13l-4.5-4h-3a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1z" />
      <path d="M15.5 9.25a4 4 0 0 1 0 5.5M18.25 6.75a7.5 7.5 0 0 1 0 10.5" />
    </>
  ),
  mute: (
    <>
      <path d="M4.5 9.5h3l4.5-4v13l-4.5-4h-3a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1z" />
      <path d="m16 9.5 5 5M21 9.5l-5 5" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="10" rx="2.75" />
      <path d="M8.25 10.5V8a3.75 3.75 0 0 1 7.5 0v2.5M12 14.5v2" />
    </>
  ),
  sleep: <path d="M19.5 14.6A7.75 7.75 0 0 1 9.4 4.5a7.75 7.75 0 1 0 10.1 10.1z" />,
  moon: <path d="M19.5 14.6A7.75 7.75 0 0 1 9.4 4.5a7.75 7.75 0 1 0 10.1 10.1z" />,
  power: <path d="M12 3.5v8M7.05 6.4a7.5 7.5 0 1 0 9.9 0" />,
  pc: (
    <>
      <rect x="2.75" y="4" width="18.5" height="12.5" rx="2.5" />
      <path d="M8.5 20.25h7M12 16.5v3.75" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="3.75" />
      <path d="M12 2.75v1.5M12 19.75v1.5M2.75 12h1.5M19.75 12h1.5M5.45 5.45l1.06 1.06M17.5 17.5l1.06 1.06M5.45 18.55l1.06-1.06M17.5 6.5l1.06-1.06" />
    </>
  ),
  system: (
    <>
      <circle cx="12" cy="12" r="8.25" />
      <path d="M12 3.75a8.25 8.25 0 0 1 0 16.5z" fill="currentColor" />
    </>
  ),
  check: <path d="m5 12.5 4.25 4.25L19 7" />,
  chevron: <path d="m9.5 5.5 6.5 6.5-6.5 6.5" />,
  arrow: <path d="M4.5 12h14.5M13.5 6.25 19.25 12l-5.75 5.75" />,
  menu: <path d="M4 8.5h16M4 15.5h16" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  shield: (
    <>
      <path d="M12 3.25 4.75 6v5.6c0 4.5 3.05 7.9 7.25 9.15 4.2-1.25 7.25-4.65 7.25-9.15V6z" />
      <path d="m8.75 12 2.25 2.25 4.25-4.5" />
    </>
  ),
  mic: (
    <>
      <rect x="9" y="3" width="6" height="11.5" rx="3" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3" />
    </>
  ),
  theater: (
    <>
      <rect x="2.75" y="6" width="18.5" height="12" rx="2.5" />
      <path d="M2.75 14.5h18.5" />
    </>
  ),
  fullscreen: <path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9M20 15v3.5a1.5 1.5 0 0 1-1.5 1.5H15M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15" />,
  search: (
    <>
      <circle cx="10.75" cy="10.75" r="6.5" />
      <path d="m15.75 15.75 4.5 4.5" />
    </>
  ),
  youtube: (
    <>
      <rect x="2.75" y="5.25" width="18.5" height="13.5" rx="4" />
      <path d="M10.25 9.25v5.5l4.75-2.75z" fill="currentColor" />
    </>
  ),
  music: (
    <>
      <path d="M9 18V6l10.5-2.25v12" />
      <circle cx="6.75" cy="18" r="2.25" />
      <circle cx="17.25" cy="15.75" r="2.25" />
    </>
  ),
  devices: (
    <>
      <rect x="2.75" y="4.5" width="13" height="9.5" rx="2" />
      <path d="M6 18h6.5" />
      <rect x="15.5" y="9" width="5.75" height="11" rx="1.75" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2.25" />
      <circle cx="9" cy="17" r="2.25" />
    </>
  ),
  pulse: <path d="M3 12h4l2.5-6 5 12 2.5-6h4" />,
  phone: (
    <>
      <rect x="6.5" y="2.75" width="11" height="18.5" rx="2.75" />
      <path d="M10.5 18h3" />
    </>
  ),
  key: (
    <>
      <circle cx="8" cy="15.5" r="4" />
      <path d="m11 12.75 8.5-8.5M16.5 7.25l2.5 2.5" />
    </>
  ),
  eye: (
    <>
      <path d="M2.75 12S6.25 5.5 12 5.5 21.25 12 21.25 12 17.75 18.5 12 18.5 2.75 12 2.75 12z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  card: (
    <>
      <rect x="2.75" y="5.25" width="18.5" height="13.5" rx="2.5" />
      <path d="M2.75 9.75h18.5M6.5 15h3" />
    </>
  ),
};

const FILL: Record<string, ReactNode> = {
  play: <path d="M8 5.6v12.8a1.1 1.1 0 0 0 1.68.93l10.1-6.4a1.1 1.1 0 0 0 0-1.86L9.68 4.67A1.1 1.1 0 0 0 8 5.6z" />,
  pause: (
    <>
      <rect x="6" y="4.75" width="4.25" height="14.5" rx="1.4" />
      <rect x="13.75" y="4.75" width="4.25" height="14.5" rx="1.4" />
    </>
  ),
  prev: (
    <>
      <rect x="4.5" y="5" width="2.4" height="14" rx="1.2" />
      <path d="M19.5 6.35v11.3a1.1 1.1 0 0 1-1.7.92l-8.5-5.65a1.15 1.15 0 0 1 0-1.9l8.5-5.6a1.1 1.1 0 0 1 1.7.93z" />
    </>
  ),
  next: (
    <>
      <rect x="17.1" y="5" width="2.4" height="14" rx="1.2" />
      <path d="M4.5 6.35v11.3a1.1 1.1 0 0 0 1.7.92l8.5-5.65a1.15 1.15 0 0 0 0-1.9l-8.5-5.6a1.1 1.1 0 0 0-1.7.93z" />
    </>
  ),
  more: (
    <>
      <circle cx="5.5" cy="12" r="1.9" />
      <circle cx="12" cy="12" r="1.9" />
      <circle cx="18.5" cy="12" r="1.9" />
    </>
  ),
  sparkle: <path d="M12 2.5c.5 4.6 2.9 7 7.5 7.5-4.6.5-7 2.9-7.5 7.5-.5-4.6-2.9-7-7.5-7.5 4.6-.5 7-2.9 7.5-7.5zM18.5 15.5c.2 1.8 1.2 2.8 3 3-1.8.2-2.8 1.2-3 3-.2-1.8-1.2-2.8-3-3 1.8-.2 2.8-1.2 3-3z" />,
};

export function Icon({ name, size = 24, className = "", strokeWidth = 1.75 }: { name: IconName; size?: number; className?: string; strokeWidth?: number }) {
  const filled = name in FILL;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" className={`shrink-0 ${className}`} fill={filled ? "currentColor" : "none"} stroke={filled ? "none" : "currentColor"} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round">
      {filled ? FILL[name] : STROKE[name]}
    </svg>
  );
}

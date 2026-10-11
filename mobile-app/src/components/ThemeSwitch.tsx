/**
 * Light / Auto / Dark. "Auto" follows the device and is the default; a choice is remembered on this
 * device (src/lib/theme.ts) and applies to the website and the app alike. A radio group: arrow keys
 * move the choice, Tab enters and leaves the group.
 */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { onThemeChoice, readThemeChoice, setThemeChoice, type ThemeChoice } from "../lib/theme.ts";
import { Icon, type IconName } from "./icons.tsx";

const OPTIONS: Array<{ value: ThemeChoice; label: string; icon: IconName }> = [
  { value: "light", label: "Light", icon: "sun" },
  { value: "system", label: "Auto", icon: "system" },
  { value: "dark", label: "Dark", icon: "moon" },
];

export function ThemeSwitch({ labels = false, className = "" }: { labels?: boolean; className?: string }) {
  const [choice, setChoice] = useState<ThemeChoice>(() => readThemeChoice());
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  useEffect(() => onThemeChoice(setChoice), []);

  const choose = (value: ThemeChoice) => {
    setChoice(value);
    setThemeChoice(value);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const at = OPTIONS.findIndex((o) => o.value === choice);
    const next = (at + step + OPTIONS.length) % OPTIONS.length;
    choose(OPTIONS[next]!.value);
    refs.current[next]?.focus();
  };

  return (
    <div role="radiogroup" aria-label="Appearance" onKeyDown={onKeyDown} className={`inline-flex items-center gap-0.5 rounded-full border border-border bg-surface p-[3px] ${className}`}>
      {OPTIONS.map((o, i) => {
        const on = choice === o.value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={labels ? undefined : o.label}
            title={o.label}
            tabIndex={on ? 0 : -1}
            onClick={() => choose(o.value)}
            className={`dome-press inline-flex h-11 items-center justify-center gap-1.5 rounded-full text-sm font-semibold ${labels ? "px-3.5" : "w-11"} ${on ? "bg-white text-text shadow-[0_1px_3px_rgb(0_20_65/0.18)] dark:bg-white/15" : "text-text-muted hover:text-text"}`}
          >
            <Icon name={o.icon} size={18} />
            {labels ? <span>{o.label}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

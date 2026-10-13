/**
 * Light / Auto / Dark. "Auto" follows the device and is the default; a choice is remembered on this
 * device (src/lib/theme.ts) and applies to the website and the app alike. A radio group: arrow keys
 * move the choice, Tab enters and leaves the group.
 *
 * Three looks: icon-only (`labels` off: each option has an aria-label and a tooltip that says what it
 * does), text labels always (`labels`), or text labels from the lg breakpoint up (`labels="lg"`, the
 * website header: icons on phones and tablets, words on a wide screen). "Auto" is drawn as a monitor
 * whose screen is half dark, so the glyph says "the device decides" rather than a bare half circle.
 */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { onThemeChoice, readThemeChoice, setThemeChoice, type ThemeChoice } from "../lib/theme.ts";
import { Icon } from "./icons.tsx";

const OPTIONS: Array<{ value: ThemeChoice; label: string; hint: string }> = [
  { value: "light", label: "Light", hint: "always the light look" },
  { value: "system", label: "Auto", hint: "follows your device" },
  { value: "dark", label: "Dark", hint: "always the dark look" },
];

/** A monitor whose screen is half dark: "Auto" follows the device. Same grid and stroke as src/components/icons.tsx. */
function AutoGlyph({ size = 18 }: { size?: number }) {
  return (
    <svg aria-hidden="true" focusable="false" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
      <rect x="2.75" y="4" width="18.5" height="12.5" rx="2.5" />
      <path d="M8.5 20.25h7M12 16.5v3.75" />
      <path d="M12 4v12.5" />
      <path d="M12 5.25h7.5a1.25 1.25 0 0 1 1.25 1.25v7.5a1.25 1.25 0 0 1-1.25 1.25H12z" fill="currentColor" stroke="none" />
    </svg>
  );
}

function OptionIcon({ value }: { value: ThemeChoice }) {
  if (value === "system") return <AutoGlyph />;
  return <Icon name={value === "light" ? "sun" : "moon"} size={18} />;
}

export function ThemeSwitch({ labels = false, className = "" }: { labels?: boolean | "lg"; className?: string }) {
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

  // icon-only: a square 44px target; words always: padded; words from lg: square below lg, padded above
  const shape = labels === true ? "px-3.5" : labels === "lg" ? "w-11 lg:w-auto lg:px-3.5" : "w-11";
  const text = labels === true ? "" : labels === "lg" ? "hidden lg:inline" : "hidden";

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
            aria-label={o.label}
            title={`${o.label}: ${o.hint}`}
            tabIndex={on ? 0 : -1}
            onClick={() => choose(o.value)}
            className={`dome-press inline-flex h-11 items-center justify-center gap-1.5 rounded-full text-sm font-semibold ${shape} ${on ? "bg-white text-text shadow-[0_1px_3px_rgb(0_20_65/0.18)] dark:bg-white/15" : "text-text-muted hover:text-text"}`}
          >
            <OptionIcon value={o.value} />
            <span className={text}>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

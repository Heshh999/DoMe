/**
 * Small accessible primitives. Every interactive element is at least 44×44 px (styles.css), has a
 * visible focus ring and a text label (icon-only buttons take `aria-label`). Looks come from the
 * design tokens in styles.css, so each primitive works in both themes.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg" | "xl";

const VARIANT: Record<Variant, string> = {
  primary: "dome-primary",
  secondary: "bg-surface text-text border border-border hover:bg-surface-hover",
  ghost: "bg-transparent text-text hover:bg-surface",
  danger: "bg-danger-fill text-white hover:brightness-110 shadow-[0_10px_24px_-12px_rgb(194_42_54/0.6)]",
};
const SIZE: Record<Size, string> = {
  sm: "gap-1.5 px-3 py-2.5 text-[14px] whitespace-nowrap",
  md: "gap-2 px-4 py-2.5 text-[15px]",
  lg: "gap-2 px-5 py-3.5 text-base min-h-[52px]",
  xl: "gap-2 px-6 py-4 text-lg min-h-[60px]",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  busy?: boolean;
  full?: boolean;
}

export function Button({ variant = "secondary", size = "md", busy = false, full = false, className = "", children, disabled, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`dome-press inline-flex items-center justify-center rounded-control font-semibold tracking-[-0.01em] select-none disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none ${VARIANT[variant]} ${SIZE[size]} ${full ? "w-full" : ""} ${className}`}
    >
      {busy ? <Spinner small /> : null}
      {children}
    </button>
  );
}

/** Round icon button; `label` is mandatory and read by screen readers. `plain` drops the fill and edge (transport rows). */
export function IconButton({ label, children, className = "", size = 52, plain = false, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; size?: number; plain?: boolean }) {
  return (
    <button type="button" aria-label={label} title={label} {...rest} style={{ width: size, height: size }} className={`dome-press inline-flex shrink-0 items-center justify-center rounded-full text-text disabled:opacity-30 disabled:cursor-not-allowed ${plain ? "hover:bg-surface" : "bg-surface border border-border hover:bg-surface-hover"} ${className}`}>
      {children}
    </button>
  );
}

export function Card({ children, className = "", as: Tag = "section", ...rest }: { children: ReactNode; className?: string; as?: "section" | "div" | "article" | "li" } & Record<string, unknown>) {
  return (
    <Tag className={`dome-card p-5 ${className}`} {...rest}>
      {children}
    </Tag>
  );
}

export function Spinner({ small = false, label = "Loading" }: { small?: boolean; label?: string }) {
  const s = small ? 16 : 28;
  return (
    <span role="status" aria-label={label} className="inline-block align-middle">
      <svg width={s} height={s} viewBox="0 0 24 24" fill="none" aria-hidden="true" className="animate-spin motion-reduce:animate-none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.22" strokeWidth="2.5" />
        <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    </span>
  );
}

export type Tone = "neutral" | "success" | "warning" | "danger" | "info";
const TONE: Record<Tone, string> = {
  neutral: "bg-surface text-text-muted border-border",
  success: "bg-success/12 text-success border-success/25",
  warning: "bg-warning/12 text-warning border-warning/30",
  danger: "bg-danger/12 text-danger border-danger/30",
  info: "bg-info/12 text-info border-info/25",
};

export function Pill({ tone = "neutral", children, pulse = false, className = "" }: { tone?: Tone; children: ReactNode; pulse?: boolean; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-semibold ${TONE[tone]} ${className}`}>
      <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full bg-current ${tone === "success" ? "shadow-[0_0_0_3px_color-mix(in_oklab,currentColor_22%,transparent)]" : ""} ${pulse ? "pulse" : ""}`} />
      {children}
    </span>
  );
}

/** A page's large title (iOS-style), with an optional line under it and an action on the right. */
export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: ReactNode; action?: ReactNode }) {
  return (
    <header className="flex items-end justify-between gap-3 pt-2 mb-5">
      <div className="min-w-0">
        <h1 className="text-[2rem] leading-[1.1] font-bold tracking-[-0.03em]">{title}</h1>
        {subtitle ? <p className="text-text-muted text-sm mt-1.5">{subtitle}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </header>
  );
}

/** A small section heading inside a page (capitalised label above a group of cards). */
export function SectionLabel({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 px-1 mb-2">
      <h2 className="text-caption font-semibold uppercase tracking-[0.12em] text-text-faint">{children}</h2>
      {action}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <Card className="text-center py-9">
      <h2 className="text-lg font-semibold">{title}</h2>
      {children ? <div className="text-text-muted text-sm mt-2 space-y-2">{children}</div> : null}
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </Card>
  );
}

export function Field({ label, hint, children, id }: { label: string; hint?: string; children: ReactNode; id: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-semibold">
        {label}
      </label>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export const inputClass = "w-full rounded-control bg-bg-sunken border border-border px-4 py-3 text-base text-text placeholder:text-text-faint transition-colors focus:border-accent/60 min-h-[48px]";

export function Toggle({ checked, onChange, label, disabled = false, id }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean; id?: string }) {
  return (
    <button id={id} type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)} className={`relative inline-flex h-[31px] w-[51px] shrink-0 items-center rounded-full transition-colors duration-200 disabled:opacity-40 ${checked ? "bg-primary" : "bg-border-strong"}`}>
      <span aria-hidden="true" className={`inline-block h-[27px] w-[27px] rounded-full bg-white shadow-[0_2px_6px_rgb(0_20_65/0.3)] transition-transform duration-200 ease-dome ${checked ? "translate-x-[22px]" : "translate-x-[2px]"}`} />
    </button>
  );
}

export function Notice({ tone = "info", title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  const look: Record<Tone, string> = { neutral: "border-border bg-bg-elevated", success: "border-success/30 bg-success/8", warning: "border-warning/35 bg-warning/8", danger: "border-danger/35 bg-danger/8", info: "border-info/25 bg-info/8" };
  const dot: Record<Tone, string> = { neutral: "bg-text-faint", success: "bg-success", warning: "bg-warning", danger: "bg-danger", info: "bg-info" };
  return (
    <div role={tone === "danger" || tone === "warning" ? "alert" : "status"} className={`flex gap-3 rounded-card border px-4 py-3.5 text-sm ${look[tone]}`}>
      <span aria-hidden="true" className={`mt-[7px] h-2 w-2 shrink-0 rounded-full ${dot[tone]}`} />
      <div className="min-w-0 flex-1">
        {title ? <p className="font-semibold text-text mb-1">{title}</p> : null}
        <div className="text-text-muted space-y-1">{children}</div>
      </div>
    </div>
  );
}

export function Steps({ steps }: { steps: string[] }) {
  if (steps.length === 0) return null;
  return (
    <ol className="list-decimal pl-5 space-y-1">
      {steps.map((s) => (
        <li key={s}>{s}</li>
      ))}
    </ol>
  );
}

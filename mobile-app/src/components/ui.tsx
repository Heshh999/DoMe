/**
 * Small accessible primitives. Every interactive element is at least 44×44 px (styles.css), has a
 * visible focus ring and a text label (icon-only buttons take `aria-label`).
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "md" | "lg" | "xl";

const VARIANT: Record<Variant, string> = {
  primary: "bg-accent text-on-accent hover:bg-accent-strong active:bg-accent-strong",
  secondary: "bg-surface text-text border border-border hover:bg-surface-hover",
  ghost: "bg-transparent text-text hover:bg-surface-hover",
  danger: "bg-danger text-white hover:bg-danger-strong",
};
const SIZE: Record<Size, string> = {
  md: "px-4 py-2.5 text-[15px]",
  lg: "px-5 py-3.5 text-base",
  xl: "px-6 py-4 text-lg",
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
      className={`inline-flex items-center justify-center gap-2 rounded-control font-medium transition-colors select-none disabled:opacity-45 disabled:cursor-not-allowed ${VARIANT[variant]} ${SIZE[size]} ${full ? "w-full" : ""} ${className}`}
    >
      {busy ? <Spinner small /> : null}
      {children}
    </button>
  );
}

/** Square icon button; `label` is mandatory and read by screen readers. */
export function IconButton({ label, children, className = "", size = 52, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; size?: number }) {
  return (
    <button type="button" aria-label={label} title={label} {...rest} style={{ width: size, height: size }} className={`inline-flex items-center justify-center rounded-full bg-surface border border-border text-text hover:bg-surface-hover active:bg-surface-hover disabled:opacity-40 disabled:cursor-not-allowed ${className}`}>
      {children}
    </button>
  );
}

export function Card({ children, className = "", as: Tag = "section", ...rest }: { children: ReactNode; className?: string; as?: "section" | "div" | "article" | "li" } & Record<string, unknown>) {
  return (
    <Tag className={`rounded-card bg-bg-elevated border border-border shadow-[var(--dome-shadow-card)] p-4 ${className}`} {...rest}>
      {children}
    </Tag>
  );
}

export function Spinner({ small = false, label = "Loading" }: { small?: boolean; label?: string }) {
  const s = small ? 16 : 28;
  return (
    <span role="status" aria-label={label} className="inline-block align-middle">
      <svg width={s} height={s} viewBox="0 0 24 24" fill="none" aria-hidden="true" className="animate-spin motion-reduce:animate-none">
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
        <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      </svg>
    </span>
  );
}

export type Tone = "neutral" | "success" | "warning" | "danger" | "info";
const TONE: Record<Tone, string> = {
  neutral: "bg-surface text-text-muted border-border",
  success: "bg-success/15 text-success border-success/30",
  warning: "bg-warning/15 text-warning border-warning/30",
  danger: "bg-danger/15 text-danger border-danger/30",
  info: "bg-info/15 text-info border-info/30",
};

export function Pill({ tone = "neutral", children, pulse = false, className = "" }: { tone?: Tone; children: ReactNode; pulse?: boolean; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold tracking-wide ${TONE[tone]} ${className}`}>
      <span aria-hidden="true" className={`h-2 w-2 rounded-full bg-current ${pulse ? "pulse" : ""}`} />
      {children}
    </span>
  );
}

export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: ReactNode; action?: ReactNode }) {
  return (
    <header className="flex items-start justify-between gap-3 mb-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
        {subtitle ? <p className="text-text-muted text-sm mt-1">{subtitle}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </header>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <Card className="text-center py-8">
      <h2 className="text-lg font-semibold">{title}</h2>
      {children ? <div className="text-text-muted text-sm mt-2 space-y-2">{children}</div> : null}
      {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
    </Card>
  );
}

export function Field({ label, hint, children, id }: { label: string; hint?: string; children: ReactNode; id: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium">
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

export const inputClass = "w-full rounded-control bg-bg-sunken border border-border px-3.5 py-3 text-base text-text placeholder:text-text-faint focus:border-border-strong min-h-[48px]";

export function Toggle({ checked, onChange, label, disabled = false, id }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean; id?: string }) {
  return (
    <button id={id} type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)} className={`relative inline-flex h-8 w-14 shrink-0 items-center rounded-full border transition-colors disabled:opacity-40 ${checked ? "bg-accent border-accent" : "bg-bg-sunken border-border-strong"}`}>
      <span aria-hidden="true" className={`inline-block h-6 w-6 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-7" : "translate-x-1"}`} />
    </button>
  );
}

export function Notice({ tone = "info", title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  const border: Record<Tone, string> = { neutral: "border-border", success: "border-success/40", warning: "border-warning/40", danger: "border-danger/40", info: "border-info/40" };
  return (
    <div role={tone === "danger" || tone === "warning" ? "alert" : "status"} className={`rounded-control border ${border[tone]} bg-surface px-4 py-3 text-sm`}>
      {title ? <p className="font-semibold mb-1">{title}</p> : null}
      <div className="text-text-muted space-y-1">{children}</div>
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

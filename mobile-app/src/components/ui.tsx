/**
 * Small accessible primitives. Every interactive element is at least 44×44 px (styles.css), has a
 * visible focus ring and a text label (icon-only buttons take `aria-label`). Looks come from the
 * design tokens in styles.css, so each primitive works in both themes.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router";

import { Icon, type IconName } from "./icons.tsx";

/** `quiet`: a text-only secondary action; `danger-ghost`: a destructive action that stays quiet until its confirmation. */
type Variant = "primary" | "secondary" | "ghost" | "danger" | "quiet" | "danger-ghost";
type Size = "sm" | "md" | "lg" | "xl";

const VARIANT: Record<Variant, string> = {
  primary: "dome-primary",
  secondary: "bg-surface text-text border border-border hover:bg-surface-hover",
  ghost: "bg-transparent text-text hover:bg-surface",
  danger: "bg-danger-fill text-white hover:brightness-110 shadow-[0_10px_24px_-12px_rgb(194_42_54/0.6)]",
  quiet: "bg-transparent text-text-muted hover:bg-surface hover:text-text",
  "danger-ghost": "bg-transparent text-danger hover:bg-danger/10",
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
  // a disabled primary gets its own quiet look (styles.css `.dome-primary:disabled`), never a faded blue bar
  const dim = variant === "primary" ? "" : "disabled:opacity-40";
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`dome-press inline-flex items-center justify-center rounded-control font-semibold tracking-[-0.01em] select-none ${dim} disabled:cursor-not-allowed disabled:shadow-none ${VARIANT[variant]} ${SIZE[size]} ${full ? "w-full" : ""} ${className}`}
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

/** A status pill. `dot={false}` drops the leading dot when the pill carries its own icon (one glyph, never two). */
export function Pill({ tone = "neutral", children, pulse = false, dot = true, className = "" }: { tone?: Tone; children: ReactNode; pulse?: boolean; dot?: boolean; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-semibold ${TONE[tone]} ${className}`}>
      {dot ? <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full bg-current ${tone === "success" ? "shadow-[0_0_0_3px_color-mix(in_oklab,currentColor_22%,transparent)]" : ""} ${pulse ? "pulse" : ""}`} /> : null}
      {children}
    </span>
  );
}

/**
 * A page's large title (iOS-style), with an optional line under it and an action on the right. `back` puts a
 * labelled back link above the title (sub-pages such as pairing); `eyebrow` a small caption above it.
 */
export function PageHeader({ title, subtitle, action, back, eyebrow }: { title: string; subtitle?: ReactNode; action?: ReactNode; back?: { to: string; label: string; ariaLabel?: string }; eyebrow?: ReactNode }) {
  return (
    <header className={`mb-5 ${back ? "pt-0" : "pt-2"}`}>
      {back ? <BackLink to={back.to} label={back.label} ariaLabel={back.ariaLabel} /> : null}
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          {eyebrow ? <p className="dome-eyebrow mb-1.5">{eyebrow}</p> : null}
          <h1 className="text-[2rem] leading-[1.1] font-bold tracking-[-0.03em]">{title}</h1>
          {subtitle ? <p className="text-text-muted text-sm mt-1.5">{subtitle}</p> : null}
        </div>
        {action ? <div className="shrink-0 pb-0.5">{action}</div> : null}
      </div>
    </header>
  );
}

/** A back link without the "‹" glyph: a chevron icon and the destination's name. */
export function BackLink({ to, label, ariaLabel }: { to: string; label: string; ariaLabel?: string | undefined }) {
  return (
    <Link to={to} aria-label={ariaLabel} className="tap -ml-2 mb-1 inline-flex items-center gap-0.5 rounded-full pl-1.5 pr-3 text-sm font-semibold text-accent hover:bg-surface">
      <Icon name="chevron" size={18} className="rotate-180" />
      {label}
    </Link>
  );
}

/** A small action in a header (Refresh, Devices…): a labelled 44 px pill, never an unlabelled icon. */
export function HeaderPill({ icon, children, to, onClick, busy = false, disabled = false, ...rest }: { icon: IconName; children: ReactNode; to?: string; onClick?: () => void; busy?: boolean; disabled?: boolean } & Record<string, unknown>) {
  const className = "tap dome-press inline-flex h-11 items-center gap-1.5 rounded-full border border-border bg-surface pl-3 pr-3.5 text-sm font-semibold text-text hover:bg-surface-hover disabled:opacity-40 disabled:cursor-not-allowed";
  if (to) {
    return (
      <Link to={to} className={className} {...rest}>
        <Icon name={icon} size={18} className="text-accent" />
        {children}
      </Link>
    );
  }
  return (
    <button type="button" onClick={onClick} disabled={disabled || busy} aria-busy={busy || undefined} className={className} {...rest}>
      {busy ? <Spinner small /> : <Icon name={icon} size={18} className="text-accent" />}
      {children}
    </button>
  );
}

export type WellTone = "accent" | "muted" | "success" | "warning" | "danger";
const WELL: Record<WellTone, string> = { accent: "", muted: "dome-well-muted", success: "dome-well-success", warning: "dome-well-warning", danger: "dome-well-danger" };

/** A tinted icon well (decorative). `size` in px; the icon is about half of it. */
export function IconWell({ icon, tone = "accent", size = 40, className = "", radius }: { icon: IconName; tone?: WellTone; size?: number; className?: string; radius?: number }) {
  return (
    <span aria-hidden="true" style={{ width: size, height: size, ...(radius !== undefined ? { borderRadius: radius } : {}) }} className={`dome-well ${WELL[tone]} ${className}`}>
      <Icon name={icon} size={Math.round(size * 0.52)} />
    </span>
  );
}

/** A segmented control (`role="tablist"`): the options are tabs, the chosen one is `aria-selected`. */
export function Segmented<T extends string>({ label, value, options, onChange, className = "" }: { label: string; value: T; options: Array<{ value: T; label: string; icon?: IconName; disabled?: boolean }>; onChange: (value: T) => void; className?: string }) {
  return (
    <div role="tablist" aria-label={label} className={`dome-segment ${className}`}>
      {options.map((o) => (
        <button key={o.value} role="tab" type="button" aria-selected={value === o.value} disabled={o.disabled} onClick={() => onChange(o.value)} className="dome-press inline-flex items-center justify-center gap-1.5 px-2 disabled:opacity-40 disabled:cursor-not-allowed">
          {o.icon ? <Icon name={o.icon} size={17} /> : null}
          <span className="truncate">{o.label}</span>
        </button>
      ))}
    </div>
  );
}

/** A card made of hairline-separated rows (`ListRow`). */
export function ListCard({ children, className = "", as: Tag = "ul", ...rest }: { children: ReactNode; className?: string; as?: "ul" | "ol" | "div" | "dl" } & Record<string, unknown>) {
  return (
    <Tag className={`dome-card dome-list overflow-hidden p-0 ${className}`} {...rest}>
      {children}
    </Tag>
  );
}

/** One row with an optional icon well, a title, a line under it and something on the right; a link when `to` is set. */
export function ListRow({ icon, tone = "accent", title, text, right, to, onClick, as: Tag = "li", className = "" }: { icon?: IconName; tone?: WellTone; title: ReactNode; text?: ReactNode; right?: ReactNode; to?: string; onClick?: () => void; as?: "li" | "div"; className?: string }) {
  const body = (
    <>
      {icon ? <IconWell icon={icon} tone={tone} size={36} /> : null}
      <span className="min-w-0 flex-1">
        <span className="block font-semibold leading-snug">{title}</span>
        {text ? <span className="mt-0.5 block text-[13px] leading-snug text-text-muted">{text}</span> : null}
      </span>
      {right !== undefined ? <span className="shrink-0">{right}</span> : to ? <Icon name="chevron" size={18} className="shrink-0 text-text-faint" /> : null}
    </>
  );
  const inner = "flex min-h-[56px] w-full items-center gap-3.5 px-4 py-3 text-left";
  return (
    <Tag className={className}>
      {to ? (
        <Link to={to} className={`${inner} transition-colors hover:bg-surface`}>
          {body}
        </Link>
      ) : onClick ? (
        <button type="button" onClick={onClick} className={`${inner} dome-press hover:bg-surface`}>
          {body}
        </button>
      ) : (
        <div className={inner}>{body}</div>
      )}
    </Tag>
  );
}

/**
 * Label / value pairs (Devices, Link): the label muted on the left, the value on the right. A long value
 * (`stack`, or any string over 40 characters) goes under its label instead, left-aligned and easy to read.
 */
export function KeyValue({ rows, className = "" }: { rows: Array<{ label: string; value: ReactNode; testId?: string; stack?: boolean }>; className?: string }) {
  return (
    <dl className={`divide-y divide-border text-sm ${className}`}>
      {rows.map((r) => {
        const stacked = r.stack ?? (typeof r.value === "string" && r.value.length > 40);
        return stacked ? (
          <div key={r.label} className="py-2.5">
            <dt className="text-xs font-medium text-text-muted">{r.label}</dt>
            <dd className="mt-0.5 font-medium leading-snug" data-testid={r.testId}>
              {r.value}
            </dd>
          </div>
        ) : (
          <div key={r.label} className="flex items-start justify-between gap-4 py-2.5">
            <dt className="shrink-0 text-text-muted">{r.label}</dt>
            <dd className="min-w-0 text-right font-medium" data-testid={r.testId}>
              {r.value}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** A tappable example or suggestion (typed commands, the butler's choices). */
export function Chip({ children, onClick, pressed, disabled = false, className = "" }: { children: ReactNode; onClick: () => void; pressed?: boolean; disabled?: boolean; className?: string }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} aria-pressed={pressed} className={`tap dome-press rounded-full border px-3.5 py-2 text-sm font-medium text-text disabled:opacity-40 ${pressed ? "border-accent bg-accent/10" : "border-border bg-surface hover:bg-surface-hover"} ${className}`}>
      {children}
    </button>
  );
}

/** A native checkbox in a tappable row: the whole row is the label. */
export function CheckRow({ id, label, hint, checked, disabled = false, onChange }: { id?: string; label: ReactNode; hint?: ReactNode; checked: boolean; disabled?: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className={`flex min-h-[48px] items-center gap-3 rounded-control border px-3.5 py-2.5 text-sm transition-colors ${checked ? "border-accent/50 bg-accent/8" : "border-border bg-surface"} ${disabled ? "opacity-70" : "cursor-pointer"}`}>
      <input id={id} type="checkbox" className="dome-check" checked={checked} disabled={disabled} onChange={(e) => onChange(e.currentTarget.checked)} />
      <span className="min-w-0 flex-1">
        <span className="block font-medium text-text">{label}</span>
        {hint ? <span className="block text-xs text-text-muted">{hint}</span> : null}
      </span>
    </label>
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

export function EmptyState({ title, children, action, icon }: { title: string; children?: ReactNode; action?: ReactNode; icon?: IconName }) {
  return (
    <Card className="text-center py-9">
      {icon ? <IconWell icon={icon} tone="muted" size={52} radius={16} className="mb-4" /> : null}
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

export const inputClass = "w-full rounded-control bg-bg-sunken border border-control-edge px-4 py-3 text-base text-text placeholder:text-text-faint transition-colors focus:border-accent/60 min-h-[48px]";

export function Toggle({ checked, onChange, label, disabled = false, id }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean; id?: string }) {
  return (
    <button id={id} type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)} className={`relative inline-flex h-[31px] w-[51px] shrink-0 items-center rounded-full transition-colors duration-200 disabled:opacity-40 ${checked ? "bg-primary" : "bg-control-edge"}`}>
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

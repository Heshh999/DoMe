import { Link } from "react-router";

import { Icon, type IconName } from "../../components/icons.tsx";
import { ThemeSwitch } from "../../components/ThemeSwitch.tsx";
import { PageHeader } from "../../components/ui.tsx";
import { useSessionStore } from "../../store/session.ts";

const GROUPS: Array<Array<{ to: string; title: string; text: string; icon: IconName }>> = [
  [
    { to: "/app/apps", title: "Apps", text: "Open and switch approved apps", icon: "apps" },
    { to: "/app/assistant", title: "Assistant", text: "Talk, and DoMe works your PC", icon: "ask" },
    { to: "/app/command", title: "Type a command", text: "Commands without any AI", icon: "keyboard" },
  ],
  [
    { to: "/app/devices", title: "Devices", text: "PCs, phones and permissions", icon: "devices" },
    { to: "/app/health", title: "Connection health", text: "What works, and what to do next", icon: "pulse" },
  ],
  [
    { to: "/app/routines", title: "Routines", text: "One-tap sequences · Pro preview", icon: "sparkle" },
    { to: "/app/layouts", title: "Custom remotes", text: "Your own layouts · Pro preview", icon: "remote" },
    { to: "/app/billing", title: "Billing", text: "Your plan and limits", icon: "card" },
    { to: "/app/settings", title: "Settings & help", text: "Install, diagnostics, sign out", icon: "settings" },
  ],
];

export function MorePage() {
  const session = useSessionStore((s) => s.session);
  return (
    <div className="space-y-5">
      <PageHeader title="More" subtitle={session ? <>Signed in as <span className="text-text">{session.account.display_name || session.account.email}</span></> : undefined} />
      {GROUPS.map((group, g) => (
        <ul key={g} className="dome-card divide-y divide-border overflow-hidden p-0">
          {group.map((i) => (
            <li key={i.to}>
              <Link to={i.to} className="flex items-center gap-3.5 px-4 py-3.5 transition-colors hover:bg-surface">
                <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent/12 text-accent">
                  <Icon name={i.icon} size={19} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold">{i.title}</span>
                  <span className="block truncate text-[13px] text-text-muted">{i.text}</span>
                </span>
                <Icon name="chevron" size={18} className="text-text-faint" />
              </Link>
            </li>
          ))}
        </ul>
      ))}
      <div className="dome-card flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <span className="font-semibold">Appearance</span>
        <ThemeSwitch />
      </div>
      <p className="flex items-center justify-center gap-1 text-center text-xs text-text-faint">
        <Link to="/privacy" className="tap inline-flex items-center rounded-full px-3 font-medium hover:text-text">
          Privacy
        </Link>
        <span aria-hidden="true">·</span>
        <Link to="/terms" className="tap inline-flex items-center rounded-full px-3 font-medium hover:text-text">
          Terms
        </Link>
        <span aria-hidden="true">·</span>
        <Link to="/support" className="tap inline-flex items-center rounded-full px-3 font-medium hover:text-text">
          Support
        </Link>
      </p>
    </div>
  );
}

import { Link } from "react-router";

import { Card } from "../../components/ui.tsx";
import { useSessionStore } from "../../store/session.ts";

const ITEMS = [
  { to: "/app/devices", title: "Devices", text: "PCs, paired phones, permissions, pairing" },
  { to: "/app/health", title: "Connection health", text: "What works, what does not, and the next step" },
  { to: "/app/routines", title: "Routines", text: "One-tap sequences (DoMe Pro preview)" },
  { to: "/app/layouts", title: "Custom remotes", text: "Your own layouts (DoMe Pro preview)" },
  { to: "/app/billing", title: "Billing", text: "Your plan and limits" },
  { to: "/app/settings", title: "Settings & help", text: "Connection check, install on iPhone, diagnostics, sign out" },
];

export function MorePage() {
  const session = useSessionStore((s) => s.session);
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">More</h1>
      {session ? (
        <p className="text-sm text-text-muted">
          Signed in as <span className="text-text">{session.account.display_name || session.account.email}</span>
        </p>
      ) : null}
      <ul className="space-y-2">
        {ITEMS.map((i) => (
          <li key={i.to}>
            <Link to={i.to} className="block">
              <Card className="flex items-center justify-between hover:bg-surface-hover">
                <span>
                  <span className="block font-semibold">{i.title}</span>
                  <span className="block text-sm text-text-muted">{i.text}</span>
                </span>
                <span aria-hidden="true" className="text-text-faint">
                  ›
                </span>
              </Card>
            </Link>
          </li>
        ))}
      </ul>
      <p className="text-xs text-text-faint">
        <Link to="/privacy" className="underline">
          Privacy
        </Link>{" "}
        ·{" "}
        <Link to="/terms" className="underline">
          Terms
        </Link>{" "}
        ·{" "}
        <Link to="/support" className="underline">
          Support
        </Link>
      </p>
    </div>
  );
}

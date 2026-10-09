/** Routines: a Pro preview. Nothing here runs or is saved until paid launch; no checkout exists yet. */
import { PLANS } from "@dome/protocol";

import { ProExplanation } from "../../components/ProExplanation.tsx";
import { Card, Pill } from "../../components/ui.tsx";
import { useSessionStore } from "../../store/session.ts";

const EXAMPLES = [
  { name: "Movie time", steps: ["Open Chrome", "Set Windows volume to 25%"] },
  { name: "Quiet mode", steps: ["Pause the media player", "Set Windows volume to 10%"] },
  { name: "Finished for the night", steps: ["Pause YouTube", "Set Windows volume to 20%", "Lock Windows"] },
];

export function RoutinesPage() {
  const session = useSessionStore((s) => s.session);
  const pro = PLANS.plans.pro;
  const enabled = session?.limits.routines === true;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Routines</h1>
        <Pill tone="info">{pro.display_name}</Pill>
      </div>
      <ProExplanation benefit={enabled ? "Running one-tap routines (not available in this version yet)" : "One-tap routines"}>
        <p>
          A routine runs up to {pro.routine_max_steps} approved actions in order on one PC, within {pro.routine_max_seconds} seconds, and stops at the first error. Every step is checked by the PC at run time, and nothing that needs a confirmation (closing apps, sleep, restart, shutdown) or manual touchpad/keyboard input can be part of a routine.
        </p>
      </ProExplanation>
      <h2 className="font-semibold">Example routines</h2>
      <ul className="space-y-3" aria-label="Example routines (preview)">
        {EXAMPLES.map((r) => (
          <Card key={r.name} as="li" className="opacity-80" aria-disabled="true">
            <div className="flex items-center justify-between">
              <p className="font-semibold">{r.name}</p>
              <span className="text-xs text-text-faint">Preview</span>
            </div>
            <ol className="mt-2 list-decimal pl-5 text-sm text-text-muted space-y-0.5">
              {r.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
            <button type="button" disabled className="mt-3 w-full rounded-control border border-border py-2.5 text-sm text-text-faint cursor-not-allowed">
              Run (available with {pro.display_name})
            </button>
          </Card>
        ))}
      </ul>
    </div>
  );
}

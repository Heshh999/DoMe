/** Routines: a Pro preview. Nothing here runs or is saved until paid launch; no checkout exists yet. */
import { PLANS } from "@dome/protocol";

import { ProExplanation } from "../../components/ProExplanation.tsx";
import { Card, IconWell, PageHeader, Pill, SectionLabel } from "../../components/ui.tsx";
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
      <PageHeader title="Routines" action={<Pill tone="info">{pro.display_name}</Pill>} />
      <ProExplanation benefit={enabled ? "Running one-tap routines (not available in this version yet)" : "One-tap routines"}>
        <p>
          A routine runs up to {pro.routine_max_steps} approved actions in order on one PC, within {pro.routine_max_seconds} seconds, and stops at the first error. Every step is checked by the PC at run time, and nothing that needs a confirmation (closing apps, sleep, restart, shutdown) or manual touchpad/keyboard input can be part of a routine.
        </p>
      </ProExplanation>
      <section className="pt-1">
        <SectionLabel>Example routines</SectionLabel>
        <ul className="space-y-3" aria-label="Example routines (preview)">
          {EXAMPLES.map((r) => (
            <Card key={r.name} as="li" className="p-4 opacity-80" aria-disabled="true">
              <div className="flex items-center gap-3.5">
                <IconWell icon="bolt" tone="muted" size={40} />
                <p className="min-w-0 flex-1 font-semibold">{r.name}</p>
                <span className="text-xs font-medium text-text-faint">Preview</span>
              </div>
              <ol className="mt-3 list-decimal space-y-0.5 pl-5 text-sm text-text-muted marker:text-text-faint">
                {r.steps.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ol>
              <button type="button" disabled className="mt-3 w-full cursor-not-allowed rounded-control border border-border py-2.5 text-sm font-medium text-text-faint">
                Run (available with {pro.display_name})
              </button>
            </Card>
          ))}
        </ul>
      </section>
    </div>
  );
}

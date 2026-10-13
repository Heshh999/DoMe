/** Custom remotes (layouts): a Pro preview reached only by a deliberate selection from More. Nothing is saved. */
import { Card, IconWell, PageHeader, Pill, SectionLabel } from "../../components/ui.tsx";
import { ProExplanation } from "../../components/ProExplanation.tsx";

const EXAMPLES = [
  { name: "Media", controls: ["Play/pause", "Next video", "YouTube volume", "Windows volume"] },
  { name: "Desk", controls: ["Touchpad", "Keyboard", "Open Chrome", "Lock Windows"] },
  { name: "Presentation", controls: ["Arrow keys", "Esc", "Open PowerPoint (approved app)", "Mute PC"] },
];

export function LayoutsPage() {
  return (
    <div className="space-y-4">
      <PageHeader title="Custom remotes" action={<Pill tone="info">Pro preview</Pill>} />
      <ProExplanation benefit="Saving your own remote layouts" />
      <section className="pt-1">
        <SectionLabel>Example layouts</SectionLabel>
        <ul className="space-y-3" aria-label="Example layouts (preview)">
          {EXAMPLES.map((l) => (
            <Card key={l.name} as="li" className="flex items-center gap-3.5 p-4">
              <IconWell icon="layout" tone="muted" size={40} />
              <div className="min-w-0 flex-1">
                <p className="font-semibold">{l.name}</p>
                <p className="mt-0.5 text-sm text-text-muted">{l.controls.join(" · ")}</p>
              </div>
            </Card>
          ))}
        </ul>
      </section>
      <p className="px-1 text-xs text-text-muted">Arrange the controls you use most into named remotes such as Media, Desk or Presentation. Every control still goes through the same checked list of actions and the same confirmations; a layout can never add a capability the PC did not grant.</p>
    </div>
  );
}

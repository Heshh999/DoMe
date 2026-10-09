/** Custom remotes (layouts): a Pro preview reached only by a deliberate selection from More. Nothing is saved. */
import { Card, Pill } from "../../components/ui.tsx";
import { ProExplanation } from "../../components/ProExplanation.tsx";

const EXAMPLES = [
  { name: "Media", controls: ["Play/pause", "Next video", "YouTube volume", "Windows volume"] },
  { name: "Desk", controls: ["Touchpad", "Keyboard", "Open Chrome", "Lock Windows"] },
  { name: "Presentation", controls: ["Arrow keys", "Esc", "Open PowerPoint (approved app)", "Mute PC"] },
];

export function LayoutsPage() {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Custom remotes</h1>
        <Pill tone="info">Preview</Pill>
      </div>
      <ProExplanation benefit="Saving your own remote layouts">
        <p>Arrange the controls you use most into named remotes such as Media, Desk or Presentation. Every control still goes through the same validated action registry and the same confirmations; a layout can never add a capability the PC did not grant.</p>
      </ProExplanation>
      <ul className="space-y-3" aria-label="Example layouts (preview)">
        {EXAMPLES.map((l) => (
          <Card key={l.name} as="li" className="opacity-80" aria-disabled="true">
            <p className="font-semibold">{l.name}</p>
            <p className="text-sm text-text-muted mt-1">{l.controls.join(" · ")}</p>
          </Card>
        ))}
      </ul>
    </div>
  );
}

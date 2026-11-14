/**
 * The Remote page's honest placeholder for Screen view (roadmap #52) while the server has it switched off (GET /healthz
 * `screen_view` not "on"; ScreenViewTile shows the real tile where it is on). The feature is built; it is only switched
 * off. Labelled SCREEN_VIEW_STATUS like the website and the FAQ (DECISIONS 63). No button, no link, no "try": nothing
 * here may read as available.
 */
import { SCREEN_VIEW_STATUS } from "../content/plans.ts";
import { Card, Pill } from "./ui.tsx";

export const SCREEN_VIEW_COMING_TEXT = "See your PC’s screen on your phone and tap what you see. Built, but not switched on for DoMe accounts yet.";

export function ScreenViewComingCard() {
  return (
    <Card aria-labelledby="screen-view-coming" data-testid="screen-view-coming">
      <div className="flex items-center justify-between gap-3">
        <h2 id="screen-view-coming" className="font-semibold">
          Screen view
        </h2>
        <Pill tone="neutral">{SCREEN_VIEW_STATUS}</Pill>
      </div>
      <p className="mt-2 text-sm text-text-muted">{SCREEN_VIEW_COMING_TEXT}</p>
    </Card>
  );
}

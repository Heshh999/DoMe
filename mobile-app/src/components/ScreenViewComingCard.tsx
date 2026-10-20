/**
 * The Remote page's honest placeholder for Screen view (roadmap #52): the feature is designed, not built.
 * Driven by SCREEN_VIEW_STATUS so the app, the website and the FAQ change together when it ships
 * (DECISIONS 63). No button, no link, no "try": nothing here may read as available.
 */
import { SCREEN_VIEW_STATUS } from "../content/plans.ts";
import { Card, Pill } from "./ui.tsx";

export const SCREEN_VIEW_COMING_TEXT = "See your PC’s screen on your phone and tap what you see. In development; not in the app yet.";

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

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SCREEN_VIEW_COMING_TEXT, ScreenViewComingCard } from "../../src/components/ScreenViewComingCard.tsx";
import { SCREEN_VIEW_STATUS } from "../../src/content/plans.ts";

describe("ScreenViewComingCard", () => {
  it("names the feature, carries the shared 'Coming' label and says it is built but not switched on yet", () => {
    render(<ScreenViewComingCard />);
    const card = screen.getByTestId("screen-view-coming");
    expect(within(card).getByRole("heading", { level: 2, name: "Screen view" })).toBeInTheDocument();
    // the label is the shared honesty constant (DECISIONS 63); pinning its value makes a relabel a deliberate test change
    expect(SCREEN_VIEW_STATUS).toBe("Coming");
    expect(within(card).getByText(SCREEN_VIEW_STATUS)).toBeInTheDocument();
    // Screen view is built; the card shows only while the server has it switched off (ScreenViewTile)
    expect(within(card).getByText(/Built, but not switched on for DoMe accounts yet\./)).toBeInTheDocument();
    expect(card.textContent).not.toMatch(/in development|not in the app/i);
    expect(card).toHaveTextContent(SCREEN_VIEW_COMING_TEXT);
  });

  it("makes no claim of availability: no button, no link, no wording that invites a try", () => {
    render(<ScreenViewComingCard />);
    const card = screen.getByTestId("screen-view-coming");
    expect(within(card).queryAllByRole("button")).toHaveLength(0);
    expect(within(card).queryAllByRole("link")).toHaveLength(0);
    expect(card.textContent ?? "").not.toMatch(/available|ready|try|start|open|watch now/i);
  });
});

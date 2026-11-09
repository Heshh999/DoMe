/**
 * "A newer DoMe is ready." (DECISIONS 92): shown only while a newer version waits for something busy to end; it names
 * what it waits for; "Update now" first says what it ends and reloads only on "End it and update"; "Not now" goes back;
 * "Hide" puts it away until something else is what it waits for. Takes touches only on itself, over the Screen view
 * stage (z-50) and above the tab bar.
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppUpdateNotice } from "../../src/components/AppUpdateNotice.tsx";
import { NO_UPDATE, UPDATE_COPY } from "../../src/lib/appUpdate.ts";
import { useAppUpdateStore } from "../../src/store/appUpdate.ts";
import { BUSY_ORDER } from "../../src/store/busy.ts";

afterEach(() => {
  act(() => useAppUpdateStore.setState(NO_UPDATE));
});

describe("AppUpdateNotice", () => {
  it("shows nothing without a newer version, or while it is being loaded (nothing to wait for)", () => {
    const { container } = render(<AppUpdateNotice onUpdateNow={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    act(() => useAppUpdateStore.setState({ ready: true, waitingFor: null }));
    expect(container).toBeEmptyDOMElement();
  });

  it("during Screen view: 'A newer DoMe is ready. It will update when you leave Screen view.', over the stage, touchable only on itself", () => {
    useAppUpdateStore.setState({ ready: true, waitingFor: "screen" });
    render(<AppUpdateNotice onUpdateNow={vi.fn()} />);
    const notice = screen.getByTestId("app-update-notice");
    expect(notice).toHaveTextContent("A newer DoMe is ready. It will update when you leave Screen view.");
    expect(notice).toHaveAttribute("role", "status");
    expect(notice).toHaveClass("pointer-events-auto");
    expect(notice.parentElement).toHaveClass("pointer-events-none", "fixed", "z-[60]");
  });

  it("'Update now' says what it ends first; only 'End it and update' applies it; 'Not now' goes back", async () => {
    useAppUpdateStore.setState({ ready: true, waitingFor: "screen" });
    const onUpdateNow = vi.fn();
    render(<AppUpdateNotice onUpdateNow={onUpdateNow} />);
    await userEvent.click(screen.getByRole("button", { name: "Update now" }));
    expect(screen.getByTestId("app-update-ends")).toHaveTextContent("Updating now ends Screen view.");
    expect(onUpdateNow).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByTestId("app-update-ends")).toBeNull();
    expect(onUpdateNow).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Update now" }));
    await userEvent.click(screen.getByRole("button", { name: "End it and update" }));
    expect(onUpdateNow).toHaveBeenCalledTimes(1);
  });

  it("names every busy kind with its own words", () => {
    useAppUpdateStore.setState({ ready: true, waitingFor: "screen" });
    render(<AppUpdateNotice onUpdateNow={vi.fn()} />);
    for (const kind of BUSY_ORDER) {
      act(() => useAppUpdateStore.setState({ ready: true, waitingFor: kind }));
      expect(screen.getByTestId("app-update-notice")).toHaveAttribute("data-kind", kind);
      expect(screen.getByTestId("app-update-notice")).toHaveTextContent(`${UPDATE_COPY.ready} ${UPDATE_COPY.when[kind]}`);
    }
  });

  it("'Hide' puts it away; it comes back when the update waits for something else; a new reason starts without the confirmation open", async () => {
    useAppUpdateStore.setState({ ready: true, waitingFor: "pairing" });
    render(<AppUpdateNotice onUpdateNow={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.queryByTestId("app-update-notice")).toBeNull();
    act(() => useAppUpdateStore.setState({ ready: true, waitingFor: "screen" }));
    expect(screen.getByTestId("app-update-notice")).toHaveTextContent(UPDATE_COPY.when.screen);
    await userEvent.click(screen.getByRole("button", { name: "Update now" }));
    expect(screen.getByTestId("app-update-ends")).toBeInTheDocument();
    act(() => useAppUpdateStore.setState({ ready: true, waitingFor: "input" }));
    expect(screen.getByTestId("app-update-notice")).toHaveTextContent(UPDATE_COPY.when.input);
    expect(screen.queryByTestId("app-update-ends")).toBeNull();
  });
});

/**
 * Apps → Close… is the most common confirmation-required action. The phone sends only ids (never the
 * PC-supplied display name), the PC's `confirmation_required` lands on that record, and the global
 * modal renders the registry label with the inventory PC name and offers Approve because the
 * challenge binds to the command this phone sent.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { GlobalConfirmation } from "../../src/app/AppShell.tsx";
import { AppsPage } from "../../src/pages/app/AppsPage.tsx";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore } from "../../src/store/live.ts";
import { FakeSocket, freshStorage, installDialogPolyfill, makeRuntime, OFFICE_PC, PC, signedIn, TS, type RuntimeHarness } from "../helpers/harness.ts";

const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../../shared/protocol/fixtures/es256-typescript.json"), "utf8")) as { digests: { challenge_text: string } };
const fixtureChallenge = JSON.parse(fixture.digests.challenge_text) as { command_id: string; pc_id: string; controller_id: string };

installDialogPolyfill();

let h: RuntimeHarness;
let socket: FakeSocket;

beforeEach(async () => {
  freshStorage();
  signedIn();
  h = makeRuntime();
  socket = await h.connect();
  useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
  const live = useLiveStore.getState();
  live.onPcStatus({ type: "pc_status", pc_id: PC, connection: "online", last_seen: TS });
  live.onState({ type: "state", pc_id: PC, at: TS, state: { remote_enabled: true, session_locked: false, extension_connected: true } });
  live.setApps(PC, [{ app_id: "notepad", display_name: "Notepad <b>x</b>", running: true, windows: [{ window_id: "w1", title: "Untitled — Notepad" }] }]);
});

afterEach(() => h.teardown());

function renderApps() {
  return render(
    <MemoryRouter initialEntries={["/app/apps"]}>
      <AppsPage />
      <GlobalConfirmation />
    </MemoryRouter>,
  );
}

describe("AppsPage", () => {
  it("Close… sends a signed command carrying only the app id; the PC's challenge opens the modal with the registry label and the inventory PC name", async () => {
    renderApps();
    expect(screen.getByText("Notepad <b>x</b>")).toBeInTheDocument(); // display name rendered as text
    const sentBefore = socket.sent.length;
    await userEvent.click(screen.getByRole("button", { name: "Close…" }));
    await waitFor(() => expect(useLiveStore.getState().commands.find((c) => c.action === "app.close")).toBeDefined());
    const rec = useLiveStore.getState().commands.find((c) => c.action === "app.close")!;
    expect(rec.pcId).toBe(PC);
    expect(rec.target).toEqual({ app_id: "notepad" });
    expect(rec.params).toEqual({});
    const frames = socket.frames().slice(sentBefore);
    const cmd = frames.find((f) => f.type === "command")!;
    expect(cmd).toBeDefined();
    expect(cmd.pc_id).toBe(PC);
    expect(JSON.stringify(cmd)).not.toContain("Notepad"); // only ids leave the phone
    expect(screen.queryByTestId("confirm-primary")).toBeNull();

    // the PC answers with a challenge for exactly this command (same controller/PC as the fixture)
    expect(fixtureChallenge.pc_id).toBe(PC);
    const text = fixture.digests.challenge_text.replace(fixtureChallenge.command_id, rec.commandId).replace('"expires_at":"2026-10-08T12:01:00.000Z"', `"expires_at":"${new Date(Date.now() + 60_000).toISOString()}"`);
    socket.receive({ type: "confirmation_required", command_id: rec.commandId, challenge_text: text });
    await waitFor(() => expect(screen.getByTestId("confirm-primary")).toBeInTheDocument());
    expect(screen.getByTestId("confirm-primary")).toHaveTextContent("Close Notepad <b>x</b>"); // registry label + app.list name, as text
    expect(screen.queryByText(/Close Notepad$/)).toBeNull(); // never the challenge's display.action_label
    expect(screen.getAllByText("Office PC")).toHaveLength(2); // PC switcher + the modal header
    expect(screen.queryByText(/Wohnzimmer/)).toBeNull();
    expect(screen.getByTestId("confirm-detail")).toHaveTextContent("Untitled — Notepad / 📝");
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(useLiveStore.getState().commands.find((c) => c.commandId === rec.commandId)!.confirmation!.bindingProblem).toBeNull();
  });

  it("Approve from the modal sends a signed confirmation frame for the challenge's PC", async () => {
    renderApps();
    await userEvent.click(screen.getByRole("button", { name: "Close…" }));
    await waitFor(() => expect(useLiveStore.getState().commands.find((c) => c.action === "app.close")).toBeDefined());
    const rec = useLiveStore.getState().commands.find((c) => c.action === "app.close")!;
    const text = fixture.digests.challenge_text.replace(fixtureChallenge.command_id, rec.commandId).replace('"expires_at":"2026-10-08T12:01:00.000Z"', `"expires_at":"${new Date(Date.now() + 60_000).toISOString()}"`);
    socket.receive({ type: "confirmation_required", command_id: rec.commandId, challenge_text: text });
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled());
    const before = socket.sent.length;
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(socket.sent.length).toBeGreaterThan(before));
    const frame = socket.frames().at(-1)!;
    expect(frame.type).toBe("confirmation");
    expect(frame.pc_id).toBe(PC);
    expect(useLiveStore.getState().commands.find((c) => c.commandId === rec.commandId)!.confirmation!.decision).toBe("approve");
    await waitFor(() => expect(screen.queryByTestId("confirm-primary")).toBeNull()); // answered: modal gone
  });

  it("controls are withheld while the PC's state is stale", () => {
    useLiveStore.getState().markAllStale();
    renderApps();
    expect(screen.getByRole("button", { name: "Close…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Open again" })).toBeDisabled();
  });
});

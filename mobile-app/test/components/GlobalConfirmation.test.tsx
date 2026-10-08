/**
 * The confirmation header names the PC from the device inventory matched by the *challenge's*
 * pc_id (ADR-0001 D7), so it agrees with the binding warning when the challenge names another PC.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { GlobalConfirmation } from "../../src/app/AppShell.tsx";
import type { CommandRecord } from "../../src/lib/commands.ts";
import { parseChallenge } from "../../src/lib/confirmations.ts";
import { useDevicesStore } from "../../src/store/devices.ts";
import { useLiveStore } from "../../src/store/live.ts";
import { installDialogPolyfill, OFFICE_PC, OTHER_PC, PC, TS } from "../helpers/harness.ts";

const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../../shared/protocol/fixtures/es256-typescript.json"), "utf8")) as { digests: { challenge_text: string } };
const KITCHEN_PC = { ...OFFICE_PC, id: OTHER_PC, name: "Kitchen PC" };

installDialogPolyfill();

/** The fixture challenge, still valid for a minute (its own expires_at is a fixed past instant). */
const challengeText = fixture.digests.challenge_text.replace('"expires_at":"2026-10-08T12:01:00.000Z"', `"expires_at":"${new Date(Date.now() + 60_000).toISOString()}"`);

/** A record this phone sent to `recordPcId`; the fixture challenge itself names PC (3333…). */
function record(recordPcId: string, bindingProblem: string | null): CommandRecord {
  const parsed = parseChallenge(challengeText);
  return {
    commandId: parsed.challenge.command_id,
    pcId: recordPcId,
    action: "app.close",
    params: {},
    target: { app_id: "notepad" },
    createdAt: Date.now(),
    expiresAt: TS,
    state: "awaiting_confirmation",
    ackAt: null,
    confirmation: { parsed, bindingProblem, receivedAt: Date.now(), decision: null, connectionLost: false },
    terminal: null,
    noAnswer: false,
    source: "button",
  };
}

beforeEach(() => {
  useLiveStore.getState().reset();
  useDevicesStore.getState().reset();
  useLiveStore.getState().setRelay("open");
});

describe("GlobalConfirmation", () => {
  it("names the PC the challenge is bound to, not the PC the record was sent to", () => {
    useDevicesStore.setState({ loaded: true, pcs: [KITCHEN_PC, OFFICE_PC], selectedPcId: OTHER_PC });
    useLiveStore.getState().upsertCommand(record(OTHER_PC, "The confirmation names a different PC."));
    render(<GlobalConfirmation />);
    expect(screen.getByText("Office PC")).toBeInTheDocument(); // inventory name for the challenge's pc_id
    expect(screen.queryByText("Kitchen PC")).toBeNull();
    expect(screen.queryByText(/Wohnzimmer/)).toBeNull(); // never the challenge's own display.pc_name
    expect(screen.getByText(/names a different PC/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("falls back to 'your PC' when the challenge's pc_id is not in the inventory", () => {
    useDevicesStore.setState({ loaded: true, pcs: [KITCHEN_PC], selectedPcId: OTHER_PC });
    useLiveStore.getState().upsertCommand(record(OTHER_PC, "The confirmation names a different PC."));
    render(<GlobalConfirmation />);
    expect(screen.getByText("your PC")).toBeInTheDocument();
    expect(screen.queryByText("Kitchen PC")).toBeNull();
  });

  it("bound challenge: header and record agree and Approve is offered", () => {
    useDevicesStore.setState({ loaded: true, pcs: [OFFICE_PC], selectedPcId: PC });
    useLiveStore.getState().upsertCommand(record(PC, null));
    render(<GlobalConfirmation />);
    expect(screen.getByTestId("confirm-primary")).toHaveTextContent("Close notepad");
    expect(screen.getByText("Office PC")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });
});

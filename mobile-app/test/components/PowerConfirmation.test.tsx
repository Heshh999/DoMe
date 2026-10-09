import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmationModal } from "../../src/components/ConfirmationModal.tsx";
import type { CommandRecord } from "../../src/lib/commands.ts";
import { parseChallenge } from "../../src/lib/confirmations.ts";
import { isPowerAction, POWER_CONFIRMATION_COPY } from "../../src/lib/power.ts";
import { installDialogPolyfill } from "../helpers/harness.ts";

const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../../shared/protocol/fixtures/es256-typescript.json"), "utf8")) as { digests: { challenge_text: string } };
installDialogPolyfill();

function powerRecord(action: "power.sleep" | "power.shutdown" | "app.close"): CommandRecord {
  // Same challenge envelope as the fixture, re-aimed at a power action with a PC-supplied detail line.
  const text = fixture.digests.challenge_text.replace('"action":"app.close"', `"action":"${action}"`).replace('"target":{"app_id":"notepad"}', '"target":null').replace('"params":{}', action === "app.close" ? '"params":{}' : '"params":{"countdown_seconds":10}').replace('"detail":"Untitled — Notepad / 📝"', '"detail":"Shuts down in 10 s. <b>Verbatim</b> text from the PC"');
  const parsed = parseChallenge(text);
  return {
    commandId: parsed.challenge.command_id,
    pcId: parsed.challenge.pc_id,
    action,
    params: action === "app.close" ? {} : { countdown_seconds: 10 },
    target: action === "app.close" ? { app_id: "notepad" } : null,
    createdAt: Date.now(),
    expiresAt: "2026-10-08T12:01:30.000Z",
    state: "awaiting_confirmation",
    ackAt: null,
    confirmation: { parsed, bindingProblem: null, receivedAt: Date.now(), decision: null, connectionLost: false },
    terminal: null,
    noAnswer: false,
    source: "button",
  };
}

const NOW = () => new Date("2026-10-08T12:00:10.000Z");

describe("power confirmations (spec §10)", () => {
  it("every Sleep/Restart/Shutdown confirmation states that remote access can end and that V1 has no remote wake, and shows the PC's display text verbatim", () => {
    render(<ConfirmationModal record={powerRecord("power.shutdown")} pcName="Office PC" onRespond={vi.fn(async () => undefined)} now={NOW} />);
    expect(screen.getByTestId("confirm-primary")).toHaveTextContent("Shut down the PC after a 10-second countdown");
    const copy = screen.getByTestId("confirm-power-copy");
    expect(copy).toHaveTextContent(POWER_CONFIRMATION_COPY);
    expect(copy.textContent).toMatch(/interrupt or end remote access/);
    expect(copy.textContent).toMatch(/cannot wake or power it on again remotely/);
    expect(screen.getByTestId("confirm-detail")).toHaveTextContent("Shuts down in 10 s. <b>Verbatim</b> text from the PC"); // text, not markup, unchanged
    expect(screen.getByTestId("confirm-detail").querySelector("b")).toBeNull();
  });
  it("sleep gets the same fixed copy; a non-power confirmation does not", () => {
    const { unmount } = render(<ConfirmationModal record={powerRecord("power.sleep")} pcName="Office PC" onRespond={vi.fn(async () => undefined)} now={NOW} />);
    expect(screen.getByTestId("confirm-power-copy")).toBeInTheDocument();
    unmount();
    render(<ConfirmationModal record={powerRecord("app.close")} pcName="Office PC" onRespond={vi.fn(async () => undefined)} now={NOW} />);
    expect(screen.queryByTestId("confirm-power-copy")).toBeNull();
    expect(isPowerAction("power.restart")).toBe(true);
    expect(isPowerAction("power.cancel")).toBe(false);
    expect(isPowerAction("windows.lock")).toBe(false);
  });
});

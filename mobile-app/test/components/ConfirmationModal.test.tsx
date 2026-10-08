import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmationModal } from "../../src/components/ConfirmationModal.tsx";
import type { CommandRecord } from "../../src/lib/commands.ts";
import { parseChallenge } from "../../src/lib/confirmations.ts";

const fixture = JSON.parse(readFileSync(resolve(__dirname, "../../../shared/protocol/fixtures/es256-typescript.json"), "utf8")) as { digests: { challenge_text: string } };

// jsdom has no <dialog>.showModal
beforeAllDialogPolyfill();
function beforeAllDialogPolyfill() {
  if (typeof HTMLDialogElement !== "undefined" && !HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function () {
      this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function () {
      this.removeAttribute("open");
    };
  }
}

function record(text: string, bindingProblem: string | null): CommandRecord {
  const parsed = parseChallenge(text);
  return {
    commandId: parsed.challenge.command_id,
    pcId: parsed.challenge.pc_id,
    action: "app.close",
    params: {},
    target: { app_id: "notepad" },
    createdAt: Date.now(),
    expiresAt: "2026-10-08T12:01:30.000Z",
    state: "awaiting_confirmation",
    ackAt: null,
    confirmation: { parsed, bindingProblem, receivedAt: Date.now(), decision: null },
    terminal: null,
    noAnswer: false,
    source: "button",
  };
}

const NOW = () => new Date("2026-10-08T12:00:10.000Z");

describe("ConfirmationModal", () => {
  it("renders the registry-derived primary line, the inventory PC name, the detail as secondary text, a countdown, and approves", async () => {
    const tampered = fixture.digests.challenge_text.replace('"action_label":"Close Notepad"', '"action_label":"Mute YouTube"').replace('"pc_name":"Wohnzimmer-PC"', '"pc_name":"Attacker PC"');
    const onRespond = vi.fn(async () => undefined);
    render(<ConfirmationModal record={record(tampered, null)} pcName="Office PC" labels={{ appNames: { notepad: "Notepad" } }} onRespond={onRespond} now={NOW} />);
    expect(screen.getByTestId("confirm-primary")).toHaveTextContent("Close Notepad");
    expect(screen.queryByText(/Mute YouTube/)).toBeNull();
    expect(screen.getByText("Office PC")).toBeInTheDocument();
    expect(screen.queryByText(/Attacker PC/)).toBeNull();
    expect(screen.getByTestId("confirm-detail")).toHaveTextContent("Untitled — Notepad / 📝");
    expect(screen.getByTestId("confirm-detail")).toHaveTextContent(/Reported by the PC/);
    expect(screen.getByText(/Expires in/)).toHaveTextContent("50s");
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    expect(onRespond).toHaveBeenCalledWith("approve");
  });

  it("only offers Decline when the challenge does not bind to the command this phone sent", async () => {
    const onRespond = vi.fn(async () => undefined);
    render(<ConfirmationModal record={record(fixture.digests.challenge_text, "The confirmation describes a different action.")} pcName={null} onRespond={onRespond} now={NOW} />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByText(/does not match what you sent/)).toBeInTheDocument();
    expect(screen.getByText("your PC")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Decline" }));
    expect(onRespond).toHaveBeenCalledWith("decline");
  });

  it("disables Approve once the challenge expired", () => {
    render(<ConfirmationModal record={record(fixture.digests.challenge_text, null)} pcName="Office PC" onRespond={async () => undefined} now={() => new Date("2026-10-08T12:02:00.000Z")} />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByText(/timed out/)).toBeInTheDocument();
  });
});

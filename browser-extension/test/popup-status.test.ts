import { describe, expect, it } from "vitest";

import { describeDetail, describeStatus } from "../src/popup/status.ts";
import type { ConnectionStateName, StatusReport } from "../src/shared/messages.ts";

function report(state: ConnectionStateName, extra: { message?: string; attached_tabs?: number } = {}): StatusReport {
  const connection: StatusReport["connection"] = { state, since: "2026-10-10T12:00:00.000Z", attempt: 0 };
  if (extra.message) connection.message = extra.message;
  return {
    connection,
    browser_instance_id: "abcdefghijklmnopqrstuv",
    profile_label: "",
    extension_id: "abcdefghijklmnopabcdefghijklmnop",
    extension_version: "0.1.0",
    browser: "edge",
    attached_tabs: extra.attached_tabs ?? 1,
  };
}

const ALL: ConnectionStateName[] = ["connecting", "connected", "disconnected", "agent_not_running", "host_missing", "host_forbidden", "incompatible", "error"];

describe("popup status copy", () => {
  it("says plainly whether it works and what to do", () => {
    expect(describeStatus(report("connected")).title).toBe("Connected");
    expect(describeStatus(report("connecting")).title).toBe("Connecting...");
    expect(describeStatus(report("agent_not_running")).title).toBe("Not connected - start DoMe on this PC");
    expect(describeStatus(report("disconnected")).title).toBe("Not connected - start DoMe on this PC");
    for (const state of ALL) {
      const copy = describeStatus(report(state));
      if (state !== "connected" && state !== "connecting") {
        expect(copy.title, state).toMatch(/^Not connected - /);
        expect(copy.hint, state).toContain("Retry connection");
      }
      // Plain words for the PC owner: no internal state names, error codes or command lines.
      expect(`${copy.title} ${copy.hint}`, state).not.toMatch(/_|dome-agent|DOME_|native host|protocol/i);
    }
  });

  it("does not promise fast retries forever: they slow down to every 30 seconds", () => {
    for (const state of ["disconnected", "agent_not_running"] as const) {
      expect(describeStatus(report(state)).hint, state).toContain("every few seconds at first and then every 30 seconds");
    }
  });

  it("maps every state to an indicator class", () => {
    expect(describeStatus(report("connected")).cls).toBe("connected");
    expect(describeStatus(report("connecting")).cls).toBe("");
    expect(describeStatus(report("agent_not_running")).cls).toBe("warn");
    expect(describeStatus(report("host_missing")).cls).toBe("error");
  });

  it("asks for a YouTube video when connected but no tab is ready", () => {
    expect(describeStatus(report("connected", { attached_tabs: 0 })).hint).toBe("Open a YouTube video in this browser to control it from your phone.");
    expect(describeStatus(report("connected", { attached_tabs: 2 })).hint).toBe("Your phone can control YouTube in this browser.");
  });

  it("labels the technical reason as details", () => {
    expect(describeDetail(report("host_missing", { message: "Specified native messaging host not found." }))).toBe("Details: Specified native messaging host not found.");
    expect(describeDetail(report("connected"))).toBe("");
  });
});

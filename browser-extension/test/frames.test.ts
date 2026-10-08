/** Every emitted frame shape validates against bridge.schema.json; generated validators agree with @dome/protocol. */
import { ProtocolError, protocolCompatible as sharedCompatible, schemas } from "@dome/protocol";
import { describe, expect, it } from "vitest";

import { errorFrame, helloFrame, playerStateEvent, responseError, responseOk, tabsChangedEvent, type YoutubeTab } from "../src/background/frames.ts";
import { OpError } from "../src/shared/errors.ts";
import { checkOutgoing, validateBridgeFrame, validateResult, validateYoutubeTab } from "../src/shared/validate.ts";
import { protocolCompatible } from "../src/shared/version.ts";

const TAB: YoutubeTab = {
  browser_instance_id: "abcdefghijklmnopqrstuv",
  tab_id: 11,
  tab_token: "AAAAAAAAAAAAAAAAAAAAAA",
  script_attached: true,
  title: "Never Gonna Give You Up",
  video_id: "dQw4w9WgXcQ",
  paused: false,
  muted: false,
  volume: 80,
  position_seconds: 10.5,
  duration_seconds: 213,
  context: "watch",
  ad_showing: false,
  is_live: false,
  in_playlist: false,
  theater: false,
  fullscreen: false,
  has_next: true,
  has_previous: false,
  active: true,
};

// eslint-disable-next-line @typescript-eslint/no-unused-vars -- drop tab_token for the unattached variant
const { tab_token: _dropped, ...UNATTACHED_BASE } = TAB;
const UNATTACHED: YoutubeTab = { ...UNATTACHED_BASE, script_attached: false };

const GOOD_OUT: unknown[] = [
  helloFrame({ browserInstanceId: "abcdefghijklmnopqrstuv", browser: "edge", extensionVersion: "0.1.0", protocolVersions: ["1.0"], profileLabel: "Work" }),
  helloFrame({ browserInstanceId: "abcdefghijklmnopqrstuv", browser: "unknown", extensionVersion: "0.1.0", protocolVersions: ["1.0"], profileLabel: "" }),
  responseOk("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", { tabs: [TAB, { ...UNATTACHED, tab_id: 12 }] }),
  responseOk("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", { tab: TAB, previous_video_id: "9bZkp7q19f0" }),
  responseOk("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", { tab: TAB }),
  responseError("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", { code: "NO_NEXT_VIDEO", message: "x".repeat(600) }),
  tabsChangedEvent([TAB], "2026-10-08T12:00:00.000Z"),
  tabsChangedEvent([], "2026-10-08T12:00:00.000Z"),
  playerStateEvent(TAB, "2026-10-08T12:00:00.000Z"),
  errorFrame("PROTOCOL_INCOMPATIBLE", "incompatible", { detail: { peer: ["2.0"], supported: ["1.0"] } }),
  errorFrame("MALFORMED_MESSAGE", "bad frame", { refRequestId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" }),
];

const BAD_OUT: unknown[] = [
  { type: "bridge_hello", browser_instance_id: "short", browser: "chrome", extension_version: "1", protocol_versions: ["1.0"] },
  { type: "bridge_hello", browser_instance_id: "abcdefghij", browser: "safari", extension_version: "1", protocol_versions: ["1.0"] },
  { type: "bridge_hello", browser_instance_id: "abcdefghij", browser: "chrome", extension_version: "1", protocol_versions: [] },
  { type: "bridge_response", request_id: "x", ok: true, result: { tab: { ...TAB, volume: 101 } } },
  { type: "bridge_response", request_id: "x", ok: true, result: { tab: { ...TAB, context: "tv" } } },
  { type: "bridge_response", request_id: "x", ok: true, result: { tab: TAB, extra: 1 } },
  { type: "bridge_response", request_id: "x", ok: false, error: { code: "lower", message: "m" } },
  { type: "bridge_event", event: "player_state", at: "t", tab: { ...TAB, tab_token: "tooshort" } },
  { type: "bridge_event", event: "tabs_changed", at: "t", tabs: Array.from({ length: 33 }, () => TAB) },
  { type: "bridge_error", error: { code: "X", message: "m" }, extra: true },
  "string",
  null,
  42,
];

const GOOD_IN: unknown[] = [
  { type: "bridge_hello_ack", protocol_version: "1.0", agent_version: "0.1.0" },
  { type: "bridge_request", request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", op: "next", args: { tab_id: 1, tab_token: "AAAAAAAAAAAAAAAAAAAAAA", expected_video_id: "abc" }, timeout_ms: 10000 },
  { type: "bridge_request", request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", op: "list_tabs", args: {} },
  { type: "bridge_error", error: { code: "AGENT_NOT_RUNNING", message: "not running" } },
];
const BAD_IN: unknown[] = [
  { type: "bridge_request", request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", op: "eval", args: {} },
  { type: "bridge_request", request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", op: "next", args: { tab_id: -1 } },
  { type: "bridge_request", request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", op: "next", args: { shell: "rm" } },
  { type: "bridge_request", request_id: "nope", op: "next", args: {} },
  { type: "bridge_request", request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", op: "next", args: {}, timeout_ms: 99 },
  { type: "bridge_hello", browser_instance_id: "abcdefghij", browser: "chrome", extension_version: "1", protocol_versions: ["1.0"] }, // wrong direction
  { type: "bridge_hello_ack", protocol_version: "1.0" },
  {},
];

describe("emitted frames", () => {
  it("every builder output validates against the shared contract", () => {
    for (const frame of GOOD_OUT) {
      schemas.validateBridgeFrame("extension_to_agent", frame);
      expect(() => checkOutgoing(frame)).not.toThrow();
    }
  });

  it("rejects an oversized frame", () => {
    const big = tabsChangedEvent(Array.from({ length: 32 }, (_, i) => ({ ...TAB, tab_id: i, title: "x".repeat(200) })));
    expect(() => checkOutgoing(big)).not.toThrow();
    const huge = { type: "bridge_error", error: { code: "X", message: "m", detail: { blob: "y".repeat(70000) } } };
    expect(() => checkOutgoing(huge)).toThrow(/PAYLOAD_TOO_LARGE|exceeds/);
  });
});

describe("generated validators agree with @dome/protocol", () => {
  it("extension_to_agent", () => {
    for (const frame of GOOD_OUT) {
      expect(() => validateBridgeFrame("extension_to_agent", frame)).not.toThrow();
      expect(() => schemas.validateBridgeFrame("extension_to_agent", frame)).not.toThrow();
    }
    for (const frame of BAD_OUT) {
      expect(() => validateBridgeFrame("extension_to_agent", frame), JSON.stringify(frame)).toThrow(OpError);
      expect(() => schemas.validateBridgeFrame("extension_to_agent", frame), JSON.stringify(frame)).toThrow(ProtocolError);
    }
  });

  it("agent_to_extension", () => {
    for (const frame of GOOD_IN) {
      expect(() => validateBridgeFrame("agent_to_extension", frame)).not.toThrow();
      expect(() => schemas.validateBridgeFrame("agent_to_extension", frame)).not.toThrow();
    }
    for (const frame of BAD_IN) {
      expect(() => validateBridgeFrame("agent_to_extension", frame), JSON.stringify(frame)).toThrow(OpError);
      expect(() => schemas.validateBridgeFrame("agent_to_extension", frame), JSON.stringify(frame)).toThrow(ProtocolError);
    }
  });

  it("youtube_tab and result defs", () => {
    validateYoutubeTab(TAB);
    schemas.validateDef("relay-frames", "youtube_tab", TAB);
    const notAttached = { browser_instance_id: "abcdefghij", tab_id: 3, script_attached: false, context: "other", ad_showing: false, is_live: false, in_playlist: false };
    validateYoutubeTab(notAttached);
    for (const bad of [{ ...TAB, tab_id: "1" }, { ...TAB, position_seconds: -1 }, { ...TAB, extra: 1 }, { ...TAB, title: "x".repeat(201) }]) {
      expect(() => validateYoutubeTab(bad)).toThrow(OpError);
      expect(() => schemas.validateDef("relay-frames", "youtube_tab", bad)).toThrow(ProtocolError);
    }
    validateResult("tabs_result", { tabs: [TAB] });
    validateResult("youtube_state_result", { tab: TAB, previous_video_id: "abc" });
    expect(() => validateResult("youtube_state_result", { tab: TAB, previous_video_id: "x".repeat(33) })).toThrow(OpError);
    expect(() => validateResult("tabs_result", { tab: TAB })).toThrow(OpError);
  });

  it("error messages name the offending path", () => {
    expect(() => validateBridgeFrame("agent_to_extension", { type: "bridge_request", request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", op: "next", args: { tab_id: -1 } })).toThrow(/required property|tab_id|oneOf/);
  });
});

describe("protocolCompatible parity with @dome/protocol", () => {
  it("agrees on every case", () => {
    const cases: Array<[unknown, string[]]> = [
      ["1.0", ["1.0"]],
      ["1.0", ["1.2"]],
      ["1.3", ["1.2"]],
      ["2.0", ["1.2"]],
      ["x", ["1.0"]],
      ["1.0\n", ["1.0"]],
      ["１.０", ["1.0"]],
      ["1.0", []],
      [10, ["1.0"]],
      ["01.0", ["1.0"]],
      ["1.0", ["1.0", "2.0"]],
      ["2.5", ["1.0", "2.7"]],
    ];
    for (const [peer, supported] of cases) {
      expect(protocolCompatible(peer, supported), `${String(peer)} vs ${supported.join(",")}`).toBe(sharedCompatible(peer as string, supported));
    }
  });
});

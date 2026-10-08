/** Builders for every frame the extension emits; all go through checkOutgoing() before posting. */
import type { bridge } from "@dome/protocol";

import type { BrowserKind } from "./api.ts";

export type BridgeHello = bridge.BridgeHello;
export type BridgeHelloAck = bridge.BridgeHelloAck;
export type BridgeRequest = bridge.BridgeRequest;
export type BridgeResponse = bridge.BridgeResponse;
export type BridgeEvent = bridge.BridgeEvent;
export type BridgeError = bridge.BridgeError;
export type AgentToExtension = bridge.AgentToExtension;
export type ExtensionToAgent = bridge.ExtensionToAgent;
export type YoutubeTab = bridge.YoutubeTab;
export type TabsResult = bridge.TabsResult;
export type YoutubeStateResult = bridge.YoutubeStateResult;

export function helloFrame(input: { browserInstanceId: string; browser: BrowserKind; extensionVersion: string; protocolVersions: readonly string[]; profileLabel: string }): BridgeHello {
  const frame: BridgeHello = {
    type: "bridge_hello",
    browser_instance_id: input.browserInstanceId,
    browser: input.browser,
    extension_version: input.extensionVersion.slice(0, 32),
    protocol_versions: [input.protocolVersions[0] ?? "1.0", ...input.protocolVersions.slice(1)],
  };
  if (input.profileLabel) frame.profile_label = input.profileLabel.slice(0, 64);
  return frame;
}

export function responseOk(requestId: string, result: TabsResult | YoutubeStateResult): BridgeResponse {
  return { type: "bridge_response", request_id: requestId, ok: true, result };
}

export function responseError(requestId: string, error: { code: string; message: string }): BridgeResponse {
  return { type: "bridge_response", request_id: requestId, ok: false, error: { code: error.code, message: error.message.slice(0, 512) } };
}

export function tabsChangedEvent(tabs: YoutubeTab[], at: string = new Date().toISOString()): BridgeEvent {
  return { type: "bridge_event", event: "tabs_changed", at, tabs: tabs.slice(0, 32) };
}

export function playerStateEvent(tab: YoutubeTab, at: string = new Date().toISOString()): BridgeEvent {
  return { type: "bridge_event", event: "player_state", at, tab };
}

export function errorFrame(code: string, message: string, options: { refRequestId?: string; detail?: Record<string, unknown> } = {}): BridgeError {
  const frame: BridgeError = { type: "bridge_error", error: { code, message: message.slice(0, 512) } };
  if (options.detail) frame.error.detail = options.detail;
  if (options.refRequestId) frame.ref_request_id = options.refRequestId;
  return frame;
}

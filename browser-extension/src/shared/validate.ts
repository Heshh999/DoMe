/**
 * Contract validation at the extension's trust boundaries, using validators precompiled from
 * shared/protocol/schemas (see scripts/gen-validators.ts). API shape mirrors
 * `schemas.validateBridgeFrame` in @dome/protocol.
 */
import { agentToExtension, extensionToAgent, tabsResult, youtubeSearchResult, youtubeStateResult, youtubeTab, type Validator } from "../generated/validators.js";
import { OpError } from "./errors.ts";

export type BridgeDirection = "extension_to_agent" | "agent_to_extension";

/** version.json → limits.max_frame_bytes */
export const MAX_FRAME_BYTES = 65536;

const encoder = new TextEncoder();

function fail(v: Validator, prefix: string): never {
  const err = v.errors?.[0];
  const path = err?.instancePath?.replace(/^\//, "") || "<root>";
  throw new OpError("MALFORMED_MESSAGE", `${prefix}${path}: ${err?.message ?? "invalid"}`.slice(0, 300));
}

export function validateBridgeFrame(direction: BridgeDirection, value: unknown): void {
  const v = direction === "extension_to_agent" ? extensionToAgent : agentToExtension;
  if (!v(value)) fail(v, "");
}

export function validateYoutubeTab(value: unknown): void {
  if (!youtubeTab(value)) fail(youtubeTab, "youtube_tab: ");
}

const RESULT_VALIDATORS = { tabs_result: tabsResult, youtube_state_result: youtubeStateResult, youtube_search_result: youtubeSearchResult } as const;

export function validateResult(kind: keyof typeof RESULT_VALIDATORS, value: unknown): void {
  const v = RESULT_VALIDATORS[kind];
  if (!v(value)) fail(v, "result: ");
}

export function frameByteLength(frame: unknown): number {
  return encoder.encode(JSON.stringify(frame)).length;
}

/** Validate an outgoing frame against the contract and the frame size limit. */
export function checkOutgoing(frame: unknown): void {
  validateBridgeFrame("extension_to_agent", frame);
  const size = frameByteLength(frame);
  if (size > MAX_FRAME_BYTES) throw new OpError("PAYLOAD_TOO_LARGE", `bridge frame of ${size} bytes exceeds ${MAX_FRAME_BYTES}`);
}

export { ProtocolError } from "./errors.ts";
export { loadsStrict, dumpsCompact, utf8ByteLength, DEFAULT_MAX_BYTES, DEFAULT_MAX_DEPTH, type JsonValue, type StrictJsonOptions } from "./strictJson.ts";
export {
  b64url,
  unb64url,
  generateControllerKeyPair,
  exportPublicJwk,
  importPublicJwk,
  assertEcPublicJwk,
  kidFromJwk,
  ECDSA_P256,
  ES256_SIGN,
  type EcPublicJwk,
} from "./keys.ts";
export { signPayload, verifyEnvelope, verifySignature, parseEnvelopeShape, type Envelope, type JwkResolver, type VerifyOptions } from "./signing.ts";
export {
  sha256B64url,
  challengeDigest,
  commandDigest,
  generatePairingCode,
  normalizePairingCode,
  formatPairingCode,
  pairingCodeHandle,
  pairingVerificationCode,
  randomNonce,
  randomUuid,
  PAIRING_ALPHABET,
  PAIRING_CODE_SYMBOLS,
} from "./digest.ts";
export { formatRfc3339, parseRfc3339, checkCommandWindow, type WindowOptions } from "./time.ts";
export {
  registry,
  schemas,
  protocolCompatible,
  PROTOCOL_VERSION,
  REGISTRY_VERSION,
  LIMITS,
  PLANS,
  ERRORS,
  CAPABILITIES,
  type ActionSpec,
  type ActionName,
  type Capability,
  type ErrorCode,
  type PlanId,
  type Risk,
  type Confirmation,
  type FrameDirection,
  type BridgeDirection,
} from "./registry.ts";
export {
  buildCommandPayload,
  signCommand,
  buildConfirmationPayload,
  signConfirmation,
  verifyAndParseCommand,
  verifyAndParseConfirmation,
  type KeyRecord,
  type KeyResolver,
  type VerifiedConfirmation,
  type CommandPayload,
  type ConfirmationPayload,
  type BuildCommandInput,
  type BuildConfirmationInput,
  type VerifiedCommand,
} from "./commands.ts";
export type * from "./generated/index.ts";

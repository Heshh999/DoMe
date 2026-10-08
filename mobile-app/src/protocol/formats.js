// Formats referenced by the precompiled validators (`code.formats` in scripts/gen-validators.ts).
// The only `format` the contract uses is `uri`; this is the same regex as shared/ts/src/registry.ts.
export default {
  uri: /^(?:https?|wss?):\/\/[^\s/?#]+[^\s]*$/,
};

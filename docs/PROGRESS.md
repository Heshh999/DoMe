# Progress log

Newest first. Each entry: what changed, what was actually run, evidence tag, what is next.

## 2026-10-08 — Contract review and hardening (before component builds)

- Four independent adversarial reviews of the contract (security/crypto, cross-language
  encoding + state machine, product fit, implementability) produced 38 findings; skeptics
  confirmed the blockers/majors against the committed state. All substantive findings were
  applied; refuted/duplicate ones were dropped. Highlights:
  - Confirmation challenge now travels as an opaque `challenge_text` string hashed verbatim
    (the nested-object form would have broken every confirmation across Python↔TypeScript).
  - `grants_snapshot` is an intersection/revocation list carrying no keys; the PC's locally
    approved store is the only key source. Plan-disabled devices are a separate state.
  - Verifiers bind `controller_id`/`account_id` to the signing key (`CONTROLLER_MISMATCH`).
  - Pairing code is generated on the PC (20 Crockford symbols); the backend sees only its
    SHA-256 handle; the 6-digit verification code is an HMAC keyed by the code, so a key-substituting
    backend cannot make both screens agree.
  - Controller `hello` carries `kid`; per-action `result` schemas; REST body schemas;
    entitlement claim schema; `tab_token` binds YouTube targets; richer `youtube_tab`;
    normative behavioural rules collected in `version.json → rules`.
  - Python `pattern` validation now uses ECMA-262 semantics (ASCII classes, true end
    anchoring); both parsers reject unsafe integers and prototype-pollution keys; a shared
    `strict-json-cases.json` fixture is run by both suites.
- Tests run: `shared/python` 128 passed; `shared/ts` 82 passed; fixtures cross-verified in both
  directions. **Evidence tag: unit-tested.**
- `tools/dev-idp` (development OIDC issuer) smoke-tested: discovery, PKCE authorize, token,
  replay rejection, userinfo.
- Next: component builds (cloud-api, pc-agent, browser-extension, mobile-app), then docs,
  then the end-to-end relay test and a security review of the implementations.

## 2026-10-08 — Phase A foundation

- Repository skeleton, ADR-0001 (auth = OIDC/PKCE via Authlib; PC linking = device-code shape;
  controller keys = non-extractable WebCrypto ECDSA P-256; envelopes = detached ES256 over exact
  bytes; hosting target = Fly.io; single FastAPI process + PostgreSQL).
- `shared/protocol/`: action registry (29 actions, 6 capabilities), plans, error codes, JSON
  Schemas for envelope / command / confirmation / relay frames / bridge frames, cross-language
  fixtures.
- `shared/python` (`dome-protocol`) and `shared/ts` (`@dome/protocol`): strict JSON, keys, signing,
  registry/frame validation, command build/verify.
- Tests run: `shared/python` 65 passed; `shared/ts` 23 passed. Each language verifies the other
  language's signed fixtures. **Evidence tag: unit-tested.**
- Next: adversarial review of the contract, then cloud-api / pc-agent / browser-extension /
  mobile-app builds and the end-to-end relay test.

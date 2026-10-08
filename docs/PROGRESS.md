# Progress log

Newest first. Each entry: what changed, what was actually run, evidence tag, what is next.

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

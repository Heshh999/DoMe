# cloud-api — contract observations

The contract under `shared/protocol/` is frozen for this component; nothing there was changed. These are
the gaps found while implementing, with the workaround used. Each proposes an additive change for the
next protocol MINOR bump.

1. **No `rest.schema.json` definition for `GET /v1/account/sessions`.** The design requires the endpoint
   (session list/revoke) but the contract has no body for it. Workaround: the endpoint returns
   `{"sessions":[{"id","created_at","last_seen_at","expires_at","current"}]}` and the API does **not**
   self-validate that body (every other body is validated). Proposed def: `sessions_response` with exactly
   that shape (`additionalProperties:false`).
2. **No definition for `GET /healthz`.** Operational endpoint outside `/v1`; returns
   `{"status","version","database","relay":{"agents","controllers","max_connections"}}`. Proposed:
   either exclude health from the "every REST response" rule explicitly or add `health_response`.
3. **No command-scoped non-terminal rejection for `confirmation` frames.** `rules.terminal_result` says
   `error` frames are only for situations without a `command_id`, yet a confirmation that fails relay
   checks (kid mismatch, not pending, bad signature) has one, and answering with a `result` would
   wrongly terminate a command the PC still owns. Workaround: `error{code, ref_pc_id}` without the
   command id. Proposed: add an optional `ref_command_id` to `error_frame`.
4. **REST-only HTTP conditions have no codes in `errors.json`.** `UNAUTHENTICATED`, `FORBIDDEN`,
   `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `LINK_DENIED`, `LINK_EXPIRED`, `PC_ALREADY_LINKED`, `SERVICE_UNAVAILABLE` are used in
   `error_body` responses only (they match the `^[A-Z_]+$` pattern and never appear in relay frames).
   Proposed: add them to `errors.json` so clients can map them centrally.
5. **`agent_link_poll_response.pc_credential` / `agent_token_response.access_token` lengths.** Fixed at
   43 chars (base64url of 32 bytes) — fine today; noting that any future token format change is a
   contract change, not a backend-only one.
6. **`pairing_status_response.granted_capabilities`** is optional with no statement of *when* it is
   present. Implemented: present iff `state == "approved"`; `controller_id`/`grant_id` likewise.
7. **`grants_snapshot.display_name`** has `maxLength: 64` but `pairing_claim_request.display_name` is
   also ≤ 64, so no truncation occurs; the API truncates defensively anyway.
8. **`pc.last_power_request` semantics** ("recently requested power action") are not defined; set at
   forward time of a confirmed `power.*` command (see `DECISIONS.md` #12).
9. **`EdDSA` is flagged "deprecated via RFC 9864" by joserfc 1.7.** The contract
   (`plans.json → entitlement_assertion.algorithm`, `entitlement.schema.json`) mandates the JWS `alg`
   value `EdDSA`; RFC 9864 introduces the fully-specified `Ed25519` algorithm name instead. joserfc still
   signs and verifies `EdDSA` when it is explicitly allowed (`algorithms=["EdDSA"]`, which the API passes)
   but emits a `SecurityWarning`. The key type/curve (Ed25519) and security are unchanged. Proposed: on the
   next MINOR bump change the algorithm identifier to `Ed25519` (agents verify by JWKS `kid`, so the
   switch is coordinated through `plans.json`).
10. **`agent_link_start_request` has no proof of possession of the PC key.** The body is
    `{pc_public_jwk, agent_version, platform, pc_name_hint?}` with `additionalProperties:false`, so the
    backend cannot verify that the caller holds the private key whose public half it registers; anyone
    who learns a PC's public JWK can start a link code for it. Workaround (DECISIONS #2): a code naming
    the key of a PC that is still linked is refused at approval (409 `PC_ALREADY_LINKED`) and silent
    re-link is limited to soft-deleted PCs, where no credential or grant can be inherited. Proposed for
    the next MINOR: an optional `proof` object on the start request — the agent signs
    `"dome-link|" + kid + "|" + <server nonce or RFC 3339 minute>` with its ES256 key (same envelope
    encoding as commands) — which the backend verifies with `dome_protocol.signing` before storing the
    row; once every shipped agent sends it, make it required.

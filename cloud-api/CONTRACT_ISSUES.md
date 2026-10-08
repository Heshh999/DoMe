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
   `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `LINK_DENIED`, `LINK_EXPIRED`, `SERVICE_UNAVAILABLE` are used in
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

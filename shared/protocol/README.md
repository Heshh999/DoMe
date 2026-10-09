# DoMe shared protocol contract

This directory is the **single source of truth** for everything that crosses a trust boundary
between the phone PWA, the cloud backend/relay, the Windows agent and the browser extension.

| File | Purpose |
| --- | --- |
| `version.json` | Current protocol version and the compatibility negotiation rule |
| `actions.json` | The action registry: every executable action, its parameter schema, required capability, risk/confirmation policy, timeout, availability and verification strategy |
| `plans.json` | Central Free/Pro plan definitions and limits (no plan-name checks anywhere else) |
| `errors.json` | Stable error codes with default retryability |
| `schemas/envelope.schema.json` | Signed envelope (`v`, `alg`, `kid`, `payload`, `sig`) |
| `schemas/command.schema.json` | The signed `command` payload |
| `schemas/confirmation.schema.json` | The signed `confirmation` payload |
| `schemas/relay-frames.schema.json` | WebSocket frames on `/ws/controller` and `/ws/agent` |
| `schemas/bridge.schema.json` | Native-messaging frames between the extension and the agent's native host (+ IPC identity rule) |
| `schemas/results.schema.json` | Per-action result shapes (named by `actions.json[action].result`) |
| `schemas/entitlement.schema.json` | Claims of the EdDSA entitlement assertion and its refresh rule |
| `schemas/rest.schema.json` | REST request/response bodies that cross component boundaries |
| `version.json` → `rules` | Normative behavioural rules (terminal result, duplicates, snapshot semantics, pairing, identity binding, caching, in-flight deadlines, coalescing) |
| `fixtures/` | Cross-language signing fixtures verified by both the Python and TypeScript implementations |

Rules:

1. Changing any schema requires bumping `version.json` according to the rule inside it and
   updating `docs/PROTOCOL.md`.
2. Verifiers reject unknown `alg`, unknown top-level fields, duplicate keys, oversized or
   deeply nested payloads, and anything failing schema validation. Nothing is silently
   reinterpreted.
3. Parameters are validated **by the PC** against `actions.json` regardless of what the backend
   or phone already checked.

## Protocol 1.1 — manual touchpad and keyboard (spec §10A)

Additive. 1.0 peers remain compatible (MINOR rule) and never receive the new frames.

| Addition | Where |
| --- | --- |
| Capabilities `pointer` and `keyboard` (Free; independently grantable; absent from existing grants until the PC owner adds them) | `actions.json → capabilities`, every capability enum in the schemas |
| Actions `input.session_start` (either capability, `alternate_capabilities`) and `input.session_stop`; `ai_eligible: false` marks human-only actions | `actions.json`, `results.schema.json → input_session_result / input_session_stop_result` |
| Signed input stream: `input_batch_payload` (≤ 64 `input_event`s, `seq`, 5 s window) inside `controller_input_batch` → `relay_to_agent_input_batch`; `agent_input_ack` (≤ 4/s, Windows acceptance only); `agent_input_session` (started/suspended/ended with reason) | `relay-frames.schema.json` |
| `agent_grant_update` — the PC owner changed a controller's capabilities locally; the relay replaces the grant's list | `relay-frames.schema.json`, `rules.grant_update` |
| `pc_state.foreground_app`, `pc_state.input_session`, `pc_state.input_restricted` | `relay-frames.schema.json` |
| Limits `input_batches_per_second`, `input_batch_max_events`, `input_batch_lifetime_seconds`, `input_age_budget_ms`, `input_lease_seconds`, `input_text_max_chars`, `input_motion_max`; plan `input_rate_limit` (identical for Free and Pro) | `version.json`, `plans.json` |
| Errors `INPUT_*` | `errors.json` |
| Rules `input_sessions`, `grant_update`, `ai_eligibility` | `version.json → rules` |
| Support tickets: `support_ticket_request`, `support_ticket(_response)`, `support_tickets_response` | `rest.schema.json` |

Library helpers: Python `build_input_batch_payload`, `verify_and_parse_input_batch`,
`input_event_capabilities`, `ActionSpec.satisfied_by`; TypeScript `buildInputBatchPayload`,
`signInputBatch`, `inputEventCapabilities`, `capabilitySatisfied`. Design brief:
`docs/design/input-control.md`.

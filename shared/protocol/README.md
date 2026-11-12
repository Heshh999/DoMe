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
| `fixtures/` | Cross-language signing fixtures verified by both the Python and TypeScript implementations (`es256-*.json`, signed with the committed TEST keys `test-controller-key.pem` and, since 1.4, `test-pc-key.pem`: commands, confirmations, the PC-signed screen answer and the pairing `pc_key_mac`); `strict-json-cases.json` (parser, pattern, timestamp, negotiation and SDP-shape decisions); `assistant-plan-cases.json` (AI-shaped plans and their structure verdict, run by cloud-api and the phone); `ai-window-cases.json`; since 1.5 `screen-dc-cases.json` (data-channel messages both parsers must accept or refuse, written byte for byte by both fixture generators) |

Rules:

1. Changing any schema requires bumping `version.json` according to the rule inside it and
   updating `docs/PROTOCOL.md`. Exception: a new REST endpoint whose bodies travel only between the
   PWA and cloud-api (deployed together), and error codes only such endpoints return, need no bump;
   they are listed with their date in `docs/PROTOCOL.md` §19.

   Stamping (`rules.command_version_stamping`): a signed payload carries the lowest version that
   defines it, not the sender's newest. Since 1.5, stamping is content-based for commands with
   `param_since` params, as for input batches: `screen.session_start` is stamped 1.4 without
   `cursor` and 1.5 with it.
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

## Protocol 1.2 — window actions, YouTube search, per-action versions

Additive. 1.1 agents and phones stay compatible with a 1.2 relay, and an updated phone keeps
working with a 1.1 PC for every action that PC knows. Deploy the cloud (relay + PWA) first, then
the agent, then the extension. Explained in `docs/PROTOCOL.md` §18.

| Addition | Where |
| --- | --- |
| Optional `since` on every action (absent = 1.0; `input.*` = 1.1; the new actions = 1.2) | `actions.json` |
| Rule `command_version_stamping`: a command carries its action's `since`, a confirmation the confirmed command's version, an input batch 1.1; verifiers refuse a stamp below `since` with `PROTOCOL_INCOMPATIBLE` (`detail{peer, required}`) | `version.json → rules` |
| Rule `bridge_negotiation`: `bridge_hello_ack.protocol_version` is the highest MINOR both sides speak; later-MINOR ops go only to an extension that negotiated them, else `EXTENSION_UPDATE_REQUIRED` | `version.json → rules`, `bridge.schema.json` |
| Actions `app.maximize`, `app.restore`, `app.set_fullscreen{enabled}` (capability `apps`, `window_target`, never start the app, F11 never replaced by a maximise) | `actions.json`, `results.schema.json → app_window_state_result`, `rules.window_actions` |
| Actions `youtube.search{query}` (target `youtube_browser_target`: a browser, optionally one tab) and `youtube.open_result{index, video_id?}` | `actions.json`, `results.schema.json → youtube_search_item / youtube_search_result`, `rules.search_queries` |
| Target schema `youtube_browser_target` (`browser_instance_id`; `tab_id` and `tab_token` only together) | `actions.json → target_schemas` |
| Verification strategies `fullscreen_observed`, `observe_search_results` | `actions.json` |
| Bridge ops `search` (→ `youtube_search_result`) and `open_result`; args `query`, `index`, `video_id` | `bridge.schema.json` |
| Errors `WINDOW_STATE_UNCHANGED`, `FULLSCREEN_UNSUPPORTED`, `APP_ELEVATED`, `SEARCH_RESULT_UNAVAILABLE`, `EXTENSION_UPDATE_REQUIRED` | `errors.json` |
| Limits `youtube_search_query_max_chars` (100), `youtube_search_results_max` (10) | `version.json` |
| `negotiation_cases` and the search-query pattern cases | `fixtures/strict-json-cases.json` |

No existing definition changed: `app_window`, `youtube_tab` and every other 1.1 shape are as
before, so a 1.1 phone still validates a 1.2 agent's `app.list` and YouTube results. Captions and
playback speed are deferred because they would need new `youtube_tab` fields.

Library helpers: Python `ActionSpec.since`, `Registry.min_version`, `negotiate_version`,
`INPUT_BATCH_PROTOCOL_VERSION`; TypeScript `ActionSpec.since`, `minVersion`, and in
`src/versions.ts` (no Ajv, so the PWA's no-eval registry facade does not mirror it)
`negotiateVersion`, `versionBelow`, `parseVersion`, `INPUT_BATCH_PROTOCOL_VERSION`. The command
builder (`buildCommandPayload`) stamps `since`; `buildConfirmationPayload` requires
`commandProtocolVersion`. Validation error messages name the path and the failed keyword, never
the offending value.

## Protocol 1.3 — plans and billing (2026-10-20)

Additive MINOR: three plans (Free, Pro, Pro Plus), Stripe billing bodies for the PWA, and AI allowances measured in a
5-hour and a weekly window instead of a calendar month. No frame, signed payload, action, result or bridge op changed,
so 1.2 agents and extensions are unaffected: they announce ≤ 1.2, the entitlement assertion's `plan` claim stays `pro`
for every paid plan (`entitlement.schema.json` is unchanged) and cloud-api answers `plan: "pro"` in
`agent_entitlement_response` for Pro Plus too. Deploy the cloud (relay + PWA) first. Explained in `docs/BILLING.md`.

| Addition or change | Where |
| --- | --- |
| `protocol_version` and `registry_version` 1.3 (no action changes) | `version.json`, `actions.json` |
| Plan `pro_plus`; per plan `ai_taste_requests`, `ai_window{hours, requests}`, `ai_week{days, requests}`, `ai_topup_allowed`, `ai_cost_cap_cents_per_week` (replacing `ai_interpretations_per_period` and `ai_cost_cap_cents_per_period`); `pricing_defaults{currency, plans{pro, pro_plus}, topup}` is the single source of the displayed prices | `plans.json` |
| Rule `ai_allowance`: one AI request is one charged turn; windows start at the first request while none is open; window, then week, then top-up balance; `AI_ALLOWANCE_EXHAUSTED{reason, resets_at}` | `version.json → rules` |
| `plan_id` gains `pro_plus`; new `paid_plan_id`, `billing_interval`, `billing_mode`, `price_pair`, `pricing`, `allowance_bucket`, `subscription_state` | `schemas/rest.schema.json` |
| Changed bodies: `plans_response` (`pricing`, `billing_enabled`, `billing_mode`, `ai_enabled`: whether the AI assistant can be used, so the website labels the AI allowances honestly), `assistant_allowance` (kind `none`/`taste`/`windows`, `taste`, `window`, `week`, `topup`, `exhausted`, `resets_at`, `warn_at_percent` 80), `assistant_turn_response.charged_from` | `schemas/rest.schema.json` |
| New PWA bodies: `billing_checkout_request`, `billing_checkout_response`, `billing_portal_response`, `billing_webhook_response`, `billing_status_response` (with `checkout_plans`: the paid plans buyable now; no `pro_plus` while the assistant is off), `plan_selection_request`, `plan_selection_response` | `schemas/rest.schema.json` |
| Errors `BILLING_NOT_AVAILABLE`, `BILLING_PROVIDER_ERROR`, `BILLING_WEBHOOK_INVALID`, `ALREADY_SUBSCRIBED`, `CHECKOUT_PROCESSING` (a page is being created, or was paid and is being confirmed: never a second page), `PLAN_NOT_AVAILABLE` (Pro Plus while the assistant is off), `BILLING_NO_CUSTOMER`, `TOPUP_NOT_ALLOWED`, `TOPUP_BALANCE_LIMIT`, `PLAN_SELECTION_CLOSED`, `PLAN_SELECTION_INVALID` (REST only); `AI_ALLOWANCE_EXHAUSTED` reworded (no "month") | `errors.json` |
| Allowance arithmetic and refresh wording cases shared by cloud-api and the phone | `fixtures/ai-window-cases.json` |

Library helpers: no helper changes. `PlanId` in `shared/ts` admits `pro_plus` through the regenerated
`src/generated/rest.ts`; `Registry.plan()` in `shared/python` reads the new plan as any other.

## Protocol 1.4 — Screen view, Phase 1 (2026-10-21)

Additive MINOR for roadmap #52 (`docs/design/screen-view.md` §11, ADR-0002, `docs/PROTOCOL.md` §20). The picture travels
WebRTC peer to peer between the PC and the phone; the relay carries only signed signaling frames. 1.2 agents and 1.3
phones stay compatible: they never receive a `screen_*` frame, and the relay refuses `screen.*` commands and 1.4-stamped
input batches to an agent that did not announce 1.4 **before forwarding** (`PROTOCOL_INCOMPATIBLE detail{peer: "agent",
required: "1.4"}`), because the shipped agent validates a batch's schema before its stamp and would answer
`MALFORMED_MESSAGE`. Deploy the cloud (relay + PWA) first, then the agent; the browser extension is untouched (its
generated validators did not change).

| Addition or change | Where |
| --- | --- |
| `protocol_version` and `registry_version` 1.4 | `version.json`, `actions.json` |
| Capabilities `screen` and `screen_audio` (granted only in a DoMe window on the PC; `screen_audio` only with `screen`) in `actions.json → capabilities` and every capability enum (`grants_snapshot`, `pairing_request`, `pairing_decision`, `agent_grant_update`, `rest.schema.json → capability`) | `actions.json`, `relay-frames.schema.json`, `rest.schema.json` |
| Actions `screen.session_start{offer_sdp, surface?, audio?, takeover?}` (`since` 1.4, capability `screen`, risk `moderate`, no confirmation, `timeout_ms` 45000, availability `windows` + `session_unlocked`, `ai_eligible: false`, `routine_allowed: false`) and `screen.session_stop{screen_session_id}` (idempotent) | `actions.json`; results `screen_session_result`, `screen_session_stop_result` (+ `screen_surface`) in `results.schema.json` |
| Frames `controller_screen_ice_request` → `relay_screen_ice_servers`; `agent_screen_answer` (envelope + `ref_controller_id`) → `relay_screen_answer` (`pc_id` + envelope); `agent_screen_session` (events `started`/`paused`/`resumed`/`ended`, reasons incl. `controller_offline`, `path`, `live_seconds`; also the relay → controller form); `agent_screen_usage` → `relay_screen_allowance` (`granted_until`, `unlimited`, `final`, `reason?`, `resets_at?`); optional `relay.screen{granted_until, unlimited, stun_urls, relayed_max_kbps?}` on `relay_to_agent_command` | `relay-frames.schema.json` (all four direction `oneOf` lists) |
| Defs `screen_answer_payload` (PC-signed, `offer_digest`, ≤ 12 KiB `answer_sdp`), `screen_dc_message` = `screen_dc_phone_to_pc` (`hb`, `keyframe`, `bye`, `audio{on}`) ∪ `screen_dc_pc_to_phone` (`geometry`, `state`, `allowance`, `restricted`), `screen_allowance_state`, `screen_ice_server`, `screen_rect`, `screen_session_id` | `relay-frames.schema.json` |
| `input_event` gains `pointer_move_to{x, y, gen}` (0..65535, needs a live screen session of the same controller), `pointer_wheel{dx, dy}` (±`input_wheel_max`) and `key: f11`; `pairing_decision.pc_key_mac`; `error_frame` `ref_*` descriptions cover `SCREEN_*` rejections | `relay-frames.schema.json` |
| `pairing_status_response.pc_key{public_jwk, mac}` (approved pairings only) | `rest.schema.json` |
| Limits `screen_sdp_max_bytes` 12288, `screen_start_command_lifetime_seconds` 60, `screen_answer_lifetime_seconds` 60, `screen_heartbeat_seconds` 2, `screen_lease_seconds` 8, `screen_connect_timeout_seconds` 20, `screen_allowance_chunk_seconds` 60, `screen_allowance_renew_before_seconds` 20, `screen_session_max_seconds` 14400, `screen_consent_timeout_seconds` 30, `screen_pc_stop_cooldown_default_seconds` 600, `screen_secure_desktop_pause_max_seconds` 120, `screen_dc_message_max_bytes` 1024, `screen_geometry_generations_kept` 4, `input_wheel_max` 1200 | `version.json → limits` |
| Rules `screen_sessions`, `screen_signaling`, `pc_key_pinning`, `screen_metering`, `screen_content`, `local_permission_ui`; amended `command_version_stamping` (content-based batch stamp; version checks BEFORE schema validation; the relay shields pre-1.4 agents), `grant_update`, `ai_eligibility`, `pairing_secret`, `input_sessions`, `controller_socket_identity` | `version.json → rules` |
| Errors `SCREEN_SESSION_OWNED`, `SCREEN_SESSION_REQUIRED`, `SCREEN_ALLOWANCE_EXHAUSTED` (`detail.resets_at`), `SCREEN_OFFER_INVALID`, `SCREEN_CAPTURE_UNAVAILABLE` (`detail: indicator \| busy \| unsupported_display \| driver \| remote_session`), `SCREEN_ENCODER_UNAVAILABLE`, `SCREEN_CONNECTION_FAILED`, `SCREEN_CONSENT_DECLINED`, `SCREEN_CONSENT_TIMEOUT`, `SCREEN_GEOMETRY_CHANGED`, `SCREEN_VIEW_DISABLED`, `INPUT_PAUSED_ON_PC`, `INPUT_TARGET_RESERVED`; phone-side only `SCREEN_PC_UNVERIFIED`, `SCREEN_ANSWER_INVALID` | `errors.json` |
| Plan fields `screen_view_seconds_per_window` (free 600, pro `null`, pro_plus `null`), `screen_view_window_hours` 5, `screen_ice_rate_limit{per_minute 6, burst 6, account_per_hour 60}`, `screen_start_rate_limit{per_minute 6, burst 6}`; read by cloud-api only, the entitlement assertion is unchanged | `plans.json` |
| Fixtures: `screen` section in both `es256-*.json` (a PC-signed answer the phone accepts, one altered after signing, one signed by an unpinned key, one stamped 1.5; `pc_key_mac` vector; `test-pc-key.pem`); `sdp_cases` | `fixtures/` |

Library helpers: Python `build_screen_answer_payload`, `verify_screen_answer_payload` (→ `VerifiedScreenAnswer`),
`screen_sdp_acceptable`, `sdp_fingerprints`, `offer_digest`, `parse_screen_dc_message`, `pc_key_mac`,
`verify_pc_key_mac`, `input_event_since`, `input_batch_protocol_version`, `SCREEN_INPUT_PROTOCOL_VERSION`,
`VerifiedInputBatch.required_protocol_version`, `Schemas.validate_screen_answer_payload`,
`Schemas.validate_screen_dc_message(direction, …)`; `input_event_capabilities` knows the new events and
`build_input_batch_payload` stamps by content. TypeScript mirrors in `screen.ts` (`buildScreenAnswerPayload`,
`signScreenAnswer`, `verifyScreenAnswer`, `screenSdpAcceptable`, `sdpFingerprints`, `offerDigest`,
`parseScreenDcMessage`), `digest.ts` (`pcKeyMac`, `verifyPcKeyMac`), `versions.ts` (`inputEventSince`,
`inputBatchProtocolVersion`, `SCREEN_INPUT_PROTOCOL_VERSION`; Ajv-free), `commands.ts` (new `InputEvent` members,
`verifyAndParseInputBatch`) and `schemas.validateScreenAnswerPayload` / `validateScreenDcMessage`. **Check order:**
every verifier in both libraries now checks protocol compatibility and `since` before schema validation, so a payload
from a newer MINOR yields `PROTOCOL_INCOMPATIBLE` instead of `MALFORMED_MESSAGE`. Design brief:
`docs/design/screen-view.md`; transport decision: `docs/adr/0002-screen-view-transport.md`.

## Protocol 1.5 — Screen view, Phase 2a (2026-11-09)

Additive MINOR for four Screen view additions (`docs/design/screen-view-2a.md`, `docs/PROTOCOL.md` §21): Next screen,
the pointer drawn by the phone, pinch zoom in Touchpad mode and hardware H.264. Only the first two touch the wire.
A 1.4 agent keeps every 1.4 behaviour, and Next screen works with it too (it is `surface: N`, which 1.4 defined), except
that in ask mode or while the post-Stop cooldown runs a 1.4 agent asks again for a switch (a new consent window, or
`SCREEN_CONSENT_DECLINED` without one when a window opened less than a minute ago); it checks consent before it ends the
old session, so after a refusal the phone goes back to the old session. On the relay and the data channels a 1.4 phone
never receives a 1.5 field or message, because `result.cursor`, `state.cursor`, `cursor_pos` and `cursor_shape` are
sent only in sessions started with `cursor`; the REST pc row is the documented exception (`docs/PROTOCOL.md` §21.9).
The relay refuses any command whose stamp the target agent did not announce **before forwarding**
(`PROTOCOL_INCOMPATIBLE detail{peer: "agent", required}`), so a 1.5-stamped start never reaches a 1.4 agent (which
would refuse it itself, with a detail that has no `required`, after the relay had spent a start token). The PC-signed answer carries the stamp of the
start it answers. Deploy the cloud (relay + PWA) first, then the agents; an open PWA from before the deploy rejects the
new `pc.protocol_versions` field in `GET /v1/pcs` and `PATCH /v1/pcs/{id}` until it reloads (a rename or a switch on or
off of a PC from it is applied but reported as failed). The browser extension is untouched.

| Addition or change | Where |
| --- | --- |
| `protocol_version` and `registry_version` 1.5 | `version.json`, `actions.json` |
| `screen.session_start.cursor: drawn \| overlay` (optional, no default: a start without it is a 1.4 start and the PC draws the pointer) and the top-level action key `param_since` (`{"cursor": "1.5"}`, a sibling of `since`; documented in the file's `$comment`) | `actions.json` |
| `screen_session_result.cursor: drawn \| overlay`, present only when the start carried `cursor` | `results.schema.json` |
| Data-channel messages PC → phone `cursor_pos{gen, x, y, visible}`, `cursor_shape{kind}` (14 named kinds) and `cursor_shape{kind: custom, width, height, hotspot_x, hotspot_y, png}` as the named defs `screen_dc_cursor_pos`, `screen_dc_cursor_shape_named`, `screen_dc_cursor_shape_custom` in `screen_dc_pc_to_phone`; `state.cursor: drawn \| overlay`; the second channel `dome-screen-cursor-v1` (unordered, no retransmissions, `cursor_pos` only) in the `screen_dc_message` description | `relay-frames.schema.json` |
| Limit `screen_dc_cursor_shape_max_bytes` 12288 (`screen_dc_message_max_bytes` stays 1024 for every other message) | `version.json → limits` |
| `pc.protocol_versions` (at most 8 `MAJOR.MINOR` strings; optional in the schema, always sent by the server) | `rest.schema.json` |
| Rules: amended `command_version_stamping` (content-based command stamp, the post-schema check, the relay's gate for every command), `screen_signaling` (2) the second channel and (5) the answer stamp, `screen_sessions` (1b) surface switch and the end reasons in (6), `screen_content` (the vocabulary and the size exception), `screen_metering` (3) a switch is a new session; new `screen_cursor` | `version.json → rules` |
| `PROTOCOL_INCOMPATIBLE` gains a `description` naming the relay's gate (no new code) | `errors.json` |
| Fixtures: the accepted screen answer is stamped 1.5 and the refused newer-MINOR case is computed by both generators (one MINOR above the library's version, 1.6 now) and signed with `test-pc-key.pem`; new `screen-dc-cases.json` (`{$comment, accepted[], rejected[]}`, each case `{name, direction, text}`), written byte for byte by both generators and run by both libraries | `fixtures/` |

Library helpers: Python `command_protocol_version(action, params, *, registry=None)` (defined in
`dome_protocol.versions`, also exported from `dome_protocol.commands` and the package), `Registry.min_version(name,
params=None)` (the action's `since` raised to the `param_since` of the keys present in the raw params),
`ActionSpec.param_since`, `build_screen_answer_payload(..., protocol_version=...)` (a required keyword; a stamp the
library does not speak is `PROTOCOL_INCOMPATIBLE`), `parse_screen_dc_message` with the per-type size cap (every
refusal `MALFORMED_MESSAGE`) and `SCREEN_CURSOR_CHANNEL_LABEL`. TypeScript `commandProtocolVersion(action, params)` and
`minVersion(action, params?)` (`commands.ts`; `buildCommandPayload` stamps by content), `registry.minVersion(action,
params?)` and `ActionSpec.paramSince` (`registry.ts`), `buildScreenAnswerPayload({ …, protocolVersion })` (required),
`parseScreenDcMessage` with the per-type size cap, `SCREEN_CURSOR_CHANNEL_LABEL` and the type `CursorKind`
(`screen.ts`, whose hand-written `ScreenDcPcToPhone` union gains the three pointer members and `state.cursor`), the
generated types `ScreenDcCursorPos`, `ScreenDcCursorShapeNamed` and `ScreenDcCursorShapeCustom`, and in `versions.ts`
(Ajv-free, not exported from the package) `parseParamSince` and `paramsProtocolVersion`, which the real registry and
the PWA's no-eval facade (`mobile-app/src/protocol/registry.ts`, now with `paramSince` and `registry.minVersion`) both
call. **Check order:** the compatibility check and the action's `since` stay before schema validation; the floor that
present params need is checked after it, on the raw signed params.

## AI assistant, step 1 — REST additions without a version bump (2026-10-11)

PWA-only endpoints (`docs/PROTOCOL.md` §19, design brief `docs/design/assistant.md`); `protocol_version`
and `registry_version` stay 1.2.

| Addition | Where |
| --- | --- |
| `GET /v1/assistant`, `POST`/`DELETE /v1/assistant/consent`, `POST /v1/assistant/turn` bodies: `assistant_text`, `assistant_capability`, `assistant_persona`, `assistant_context`, `assistant_recent`, `assistant_turn_request`, `assistant_step`, `assistant_allowance`, `assistant_turn_response`, `assistant_consent_version` (`2026-10-11`), `assistant_consent_request`, `assistant_status_response` | `schemas/rest.schema.json` |
| Errors `AI_NOT_AVAILABLE`, `AI_CONSENT_REQUIRED`, `AI_ALLOWANCE_EXHAUSTED`, `AI_SPEND_LIMIT_REACHED`, `AI_UNAVAILABLE`, `AI_DECLINED`, `AI_BUSY`, `AI_TURN_DUPLICATE` (REST error bodies of those routes only) | `errors.json` |
| Plan fields `ai_cost_cap_cents_per_period`, `ai_turn_rate_limit` (read by cloud-api only) | `plans.json` |
| `rules.search_queries`: the assistant clause (text only) | `version.json` |
| Plan-structure cases shared by cloud-api's validator and the phone | `fixtures/assistant-plan-cases.json` |

## Account deletion — REST additions without a version bump (2026-11-12)

PWA-only (`docs/PROTOCOL.md` §19.2, cloud-api DECISIONS #121); `protocol_version` and `registry_version` stay 1.5. The
relay frames already carried `revoked{reason: account_deleted}` for phones and PCs since 1.0, so nothing on the wire
changes for the agent or the extension.

| Addition | Where |
| --- | --- |
| `DELETE /v1/account` (session + CSRF + exact Origin, a session from a sign-in again with `prompt=login` whose password entry the provider confirmed within the last 10 minutes) with `account_delete_request{confirm: "delete my account"}` (the exact phrase); 204 without a body, the session cookie cleared | `schemas/rest.schema.json` |
| `sign_in_problem`: the values of `/?sign_in=` the sign-in callback redirects to (`email_unverified`, `try_again`, and now `account_deleted` for a deleted account's identity) | `schemas/rest.schema.json` |
| `GET /v1/auth/login?prompt=login`: asks the provider for its login page with `max_age=0` (a fresh password entry before deletion, its `auth_time` checked at the callback); `login` is the only accepted value, anything else is 400 `MALFORMED_MESSAGE` | query parameter, no body (`docs/PROTOCOL.md` §11) |
| Error `REAUTH_REQUIRED` (403, REST only: no password entry confirmed by the provider within the last 10 minutes) | `errors.json` |

Generated TypeScript types: `rest.AccountDeleteRequest`, `rest.SignInProblem` (`src/generated/rest.ts`).

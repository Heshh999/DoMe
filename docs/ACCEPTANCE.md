# DoMe acceptance: requirements-to-evidence matrix

Status: written on 2026-10-09 from the repository as it stands. This is the matrix required by
`docs/spec/MASTER_PROMPT.md` §17: for every required scenario and every Phase A/B deliverable it
names the evidence that **exists now**, tagged, with the file or test that produces it, and the
**exact outstanding manual check**. Nothing in this repository has been **Windows-device-tested**,
**iPhone-tested** or run against a real browser on youtube.com; the only hosting that has run is
loopback on a Linux build machine.

Evidence tags (spec §17): **unit-tested** · **integration-tested** · **sandbox-provider-tested** ·
**Windows-device-tested** · **iPhone-tested** · **load-tested** · **not yet verified**. A tag applies
only to what the named test actually exercises; "integration-tested" here always means *Linux, real
processes and real signatures, with the Windows OS adapters replaced by `dome_agent/testing/fake_platform.py`
and the browser by `dome_agent/testing/fake_extension.py`*.

Note on the working tree: `shared/protocol/version.json`, `errors.json` and
`relay-frames.schema.json` carry uncommitted edits (controller `hello` proof of possession and a
stricter late-result re-send rule) made after the test runs recorded below; the component code and
tests have not been updated for them yet. The counts below are from the last full runs on 2026-10-08
(`docs/HANDOFF.md`, `docs/PROGRESS.md`) and must be re-run after that change lands.

## 1. Test inventory (what the tags refer to)

| Suite | Command | Last recorded run (2026-10-09, Linux) | What it proves |
| --- | --- | --- | --- |
| `shared/python` | `cd shared/python && uv run pytest -q` | 132 passed | strict JSON, ES256 envelopes over exact bytes, registry/schema validation, pairing derivations; verifies the TypeScript fixtures |
| `shared/ts` | `cd shared/ts && pnpm test` | 84 passed | the same in TypeScript; verifies the Python fixtures |
| `cloud-api` | `cd cloud-api && uv run pytest -q` | 89 passed (PostgreSQL 16 + `tools/dev-idp` + uvicorn in-process); ruff, mypy clean | REST, OIDC login, sessions, linking, pairing, relay routing/lifecycle, abuse limits, security fixes; every REST body validated against `rest.schema.json` |
| `pc-agent` | `cd pc-agent && uv run pytest -q` | 194 passed; ruff, mypy clean | authorization order, executor, confirmations, bridge framing, relay client, store/journal, handlers, link/pairing, CLI, processes |
| `browser-extension` | `cd browser-extension && pnpm test` (`pnpm check` adds tsc, eslint, build) | 90 passed in 7 files; build produced `dist/` | manifest, frames, worker, player adapter on six DOM fixtures, content entry; bundle smoke-run with `eval` disabled |
| `mobile-app` | `cd mobile-app && pnpm test` (`pnpm check`) | 206 passed in 21 files; typecheck, lint, build clean | protocol facade parity, intents, keys, pairing, commands, confirmations, relay client, api, stores, components |
| `tests/` (cross-component) | `cd tests && uv run pytest -q` | 20 passed in 191 s (2026-10-09) | real `dome-agent` process + real relay + real PostgreSQL + real OIDC login + ES256 controller simulator + fake extension; includes the load smoke |

Reproduce: `make setup && make db-start db-create`, then the commands above (`docs/HANDOFF.md` §2).

## 2. Spec §17 required scenarios

Columns: **Evidence now** names the tag and the tests; **Outstanding** is the exact manual check that
would upgrade the tag, with where to record it.

### Scenario 1 — A new Free customer pairs a real iPhone with a real Windows PC and controls YouTube with the browser in the background

| Evidence now | Tag |
| --- | --- |
| Full path with the real agent process: OIDC sign-in via `tools/dev-idp`, `dome-agent link --no-browser` approved in the account, PC-side pairing (20-symbol code, SHA-256 handle only, HMAC verification code equal on both ends, approval on the PC), controller-signed `youtube.next` → relay → agent → fake extension observes a video transition → result validates against the per-action schema; relay rows hold lifecycle fields only. `tests/test_e2e_youtube.py::test_next_changes_the_intended_video`, `tests/conftest.py::RealAgent` | integration-tested (Linux, fake platform, fake extension) |
| Pairing UI on the phone: claim body carries only `code_hash` + public JWK, verification code displayed, 2 s polling, relay rebind on approval. `mobile-app/test/components/PairPage.test.tsx` | unit-tested |
| Extension: Next succeeds only when a new `video_id` is observed; SPA navigation keeps the attachment. `browser-extension/test/player-adapter.test.ts` "next succeeds only when a video transition is observed…", `background.test.ts` "a tabs.onUpdated(loading) caused by next … does not turn the observed success into TARGET_GONE" | unit-tested (hand-written DOM fixtures) |

**Outstanding:** On a Windows 10/11 PC with Chrome: build or run the agent (`docs/WINDOWS_INSTALL.md`
§4), register the native host, load the extension unpacked, pair a real iPhone through
`docs/IPHONE_SETUP.md` §3, focus another application on the PC, send Next from the phone; the tab
advances, the result shows `previous_video_id` and the **new** title, audio continues. Record as
**Windows-device-tested** + **iPhone-tested** here with date, Windows build, Chrome version, iOS version
(`browser-extension/README.md` checklist item 2; `mobile-app/README.md` "Real relay path").

### Scenario 2 — Next changes the intended video, Pause stops it, seek/volume work where supported, multiple tabs require correct targeting

| Evidence now | Tag |
| --- | --- |
| `youtube.set_paused`, `seek_relative`, player volume vs `windows.set_volume` (fake platform read-back), `list_tabs` with two tabs → `TARGET_REQUIRED` then an explicit target; `NO_NEXT_VIDEO`, `UNSUPPORTED_CONTEXT` during an ad, `ACTIVATION_REQUIRED` for fullscreen. `tests/test_e2e_youtube.py::test_pause_seek_player_volume_and_two_tabs`, `::test_end_of_queue_ad_and_fullscreen_are_reported_honestly`, `::test_target_identity_is_preserved` (`TARGET_CHANGED`, `TARGET_GONE`, `TAB_NOT_CONTROLLABLE`) | integration-tested |
| Phone-side target resolution order (explicit → single → single playing → ask). `mobile-app/test/targets.test.ts` | unit-tested |
| Player adapter: seek clamping and read-back, mute via YouTube's button with element fallback, theater, ad/live/Shorts guards, hidden live badge on VOD is not live. `browser-extension/test/player-adapter.test.ts` | unit-tested (fixtures mirror, but are not copies of, youtube.com) |

**Outstanding:** `browser-extension/README.md` checklist items 3–7 on a real PC: two playing tabs
require a pick and affect only the chosen tab; pre-roll ad → Next refused, Pause/Mute work, ad never
skipped; VOD page `is_live:false` and ±10 s seek lands within 1.5 s; live stream (with and without
DVR) `is_live:true`; Shorts; playlist end; volume 35 reads back 35. Record as **Windows-device-tested**.
Selector drift against the live site is the known risk (`browser-extension/KNOWN_ISSUES.md` K1).

### Scenario 3 — Approved app launch/focus works or reports the real OS restriction; unapproved app IDs, injected arguments and shell-like text are rejected

| Evidence now | Tag |
| --- | --- |
| Approval rules: only absolute, existing `.exe` paths outside temp folders; app-id rules; SHA-256 pinned and a changed executable refused; relative/argument-like paths and non-exe rejected. `pc-agent/tests/test_approved_apps.py` (8 tests), `test_handlers.py::test_launch_refuses_changed_executable`, `test_authz.py::test_app_not_approved`, `test_handlers.py::test_focus_denied_and_app_not_running` | unit-tested |
| `app.close` is a confirmed action; refused close reported as `CLOSE_REFUSED`, never force-killed. `pc-agent/tests/test_confirmations.py::test_app_close_happy_and_refused`; phone side `mobile-app/test/components/AppsPage.test.tsx` | unit-tested |
| The text parser never produces a path, argument or guess: unapproved app → clarification; app names cannot smuggle arguments or paths; injection cases. `mobile-app/test/intents.test.ts` "rejections and clarifications", "injection resistance" | unit-tested |
| The wire contract has no field that could carry a path or argument (`actions.json` params are `app_id` only, `additionalProperties: false`); every frame is strict-validated. `shared/python/tests/test_signing.py::test_extra_field_rejected`, `shared/ts/test/signing.test.ts` | unit-tested |
| No cross-component test launches an app: the fake platform records calls only. | — |

**Outstanding:** `pc-agent/README.md` checklist "Apps (pywin32)": approve Notepad; launch, focus,
minimize from the phone; `app.close` with unsaved text → `CLOSE_REFUSED` and the dialog stays on the
PC; focus from a non-foreground process → `FOCUS_DENIED` where Windows refuses. Record as
**Windows-device-tested**.

### Scenario 4 — Phone cellular and PC home internet work through the managed relay with no port forwarding or customer VPN

| Evidence now | Tag |
| --- | --- |
| Topology is outbound-only on the PC (`wss://…/ws/agent` with a bearer token, no listener) and the relay routes by account/PC/controller; the agent reconnects with backoff 1 → 60 s and application pings every 25 s; the PWA pings every 25 s with a 75 s idle rule and a resume nudge. `pc-agent/tests/test_relay_client.py` (10 tests), `mobile-app/test/relay.test.ts`, `cloud-api/tests/test_relay_routing.py::test_agent_socket_auth_rules` | unit-tested / integration-tested on loopback only |
| Hosting constraints the code imposes are written down (`docs/ARCHITECTURE.md` §5, `docs/OPERATIONS.md` §9). `deploy/` is a placeholder; no Fly.io deployment exists. | not yet verified |

**Outstanding:** Deploy a staging service (`docs/OPERATIONS.md` §3), put the PC on a home connection
behind NAT with no port forwarding, put the iPhone on cellular with Wi-Fi off, run Scenario 1. Also
leave both idle for 30 minutes and confirm the pill stays *Online* (proxy idle timeouts vs the 25 s
pings). Record as **Windows-device-tested** + **iPhone-tested** with the hosting provider named.

### Scenario 5 — Two customer accounts cannot enumerate, subscribe to, control, alter billing for, or download diagnostics from each other's PCs

| Evidence now | Tag |
| --- | --- |
| Account B cannot list, subscribe to (`GRANT_MISSING` with `ref_pc_id`), pair with (`PAIRING_CODE_INVALID`) or command account A's PC, including a forged `controller_id` and a foreign pairing code. `tests/test_e2e_security.py::test_two_accounts_cannot_see_or_control_each_other`; `cloud-api/tests/test_relay_routing.py::test_cross_account_isolation`, `::test_envelope_kid_must_match_socket`; `test_pairing.py::test_other_account_cannot_claim_or_see`; `test_auth_and_sessions.py::test_security_events_are_account_scoped`, `::test_private_endpoints_require_session`; `test_agent_link.py::test_pc_key_linked_to_another_account_is_refused`; `test_security_fixes.py::test_command_id_from_another_account_is_a_plain_reuse_conflict` | integration-tested |
| Every account-owned query is scoped by `account_id` (independent review traced them; `docs/SECURITY.md` §5) | code review, integration-tested |
| Within one account, a session that holds a paired phone's (public) `kid` but not its key stays unbound: `hello` without a valid signed `hello_proof` gets no `controller_id`, `subscribe`/`cancel` answer `GRANT_MISSING`; a proof signed by another key, replayed, or naming another account is refused (`UNKNOWN_KEY`, close 4003, `controller_hello_proof_rejected` event). `cloud-api/tests/test_hello_proof.py`; `shared/python/tests/test_hello_proof.py`; `shared/ts/test/helloProof.test.ts`; `mobile-app/test/relay.test.ts` (the PWA signs the proof on every connect) | integration-tested, unit-tested |
| Billing: no billing endpoints exist, so there is nothing to alter (Phase C). Diagnostics: no server-side diagnostics store or download exists (`support_diagnostics` table empty); bundles are produced locally on each device. | not applicable yet |

**Outstanding:** When Phase C adds billing and support-diagnostics endpoints, add the cross-account
tests for them before enabling the routes (`docs/BILLING.md` §12). No device check needed.

### Scenario 6 — Pairing expires correctly; a stolen/used code cannot create a new grant; account recovery does not auto-approve a controller

| Evidence now | Tag |
| --- | --- |
| Expired code rejected; used code rejected; wrong code rejected; a new `start` expires the previous open session; approval requires the PC (`pairing_decision`), no grant exists before it; declined pairing grants nothing; a claim made while the PC was offline is delivered on reconnect; wrong `kid` at approval creates nothing. `tests/test_e2e_security.py::test_pairing_requires_pc_approval_and_codes_are_single_use`, `::test_expired_code_is_rejected`, `::test_declined_pairing_grants_nothing`, `::test_claim_while_pc_offline_is_delivered_on_reconnect`; `cloud-api/tests/test_pairing.py` (11 tests); `pc-agent/tests/test_link_pairing.py::test_pairing_request_mismatches_are_refused` | integration-tested |
| Backend sees only SHA-256 of the code; the 6-digit verification code is an HMAC keyed by the code, so a key-substituting backend cannot make both displays agree. `shared/python/tests/test_registry_and_schemas.py::test_pairing_code_helpers`, `mobile-app/test/pairing.test.ts` "a different kid yields a different verification code", `cloud-api/tests/test_agent_link.py::test_only_hashes_are_stored` | unit-tested / integration-tested |
| A new sign-in creates a session only; controllers are created exclusively by `pairing_decision{approved}` from the PC (`cloud-api/dome_api/routes/pairing.py`, relay `agent_ws.py`). | integration-tested (above) |

**Outstanding:** On real devices, let a code expire (5 min) and try it; photograph a QR, approve on
the PC, then try the same QR from a second phone → `PAIRING_CODE_INVALID`. Record as **iPhone-tested**.

### Scenario 7 — Revocation terminates live control; local emergency disable prevents further actions until locally re-enabled

| Evidence now | Tag |
| --- | --- |
| REST revocation mid-session → `revoked` frame + close 4003 on the phone's sockets, new `grants_snapshot` to the PC, next command from that `kid` rejected; `dome-agent disable` → `PC_REMOTE_DISABLED` until `enable`; local `dome-agent revoke` reaches the relay. `tests/test_e2e_security.py::test_account_revocation_terminates_live_control`, `::test_local_emergency_disable_and_local_revoke`; `cloud-api/tests/test_relay_lifecycle.py::test_rest_revocation_closes_sockets_and_resnapshots`, `::test_grant_revocation_is_scoped_to_one_pc`, `::test_agent_initiated_revocation`, `::test_unlink_pc_revokes_everything`, `::test_logout_closes_controller_sockets` | integration-tested |
| The snapshot can never add a controller or widen a grant; in-flight commands of a revoked controller are canceled; local disable wins over every remote frame; a revoked frame discards the credential. `pc-agent/tests/test_store.py::test_apply_snapshot_cannot_widen`, `test_agent_e2e.py::test_snapshot_revocation_cancels_in_flight`, `::test_local_disable_wins_over_remote`, `::test_revoked_frame_discards_credential`, `::test_local_revocation_while_offline_reaches_relay_on_reconnect` | unit-tested |
| Phone handles 4003 by showing *Not paired* and stopping. `mobile-app/test/relay.test.ts` "close code 4003 …" | unit-tested |

**Outstanding:** Tray → **Disable remote control** on a Windows PC: icon turns red and the phone's
next command fails `PC_REMOTE_DISABLED`; re-enable from the tray. Record as **Windows-device-tested**
(`pc-agent/README.md` "Tray").

### Scenario 8 — Expired commands, changed targets, mismatched confirmations, duplicate IDs and replays are rejected or return their original result safely

| Evidence now | Tag |
| --- | --- |
| Identical re-send re-emits the same result; same id with different bytes → `COMMAND_ID_REUSED`; expired → `COMMAND_EXPIRED`; confirmation with a mismatched digest → `CONFIRMATION_INVALID`; changed target → `TARGET_CHANGED`. `tests/test_e2e_security.py::test_replay_duplicate_expiry_and_confirmation_rules`; `cloud-api/tests/test_relay_routing.py::test_duplicate_and_reuse`, `::test_duplicate_while_running_returns_current_ack`, `::test_confirmation_transaction_is_forwarded_verbatim`, `::test_invalid_challenge_text_fails_the_command`, `::test_cancel_is_forwarded_only_for_own_commands` | integration-tested |
| PC side: future-dated → `CLOCK_SKEW`, over-long lifetime → `MALFORMED_MESSAGE`, duplicate while queued replays the accepted ack, challenge consume is single-use, wrong kid / digest / expired / target-changed-before-approval. `pc-agent/tests/test_authz.py::test_expired_and_future_commands`, `::test_duplicate_and_id_reuse`, `::test_duplicate_while_queued_replays_accepted_ack`; `test_confirmations.py` (14 tests); `test_store.py::test_challenge_consume_is_single_use` | unit-tested |
| Signature layer: tampering, whitespace change, wrong signer, malleable `s`, extra/missing fields, unknown alg, 64-byte signature rule; identical behaviour in both languages via shared fixtures. `shared/python/tests/test_signing.py` (19 tests), `shared/ts/test/signing.test.ts`, `test_fixtures.py`, `fixtures.test.ts` | unit-tested |

**Outstanding:** none at device level; the logic does not depend on the OS. Re-run after the
pending contract change (proof-of-possession `hello`).

### Scenario 9 — Disconnect after a side effect, agent crash, browser restart, extension worker termination and PWA background/resume produce truthful outcomes without duplicate execution

| Evidence now | Tag |
| --- | --- |
| SIGKILL of the real agent between the side effect and the result → relay emits `outcome_unknown`; after restart the journal shows `outcome_unknown`, the fake platform recorded exactly one effect, nothing re-executes; supersession (4001) and reconnect keep grants. `tests/test_e2e_reliability.py::test_crash_after_side_effect_is_outcome_unknown_and_never_repeated`, `::test_superseded_then_reconnect_refreshes_state_and_keeps_grants`; `cloud-api/tests/test_relay_lifecycle.py::test_agent_disconnect_terminates_inflight_commands`, `::test_late_result_corrects_outcome_unknown_exactly_once`, `::test_in_flight_deadline_sweeper`, `::test_startup_sweep_closes_rows_from_a_previous_process`; `pc-agent/tests/test_store.py::test_recover_after_restart_marks_executing_outcome_unknown`, `test_agent_e2e.py::test_crash_recovery_reports_outcome_unknown_once`, `::test_late_result_resent_after_reconnect` | integration-tested |
| A command the relay already wrote to the PC's socket is never reported as `failed` when the connection drops or the deadline passes, acked or not: the relay emits `outcome_unknown`, and after reconnect the PC re-sends every result finished after the previous connection's last inbound frame so the phone learns what really happened (queued commands → `failed/PC_OFFLINE`, executed ones → their real result), exactly one correction per command. `cloud-api/tests/test_relay_lifecycle.py::test_agent_disconnect_terminates_inflight_commands`, `::test_in_flight_deadline_sweeper`; `pc-agent/tests/test_agent_e2e.py::test_late_result_resent_after_reconnect` | integration-tested (relay), unit-tested (agent) |
| Extension: lost content-script answer → `OUTCOME_UNKNOWN` for mutating ops (never retried); backoff + `chrome.alarms` reconnect after worker termination; `browser_instance_id` survives a restart; reload gives a new `tab_token`. `browser-extension/test/background.test.ts` "a content script that never answers…", "backs off 1→2→4 s with alarms…", "browser_instance_id survives a worker restart"; `pc-agent/tests/test_bridge.py::test_request_timeout_non_idempotent_is_outcome_unknown`, `test_handlers.py::test_youtube_next_lost_response_is_outcome_unknown` | unit-tested (chrome API stub) |
| PWA: resume nudge re-subscribes and detects a dead-but-open socket within 5 s; no-answer is flagged, never claimed as failure; post-terminal results dropped except one agent correction; power request after a disconnect reads "cannot tell whether it ran". `mobile-app/test/relay.test.ts` "a nudge with no inbound frame within the deadline…", `commands.test.ts` "no answer by the deadline is flagged as noAnswer…", "post-terminal results are dropped, except…", `power.test.ts`, `components/Dashboard.test.tsx` "power request followed by a disconnect…" | unit-tested (fake socket) |

**Outstanding:** (a) Windows: kill `DoMe.exe` from Task Manager during a Next → the phone shows
*Outcome unknown*, restart the agent → the result is corrected once and no second skip happens. (b)
Chrome: `chrome://serviceworker-internals` → Stop the DoMe worker while the agent is up → reconnects
without a click (`browser-extension/README.md` item 8); restart Chrome → `browser_instance_id`
unchanged. (c) iPhone: background the PWA for 2 minutes with a command in flight, resume → *Online ·
refreshing* → *Online* within ~6 s and no replay (`mobile-app/KNOWN_ISSUES.md` #5). Record each with
its tag.

### Scenario 10 — Offline PCs never execute a backlog upon reconnect; lost connectivity is not presented as proof of shutdown

| Evidence now | Tag |
| --- | --- |
| Command while the agent is down → `PC_OFFLINE` at once, nothing queued, agent restart executes nothing. `tests/test_e2e_reliability.py::test_offline_pc_never_queues`; `cloud-api/tests/test_relay_routing.py::test_offline_pc_is_never_queued`; `pc-agent/tests/test_executor.py::test_fail_queued_offline_does_not_send` | integration-tested |
| Power request + disconnect wording never claims acceptance without an `executing` ack or agent result. `mobile-app/test/power.test.ts` "copy never claims acceptance without evidence"; `pc-agent/tests/test_executor.py::test_deferred_power_completion`, `::test_power_cancel_too_late_from_relay_cancel_frame_keeps_real_outcome` | unit-tested |
| The PWA's offline screen states that nothing is sent or stored (`OfflineScreen.tsx`); the dashboard disables controls when offline. `Dashboard.test.tsx` | unit-tested |

**Outstanding:** On a Windows PC, request Sleep from the phone and pull the network cable during the
countdown: the phone must say "requested … DoMe cannot tell whether it ran", and after reconnect the
PC's actual state must be shown. Record as **Windows-device-tested**. Do this on a disposable machine
(spec §17 note on destructive tests).

### Scenario 11 — Multiple controllers and volume-slider bursts behave predictably; device and usage limits survive concurrent requests

| Evidence now | Tag |
| --- | --- |
| 20-step volume burst: every command terminates, only `COMMAND_SUPERSEDED` besides success, final value applied. `tests/test_e2e_reliability.py::test_volume_slider_burst_coalesces_to_the_latest_value`; `pc-agent/tests/test_executor.py::test_coalescing_supersedes_within_group_not_across_targets`, `::test_queue_full`; `cloud-api/tests/test_relay_routing.py::test_queue_depth_and_rate_limit`; phone throttle ≤ 1 per 250 ms + release value `mobile-app/test/throttle.test.ts`, `components/VolumeSlider.test.tsx` | integration-tested / unit-tested |
| Two controllers on one PC are exercised by the pairing/revocation tests (second controller per account) and the load smoke (40 controllers). | integration-tested |
| Device limits are transactional (`SELECT … FOR UPDATE` on the account row): second PC on Free created disabled; controller limit enforced at claim. `tests/test_e2e_security.py::test_second_pc_is_limited_on_free`; `cloud-api/tests/test_agent_link.py::test_second_pc_exceeds_free_limit_but_is_created_disabled`; `test_pairing.py::test_controller_limit_is_enforced_at_claim`. A test that fires concurrent limit requests does not exist. | integration-tested (sequential); concurrency not yet verified |
| Per-controller frame flood is throttled without growing `security_events`; legitimate rate is not. `cloud-api/tests/test_security_fixes.py::test_controller_frame_flood_is_throttled_without_growing_security_events`, `::test_legitimate_rate_is_not_throttled_at_the_socket` | integration-tested |

**Outstanding:** (a) Add a concurrent test: two simultaneous `PATCH /v1/pcs/{id} {enabled:true}` /
two simultaneous pairing claims against a Free account must leave exactly one enabled/paired (the
lock is there; the proof is not). (b) iPhone: drag the volume slider for 5 s; the PC's mixer follows
and ends on the released value (**Windows-device-tested** + **iPhone-tested**).

### Scenario 12 — Pro checkout, renewal, failed payment, out-of-order/duplicate webhooks, cancellation, downgrade and account deletion behave correctly in sandbox

| Evidence now | Tag |
| --- | --- |
| None. No Stripe code exists; `GET /v1/plans` reports `billing_enabled: false`; the Phase C tables exist empty in migration 0001; the state machine and verification plan are design only (`docs/BILLING.md` §4–§12). Plan switching in tests is done with SQL. | not yet verified (not implemented) |
| Downgrade keeps settings and security controls (Scenario 13); plan-disabled selection defaults to most-recently-seen. `cloud-api/tests/test_entitlement_and_misc.py::test_plan_disabled_controllers_after_downgrade`, `test_relay_lifecycle.py::test_plan_disabled_pc_is_refused_but_keeps_grants` | integration-tested |

**Outstanding:** Implement Phase C, then run `docs/BILLING.md` §12 against Stripe test mode with the
Stripe CLI (checkout, renewal, `invoice.payment_failed`, duplicated and reordered webhook deliveries,
cancellation at period end, downgrade selection, deletion job). Record as **sandbox-provider-tested**.

### Scenario 13 — Editing client state cannot unlock paid server/agent capabilities; downgrading preserves settings and security controls

| Evidence now | Tag |
| --- | --- |
| A hand-built routine-origin command on a Free account is refused by the **PC** with `ENTITLEMENT_REQUIRED` regardless of client claims; the volume did not change; `/v1/session` still reports Free. `tests/test_e2e_security.py::test_client_state_cannot_unlock_pro`; `pc-agent/tests/test_authz.py::test_routine_step_requires_entitlement` | integration-tested |
| Entitlement assertions are EdDSA JWS verified by the agent against the service JWKS with binding checks; wrong `typ`/`alg`/key rejected; grace only on network error/5xx, never on a 4xx. `pc-agent/tests/test_entitlement.py` (5 tests); `cloud-api/tests/test_entitlement_and_misc.py::test_entitlement_free_is_null_and_pro_verifies_against_jwks` | unit-tested / integration-tested |
| Downgrade: grants, local approvals and revocation keep working for plan-disabled devices (`rules.plan_state`). `test_plan_disabled_pc_is_refused_but_keeps_grants`, `pc-agent/tests/test_agent_e2e.py::test_snapshot_pc_disabled_refuses_but_keeps_grants` | integration-tested / unit-tested |

**Outstanding:** none beyond Scenario 12's provider tests.

### Scenario 14 — Routine validation/cancellation works; a failed step produces a partial result without silently continuing unsafe steps

| Evidence now | Tag |
| --- | --- |
| Routines are **not implemented** (Phase C): no API routes, no agent-side executor; the Routines page is a labelled preview. What exists: the `routine_allowed` flag per action in `actions.json`, the PC's refusal of routine-origin steps without a Pro entitlement (Scenario 13), the per-command cancel path (`pc-agent/tests/test_agent_e2e.py::test_cancel_frame_cancels_queued_and_awaiting`, `test_executor.py::test_cancel_for_controller_cancels_running`). | not yet verified (not implemented) |

**Outstanding:** Implement routines (≤ 10 steps, ≤ 60 s, stop on error, per-step results) and add
tests for validation, cancellation mid-run and a failing step; then record as integration-tested.

### Scenario 15 — If AI/voice is released: unsupported speech APIs, permission denial, prompt injection, invalid model output, provider outages, quotas and canceled recordings have tested fallbacks

| Evidence now | Tag |
| --- | --- |
| Not released and not implemented (Phase E). Allowances are 0 in `plans.json`; `DOME_AI_ENABLED=false`; no provider adapter, no microphone code. The deterministic parser's injection tests are the only related evidence (`mobile-app/test/intents.test.ts` "injection resistance"). | not applicable (not released) |

**Outstanding:** gated on a Phase E decision; the fallbacks must be tested on a real iPhone before any
pricing copy mentions AI or voice.

### Scenario 16 — Installer, startup-at-login, native-host registration, update verification, uninstall and supported-version negotiation are exercised on supported Windows/browser versions

| Evidence now | Tag |
| --- | --- |
| Version negotiation: agent announcing `["2.0"]` gets `PROTOCOL_INCOMPATIBLE` with both lists; MINOR compatibility rule; extension ↔ agent negotiation both ways. `cloud-api/tests/test_relay_routing.py::test_hello_is_required_and_validated`; `shared/python/tests/test_registry_and_schemas.py::test_minor_version_compat`; `pc-agent/tests/test_bridge.py::test_incompatible_extension_is_refused`; `browser-extension/test/background.test.ts` "incompatible agent protocol…", "a newer compatible minor version from the agent is accepted" | unit-tested / integration-tested |
| Native-host manifest contents and `allowed_origins` composition; native host process forwards frames; host without agent reports an error. `pc-agent/tests/test_bridge.py::test_manifest_origins`, `test_processes.py::test_native_host_process_forwards_frames`, `::test_native_host_without_agent_reports_error` (Linux: manifest written, registry step skipped) | unit-tested |
| Installer: none (PyInstaller specs written, never built); start-at-login: HKCU Run key code in `platform/windows/startup.py`, never run; updater: none; uninstall: manual steps (`docs/WINDOWS_INSTALL.md` §5, §9). | not yet verified / not implemented |

**Outstanding:** On Windows 10 and 11 with Chrome and Edge: build both PyInstaller specs; `DoMe.exe
link`; `install-native-host` and confirm the `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.dome.agent`
(and Edge) keys; tray **Start at login** writes `HKCU\…\Run\DoMe` and the agent starts after
sign-in; `uninstall-native-host` removes the keys and manifest; then the full checklist in
`pc-agent/README.md`. Code signing, an installer package and a signed update manifest are blocked on
founder-held credentials and a packaging decision (`docs/WINDOWS_INSTALL.md` §9). Record as
**Windows-device-tested** with Windows build numbers.

### Scenario 17 — A small load test covers declared concurrent connections and dispatch rate with bounded resource use, plus a sustained idle/reconnect test

| Evidence now | Tag |
| --- | --- |
| `tests/test_load_smoke.py::test_relay_load_smoke`: 40 simulated agent sockets + 40 controller sockets (one account each because Free limits are real), 400 `system.ping` commands; prints throughput, latency percentiles and RSS as numbers. One recorded run (2026-10-08, loopback, every endpoint inside the test process, shared 4-CPU machine): 400 commands in 13.47 s = 30 commands/s, round trip p50 1314 ms / p95 1629 ms, RSS 215 → 218 MiB. These are numbers from one run on a loaded build machine, **not** a capacity claim and not the spec's device latency targets (median < 500 ms, p95 < 1.5 s button-to-observed-result), which remain **unmeasured**. | load-tested (small) |
| Connection cap returns HTTP 503; pre-hello sockets count toward the cap; oversized frames close with 1009. `cloud-api/tests/test_relay_routing.py::test_relay_connection_cap_returns_503`, `test_security_fixes.py::test_pre_hello_sockets_count_toward_the_connection_cap`, `test_relay_routing.py::test_oversized_frame_closes_1009_and_malformed_frames_get_errors` | integration-tested |
| Sustained idle/reconnect test: none. | not yet verified |

**Outstanding:** On a staging deployment: (a) run the load smoke from a separate machine against the
declared `DOME_RELAY_MAX_CONNECTIONS` with the API process's RSS and CPU recorded from the host;
(b) keep 20 real or simulated agents connected for 24 h, restart the service once, and record
reconnect time distribution and memory. Publish test conditions and results here (spec §16).

## 3. Phase A deliverables (spec §18) — evidence

| Deliverable | What exists | Evidence |
| --- | --- | --- |
| Workspace inspected, existing code preserved | `docs/PROGRESS.md` "Phase A foundation"; component builds resumed WIP rather than rewriting (`docs/HANDOFF.md` §8) | n/a |
| Concrete auth/hosting approach | ADR-0001: OIDC Authorization Code + PKCE via Authlib; device-code-shaped PC linking; non-extractable WebCrypto controller keys; Fly.io as the (reversible) target | decision record |
| Concise architecture decision record | `docs/adr/0001-foundational-decisions.md` (D1–D10) | n/a |
| Action contracts and tenancy boundaries | `shared/protocol/` (29 actions, 6 capabilities, schemas, `version.json → rules`); `account_id` on every owned table and query | unit-tested (`shared/*` 210 tests); integration-tested (Scenario 5) |
| Thin authenticated relay/agent/extension path | `cloud-api` relay, `pc-agent` relay client + bridge, `browser-extension` worker | integration-tested (`tests/test_e2e_youtube.py`) with a fake extension and fake platform |
| Real Next/Pause first | `youtube.next` with an observed transition and `youtube.set_paused` through the real agent process | integration-tested (Linux); **not yet verified** in a real browser |
| No real PC dispatch endpoint before authentication and authorization | `/ws/agent` requires a bearer PC token and refuses any `Origin`; `/ws/controller` requires a session cookie, exact `Origin` and a bound `kid`; every command is signature-verified and grant-checked before forwarding | integration-tested (`test_relay_routing.py::test_agent_socket_auth_rules`, `::test_controller_socket_requires_session_and_exact_origin`, `::test_hello_without_known_kid_cannot_subscribe_or_command`, `::test_rejection_rules_each_become_a_relay_result`) |

## 4. Phase B deliverables (spec §18) — evidence

| Deliverable | What exists | Evidence | Outstanding |
| --- | --- | --- | --- |
| Pairing/revocation | PC-generated code, hash-only backend, HMAC verification code, PC approval, offline claim delivery; account-side, grant-level, PC-side and unlink revocation | integration-tested (Scenarios 6, 7) | real-device pairing by QR (**iPhone-tested**) |
| Native extension integration | MV3 extension + `dome-native-host` + agent bridge with strict framing, token-bound tabs, transition-observed Next | unit-tested both sides; bridge protocol integration-tested with `FakeExtension`; native host process test on Linux | handshake in real Chrome/Edge against youtube.com (**Windows-device-tested**) |
| All supported core controls | 29 registry actions with exactly one handler each; results validated against per-action schemas | unit-tested (`pc-agent/tests/test_handlers.py::test_every_registry_action_has_exactly_one_handler`, `::test_handler_success_result_validates`); YouTube subset integration-tested | every Windows adapter (volume, media sessions, apps, lock, power) **not yet verified** on a device |
| Deterministic text parsing | `mobile-app/src/lib/intents.ts`: ordered rule table, number words, clarifications, injection rejection, no AI | unit-tested (`intents.test.ts`, 30+ cases incl. the spec §13 table) | — |
| Mobile onboarding | Sign-in, PC link approval page, pairing by QR/code with verification code, Devices, Settings/help, Billing status, public pages | unit-tested (components); production build clean | Lighthouse/PWA audit and the `mobile-app/README.md` manual table on an iPhone (**iPhone-tested**) |
| Truthful state/error handling | Lifecycle labels, *Not delivered* vs *Failed*, no-answer flag, stale-state disabling (75 s), offline screen, power-request evidence rule, recovery steps per code | unit-tested (`Dashboard.test.tsx`, `commands.test.ts`, `power.test.ts`); integration-tested for the agent/relay side (Scenarios 9, 10) | resume behaviour on iOS Safari |
| Permission/replay failure tests | Scenarios 5–8 | integration-tested | re-run after the pending contract change |
| Development installer packaged | PyInstaller specs for `DoMe.exe` and `dome-native-host.exe`; no MSI/MSIX | **not yet verified** (never built) | build on Windows; choose a packaging tool (`docs/WINDOWS_INSTALL.md` §9) |
| Real-device evidence | none | **not yet verified** | Scenarios 1, 2, 3, 7, 9, 10, 11, 16 device checks above |

## 5. Required documents (spec §18) — status

| Document | Status |
| --- | --- |
| `README.md` with reproducible setup and first-run | exists; the table rows for `deploy/` and `.github/workflows/` describe intended content — at the time of writing `deploy/` holds a placeholder README and no workflow file exists (see `docs/OPERATIONS.md` §0) |
| `docs/ARCHITECTURE.md` with trust/data boundaries | exists |
| `docs/PRODUCT_AND_PLANS.md` released vs planned | exists |
| `docs/SECURITY.md`, pairing/revocation design, threat model | exists (threat → control → test table) |
| `docs/PROTOCOL.md` with examples and versioning | exists |
| `docs/BILLING.md` | exists (design; nothing implemented) |
| `docs/COST_MODEL.md`, `docs/DATA_RETENTION.md` | exist (assumptions dated; no purge job implemented) |
| `docs/WINDOWS_INSTALL.md`, `docs/IPHONE_SETUP.md` | exist (as implemented; device steps unverified) |
| `docs/TROUBLESHOOTING.md`, operational guidance (`docs/OPERATIONS.md`) | exist (this pass) |
| `docs/ACCEPTANCE.md` | this file |
| `docs/PROGRESS.md`, `docs/HANDOFF.md` | exist |
| Lockfiles, migrations, `.env.example`, build/test commands | `uv.lock` ×5, `pnpm-lock.yaml` ×3, Alembic `0001`, `.env.example`, `Makefile` — exist |
| CI configuration without secrets | **missing at the time of writing** (`.github/workflows/` is empty); `docs/HANDOFF.md` and `README.md` refer to `ci.yml` as written but not run — reconcile when it lands |

## 6. Engineering targets (spec §16) — measured, not claimed

| Target | Status |
| --- | --- |
| Median button-to-observed-result < 500 ms, p95 < 1.5 s on a documented healthy network | **unmeasured**. The only latency numbers are the loopback load smoke above (p50 1314 ms under 40-way contention inside one test process), which measures neither a device nor a network. |
| Modest idle agent memory/CPU | **unmeasured** on Windows; the agent is not built. |
| Reconnect within a documented bounded interval | Bound documented: agent 1 → 60 s full-jitter backoff, PWA 1 → 30 s ±30 %, extension 1 → 60 s. **unit-tested** (`pc-agent/tests/test_relay_client.py::test_backoff_grows_and_is_jittered`, `mobile-app/test/relay.test.ts`, `browser-extension/test/background.test.ts`); wall-clock measurement **not yet verified**. |

## 7. How to record device evidence

Append a dated row under the scenario, in this form, and update the tag in the table:

```
2026-MM-DD · Windows-device-tested · Windows 11 23H2 (22631.x), Chrome 1xx, DoMe.exe <version/commit>,
iPhone 15 iOS 17.x, hosting: <staging URL or loopback> · Steps: <checklist item ids> ·
Result: pass / fail (what was observed) · Follow-up: <issue or fix>
```

Do not attach screenshots or videos that were not taken during that run, and do not round numbers
up. A scenario is accepted when every row of its "Outstanding" check has a passing dated entry and
the automated suites still pass on the commit being released.

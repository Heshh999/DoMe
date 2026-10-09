# Contract issues found while building pc-agent (contract left unchanged)

1. **`pairing_request` has no `controller_id`.** `grants` rows are keyed by `controller_id` and the
   KeyRecord binding needs it, but the relay only assigns the id after `pairing_decision{approve}`.
   The design's "approve stores the grant so the agent accepts commands even before the next
   snapshot" therefore cannot hold: until the snapshot names the controller, a command from the new kid
   fails `CONTROLLER_MISMATCH`. Workaround: the grant is stored under a provisional id
   (`pending-<kid>`) that `apply_snapshot` rewrites by kid. Proposal: let the relay pre-allocate and
   include `controller_id` in `pairing_request` (the row is created on approval with that id).
2. **`power_result` wording vs `rules.in_flight`.** `power_result` says "the countdown was armed",
   while `rules.in_flight` exempts "armed power countdowns until fires_at + timeout_ms", which only
   makes sense if the command stays in flight during the countdown. The agent follows the rule (ack
   `executing`, result when the OS call is issued, `pending_power_action` in state frames for the
   countdown). Proposal: state explicitly in `actions.json` that the result follows the OS call and
   that cancellation ends the command as `canceled/POWER_CANCELED`.
3. **`agent_result` for a user-initiated `cancel` has no error code.** `COMMAND_SUPERSEDED` and
   `POWER_CANCELED` exist, but a plain queued/awaiting command canceled by the relay `cancel` frame has
   no dedicated code; the agent emits `canceled` without `error`. Proposal: add `COMMAND_CANCELED`.
4. **`cancel` for an executing command is undefined.** The agent ignores it (logged); a code such as
   `CANNOT_CANCEL` or a documented "ignored" rule would make the behaviour explicit.
5. **`EdDSA` algorithm name.** RFC 9864 deprecates the polymorphic `EdDSA` identifier in favour of
   `Ed25519`; `joserfc` warns when `EdDSA` is used. The agent keeps the contract value and silences
   only that warning during verification. Proposal: switch `plans.json → entitlement_assertion.algorithm`
   and the issuer header to `Ed25519` in a protocol MINOR bump once joserfc/cloud-api agree.
6. **`relay_to_agent_cancel.controller_id` vs provisional ids**: a cancel for a command whose grant
   was still provisional cannot match a journal row (the row stores the real controller id only after
   the snapshot). Harmless in practice because such commands are rejected before being journaled.
7. **Verification strategy `observe_video_transition` is executed by the extension**, but the agent
   also re-checks that `tab.video_id != previous_video_id` and fails with `NO_NEXT_VIDEO` /
   `NO_PREVIOUS_VIDEO` when the extension answered `ok:true` without a transition. The contract should
   say which side is authoritative so the extension and agent cannot disagree on a success.
8. **`input.session_start` availability lacks `windows`** (`actions.json` lists only
   `session_unlocked`). On a non-Windows host without the fake platform a session could start that can
   never inject. The agent's handler refuses `PLATFORM_UNSUPPORTED` itself. Proposal: add `windows` to
   the availability list.
9. **No `restarted` reason in `agent_input_session.reason`.** When the owning controller starts again
   (page reload, PC switch back) the agent ends the old session with `stopped`; `pc_switch` is listed
   but the agent cannot know the phone switched PCs (the PWA sends `input.session_stop` before
   switching). Proposal: either a `restarted` reason or a note that `stopped` covers it.
10. **`input_session{suspended}` and holds.** The rule says a suspension discards pending events and
    requires a fresh start but does not say what happens to held buttons; the frame requires
    `holds_released`. The agent releases them at suspension and reports the count. Proposal: state it.
11. **Batch rejections are `error` frames without a reference.** `error_frame` has only `ref_pc_id`;
    an agent → relay error for a dropped batch cannot name the session or the controller, so the relay
    must route it by the owning controller of the PC's live session (it has `pc_state.input_session`).
    The agent rate-limits these to one per code per second and puts counts in the acks. Proposal: an
    optional `ref_input_session_id` on `error_frame`.
    **Consequence confirmed by review:** the current cloud-api relay only logs agent `error` frames
    (`relay/agent_ws.py`), so NO batch-rejection reason (`INPUT_STALE`, `INPUT_SEQUENCE_INVALID`,
    `INPUT_NOT_PERMITTED`, `INPUT_SUSPENDED`, `INPUT_RESTRICTED`, `INPUT_INJECTION_FAILED`,
    `INPUT_TARGET_CHANGED`) reaches the phone in the integrated system. Concrete proposal for 1.2:
    `error_frame.ref_input_session_id` (optional, `^[A-Za-z0-9_-]{22}$`), allowed only agent → relay;
    the relay routes such a frame to the owning controller's 1.1 sockets exactly like `input_ack`
    (owner looked up from its live-session table) and drops it when the id is not the live session.
    The agent would fill it in `InputSessionManager._report` (one line). Until then the agent makes the
    deliverable signals carry the meaning: `dropped_events` in `input_ack`, a state frame when
    `pc_state.input_restricted` or `foreground_app` changes, `input_session{suspended|ended, reason}`.
    pc-agent cannot add the integration test "reason reaches the controller on the real relay path":
    the contract has no field for it and the relay is another component.
12. **`grant_update.capabilities` has `minItems: 1`**, so a grant cannot be emptied through this frame;
    the agent refuses the local change ("revoke instead"). Fine, but worth a sentence in
    `rules.grant_update`.
13. **Provisional controllers and `grant_update`** (follow-up to #1): a capability change made before
    the first snapshot names the controller cannot be sent (no `controller_id`); the agent journals it
    by kid and sends it after the snapshot. Pre-allocating `controller_id` in `pairing_request` would
    remove this too.
14. **Elevated foreground is not an end reason.** `rules.input_sessions` ends a session on "a secure
    desktop"; an elevated window in front is a different, transient state (the customer can click
    elsewhere). The agent keeps the session, sets `pc_state.input_restricted` and answers
    `INPUT_RESTRICTED` per batch. Proposal: say so explicitly.
15. **Bridge protocol error detail vs the version list.** `test_bridge.py` was updated at the 1.1 bump to
    expect `supported == ["1.1"]`, but the agent announces every MINOR it speaks (`["1.0", "1.1"]`, also
    asserted by the relay hello test) as the compatibility rule intends. The test was corrected; the
    contract could state that `supported` lists all accepted versions.
16. **`rules.input_sessions` age check vs clock skew.** The rule says "requires now - issued_at <=
    input_age_budget_ms" with `issued_at` stamped by the phone, while the envelope window tolerates
    `max_clock_skew_seconds` = 5 s and the budget is 1 s. Read literally, a phone clock ≥ 1 s behind the
    PC rejects every batch (`INPUT_STALE`) and a phone clock ahead hides relay stalls. The agent judges
    the age against a per-session estimate of the phone's clock offset (DECISIONS.md #39). Proposed
    wording: "requires the batch's transit delay — now − issued_at corrected by the agent's estimate of
    the controller's clock offset (bounded by max_clock_skew_seconds, e.g. a windowed minimum of
    now − issued_at over the session's batches) — to be <= input_age_budget_ms; the agent may
    additionally bound the relay → agent leg with relay.received_at the same way".
17. **UIPI and `INPUT_RESTRICTED`.** Windows drops input aimed at a higher-integrity window without an
    error (`SendInput` returns the full count), so "Windows accepted" in `input_ack` is not observable
    there. The agent refuses keyboard events with `INPUT_RESTRICTED` while it knows an elevated window
    or a protected desktop is in front (DECISIONS.md #41) and lets pointer events through. The contract
    could say that `input_restricted: true` means keyboard input is refused and pointer input is
    best-effort.

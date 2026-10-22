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
    **Resolved by the maintainer (protocol 1.1 amendment, 2026-10-09):** `error_frame` gained `ref_input_session_id` and `ref_controller_id`; the agent sets both on every input rejection and cloud-api routes the frame to that controller's 1.1 sockets (`relay/agent_ws.py::_on_agent_error`), stripping `ref_controller_id` and adding `ref_pc_id`.

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
    **Resolved by the maintainer (protocol 1.1 amendment, 2026-10-09):** `rules.input_sessions` now defines the age against the per-session clock-offset estimate (as implemented by `AgeEstimator`).

17. **UIPI and `INPUT_RESTRICTED`.** Windows drops input aimed at a higher-integrity window without an
    error (`SendInput` returns the full count), so "Windows accepted" in `input_ack` is not observable
    there. The agent refuses keyboard events with `INPUT_RESTRICTED` while it knows an elevated window
    or a protected desktop is in front (DECISIONS.md #41) and lets pointer events through. The contract
    could say that `input_restricted: true` means keyboard input is refused and pointer input is
    best-effort.

18. **`screen_session_result.allowance.remaining_seconds` cannot be filled by the agent** (protocol 1.4). The
    relay's ticket on the forwarded command (`relay.screen{granted_until|null, unlimited, stun_urls,
    relayed_max_kbps?}`) tells the PC when its ticket ends, not what is left in the 5-hour window, and
    `screen_allowance` carries `granted_until` / `resets_at` but no remaining figure either, while the result
    schema wants `allowance{unlimited, remaining_seconds, resets_at}`. The agent therefore answers
    `remaining_seconds: null` and `resets_at` from the last `screen_allowance` (null at start), and forwards
    the same `null` in the data-channel `allowance` message. The phone has the window figures from
    `screen_ice_servers.allowance`, received seconds earlier. Suggest either adding `remaining_seconds` /
    `resets_at` to `relay.screen` and `screen_allowance`, or dropping them from the result.
19. **The detail key of the Screen view errors is not specified by `errors.json`.** The design (§4.2, §7) writes
    `SCREEN_OFFER_INVALID` with a `reason` and `SCREEN_CAPTURE_UNAVAILABLE` with a `detail`
    (`indicator | driver | busy | unsupported_display | remote_session`), and the agent follows that; the worker
    pipe carries one short word that the agent puts back under the right key. A small `detail` schema per code
    (as the shared `sdp_cases` fixture does for the SDP shape) would let both the phone's copy and the tests pin
    the vocabulary.
20. **A start whose forwarded command carries no `relay.screen` ticket has no code of its own.** The relay is
    meant to attach the first ticket to every `screen.session_start` it forwards; if it does not (an older relay,
    a dropped field) the agent must refuse rather than stream unmetered, and answers `ACTION_UNAVAILABLE`
    ("The DoMe service attached no Screen view allowance to this start. Try again."), which the phone shows as a
    generic refusal. A dedicated code (or reusing `SCREEN_VIEW_DISABLED` for "the relay did not allow it")
    would let the phone say so.
21. **Resolved (2026-10-22).** `SCREEN_SESSION_REQUIRED` / `SCREEN_GEOMETRY_CHANGED` reach the phone through the
    relay's error forwarding (follow-up to #11): the agent sends them as `error` frames with `ref_controller_id` and
    `ref_input_session_id`, exactly like the `INPUT_*` batch rejections, and the relay forwards `SCREEN_*` errors to
    the issuing 1.4 socket (`cloud-api/dome_api/relay/agent_ws.py`, the `SCREEN_` prefix next to `INPUT_`; tested by
    cloud-api's `test_1_4_batches_reach_a_1_4_agent_and_screen_rejections_reach_only_the_issuing_1_4_socket` and the
    e2e stale-`gen` step in `tests/test_e2e_screen.py`).
22. **`screen_session_result.consent` is `not_required | granted` only** (`results.schema.json`, the
    `screen_session_result.consent` description: "A refusal is a failed result … never a consent value"; the phone's
    side is mobile-app CONTRACT_ISSUES #16): the agent sends the result after the person at the PC answered, so
    "pending" never appears in a result; the phone's "Waiting for someone at the PC" state comes from the `executing`
    ack with no result yet.
23. **`rules.input_sessions` (2a) and `limits.screen_geometry_generations_kept` disagree.** The rule accepts a
    `pointer_move_to` "only when gen equals the current geometry generation"; the limit (4, "how many gen values the
    agent still maps", `PROTOCOL.md` §20) and design §8.2 ("the last 4 are kept") suggest older generations are still
    mapped. The agent follows the rule: only the current `gen` is mapped, and while a new generation is being announced
    (the worker rebuilds after a monitor change) every `pointer_move_to` is `SCREEN_GEOMETRY_CHANGED`, so a tap aimed at
    an older picture is never mapped onto a rectangle that may have moved. The agent keeps the last 4 generations'
    monitors only as history. Suggest dropping the limit, or rewording it as "generations a phone may still hold".
24. **`ACTION_UNAVAILABLE{reason: "screen_view_not_included"}`** (2026-10-22, inside the unreleased 1.4): every Windows
    build so far is made without the `screen` extra (no aiortc / PyAV), yet the agent must announce 1.4 because
    pairing needs `pc_key_mac`. Such a build refuses `screen.session_start` before any window or bar with
    `ACTION_UNAVAILABLE` and the detail `reason: screen_view_not_included` (`errors.json` lists no detail keys for
    `ACTION_UNAVAILABLE`; the detail is additive), and the phone shows "This version of DoMe on your PC doesn't include
    Screen view yet" instead of a capture failure that blames the display. Suggest naming the detail in `errors.json`
    (or a capability flag in `hello`) at the next contract round.
25. **`screen_session{paused|resumed}` is sent per pause cause** (2026-10-22): the agent keeps the causes as a set
    (`secure_desktop`, `indicator`), pauses the worker when the first appears and resumes it only when none is left,
    and emits one `paused` and one `resumed` frame per cause. The relay discounts only a `secure_desktop` pair it saw,
    which is now exactly the secure desktop's own time even when a missing bar overlaps it; the phone keeps the same
    set (mobile-app). `relay-frames.schema.json` allows this as is; `rules.screen_sessions` does not say whether pauses
    nest — suggest saying so.

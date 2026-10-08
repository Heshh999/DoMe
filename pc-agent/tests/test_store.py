from __future__ import annotations

import uuid

from dome_agent.store import JOURNAL_MAX_ROWS, PROVISIONAL_PREFIX, Store

from .helpers import Controller


def test_settings_defaults(store: Store) -> None:
    assert store.remote_enabled is False  # off until the user enables it
    assert store.media_while_locked is False
    store.set_remote_enabled(True)
    assert store.remote_enabled is True


def test_journal_insert_duplicate_and_states(store: Store) -> None:
    cid = str(uuid.uuid4())
    assert store.journal_insert(command_id=cid, digest="d1", action="system.ping", controller_id="c")
    assert not store.journal_insert(command_id=cid, digest="d2", action="system.ping", controller_id="c")
    row = store.journal_get(cid)
    assert row is not None and row.state == "created" and row.digest == "d1"
    store.journal_set_state(cid, "succeeded", frame={"type": "result", "state": "succeeded"}, sent=False)
    row = store.journal_get(cid)
    assert row is not None and row.terminal and row.finished_at is not None and row.sent is False
    assert [r.command_id for r in store.journal_unsent_terminal()] == [cid]
    store.journal_mark_sent(cid)
    assert store.journal_unsent_terminal() == []


def test_journal_is_bounded(store: Store) -> None:
    for i in range(JOURNAL_MAX_ROWS + 25):
        store.journal_insert(command_id=str(uuid.uuid4()), digest=str(i), action="system.ping", controller_id="c")
    assert store.journal_count() == JOURNAL_MAX_ROWS


def test_recover_after_restart_marks_executing_outcome_unknown(store: Store) -> None:
    executing = str(uuid.uuid4())
    queued = str(uuid.uuid4())
    awaiting = str(uuid.uuid4())
    done = str(uuid.uuid4())
    for cid, state in (
        (executing, "executing"),
        (queued, "accepted"),
        (awaiting, "awaiting_confirmation"),
        (done, "succeeded"),
    ):
        store.journal_insert(command_id=cid, digest="d", action="youtube.next", controller_id="c", state=state)
    store.set_pending_power(executing, "power.sleep", "2026-01-01T00:00:00.000Z")
    out = store.recover_after_restart()
    assert out["outcome_unknown"] == [executing]
    assert out["failed_offline"] == [queued]
    assert out["expired_confirmation"] == [awaiting]
    row = store.journal_get(executing)
    assert (
        row is not None and row.state == "outcome_unknown" and row.error_code == "OUTCOME_UNKNOWN" and row.sent is False
    )
    assert row.frame is not None and row.frame["state"] == "outcome_unknown"
    assert store.journal_get(queued).state == "failed"  # type: ignore[union-attr]
    assert store.journal_get(done).state == "succeeded"  # type: ignore[union-attr]
    assert store.get_pending_power() is None
    # the outcome_unknown result is re-sent later as a late correction; PC_OFFLINE ones are not
    assert [r.command_id for r in store.journal_unsent_terminal()] == [executing, awaiting]


def test_apply_snapshot_intersection(store: Store) -> None:
    account, pc = str(uuid.uuid4()), str(uuid.uuid4())
    a = Controller(account, pc)
    b = Controller(account, pc)
    c = Controller(account, pc)  # never approved locally
    a.grant_locally(store)
    b.grant_locally(store, capabilities=("status", "media"))
    snap = str(uuid.uuid4())
    result = store.apply_snapshot(
        snap, True, [a.snapshot_entry(capabilities=("status", "media", "volume")), c.snapshot_entry()]
    )
    assert result.snapshot_id == snap
    assert result.revoked_controller_ids == (b.controller_id,)
    assert result.unknown_controllers == ((c.controller_id, c.kid),)
    assert result.active_controller_ids == (a.controller_id,)
    row_a = store.get_grant(a.controller_id)
    assert row_a is not None and row_a.effective_capabilities(snap) == ("status", "media", "volume")  # local ∩ snapshot
    assert row_a.effective_capabilities("other") == ()
    row_b = store.get_grant(b.controller_id)
    assert row_b is not None and row_b.revoked and row_b.revoked_reason == "snapshot_revocation"
    assert store.get_grant(c.controller_id) is None  # a snapshot can never add a controller
    assert store.current_snapshot_id() == snap


def test_apply_snapshot_cannot_widen(store: Store) -> None:
    a = Controller(str(uuid.uuid4()), str(uuid.uuid4()))
    a.grant_locally(store, capabilities=("status",))
    snap = str(uuid.uuid4())
    store.apply_snapshot(snap, True, [a.snapshot_entry(capabilities=("status", "power"))])
    row = store.get_grant(a.controller_id)
    assert row is not None and row.effective_capabilities(snap) == ("status",)


def test_apply_snapshot_resolves_provisional_controller_id(store: Store) -> None:
    a = Controller(str(uuid.uuid4()), str(uuid.uuid4()))
    store.add_grant(controller_id=None, kid=a.kid, public_jwk=a.jwk, capabilities=a.capabilities, display_name="phone")
    row = store.get_grant_by_kid(a.kid)
    assert row is not None and row.controller_id.startswith(PROVISIONAL_PREFIX)
    snap = str(uuid.uuid4())
    result = store.apply_snapshot(snap, True, [a.snapshot_entry()])
    assert result.unknown_controllers == ()
    assert result.active_controller_ids == (a.controller_id,)
    assert store.get_grant(a.controller_id) is not None


def test_challenge_consume_is_single_use(store: Store) -> None:
    from dome_agent.store import ChallengeRow

    cid = str(uuid.uuid4())
    store.journal_insert(
        command_id=cid, digest="d", action="power.sleep", controller_id="c", state="awaiting_confirmation"
    )
    row = ChallengeRow(
        challenge_id=str(uuid.uuid4()),
        command_id=cid,
        controller_id="c",
        kid="k" * 43,
        challenge_text="{}",
        digest="x",
        target_state_digest="y",
        expires_at="2030-01-01T00:00:00.000Z",
        consumed_at=None,
    )
    store.insert_challenge(row)
    assert store.consume_challenge(row.challenge_id, new_command_state="accepted")
    assert not store.consume_challenge(row.challenge_id, new_command_state="accepted")
    assert store.journal_get(cid).state == "accepted"  # type: ignore[union-attr]


def test_pending_power_single_row(store: Store) -> None:
    store.set_pending_power("a", "power.sleep", "t1")
    store.set_pending_power("b", "power.shutdown", "t2")
    pending = store.get_pending_power()
    assert pending is not None and pending.command_id == "b"
    assert store.clear_pending_power() is not None
    assert store.get_pending_power() is None


def test_security_events_bounded(store: Store) -> None:
    for i in range(520):
        store.add_security_event("x", i=i)
    assert len(store.list_security_events(1000)) == 500

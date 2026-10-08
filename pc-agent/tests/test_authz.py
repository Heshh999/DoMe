"""Authorization order and every rejection code, on a fully wired agent with the fake platform."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from dome_protocol import now_utc

from dome_agent.testing.fake_extension import FakeTab

from .conftest import AgentHarness
from .helpers import Controller, payload_of


async def authorize(h: AgentHarness, envelope: dict[str, Any], *, snapshot: bool = True) -> Any:
    return await h.agent.authz.authorize(envelope, snapshot_received=snapshot)


async def test_execute_decision_journals_created(harness: AgentHarness, controller: Controller) -> None:
    env = controller.command("system.ping")
    d = await authorize(harness, env)
    assert d.kind == "execute" and d.journaled
    row = harness.agent.store.journal_get(payload_of(env)["command_id"])
    assert row is not None and row.state == "created"


async def test_unknown_kid(harness: AgentHarness, fake_api: Any) -> None:
    stranger = Controller(fake_api.account_id, fake_api.pc_id)
    d = await authorize(harness, stranger.command("system.ping"))
    assert d.kind == "rejected" and d.error.code == "UNKNOWN_KEY" and not d.journaled


async def test_bad_signature(harness: AgentHarness, controller: Controller) -> None:
    env = controller.command("system.ping")
    env["payload"] = env["payload"].replace('"system.ping"', '"system.get_status"')
    d = await authorize(harness, env)
    assert d.kind == "rejected" and d.error.code == "SIGNATURE_INVALID"


async def test_controller_mismatch_is_not_journaled(harness: AgentHarness, controller: Controller) -> None:
    d = await authorize(harness, controller.command("system.ping", controller_id=str(uuid.uuid4())))
    assert d.kind == "rejected" and d.error.code == "CONTROLLER_MISMATCH" and not d.journaled and not d.mismatch


async def test_account_mismatch_counts(harness: AgentHarness, controller: Controller) -> None:
    d = await authorize(harness, controller.command("system.ping", account_id=str(uuid.uuid4())))
    assert d.kind == "rejected" and d.error.code == "ACCOUNT_MISMATCH" and d.mismatch and not d.journaled


async def test_target_pc_mismatch_counts(harness: AgentHarness, controller: Controller) -> None:
    d = await authorize(harness, controller.command("system.ping", target_pc_id=str(uuid.uuid4())))
    assert d.kind == "rejected" and d.error.code == "TARGET_PC_MISMATCH" and d.mismatch and not d.journaled
    assert any(e["kind"] == "command_identity_mismatch" for e in harness.agent.store.list_security_events())


async def test_pc_reconnecting_before_snapshot(harness: AgentHarness, controller: Controller) -> None:
    env = controller.command("system.ping")
    d = await authorize(harness, env, snapshot=False)
    assert d.kind == "rejected" and d.error.code == "PC_RECONNECTING" and not d.journaled
    assert harness.agent.store.journal_get(payload_of(env)["command_id"]) is None


async def test_expired_and_future_commands(harness: AgentHarness, controller: Controller) -> None:
    d = await authorize(harness, controller.command("system.ping", issued_at=now_utc() - timedelta(seconds=120)))
    assert d.kind == "rejected" and d.error.code == "COMMAND_EXPIRED"
    d = await authorize(harness, controller.command("system.ping", issued_at=now_utc() + timedelta(seconds=120)))
    assert d.kind == "rejected" and d.error.code == "CLOCK_SKEW"
    d = await authorize(harness, controller.command("system.ping", lifetime=600))
    assert d.kind == "rejected" and d.error.code == "MALFORMED_MESSAGE"


async def test_remote_disabled(harness: AgentHarness, controller: Controller) -> None:
    harness.agent.store.set_remote_enabled(False)
    d = await authorize(harness, controller.command("system.ping"))
    assert d.kind == "rejected" and d.error.code == "PC_REMOTE_DISABLED"


async def test_pc_plan_disabled(harness: AgentHarness, controller: Controller) -> None:
    harness.agent.store.apply_snapshot(str(uuid.uuid4()), False, [controller.snapshot_entry()])
    d = await authorize(harness, controller.command("system.ping"))
    assert d.kind == "rejected" and d.error.code == "PC_PLAN_DISABLED"


async def test_controller_plan_disabled(harness: AgentHarness, controller: Controller) -> None:
    harness.agent.store.apply_snapshot(str(uuid.uuid4()), True, [controller.snapshot_entry(status="plan_disabled")])
    d = await authorize(harness, controller.command("system.ping"))
    assert d.kind == "rejected" and d.error.code == "CONTROLLER_PLAN_DISABLED"


async def test_grant_missing_uses_intersection(harness: AgentHarness, controller: Controller) -> None:
    # snapshot narrows the local grant to status only
    harness.agent.store.apply_snapshot(str(uuid.uuid4()), True, [controller.snapshot_entry(capabilities=("status",))])
    d = await authorize(harness, controller.command("windows.set_volume", {"value": 10}))
    assert d.kind == "rejected" and d.error.code == "GRANT_MISSING"
    d = await authorize(harness, controller.command("system.ping"))
    assert d.kind == "execute"


async def test_revoked_controller(harness: AgentHarness, controller: Controller) -> None:
    harness.agent.store.revoke_grant(controller.controller_id, "test")
    d = await authorize(harness, controller.command("system.ping"))
    assert d.kind == "rejected" and d.error.code == "CONTROLLER_REVOKED"


async def test_duplicate_and_id_reuse(harness: AgentHarness, controller: Controller) -> None:
    env = controller.command("system.ping")
    cid = payload_of(env)["command_id"]
    first = await authorize(harness, env)
    assert first.kind == "execute"
    # while still running: duplicate re-emits the current ack
    dup = await authorize(harness, env)
    assert dup.kind == "duplicate" and dup.frames[0]["type"] == "ack"
    harness.agent.store.journal_set_state(cid, "succeeded", frame={"type": "result", "command_id": cid, "origin": "agent", "state": "succeeded", "at": "2026-01-01T00:00:00.000Z", "duration_ms": 1})
    dup = await authorize(harness, env)
    assert dup.kind == "duplicate" and dup.frames[0]["type"] == "result" and dup.frames[0]["state"] == "succeeded"
    other = controller.command("system.ping", command_id=cid)  # same id, different bytes
    d = await authorize(harness, other)
    assert d.kind == "rejected" and d.error.code == "COMMAND_ID_REUSED"


async def test_availability_platform_unsupported_without_windows(harness: AgentHarness, controller: Controller) -> None:
    from dome_agent.platform.unsupported import build_unsupported_platform

    harness.agent.services.platform = build_unsupported_platform()
    d = await authorize(harness, controller.command("windows.get_volume"))
    assert d.kind == "rejected" and d.error.code == "PLATFORM_UNSUPPORTED" and d.journaled


async def test_availability_extension_disconnected(harness: AgentHarness, controller: Controller) -> None:
    d = await authorize(harness, controller.command("youtube.list_tabs"))
    assert d.kind == "rejected" and d.error.code == "EXTENSION_DISCONNECTED" and d.journaled


async def test_availability_session_locked(harness: AgentHarness, controller: Controller) -> None:
    harness.fake.locked = True
    d = await authorize(harness, controller.command("windows.lock"))
    assert d.kind == "rejected" and d.error.code == "PC_SESSION_LOCKED"
    # media while locked: refused unless the local setting allows it
    harness.fake.add_session("app#0")
    d = await authorize(harness, controller.command("media.set_paused", {"paused": True}, {"session_id": "app#0"}))
    assert d.error.code == "PC_SESSION_LOCKED"
    harness.agent.store.set_bool("media_while_locked", True)
    d = await authorize(harness, controller.command("media.set_paused", {"paused": True}, {"session_id": "app#0"}))
    assert d.kind == "execute"
    d = await authorize(harness, controller.command("app.launch", {"app_id": "x"}))
    assert d.error.code == "PC_SESSION_LOCKED_MEDIA_ONLY"


async def test_youtube_target_resolution(harness: AgentHarness, controller: Controller) -> None:
    tab = FakeTab(tab_id=7)
    detached = FakeTab(tab_id=8, script_attached=False)
    ext = await harness.connect_extension(tab, detached)
    good = {"browser_instance_id": ext.browser_instance_id, "tab_id": 7, "tab_token": tab.tab_token}
    d = await authorize(harness, controller.command("youtube.get_state", {}, good))
    assert d.kind == "execute"
    d = await authorize(harness, controller.command("youtube.get_state", {}, {**good, "tab_id": 99}))
    assert d.error.code == "TARGET_GONE"
    d = await authorize(harness, controller.command("youtube.get_state", {}, {**good, "browser_instance_id": "bi_other001"}))
    assert d.error.code == "TARGET_GONE"
    d = await authorize(harness, controller.command("youtube.get_state", {}, {**good, "tab_token": "B" * 22}))
    assert d.error.code == "TARGET_CHANGED"
    d = await authorize(harness, controller.command("youtube.get_state", {}, {**good, "expected_video_id": "otherVideo1"}))
    assert d.error.code == "TARGET_CHANGED"
    d = await authorize(harness, controller.command("youtube.get_state", {}, {"browser_instance_id": ext.browser_instance_id, "tab_id": 8, "tab_token": "B" * 22}))
    assert d.error.code == "TAB_NOT_CONTROLLABLE"
    d = await authorize(harness, controller.command("youtube.get_state", {}, None))
    assert d.error.code == "TARGET_REQUIRED"


async def test_app_not_approved(harness: AgentHarness, controller: Controller) -> None:
    d = await authorize(harness, controller.command("app.launch", {"app_id": "notepad"}))
    assert d.kind == "rejected" and d.error.code == "APP_NOT_APPROVED" and d.journaled
    d = await authorize(harness, controller.command("app.focus", {}, {"app_id": "notepad"}))
    assert d.error.code == "APP_NOT_APPROVED"


async def test_invalid_params_and_unknown_action(harness: AgentHarness, controller: Controller) -> None:
    d = await authorize(harness, controller.command("windows.set_volume", {"value": 101}))
    assert d.error.code == "INVALID_PARAMETERS"
    d = await authorize(harness, controller.command("windows.set_volume", {"value": 10, "extra": 1}))
    assert d.error.code == "INVALID_PARAMETERS"
    d = await authorize(harness, controller.command("windows.explode"))
    assert d.error.code == "UNKNOWN_ACTION"


async def test_routine_step_requires_entitlement(harness: AgentHarness, controller: Controller) -> None:
    origin = {"kind": "routine", "routine_id": str(uuid.uuid4()), "step": 0}
    d = await authorize(harness, controller.command("windows.set_volume", {"value": 10}, origin=origin))
    assert d.kind == "rejected" and d.error.code == "ENTITLEMENT_REQUIRED"
    d = await authorize(harness, controller.command("youtube.next", {}, {"browser_instance_id": "bi_fake0001", "tab_id": 1, "tab_token": "A" * 22}, origin=origin))
    assert d.error.code in ("ENTITLEMENT_REQUIRED", "EXTENSION_DISCONNECTED")
    harness.agent.entitlement._jwks = harness.api.jwks()  # noqa: SLF001
    harness.agent.entitlement.apply_assertion(harness.api.make_assertion(plan="pro"))
    d = await authorize(harness, controller.command("windows.set_volume", {"value": 10}, origin=origin))
    assert d.kind == "execute"


async def test_disruptive_action_yields_confirmation(harness: AgentHarness, controller: Controller) -> None:
    env = controller.command("power.sleep", {"countdown_seconds": 0}, lifetime=90)
    d = await authorize(harness, env)
    assert d.kind == "confirm" and d.pending is not None
    assert [f["type"] for f in d.frames] == ["ack", "confirmation_required"]
    assert d.frames[0]["state"] == "awaiting_confirmation"
    row = harness.agent.store.journal_get(payload_of(env)["command_id"])
    assert row is not None and row.state == "awaiting_confirmation"
    # identical re-send replays both frames with the SAME challenge text
    dup = await authorize(harness, env)
    assert dup.kind == "duplicate" and dup.frames[1]["challenge_text"] == d.frames[1]["challenge_text"]


@pytest.mark.parametrize("state", ["created", "accepted"])
async def test_duplicate_while_queued_replays_accepted_ack(harness: AgentHarness, controller: Controller, state: str) -> None:
    env = controller.command("system.ping")
    cid = payload_of(env)["command_id"]
    await authorize(harness, env)
    harness.agent.store.journal_set_state(cid, state)
    dup = await authorize(harness, env)
    assert dup.kind == "duplicate" and dup.frames == [dup.frames[0]] and dup.frames[0]["state"] == "accepted"

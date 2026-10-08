"""End-to-end through fake relay + fake platform + fake extension: a Python-signed controller
command flows hello → snapshot → command → ack → result, plus the protocol edge cases."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from dome_protocol import load_schemas, now_utc

from dome_agent.testing.fake_extension import FakeTab

from .conftest import AgentHarness
from .helpers import Controller, payload_of, relay_command_frame


async def test_hello_snapshot_command_ack_result(harness: AgentHarness, controller: Controller) -> None:
    conn = harness.relay.current
    assert conn is not None and conn.hello is not None
    assert conn.hello["component"] == "agent" and conn.hello["protocol_versions"] == ["1.0"]
    # a state frame followed the snapshot (rules.state_cache)
    tab = FakeTab(tab_id=5)
    ext = await harness.connect_extension(tab)
    env = controller.command(
        "youtube.next",
        {},
        {
            "browser_instance_id": ext.browser_instance_id,
            "tab_id": 5,
            "tab_token": tab.tab_token,
            "expected_video_id": tab.video_id,
        },
    )
    cid = payload_of(env)["command_id"]
    await harness.send_command(env)
    ack1 = await harness.ack(cid)
    ack2 = await harness.ack(cid)
    assert (ack1["state"], ack2["state"]) == ("accepted", "executing")
    res = await harness.result(cid)
    assert res["origin"] == "agent" and res["state"] == "succeeded"
    assert res["result"]["previous_video_id"] == "dQw4w9WgXcQ"
    assert res["result"]["tab"]["video_id"] == tab.video_id != "dQw4w9WgXcQ"
    load_schemas().validate_frame("agent_to_relay", res)
    assert len(ext.requests) == 1  # exactly one op reached the browser
    # state frame reflects the new tab state (debounced)
    state = await harness.relay.expect("state", timeout=5)
    assert state["state"]["extension_connected"] is True and state["state"]["platform"] == "development"


async def test_duplicate_identical_reemits_result(harness: AgentHarness, controller: Controller) -> None:
    env = controller.command("windows.set_volume", {"value": 42})
    cid = payload_of(env)["command_id"]
    await harness.send_command(env)
    first = await harness.result(cid)
    assert first["state"] == "succeeded"
    await harness.send_command(env)  # identical bytes
    second = await harness.result(cid)
    assert second["result"] == first["result"] and second["at"] == first["at"]
    assert harness.fake.count("set_volume") == 1


async def test_same_id_different_bytes_is_rejected(harness: AgentHarness, controller: Controller) -> None:
    env = controller.command("windows.set_volume", {"value": 42})
    cid = payload_of(env)["command_id"]
    await harness.send_command(env)
    await harness.result(cid)
    await harness.send_command(controller.command("windows.set_volume", {"value": 43}, command_id=cid))
    res = await harness.result(cid)
    assert res["state"] == "failed" and res["error"]["code"] == "COMMAND_ID_REUSED"
    assert harness.fake.volume == 42


async def test_expired_command(harness: AgentHarness, controller: Controller) -> None:
    env = controller.command("system.ping", issued_at=now_utc() - timedelta(seconds=60))
    cid = payload_of(env)["command_id"]
    await harness.send_command(env)
    res = await harness.result(cid)
    assert res["state"] == "failed" and res["error"]["code"] == "COMMAND_EXPIRED" and res["error"]["retryable"] is True


async def test_unknown_kid(harness: AgentHarness, controller: Controller) -> None:
    stranger = Controller(controller.account_id, controller.pc_id)
    env = stranger.command("system.ping")
    cid = payload_of(env)["command_id"]
    await harness.send_command(env)
    res = await harness.result(cid)
    assert res["state"] == "failed" and res["error"]["code"] == "UNKNOWN_KEY"
    assert harness.agent.store.journal_get(cid) is None
    assert any(e["kind"] == "command_rejected" for e in harness.agent.store.list_security_events())


async def test_snapshot_revocation_cancels_in_flight(harness: AgentHarness, controller: Controller) -> None:
    harness.fake.add_session("slow#0")
    original = harness.agent.services.platform.media.set_paused

    def slow(session_id: str, paused: bool) -> Any:
        import time

        time.sleep(1.0)
        return original(session_id, paused)

    harness.agent.services.platform.media.set_paused = slow  # type: ignore[method-assign]
    running = controller.command("media.set_paused", {"paused": True}, {"session_id": "slow#0"})
    queued = controller.command("system.ping")
    await harness.send_command(running)
    await harness.send_command(queued)
    await harness.ack(payload_of(running)["command_id"], state="executing")
    harness.relay.controllers.clear()  # the account revoked the phone
    await harness.relay.send_snapshot()
    r_running = await harness.result(payload_of(running)["command_id"])
    r_queued = await harness.result(payload_of(queued)["command_id"])
    assert r_queued["state"] == "canceled" and r_queued["error"]["code"] == "CONTROLLER_REVOKED"
    assert r_running["state"] == "canceled"
    # and the controller is refused from now on
    env = controller.command("system.ping")
    await harness.send_command(env)
    res = await harness.result(payload_of(env)["command_id"])
    assert res["error"]["code"] == "CONTROLLER_REVOKED"
    grant = harness.agent.store.get_grant(controller.controller_id)
    assert grant is not None and grant.revoked_reason == "snapshot_revocation"


async def test_snapshot_unknown_controller_is_ignored_and_logged(harness: AgentHarness, controller: Controller) -> None:
    ghost = Controller(controller.account_id, controller.pc_id, display_name="Ghost")
    harness.relay.controllers.append(ghost.snapshot_entry())
    await harness.relay.send_snapshot()
    await harness.relay.expect("state")
    assert harness.agent.store.get_grant(ghost.controller_id) is None
    assert any(
        e["kind"] == "snapshot_unknown_controller" and e["detail"]["controller_id"] == ghost.controller_id
        for e in harness.agent.store.list_security_events()
    )
    env = ghost.command("system.ping")
    await harness.send_command(env)
    res = await harness.result(payload_of(env)["command_id"])
    assert res["error"]["code"] == "UNKNOWN_KEY"
    # the legitimate controller is unaffected
    env = controller.command("system.ping")
    await harness.send_command(env)
    assert (await harness.result(payload_of(env)["command_id"]))["state"] == "succeeded"


async def test_snapshot_pc_disabled_refuses_but_keeps_grants(harness: AgentHarness, controller: Controller) -> None:
    harness.relay.pc_enabled = False
    await harness.relay.send_snapshot()
    await harness.relay.expect("state")
    env = controller.command("system.ping")
    await harness.send_command(env)
    res = await harness.result(payload_of(env)["command_id"])
    assert res["error"]["code"] == "PC_PLAN_DISABLED"
    grant = harness.agent.store.get_grant(controller.controller_id)
    assert grant is not None and not grant.revoked


async def test_mismatch_handling_three_strikes_reconnects(harness: AgentHarness, controller: Controller) -> None:
    before = harness.relay.connect_count
    for _ in range(3):
        env = controller.command("system.ping", target_pc_id=str(uuid.uuid4()))
        cid = payload_of(env)["command_id"]
        await harness.send_command(env)
        res = await harness.result(cid)
        assert res["state"] == "failed" and res["error"]["code"] == "TARGET_PC_MISMATCH"
        assert harness.agent.store.journal_get(cid) is None  # not journaled
    await harness.relay.wait_connected(timeout=15)
    assert harness.relay.connect_count == before + 1
    events = [e["kind"] for e in harness.agent.store.list_security_events()]
    assert "mismatch_storm_reconnect" in events and events.count("command_identity_mismatch") >= 3
    await harness.wait_snapshot_applied()


async def test_command_before_snapshot_is_pc_reconnecting(
    settings: Any, fake_api: Any, fake_relay: Any, fake_state: Any, controller: Controller
) -> None:
    from dome_agent.agent import Agent
    from dome_agent.store import Store
    from dome_agent.testing.fake_platform import build_fake_platform

    from .helpers import link_identity

    cred = fake_api.issue_credential()
    link_identity(
        settings.state_dir,
        fake_api.pc_id,
        fake_api.account_id,
        credential=cred,
        api_url=fake_api.url,
        relay_url=fake_relay.url,
    )
    st = Store(settings.db_path)
    st.set_remote_enabled(True)
    controller.grant_locally(st)
    st.close()
    fake_relay.auto_snapshot = False
    fake_relay.controllers.append(controller.snapshot_entry())
    agent = Agent(settings, platform=build_fake_platform(fake_state))
    await agent.start()
    try:
        await fake_relay.wait_connected()
        env = controller.command("system.ping")
        cid = payload_of(env)["command_id"]
        await fake_relay.send(relay_command_frame(env))
        res = await fake_relay.expect("result", command_id=cid)
        assert res["state"] == "failed" and res["error"]["code"] == "PC_RECONNECTING"
        assert agent.store.journal_get(cid) is None
        await fake_relay.send_snapshot()
        await fake_relay.expect("state")
        await fake_relay.send(relay_command_frame(env))
        res = await fake_relay.expect("result", command_id=cid)
        assert res["state"] == "succeeded"
    finally:
        await agent.stop()


async def test_late_result_resent_after_reconnect(harness: AgentHarness, controller: Controller) -> None:
    harness.fake.add_session("slow#0")
    original = harness.agent.services.platform.media.set_paused
    started = asyncio.Event()

    def slow(session_id: str, paused: bool) -> Any:
        import time

        started.set()
        time.sleep(1.5)
        return original(session_id, paused)

    harness.agent.services.platform.media.set_paused = slow  # type: ignore[method-assign]
    env = controller.command("media.set_paused", {"paused": True}, {"session_id": "slow#0"})
    cid = payload_of(env)["command_id"]
    queued = controller.command("system.ping")
    await harness.send_command(env)
    await harness.send_command(queued)
    await harness.ack(cid, state="executing")
    await harness.relay.close(1012, "deploy")  # connection drops while executing
    await harness.relay.wait_connected(timeout=15)
    await harness.wait_snapshot_applied()
    res = await harness.relay.expect("result", command_id=cid, timeout=15)
    assert res["state"] == "succeeded" and res["result"]["session"]["status"] == "paused"
    # the queued-but-not-started command was failed PC_OFFLINE in the journal and NOT replayed/re-sent
    row = harness.agent.store.journal_get(payload_of(queued)["command_id"])
    assert row is not None and row.state == "failed" and row.error_code == "PC_OFFLINE"
    assert harness.fake.count("media_set_paused") == 1
    queued_id = payload_of(queued)["command_id"]
    assert all(not (f["type"] == "result" and f.get("command_id") == queued_id) for f in harness.relay.drain())


async def test_cancel_frame_cancels_queued_and_awaiting(harness: AgentHarness, controller: Controller) -> None:
    env = controller.command("power.shutdown", {"countdown_seconds": 30}, lifetime=90)
    cid = payload_of(env)["command_id"]
    await harness.send_command(env)
    await harness.relay.expect("confirmation_required", command_id=cid)
    await harness.relay.send({"type": "cancel", "command_id": cid, "controller_id": controller.controller_id})
    res = await harness.result(cid)
    assert res["state"] == "canceled"
    # cancel for a wrong controller id is ignored
    env2 = controller.command("power.shutdown", {"countdown_seconds": 30}, lifetime=90)
    cid2 = payload_of(env2)["command_id"]
    await harness.send_command(env2)
    req = await harness.relay.expect("confirmation_required", command_id=cid2)
    await harness.relay.send({"type": "cancel", "command_id": cid2, "controller_id": str(uuid.uuid4())})
    await asyncio.sleep(0.2)
    assert harness.agent.store.journal_get(cid2).state == "awaiting_confirmation"  # type: ignore[union-attr]
    # armed power countdown can be canceled through the cancel frame too
    await harness.send_confirmation(controller.confirmation(cid2, req["challenge_text"]))
    await harness.ack(cid2, state="executing")
    state = await harness.relay.expect("state", timeout=5)
    assert state["state"]["pending_power_action"]["command_id"] == cid2
    await harness.relay.send({"type": "cancel", "command_id": cid2, "controller_id": controller.controller_id})
    res = await harness.result(cid2)
    assert res["state"] == "canceled" and res["error"]["code"] == "POWER_CANCELED"
    assert harness.fake.count("power_shutdown") == 0


async def test_local_disable_wins_over_remote(harness: AgentHarness, controller: Controller) -> None:
    env = controller.command("power.sleep", {"countdown_seconds": 30}, lifetime=90)
    cid = payload_of(env)["command_id"]
    await harness.send_command(env)
    req = await harness.relay.expect("confirmation_required", command_id=cid)
    await harness.send_confirmation(controller.confirmation(cid, req["challenge_text"]))
    await harness.ack(cid, state="executing")
    await harness.agent.set_remote_enabled(False)  # tray "Disable remote control"
    res = await harness.result(cid)
    assert res["state"] == "canceled" and res["error"]["code"] == "POWER_CANCELED"
    state = await harness.relay.expect("state", timeout=5)
    assert state["state"]["remote_enabled"] is False
    env = controller.command("system.ping")
    await harness.send_command(env)
    res = await harness.result(payload_of(env)["command_id"])
    assert res["error"]["code"] == "PC_REMOTE_DISABLED"
    # no relay frame can turn it back on; a new snapshot does not either
    await harness.relay.send_snapshot()
    await harness.relay.expect("state")
    assert harness.agent.store.remote_enabled is False
    await harness.agent.set_remote_enabled(True)
    env = controller.command("system.ping")
    await harness.send_command(env)
    assert (await harness.result(payload_of(env)["command_id"]))["state"] == "succeeded"


async def test_local_revocation_sends_revoke_controller(harness: AgentHarness, controller: Controller) -> None:
    assert await harness.agent.revoke_controller_locally(controller.controller_id)
    frame = await harness.relay.expect("revoke_controller")
    assert (
        frame["controller_id"] == controller.controller_id
        and frame["kid"] == controller.kid
        and frame["reason"] == "local_revocation"
    )
    env = controller.command("system.ping")
    await harness.send_command(env)
    assert (await harness.result(payload_of(env)["command_id"]))["error"]["code"] == "CONTROLLER_REVOKED"


async def test_superseded_4001_requires_manual_reconnect(harness: AgentHarness, controller: Controller) -> None:
    before = harness.relay.connect_count
    await harness.relay.close(4001, "superseded")
    for _ in range(100):
        if harness.agent.relay.state == "superseded":  # type: ignore[union-attr]
            break
        await asyncio.sleep(0.05)
    assert harness.agent.relay.state == "superseded"  # type: ignore[union-attr]
    await asyncio.sleep(1.5)
    assert harness.relay.connect_count == before  # no automatic reconnect
    harness.agent.relay.request_reconnect()  # type: ignore[union-attr]
    await harness.relay.wait_connected(timeout=10)
    await harness.wait_snapshot_applied()
    assert harness.relay.connect_count == before + 1


async def test_revoked_frame_discards_credential(harness: AgentHarness) -> None:
    assert harness.agent.identity.read_credential() is not None
    await harness.relay.send({"type": "revoked", "reason": "pc_unlinked"})
    for _ in range(100):
        if harness.agent.relink_required:
            break
        await asyncio.sleep(0.05)
    assert harness.agent.relink_required and harness.agent.relink_reason == "revoked"
    assert harness.agent.identity.read_credential() is None
    assert harness.agent.store.list_grants() != []  # local grants kept for inspection
    assert harness.agent.relay.state == "stopped"  # type: ignore[union-attr]


async def test_identity_mismatch_in_snapshot_stops(harness: AgentHarness) -> None:
    harness.relay.snapshot_pc_id = str(uuid.uuid4())
    await harness.relay.send_snapshot()
    for _ in range(100):
        if harness.agent.relink_required:
            break
        await asyncio.sleep(0.05)
    assert harness.agent.relink_required and harness.agent.relink_reason == "identity_mismatch"
    assert harness.agent.identity.read_credential() is not None  # not discarded: the relay misbehaved, not the account


async def test_crash_recovery_reports_outcome_unknown_once(
    settings: Any, fake_api: Any, fake_relay: Any, fake_state: Any, controller: Controller
) -> None:
    """Simulated crash: a journal row is left in `executing`; the restarted agent re-sends outcome_unknown
    as a late correction and never re-executes."""
    from dome_agent.agent import Agent
    from dome_agent.store import Store
    from dome_agent.testing.fake_platform import build_fake_platform

    from .helpers import link_identity

    cred = fake_api.issue_credential()
    link_identity(
        settings.state_dir,
        fake_api.pc_id,
        fake_api.account_id,
        credential=cred,
        api_url=fake_api.url,
        relay_url=fake_relay.url,
    )
    st = Store(settings.db_path)
    st.set_remote_enabled(True)
    controller.grant_locally(st)
    crashed = str(uuid.uuid4())
    st.journal_insert(
        command_id=crashed, digest="d", action="youtube.next", controller_id=controller.controller_id, state="executing"
    )
    st.close()
    fake_relay.controllers.append(controller.snapshot_entry())
    agent = Agent(settings, platform=build_fake_platform(fake_state))
    await agent.start()
    try:
        await fake_relay.wait_connected()
        res = await fake_relay.expect("result", command_id=crashed)
        assert res["state"] == "outcome_unknown" and res["error"]["code"] == "OUTCOME_UNKNOWN" and res["warning"]
        assert fake_state.calls == []
    finally:
        await agent.stop()

"""Relay lifecycle: agent disconnect → outcome_unknown / PC_OFFLINE, in-flight deadline sweeper,
late-result correction, revocation (REST and agent-initiated) closing sockets and re-snapshotting,
superseding agent connections, plan-disabled PC, PC unlink."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from tests.conftest import AgentSim, Browser, ControllerSim, Env, close_code, expect_nothing


async def test_agent_disconnect_terminates_inflight_commands(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    await paired.subscribe(online_agent.pc_id)
    assert (await paired.recv())["type"] == "pc_status"
    executing = await paired.command(online_agent.pc_id, "system.ping")
    accepted_only = await paired.command(online_agent.pc_id, "system.ping")
    await online_agent.recv_type("command")
    await online_agent.recv_type("command")
    await online_agent.ack(executing, "executing")
    await online_agent.ack(accepted_only, "accepted")
    assert (await paired.recv_type("ack", command_id=executing))["state"] == "executing"
    await paired.recv_type("ack", command_id=accepted_only)
    await online_agent.abort()  # crash, no close handshake
    frames: dict[str, Any] = {}
    for _ in range(3):
        f = await paired.recv()
        frames.setdefault(f["type"], []).append(f)
    results = {r["command_id"]: r for r in frames["result"]}
    assert (
        results[executing]["state"] == "outcome_unknown"
        and results[executing]["origin"] == "relay"
        and results[executing]["error"]["code"] == "OUTCOME_UNKNOWN"
    )
    assert results[accepted_only]["state"] == "failed" and results[accepted_only]["error"]["code"] == "PC_OFFLINE"
    assert frames["pc_status"][0]["connection"] == "offline"
    rows = {r["command_id"]: r for r in (await alice.get("/v1/commands"))["commands"]}
    assert rows[executing]["state"] == "outcome_unknown" and rows[accepted_only]["state"] == "failed"
    kinds = [e["kind"] for e in (await alice.get("/v1/account/security-events?limit=20"))["events"]]
    assert "agent_disconnected" in kinds and "agent_connected" in kinds
    pcs = (await alice.get("/v1/pcs"))["pcs"]
    assert pcs[0]["connection"] == "offline" and pcs[0]["last_seen"] is not None


async def test_late_result_corrects_outcome_unknown_exactly_once(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    cid = await paired.command(online_agent.pc_id, "system.ping")
    await online_agent.recv_type("command")
    await online_agent.ack(cid, "executing")
    await paired.recv_type("ack", command_id=cid)
    await online_agent.abort()
    unknown = await paired.recv_type("result", command_id=cid)
    assert unknown["state"] == "outcome_unknown"
    await online_agent.connect()
    await online_agent.result(cid, "succeeded", result=online_agent.ping_result(), duration_ms=900)
    correction = await paired.recv_type("result", command_id=cid)
    assert correction["origin"] == "agent" and correction["state"] == "succeeded"
    rows = {r["command_id"]: r for r in (await alice.get("/v1/commands"))["commands"]}
    assert rows[cid]["state"] == "succeeded" and rows[cid]["duration_ms"] == 900
    # a second late result for the same command is dropped
    await online_agent.result(cid, "failed", error={"code": "OS_ERROR", "message": "x", "retryable": False})
    await expect_nothing(paired.ws)  # type: ignore[arg-type]
    # a result for an unknown command is dropped as well
    await online_agent.result("00000000-0000-4000-8000-000000000001", "succeeded", result=online_agent.ping_result())
    await expect_nothing(paired.ws)  # type: ignore[arg-type]
    assert rows[cid]["state"] == "succeeded"


async def test_in_flight_deadline_sweeper(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    import uuid

    from dome_api.relay.router import compute_deadline

    not_acked = await paired.command(online_agent.pc_id, "system.ping")
    executing = await paired.command(online_agent.pc_id, "system.ping")
    await online_agent.recv_type("command")
    await online_agent.recv_type("command")
    await online_agent.ack(executing, "executing")
    await paired.recv_type("ack", command_id=executing)
    conn = env.services.relay.agents[uuid.UUID(online_agent.pc_id)]
    inf = conn.inflight[uuid.UUID(not_acked)]
    # deadline = max(expires_at, received + timeout_ms) + 10 s for a non-confirmed action
    assert dt.timedelta(seconds=39) <= inf.deadline - inf.received_at <= dt.timedelta(seconds=41)
    assert compute_deadline is not None
    past = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    for cid in (not_acked, executing):
        conn.inflight[uuid.UUID(cid)].deadline = past
    results = {}
    for _ in range(2):
        r = await paired.recv_type("result", timeout=5)
        results[r["command_id"]] = r
    assert results[not_acked]["state"] == "failed" and results[not_acked]["error"]["code"] == "COMMAND_EXPIRED"
    assert results[executing]["state"] == "outcome_unknown"
    assert not conn.inflight
    # the agent's eventual result for the expired one is dropped (post-terminal, not outcome_unknown)
    await online_agent.result(not_acked, "succeeded", result=online_agent.ping_result())
    await expect_nothing(paired.ws)  # type: ignore[arg-type]


async def test_power_countdown_is_exempt_from_deadline_until_fires_at(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    import uuid

    from dome_protocol import format_rfc3339, now_utc

    ctrl = ControllerSim(env, alice)
    await ctrl.pair(online_agent, ("status", "power"))
    await ctrl.connect()
    try:
        cid = await ctrl.command(online_agent.pc_id, "power.shutdown", {"countdown_seconds": 60}, lifetime=90)
        await online_agent.recv_type("command")
        await online_agent.ack(cid, "executing")
        await ctrl.recv_type("ack", command_id=cid)
        fires_at = now_utc() + dt.timedelta(seconds=120)
        await online_agent.state(
            pending_power_action={"action": "power.shutdown", "command_id": cid, "fires_at": format_rfc3339(fires_at)}
        )
        conn = env.services.relay.agents[uuid.UUID(online_agent.pc_id)]
        conn.inflight[uuid.UUID(cid)].deadline = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
        await expect_nothing(ctrl.ws, 1.0)  # type: ignore[arg-type]  # armed countdown: not swept
        assert uuid.UUID(cid) in conn.inflight
    finally:
        await ctrl.close()


async def test_rest_revocation_closes_sockets_and_resnapshots(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    second = ControllerSim(env, alice, name="Second")
    await second.pair(online_agent)
    assert {c["kid"] for c in (online_agent.snapshot or {})["controllers"]} == {paired.kid, second.kid}
    inflight = await paired.command(online_agent.pc_id, "system.ping")
    await online_agent.recv_type("command")
    await alice.delete(f"/v1/controllers/{paired.controller_id}")
    revoked = await paired.recv_type("revoked")
    assert revoked["reason"] == "controller_revoked"
    assert await close_code(paired.ws) == 4003  # type: ignore[arg-type]
    paired.ws = None
    cancel = await online_agent.recv_type("cancel", command_id=inflight)
    assert cancel["controller_id"] == paired.controller_id
    snap = await online_agent.recv_type("grants_snapshot")
    assert [c["kid"] for c in snap["controllers"]] == [second.kid]
    ctrls = {c["id"]: c["status"] for c in (await alice.get("/v1/controllers"))["controllers"]}
    assert ctrls[paired.controller_id] == "revoked" and ctrls[second.controller_id] == "active"
    # the revoked phone can neither bind nor command any more
    ack = await paired.connect()
    assert "controller_id" not in ack
    cid = await paired.command(online_agent.pc_id, "system.ping")
    r = await paired.recv_type("result", command_id=cid)
    assert r["error"]["code"] == "GRANT_MISSING"
    # idempotent
    await alice.delete(f"/v1/controllers/{paired.controller_id}")
    await second.close()


async def test_grant_revocation_is_scoped_to_one_pc(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    await alice.delete(f"/v1/grants/{paired.grant_id}")
    revoked = await paired.recv_type("revoked")
    assert revoked["reason"] == "grant_revoked" and revoked["pc_id"] == online_agent.pc_id
    assert await close_code(paired.ws) == 4003  # type: ignore[arg-type]
    paired.ws = None
    snap = await online_agent.recv_type("grants_snapshot")
    assert snap["controllers"] == []
    assert (await alice.get(f"/v1/pcs/{online_agent.pc_id}/grants"))["grants"] == []
    ctrl = next(c for c in (await alice.get("/v1/controllers"))["controllers"] if c["id"] == paired.controller_id)
    assert ctrl["status"] == "active"  # the controller itself survives; it just has no grant
    ack = await paired.connect()
    assert ack["controller_id"] == paired.controller_id
    cid = await paired.command(online_agent.pc_id, "system.ping")
    assert (await paired.recv_type("result", command_id=cid))["error"]["code"] == "GRANT_MISSING"


async def test_agent_initiated_revocation(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    await online_agent.send(
        {
            "type": "revoke_controller",
            "controller_id": paired.controller_id,
            "kid": paired.kid,
            "reason": "local_revocation",
        }
    )
    revoked = await paired.recv_type("revoked")
    assert revoked["reason"] == "grant_revoked" and revoked["pc_id"] == online_agent.pc_id
    assert await close_code(paired.ws) == 4003  # type: ignore[arg-type]
    paired.ws = None
    snap = await online_agent.recv_type("grants_snapshot")
    assert snap["controllers"] == []
    events = (await alice.get("/v1/account/security-events?limit=5"))["events"]
    assert any(e["kind"] == "grant_revoked" and e["actor"] == "pc" for e in events)
    # a revoke naming the wrong kid is ignored
    other = ControllerSim(env, alice)
    await online_agent.send(
        {
            "type": "revoke_controller",
            "controller_id": paired.controller_id,
            "kid": other.kid,
            "reason": "local_revocation",
        }
    )
    await expect_nothing(online_agent.ws)  # type: ignore[arg-type]


async def test_logout_closes_controller_sockets(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    await alice.request("POST", "/v1/auth/logout", expect=204)
    revoked = await paired.recv_type("revoked")
    assert revoked["reason"] == "session_ended"
    assert await close_code(paired.ws) == 4003  # type: ignore[arg-type]
    paired.ws = None
    try:
        await paired.connect()
    except Exception as exc:  # noqa: BLE001
        assert "401" in str(exc) or getattr(getattr(exc, "response", None), "status_code", None) == 401
    else:
        raise AssertionError("socket accepted with a revoked session")


async def test_new_agent_connection_supersedes_old(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    old_ws = online_agent.ws
    cid = await paired.command(online_agent.pc_id, "system.ping")
    await online_agent.recv_type("command")
    online_agent.ws = None
    await online_agent.connect()
    assert await close_code(old_ws) == 4001  # type: ignore[arg-type]
    r = await paired.recv_type("result", command_id=cid)
    assert r["state"] == "failed" and r["error"]["code"] == "PC_OFFLINE"
    cid = await paired.command(online_agent.pc_id, "system.ping")
    assert (await online_agent.serve_one())["command_id"] == cid
    assert (await paired.recv_type("result", command_id=cid))["state"] == "succeeded"


async def test_plan_disabled_pc_is_refused_but_keeps_grants(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    await paired.subscribe(online_agent.pc_id)
    assert (await paired.recv())["type"] == "pc_status"
    body = await alice.patch(f"/v1/pcs/{online_agent.pc_id}", {"enabled": False, "name": "Renamed"}, schema="pc")
    assert body["enabled"] is False and body["name"] == "Renamed"
    snap = await online_agent.recv_type("grants_snapshot")
    assert snap["pc_enabled"] is False and [c["kid"] for c in snap["controllers"]] == [paired.kid]  # grants stay
    status = await paired.recv_type("pc_status")
    assert status["enabled"] is False and status["connection"] == "online"
    cid = await paired.command(online_agent.pc_id, "system.ping")
    r = await paired.recv_type("result", command_id=cid)
    assert r["error"]["code"] == "PC_PLAN_DISABLED"
    await expect_nothing(online_agent.ws)  # type: ignore[arg-type]
    await alice.patch(f"/v1/pcs/{online_agent.pc_id}", {"enabled": True}, schema="pc")
    assert (await online_agent.recv_type("grants_snapshot"))["pc_enabled"] is True
    await paired.recv_type("pc_status", enabled=True)
    cid = await paired.command(online_agent.pc_id, "system.ping")
    assert (await online_agent.serve_one())["command_id"] == cid


async def test_unlink_pc_revokes_everything(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    await paired.subscribe(online_agent.pc_id)
    assert (await paired.recv())["type"] == "pc_status"
    await alice.delete(f"/v1/pcs/{online_agent.pc_id}")
    revoked = await online_agent.recv_type("revoked")
    assert revoked["reason"] == "pc_unlinked"
    assert await close_code(online_agent.ws) == 4003  # type: ignore[arg-type]
    online_agent.ws = None
    # the subscribed phone is told, once, that the PC is gone and its subscription is dropped
    final = await paired.recv_type("pc_status", pc_id=online_agent.pc_id)
    assert final["connection"] == "offline" and final["enabled"] is False
    import uuid as _uuid

    assert _uuid.UUID(online_agent.pc_id) not in env.services.relay.subs
    assert (await alice.get("/v1/pcs"))["pcs"] == []
    r = await online_agent.rest("POST", "/v1/agent/token", {"pc_credential": online_agent.credential}, expect=401)
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"
    cid = await paired.command(online_agent.pc_id, "system.ping")
    r2 = await paired.recv_type("result", command_id=cid)
    assert r2["error"]["code"] == "ACCOUNT_MISMATCH"
    # the old access token no longer opens a socket
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    try:
        await connect(env.ws_base + "/ws/agent", additional_headers={"Authorization": f"Bearer {online_agent.token}"})
    except InvalidStatus as exc:
        assert exc.response.status_code == 401
    else:
        raise AssertionError("unlinked PC token accepted")


async def test_startup_sweep_closes_rows_from_a_previous_process(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    import uuid

    from tests.conftest import sql

    cid = await paired.command(online_agent.pc_id, "system.ping")
    await online_agent.recv_type("command")
    conn = env.services.relay.agents[uuid.UUID(online_agent.pc_id)]
    conn.inflight.pop(uuid.UUID(cid))  # simulate a row orphaned by a crashed relay
    assert await env.services.relay.startup_sweep() >= 1
    assert sql(env.database_url, "SELECT state, error_code FROM commands WHERE id = %s", (cid,))[0] == (
        "expired",
        "COMMAND_EXPIRED",
    )
    await asyncio.sleep(0)

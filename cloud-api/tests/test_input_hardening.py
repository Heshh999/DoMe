"""Review fixes on the protocol 1.1 relay path: a sustained input_batch flood closes the socket with one
security event, per-controller RATE_LIMITED refusals are throttled like socket refusals, the owner of an input
session gets each lifecycle frame once even while subscribed, agent-reported owners need a live grant and are
kept in a bounded map, agent-side rejection rows are capped per PC, 1.0 subscribers get state frames without
the 1.1 pc_state fields, and PC_RECONNECTING for input batches before the agent's first snapshot."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

import pytest
from dome_protocol import dumps_compact, loads_strict
from websockets.exceptions import ConnectionClosed

from dome_api.plans import plan_for
from dome_api.relay.manager import MAX_AGENT_INPUT_SESSIONS, AgentConn, ConnectionManager
from dome_api.security.ratelimit import TokenBucketLimiter
from tests.conftest import (
    AgentSim,
    Browser,
    ControllerSim,
    Env,
    close_code,
    expect_nothing,
    new_input_session_id,
    sql,
)

INPUT_CAPS = ("status", "media", "volume", "pointer", "keyboard")
MOVE = {"type": "pointer_move", "dx": 5, "dy": -3}
FOREGROUND = {"process_name": "notepad.exe", "window_title": "Untitled - Notepad", "elevated": False}


async def _input_pair(env: Env, alice: Browser, agent: AgentSim, caps: tuple[str, ...] = INPUT_CAPS) -> ControllerSim:
    ctrl = ControllerSim(env, alice, name="Touchpad phone")
    await ctrl.pair(agent, caps)
    await ctrl.connect()
    return ctrl


def _wire(ctrl: ControllerSim, pc_id: str, sid: str, seq: int) -> str:
    return str(
        dumps_compact({"type": "input_batch", "pc_id": pc_id, "envelope": ctrl.input_envelope(pc_id, sid, seq, [MOVE])})
    )


async def _drain(sim: AgentSim | ControllerSim, sink: list[dict[str, Any]], stop: asyncio.Event) -> None:
    """Keep reading so the relay never blocks on a full client buffer; record every frame."""
    while not stop.is_set():
        try:
            sink.append(await sim.recv(timeout=0.2))
        except TimeoutError:
            continue
        except ConnectionClosed:
            return


def _security_kinds(env: Env, account_id: str, kind: str) -> list[tuple[Any, ...]]:
    return sql(
        env.database_url,
        "SELECT kind, detail FROM security_events WHERE account_id = %s AND kind = %s ORDER BY id",
        (account_id, kind),
    )


async def _await_security_kinds(
    env: Env, account_id: str, kind: str, at_least: int, timeout: float = 5.0
) -> list[tuple[Any, ...]]:
    """Security events are written after the frame is handled; wait for them instead of racing the write."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        rows = _security_kinds(env, account_id, kind)
        if len(rows) >= at_least or asyncio.get_running_loop().time() > deadline:
            return rows
        await asyncio.sleep(0.05)


# ----- finding 1: sustained input flood ------------------------------------------------------------


async def test_sustained_input_flood_closes_socket_with_one_security_event(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    """300 batches/s (7.5x the budget) for as long as the socket stays open: the relay forwards about the
    budget, answers RATE_LIMITED at most once per second, then closes 4000 with exactly one
    ``controller_throttled`` event after the refusal budget (2x the input rate, 5 s capacity) runs dry."""
    pc = online_agent.pc_id
    ctrl = await _input_pair(env, alice, online_agent)
    sid = new_input_session_id()
    stop = asyncio.Event()
    agent_frames: list[dict[str, Any]] = []
    ctrl_frames: list[dict[str, Any]] = []
    agent_task = asyncio.create_task(_drain(online_agent, agent_frames, stop))
    assert ctrl.ws is not None
    rate, seq, closed_after = 300.0, 0, None
    started = time.monotonic()
    try:
        while time.monotonic() - started < 30:
            due = int((time.monotonic() - started) * rate)
            try:
                while seq < due:
                    seq += 1
                    await ctrl.ws.send(_wire(ctrl, pc, sid, seq))
            except ConnectionClosed:
                closed_after = time.monotonic() - started
                break
            # read what the relay answered so far without blocking the send schedule
            try:
                while True:
                    ctrl_frames.append(await ctrl.recv(timeout=0.002))
            except TimeoutError:
                pass
            except ConnectionClosed:
                closed_after = time.monotonic() - started
                break
        assert closed_after is not None, "the flood never closed the socket"
        # Sustained, not instantaneous. Signing every batch in this process caps how fast the test can flood,
        # so the deadline follows the rate actually reached: the socket bucket passes 80 + 40/s, the refusal
        # budget holds 400 and leaks 80/s, so at r batches/s it runs dry after 480 / (r - 120) s (~2.7 s at 300).
        achieved = seq / closed_after
        assert achieved > 150, f"only {achieved:.0f} batches/s: this machine cannot flood hard enough to test this"
        assert 1.0 <= closed_after <= 1.5 * 480 / (achieved - 120) + 1.0, (closed_after, achieved)
        try:
            await ctrl.ws.send(_wire(ctrl, pc, sid, seq + 1))
        except ConnectionClosed as exc:
            assert exc.rcvd is not None and exc.rcvd.code == 4000, exc
        else:
            assert await close_code(ctrl.ws) == 4000
        ctrl.ws = None
        limited = [f for f in ctrl_frames if f["type"] == "error" and f["error"]["code"] == "RATE_LIMITED"]
        assert 1 <= len(limited) <= int(closed_after) + 2, len(limited)  # one per second at most
        await asyncio.sleep(0.3)
        forwarded = sum(1 for f in agent_frames if f["type"] == "input_batch")
        assert forwarded <= 80 + 40 * (closed_after + 1), forwarded  # burst + sustained budget, no more
        throttled = await _await_security_kinds(env, alice.account_id, "controller_throttled", 1)
        assert len(throttled) == 1, throttled
        assert throttled[0][1]["reason"] == "input_flood" and throttled[0][1]["refused_frames"] > 400
    finally:
        stop.set()
        await agent_task
        await ctrl.close()


async def test_moderate_input_overshoot_keeps_the_socket(env: Env, alice: Browser, online_agent: AgentSim) -> None:
    """100 batches/s for 3 s (2.5x the budget, below the 2x refusal allowance plus the budget itself): refused
    batches are answered RATE_LIMITED once per second, the socket stays open and no throttling event is written."""
    pc = online_agent.pc_id
    ctrl = await _input_pair(env, alice, online_agent)
    sid = new_input_session_id()
    stop = asyncio.Event()
    agent_frames: list[dict[str, Any]] = []
    agent_task = asyncio.create_task(_drain(online_agent, agent_frames, stop))
    assert ctrl.ws is not None
    rate, seq = 100.0, 0
    started = time.monotonic()
    try:
        while time.monotonic() - started < 3:
            due = int((time.monotonic() - started) * rate)
            while seq < due:
                seq += 1
                await ctrl.ws.send(_wire(ctrl, pc, sid, seq))
            await asyncio.sleep(0.005)
        await asyncio.sleep(1.1)
        seq += 1
        await ctrl.ws.send(_wire(ctrl, pc, sid, seq))  # still open and still forwarding
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if any(
                f["type"] == "input_batch" and loads_strict(f["envelope"]["payload"])["seq"] == seq
                for f in agent_frames
            ):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("the batch after the overshoot was not forwarded")
        assert _security_kinds(env, alice.account_id, "controller_throttled") == []
    finally:
        stop.set()
        await agent_task
        await ctrl.close()


async def test_per_controller_rate_limited_errors_are_throttled_per_socket(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    """Two sockets of the same phone each pass their own socket bucket, so the per-controller input budget
    (burst 80) refuses about half of 160 batches; each socket hears RATE_LIMITED at most once per second."""
    pc = online_agent.pc_id
    first = await _input_pair(env, alice, online_agent)
    second = ControllerSim(env, alice, key=first.key, controller_id=first.controller_id)
    await second.connect()
    # The per-controller budget for this test only: same burst, no refill, so the outcome does not depend on
    # how fast this machine pushes 160 frames (with refill, a slow run never exhausted it).
    limiters = env.services.limiters
    free_plan = plan_for("free")
    saved_input = dict(limiters._input)  # noqa: SLF001
    limiters._input[free_plan.id] = TokenBucketLimiter(0, free_plan.input_rate_limit.burst)  # noqa: SLF001
    sid = new_input_session_id()
    wires = (
        [_wire(first, pc, sid, seq) for seq in range(1, 81)],
        [_wire(second, pc, sid, seq) for seq in range(1001, 1081)],
    )
    stop = asyncio.Event()
    agent_frames: list[dict[str, Any]] = []
    agent_task = asyncio.create_task(_drain(online_agent, agent_frames, stop))
    try:
        assert first.ws is not None and second.ws is not None
        for a, b in zip(*wires, strict=True):
            await first.ws.send(a)
            await second.ws.send(b)
        await asyncio.sleep(1.0)
        for sock in (first, second):
            errors: list[dict[str, Any]] = []
            with contextlib.suppress(TimeoutError):
                while True:
                    errors.append(await sock.recv(timeout=0.3))
            limited = [f for f in errors if f["type"] == "error" and f["error"]["code"] == "RATE_LIMITED"]
            assert len(limited) <= 2, len(limited)  # not one per refused batch
        forwarded = sum(1 for f in agent_frames if f["type"] == "input_batch")
        assert 80 <= forwarded < 160, forwarded
        rejected = await _await_security_kinds(env, alice.account_id, "input_rejected", 1)
        assert len(rejected) == 1 and rejected[0][1]["reason"] == "RATE_LIMITED"
    finally:
        limiters._input.clear()  # noqa: SLF001
        limiters._input.update(saved_input)  # noqa: SLF001
        stop.set()
        await agent_task
        await first.close()
        await second.close()


# ----- finding 4: subscribed owner receives each input_session once ---------------------------------


async def test_subscribed_owner_receives_each_input_session_frame_once(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    pc = online_agent.pc_id
    owner = await _input_pair(env, alice, online_agent)
    await owner.subscribe(pc)  # the PWA always subscribes to the PC it drives
    assert (await owner.recv())["type"] == "pc_status"
    watcher = ControllerSim(env, alice, name="Other phone")
    await watcher.pair(online_agent)
    await watcher.connect()
    await watcher.subscribe(pc)
    assert (await watcher.recv())["type"] == "pc_status"
    sid = new_input_session_id()
    try:
        started = await online_agent.input_session(sid, owner.controller_id or "", "started", "started")
        assert await owner.recv_type("input_session") == started
        assert await watcher.recv_type("input_session") == started
        await expect_nothing(owner.ws, 0.5)  # type: ignore[arg-type]  # no second copy via the subscription
        ended = await online_agent.input_session(sid, owner.controller_id or "", "ended", "stopped")
        assert await owner.recv_type("input_session") == ended
        assert await watcher.recv_type("input_session") == ended
        await expect_nothing(owner.ws, 0.5)  # type: ignore[arg-type]
    finally:
        await owner.close()
        await watcher.close()


# ----- finding 5: owners need a live grant, the map is bounded, agent rejections are capped -----------


async def test_input_session_owner_requires_live_grant_on_this_pc(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    owner = await _input_pair(env, alice, online_agent)
    # a controller of the account paired to nothing on this PC
    stranger = ControllerSim(env, alice, name="No grant here")
    second_pc = AgentSim(env)
    sql(env.database_url, "UPDATE accounts SET plan = 'pro' WHERE id = %s", (alice.account_id,))
    await second_pc.link(alice, "Second PC")
    await second_pc.connect()
    await stranger.pair(second_pc, INPUT_CAPS)
    await stranger.connect()
    try:
        sid = new_input_session_id()
        await online_agent.input_session(sid, stranger.controller_id or "", "started", "started")
        await expect_nothing(stranger.ws, 0.5)  # type: ignore[arg-type]
        await online_agent.input_ack(sid, 1)  # the owner was never learned: dropped
        await expect_nothing(stranger.ws, 0.4)  # type: ignore[arg-type]
        # a revoked controller cannot become an owner either
        await alice.delete(f"/v1/controllers/{owner.controller_id}")
        assert await close_code(owner.ws) == 4003  # type: ignore[arg-type]
        owner.ws = None
        await online_agent.input_session(new_input_session_id(), owner.controller_id or "", "started", "started")
        await asyncio.sleep(0.3)
        rejected = [
            d
            for _, d in _security_kinds(env, alice.account_id, "relay_frame_rejected")
            if d.get("frame") == "input_session"
        ]
        assert [d["reason"] for d in rejected] == ["no_live_grant", "no_live_grant"]
    finally:
        await owner.close()
        await stranger.close()
        await second_pc.close()


def test_agent_input_session_map_keeps_latest_per_controller_and_is_bounded() -> None:
    conn = AgentConn(None, uuid.uuid4(), uuid.uuid4())  # type: ignore[arg-type]  # no socket needed for the map
    a, b = uuid.uuid4(), uuid.uuid4()
    conn.remember_input_session("s1", a)
    conn.remember_input_session("s2", a)
    assert conn.input_sessions == {"s2": a}  # one live session per controller
    conn.remember_input_session("s3", b)
    assert conn.input_sessions == {"s2": a, "s3": b}
    for i in range(20):
        conn.remember_input_session(f"x{i}", uuid.uuid4())
    assert len(conn.input_sessions) == MAX_AGENT_INPUT_SESSIONS
    assert list(conn.input_sessions)[-1] == "x19"  # newest kept, oldest evicted


async def test_agent_rejection_events_are_capped_per_pc(
    env: Env, alice: Browser, bob: Browser, online_agent: AgentSim
) -> None:
    cap = env.settings.relay_security_events_per_connection_per_minute
    foreign = ControllerSim(env, bob)
    for _ in range(cap + 10):
        await online_agent.grant_update(str(uuid.uuid4()), foreign.kid, ["status", "pointer"])
    await online_agent.send({"type": "ping"})
    await online_agent.recv_type("pong")
    rejected = await _await_security_kinds(env, alice.account_id, "relay_frame_rejected", cap)
    throttled = await _await_security_kinds(env, alice.account_id, "relay_events_throttled", 1)
    assert len(rejected) == cap, len(rejected)
    assert len(throttled) == 1 and throttled[0][1]["first_suppressed"] == "relay_frame_rejected"


# ----- finding 6: 1.0 subscribers get state frames without 1.1 fields -------------------------------


async def test_protocol_1_0_subscribers_get_state_without_1_1_fields(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    pc = online_agent.pc_id
    current = await _input_pair(env, alice, online_agent)
    legacy = ControllerSim(env, alice, key=current.key, controller_id=current.controller_id)
    await legacy.connect(protocol_versions=("1.0",))
    try:
        await current.subscribe(pc)
        await legacy.subscribe(pc)
        assert (await current.recv())["type"] == "pc_status"
        assert (await legacy.recv())["type"] == "pc_status"
        sent = await online_agent.state(
            foreground_app=FOREGROUND,
            input_session={
                "controller_id": current.controller_id,
                "pointer": True,
                "keyboard": True,
                "lease_expires_at": "2026-10-09T10:00:03Z",
            },
            input_restricted=False,
        )
        live_new = await current.recv_type("state")
        live_old = await legacy.recv_type("state")
        assert live_new == sent  # 1.1 sockets: unchanged
        assert {"foreground_app", "input_session", "input_restricted"}.isdisjoint(live_old["state"])
        assert live_old["state"]["volume"] == sent["state"]["volume"] and live_old["at"] == sent["at"]
        # the cached frame a fresh 1.0 subscription receives is stripped the same way
        late = ControllerSim(env, alice, key=current.key, controller_id=current.controller_id)
        await late.connect(protocol_versions=("1.0",))
        await late.subscribe(pc)
        cached = await late.recv_type("state")
        assert {"foreground_app", "input_session", "input_restricted"}.isdisjoint(cached["state"])
        assert cached["at"] == sent["at"]
        await late.close()
        late_new = ControllerSim(env, alice, key=current.key, controller_id=current.controller_id)
        await late_new.connect()
        await late_new.subscribe(pc)
        assert (await late_new.recv_type("state"))["state"]["foreground_app"] == FOREGROUND
        await late_new.close()
    finally:
        await current.close()
        await legacy.close()


# ----- finding 7: PC_RECONNECTING between hello_ack and the first snapshot ---------------------------


async def test_input_batch_before_first_snapshot_is_pc_reconnecting(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    pc = online_agent.pc_id
    ctrl = await _input_pair(env, alice, online_agent)
    sid = new_input_session_id()
    release = asyncio.Event()
    entered = asyncio.Event()
    original = ConnectionManager.build_snapshot

    async def held_build_snapshot(self: ConnectionManager, db: Any, pc_row: Any) -> dict[str, Any]:
        if str(pc_row.id) == pc and not release.is_set():
            entered.set()
            await release.wait()
        return await original(self, db, pc_row)

    try:
        await online_agent.close()
        await asyncio.sleep(0.2)
        monkeypatch.setattr(ConnectionManager, "build_snapshot", held_build_snapshot)
        ack = await online_agent.connect(expect_snapshot=False)
        assert ack["type"] == "hello_ack"
        await asyncio.wait_for(entered.wait(), 5)  # registered, snapshot not yet sent
        await ctrl.input_batch(pc, sid, 1, [MOVE])
        err = await ctrl.recv_type("error")
        assert err["error"]["code"] == "PC_RECONNECTING" and err["ref_pc_id"] == pc
        release.set()
        assert (await online_agent.recv_type("grants_snapshot"))["pc_id"] == pc
        await ctrl.input_batch(pc, sid, 2, [MOVE])
        assert loads_strict((await online_agent.recv_type("input_batch"))["envelope"]["payload"])["seq"] == 2
    finally:
        release.set()
        await ctrl.close()

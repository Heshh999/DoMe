"""The relay stamps an input batch the moment it arrives on the socket, not when its turn in the loop comes, so the
agent's relay-leg age sees the relay's own queue (DECISIONS #112); a batch that waited the whole age budget behind a
newer one dies at the relay unrouted while the newest never does; frames keep their order through the read-ahead; and
the ``relay.input_timing`` line (DECISIONS #113): once per second per streaming socket, counts and milliseconds only."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from collections.abc import MutableMapping, Sequence
from typing import Any

import pytest
from dome_protocol import loads_strict, now_utc, parse_rfc3339
from starlette.websockets import WebSocketDisconnect
from structlog.testing import capture_logs

from dome_api.logging import redact
from dome_api.relay import controller_ws, router
from dome_api.relay.controller_ws import InboundFrame, Inbox
from dome_api.relay.input_timing import InputTiming
from tests.conftest import AgentSim, Browser, ControllerSim, Env, expect_nothing, new_input_session_id

INPUT_CAPS = ("status", "media", "volume", "pointer", "keyboard")
MOVE = {"type": "pointer_move", "dx": 5, "dy": -3}
TEXT = {"type": "text", "text": "hello"}
TIMING_KEYS = {
    "event",
    "log_level",
    "controller_id",
    "pc_id",
    "window_ms",
    "received",
    "forwarded",
    "dropped",
    "refused",
    "auth_hits",
    "auth_misses",
    "queued_max",
    "auth_max_ms",
    "auth_mean_ms",
    "verify_max_ms",
    "verify_mean_ms",
    "forward_max_ms",
    "forward_mean_ms",
    "lag_max_ms",
    "lag_mean_ms",
}


async def _input_pair(env: Env, alice: Browser, agent: AgentSim) -> ControllerSim:
    ctrl = ControllerSim(env, alice, name="Touchpad phone")
    await ctrl.pair(agent, INPUT_CAPS)
    await ctrl.connect()
    return ctrl


def _slow_route(monkeypatch: pytest.MonkeyPatch, delay: float) -> None:
    """Every input batch spends ``delay`` seconds in ``route_input_batch``, as on a relay that has fallen behind."""
    original = router.route_input_batch  # the loop calls it through controller_ws's namespace

    async def slow(*args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(delay)
        await original(*args, **kwargs)

    monkeypatch.setattr(controller_ws, "route_input_batch", slow)


def _seq(frame: dict[str, Any]) -> int:
    seq: int = loads_strict(frame["envelope"]["payload"])["seq"]
    return seq


def _timing_lines(logs: Sequence[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [entry for entry in logs if entry["event"] == "relay.input_timing"]


# ----- the stamp (DECISIONS #112) --------------------------------------------------------------------


async def test_received_at_is_stamped_on_arrival_not_when_the_loop_gets_to_the_batch(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two batches sent back to back while the route takes 0.4 s: both are forwarded one after the other, 0.8 s in all,
    yet each carries the stamp of its arrival, batch 2's taken while batch 1 was still being routed."""
    pc = online_agent.pc_id
    ctrl = await _input_pair(env, alice, online_agent)
    delay = 0.4
    _slow_route(monkeypatch, delay)
    sid = new_input_session_id()
    try:
        sent_at = now_utc()
        await ctrl.input_batch(pc, sid, 1, [MOVE])
        await ctrl.input_batch(pc, sid, 2, [MOVE])
        first = await online_agent.recv_type("input_batch", timeout=5)
        second = await online_agent.recv_type("input_batch", timeout=5)
        forwarded_at = now_utc()
        assert [_seq(first), _seq(second)] == [1, 2]
        stamps = [parse_rfc3339(f["relay"]["received_at"]) for f in (first, second)]
        assert forwarded_at - sent_at >= dt.timedelta(seconds=2 * delay - 0.05)  # both went through the delay
        for stamp in stamps:  # but were stamped before it
            assert stamp - sent_at < dt.timedelta(seconds=delay / 2), (stamp, sent_at)
        assert stamps[1] - stamps[0] < dt.timedelta(seconds=delay / 2)
    finally:
        await ctrl.close()


async def test_a_batch_that_waited_the_age_budget_behind_a_newer_one_dies_at_the_relay(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four batches at once into a route that takes 0.5 s with a 0.2 s budget: 1 is routed at once; 2 and 3 have then
    waited 0.5 s with a newer batch behind them and are dropped unrouted, the phone hearing INPUT_STALE once; 4 waited
    as long but is the newest, so it reaches the PC. The sequence guard is untouched by the drops."""
    pc = online_agent.pc_id
    ctrl = await _input_pair(env, alice, online_agent)
    monkeypatch.setattr(env.services.limiters, "input_age_budget", 0.2)
    _slow_route(monkeypatch, 0.5)
    sid = new_input_session_id()
    try:
        for seq in range(1, 5):
            await ctrl.input_batch(pc, sid, seq, [MOVE])
        seqs = [_seq(await online_agent.recv_type("input_batch", timeout=5)) for _ in range(2)]
        assert seqs == [1, 4]
        assert online_agent.ws is not None
        await expect_nothing(online_agent.ws, 0.3)
        err = await ctrl.recv_type("error", timeout=5)
        assert err["error"]["code"] == "INPUT_STALE" and err["ref_pc_id"] == pc
        assert ctrl.ws is not None
        await expect_nothing(ctrl.ws, 0.5)  # the second drop within the same second said nothing more
        await ctrl.input_batch(pc, sid, 5, [MOVE])
        assert _seq(await online_agent.recv_type("input_batch", timeout=5)) == 5
        await ctrl.input_batch(pc, sid, 4, [MOVE])  # a replay of what was forwarded is still refused
        assert (await ctrl.recv_type("error", timeout=5))["error"]["code"] == "INPUT_SEQUENCE_INVALID"
    finally:
        await ctrl.close()


async def test_frames_keep_their_order_through_the_read_ahead(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command, a frame the parser refuses, a second batch and a ping queue behind a slow batch 1: the PC gets batch 1,
    the command and batch 2 in that order; the phone gets the parse error and the pong, and the socket stays open."""
    pc = online_agent.pc_id
    ctrl = await _input_pair(env, alice, online_agent)
    _slow_route(monkeypatch, 0.2)
    sid = new_input_session_id()
    try:
        await ctrl.input_batch(pc, sid, 1, [MOVE])
        await ctrl.command(pc, "system.ping")
        assert ctrl.ws is not None
        await ctrl.ws.send("not json")
        await ctrl.input_batch(pc, sid, 2, [MOVE])
        await ctrl.send({"type": "ping"})
        order: list[tuple[str, int | None]] = []
        for _ in range(3):
            frame = await online_agent.recv(timeout=5)
            order.append((frame["type"], _seq(frame) if frame["type"] == "input_batch" else None))
        assert order == [("input_batch", 1), ("command", None), ("input_batch", 2)]
        phone = [await ctrl.recv(timeout=5) for _ in range(2)]
        assert sorted(f["type"] for f in phone) == ["error", "pong"]
        assert next(f for f in phone if f["type"] == "error")["error"]["code"] == "MALFORMED_MESSAGE"
        await ctrl.input_batch(pc, sid, 3, [MOVE])
        assert _seq(await online_agent.recv_type("input_batch", timeout=5)) == 3
    finally:
        await ctrl.close()


async def test_inbox_keeps_order_bounds_its_depth_and_raises_the_readers_failure_last() -> None:
    inbox = Inbox(2)
    ping = InboundFrame({"type": "ping"}, now_utc(), 0.0)
    batch = InboundFrame({"type": "input_batch", "pc_id": "p", "envelope": {}}, now_utc(), 0.1)
    inbox.put(ping)
    assert not inbox.holds_input_batch()
    inbox.put(batch)
    assert inbox.holds_input_batch()
    with pytest.raises(TimeoutError):  # at the depth the reader waits for space
        await asyncio.wait_for(inbox.space(), 0.05)
    inbox.fail(WebSocketDisconnect(1000))
    assert await inbox.get() is ping  # the failure waits until what arrived before it is handled
    await asyncio.wait_for(inbox.space(), 1)
    assert await inbox.get() is batch
    assert not inbox.holds_input_batch()
    with pytest.raises(WebSocketDisconnect):
        await inbox.get()


# ----- relay.input_timing (DECISIONS #113) -----------------------------------------------------------


async def test_relay_input_timing_is_one_line_per_second_with_counts_and_milliseconds_only(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    """A burst of 20 batches writes no line of its own; the batch a second later writes one line for all of them; the
    line carries counts, cache hits and misses and per-stage milliseconds, nothing about the batches themselves, and
    the production redactor leaves it as it is. The window open when the socket closes is written out."""
    pc = online_agent.pc_id
    ctrl = await _input_pair(env, alice, online_agent)
    sid = new_input_session_id()
    try:
        with capture_logs() as logs:
            burst_started = time.monotonic()
            for seq in range(1, 21):
                await ctrl.input_batch(pc, sid, seq, [MOVE] if seq % 2 else [TEXT])
            for _ in range(20):
                await online_agent.recv_type("input_batch", timeout=5)
            burst_seconds = time.monotonic() - burst_started
            during_burst = len(_timing_lines(logs))
            assert during_burst <= int(burst_seconds) + 1, during_burst  # never per batch: at most one per second
            await asyncio.sleep(1.1)
            await ctrl.input_batch(pc, sid, 21, [MOVE])
            await online_agent.recv_type("input_batch", timeout=5)
            lines = _timing_lines(logs)
            assert len(lines) == during_burst + 1
            line = lines[-1]
            assert set(line) == TIMING_KEYS, sorted(line)
            assert line["log_level"] == "info"
            assert line["controller_id"] == ctrl.controller_id and line["pc_id"] == pc
            assert sum(entry["received"] for entry in lines) == 21
            assert sum(entry["forwarded"] for entry in lines) == 21
            assert all(entry["dropped"] == 0 and entry["refused"] == [] for entry in lines)
            assert sum(entry["auth_hits"] + entry["auth_misses"] for entry in lines) == 21
            assert sum(entry["auth_misses"] for entry in lines) >= 1  # the first batch read the database
            assert line["window_ms"] >= 1000 and line["queued_max"] >= 0
            for key in (k for k in TIMING_KEYS if k.endswith("_ms")):
                assert isinstance(line[key], int | float) and line[key] >= 0, (key, line[key])
            text = json.dumps(line, default=str)
            for forbidden in ("envelope", "payload", "hello", ctrl.kid, sid):
                assert forbidden not in text
            assert redact(line) == line
            # a window still open when the socket closes is written out
            await ctrl.input_batch(pc, sid, 22, [MOVE])
            await online_agent.recv_type("input_batch", timeout=5)
            before = len(_timing_lines(logs))
            await ctrl.close()
            deadline = time.monotonic() + 5
            while len(_timing_lines(logs)) == before and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            final = _timing_lines(logs)
            assert len(final) == before + 1 and final[-1]["received"] == final[-1]["forwarded"] == 1
    finally:
        await ctrl.close()


def test_input_timing_window_counts_and_is_rate_limited_by_the_interval() -> None:
    timing = InputTiming()
    assert not timing.flush(10.0, 1.0, controller_id="c")  # no batch yet: nothing to say
    timing.count(10.0, "pc-a")
    timing.route(0.0004, 3)
    timing.auth.add(0.004)
    timing.auth_misses += 1
    timing.verify.add(0.0012)
    timing.forward.add(0.0003)
    timing.forwarded += 1
    timing.count(10.5, "pc-a")
    timing.route(0.0002, 1)
    timing.auth.add(0.0002)
    timing.auth_hits += 1
    timing.verify.add(0.0008)
    timing.refuse("INPUT_STALE")
    timing.count(10.6, "pc-b")
    timing.dropped += 1
    timing.count(10.7, "pc-a")
    timing.refuse("RATE_LIMITED")
    timing.refuse("INPUT_STALE")
    assert not timing.flush(10.9, 1.0, controller_id="c")  # within the second
    with capture_logs() as logs:
        assert timing.flush(11.0, 1.0, controller_id="c")
    [line] = logs
    assert line["event"] == "relay.input_timing" and line["log_level"] == "info"
    assert line["controller_id"] == "c" and line["pc_id"] == "pc-a" and line["pcs"] == 2
    assert line["window_ms"] == 1000 and line["received"] == 4 and line["forwarded"] == 1 and line["dropped"] == 1
    assert line["refused"] == [{"reason": "INPUT_STALE", "count": 2}, {"reason": "RATE_LIMITED", "count": 1}]
    assert line["auth_hits"] == 1 and line["auth_misses"] == 1 and line["queued_max"] == 3
    assert line["auth_max_ms"] == 4.0 and line["auth_mean_ms"] == 2.1
    assert line["verify_max_ms"] == 1.2 and line["verify_mean_ms"] == 1.0
    assert line["forward_max_ms"] == 0.3 and line["forward_mean_ms"] == 0.3
    assert line["lag_max_ms"] == 0.4 and line["lag_mean_ms"] == 0.3
    # the next window opens with the next batch; a window in progress is written out when the socket closes
    assert not timing.flush(12.5, 1.0, controller_id="c")
    timing.count(12.6, "pc-a")
    timing.refuse("PC_OFFLINE")
    assert not timing.flush(12.7, 1.0, controller_id="c")
    with capture_logs() as logs:
        assert timing.flush(12.7, 1.0, controller_id="c", final=True)
    [line] = logs
    assert line["received"] == 1 and line["refused"] == [{"reason": "PC_OFFLINE", "count": 1}] and "pcs" not in line
    # interval 0 turns the line off, the final one included
    timing.count(20.0, "pc-a")
    with capture_logs() as logs:
        assert not timing.flush(30.0, 0.0, controller_id="c", final=True)
    assert logs == []
    # every field survives the production redactor, a refusal code that ends in _key included
    timing.refuse("UNKNOWN_KEY")
    summary = timing.summary(31.0)
    assert redact(summary) == summary

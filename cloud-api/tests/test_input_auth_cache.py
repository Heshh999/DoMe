"""The relay's per-socket input authorization cache (DECISIONS #110): a steady touchpad stream reads the controller,
plan, PC and grant rows at most once per ``relay_input_auth_ttl_seconds`` instead of for every batch; every change the
relay makes or hears about (grant_update, PATCH /pcs, unlink, plan selection and billing plan changes, another phone
binding after a downgrade, revocations) applies to the very next batch; a change made behind the relay's back (SQL)
applies within the TTL; a read that raced an invalidation is used once and never cached. Also the exact fast path for
validating ``input_batch`` frames against their own branch of the ``controller_to_relay`` union."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

import pytest
from dome_protocol import ProtocolError, loads_strict
from sqlalchemy import event

from dome_api.billing.service import BillingService
from dome_api.relay import router
from dome_api.relay.controller_ws import validate_inbound_frame
from dome_api.relay.manager import MAX_INPUT_AUTH_PCS, ControllerConn, InputAuthorization
from tests.conftest import (
    SCHEMAS,
    AgentSim,
    Browser,
    ControllerSim,
    Env,
    close_code,
    deliver,
    expect_nothing,
    fake_stripe,
    new_input_session_id,
    sql,
)
from tests.test_billing_lifecycle import subscribe, use_clock

INPUT_CAPS = ("status", "media", "volume", "pointer", "keyboard")
MOVE = {"type": "pointer_move", "dx": 5, "dy": -3}
TEXT = {"type": "text", "text": "hello"}
LONG_TTL = 3600.0  # only an invalidation can explain a refusal on the next batch


async def _input_pair(
    env: Env, browser: Browser, agent: AgentSim, name: str = "Touchpad phone", *, connect: bool = True
) -> ControllerSim:
    ctrl = ControllerSim(env, browser, name=name)
    await ctrl.pair(agent, INPUT_CAPS)
    if connect:
        await ctrl.connect()
    return ctrl


def _conns(env: Env, ctrl: ControllerSim) -> list[ControllerConn]:
    return [c for c in env.services.relay.controllers.values() if str(c.controller_id) == ctrl.controller_id]


async def _forwarded(ctrl: ControllerSim, agent: AgentSim, pc: str, sid: str, seq: int, events: Any = None) -> None:
    """Send one batch and require it at the PC; afterwards the socket holds an allowing cache entry for the PC."""
    await ctrl.input_batch(pc, sid, seq, events if events is not None else [MOVE])
    assert loads_strict((await agent.recv_type("input_batch"))["envelope"]["payload"])["seq"] == seq
    assert any(uuid.UUID(pc) in c.input_auth for c in _conns(agent.env, ctrl))


async def _refused(ctrl: ControllerSim, pc: str, sid: str, seq: int, code: str, events: Any = None) -> None:
    await ctrl.input_batch(pc, sid, seq, events if events is not None else [MOVE])
    err = await ctrl.recv_type("error")
    assert err["error"]["code"] == code and err["ref_pc_id"] == pc, err


def _count_reads(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    reads: list[float] = []
    original = router.load_input_authorization

    async def counted(*args: Any, **kwargs: Any) -> InputAuthorization:
        reads.append(time.monotonic())
        return await original(*args, **kwargs)

    monkeypatch.setattr(router, "load_input_authorization", counted)
    return reads


# ----- the steady stream ---------------------------------------------------------------------------


async def test_steady_input_stream_reads_the_database_at_most_once_per_ttl(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    """100 batches at the touchpad's 40/s: every one forwarded, the authorization rows read once per TTL at most, and
    the grant lookup (one per read, never one per batch) is the only one on the wire."""
    pc = online_agent.pc_id
    ttl = env.services.relay.input_auth_ttl
    assert ttl > 0
    ctrl = await _input_pair(env, alice, online_agent)
    reads = _count_reads(monkeypatch)
    grant_lookups: list[str] = []

    def on_execute(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        if "FROM grants" in statement:
            grant_lookups.append(statement)

    engine = env.services.engine.sync_engine
    event.listen(engine, "before_cursor_execute", on_execute)
    sid = new_input_session_id()
    total = 100
    seqs: list[int] = []

    async def drain() -> None:
        while len(seqs) < total:
            frame = await online_agent.recv_type("input_batch", timeout=10)
            seqs.append(loads_strict(frame["envelope"]["payload"])["seq"])

    try:
        drainer = asyncio.create_task(drain())
        started = time.monotonic()
        for seq in range(1, total + 1):
            await asyncio.sleep(max(0.0, started + (seq - 1) / 40 - time.monotonic()))
            await ctrl.input_batch(pc, sid, seq, [MOVE])
        await asyncio.wait_for(drainer, 15)
        elapsed = time.monotonic() - started
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
        await ctrl.close()
    assert seqs == list(range(1, total + 1))
    assert 1 <= len(reads) <= int(elapsed / ttl) + 1, (len(reads), elapsed)
    assert len(grant_lookups) <= len(reads), (len(grant_lookups), len(reads))


async def test_ttl_zero_reads_for_every_batch(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    pc = online_agent.pc_id
    monkeypatch.setattr(env.services.relay, "input_auth_ttl", 0.0)
    ctrl = await _input_pair(env, alice, online_agent)
    reads = _count_reads(monkeypatch)
    sid = new_input_session_id()
    try:
        for seq in range(1, 6):
            await _forwarded(ctrl, online_agent, pc, sid, seq)
    finally:
        await ctrl.close()
    assert len(reads) == 5


# ----- changes the relay makes or hears about apply to the next batch ------------------------------


async def test_grant_update_applies_to_the_next_batch(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    pc = online_agent.pc_id
    monkeypatch.setattr(env.services.relay, "input_auth_ttl", LONG_TTL)
    ctrl = await _input_pair(env, alice, online_agent)
    sid = new_input_session_id()
    try:
        await _forwarded(ctrl, online_agent, pc, sid, 1)
        # narrowed on the PC: keyboard stays, pointer goes
        await online_agent.grant_update(ctrl.controller_id or "", ctrl.kid, ["status", "keyboard"])
        await online_agent.recv_type("grants_snapshot")  # pushed after the relay committed and invalidated
        await _refused(ctrl, pc, sid, 2, "INPUT_NOT_PERMITTED")
        await _forwarded(ctrl, online_agent, pc, sid, 2, [TEXT])
        # widened again: pointer is back on the next batch
        await online_agent.grant_update(ctrl.controller_id or "", ctrl.kid, list(INPUT_CAPS))
        await online_agent.recv_type("grants_snapshot")
        await _forwarded(ctrl, online_agent, pc, sid, 3)
    finally:
        await ctrl.close()


async def test_pc_disable_and_unlink_apply_to_the_next_batch(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    pc = online_agent.pc_id
    monkeypatch.setattr(env.services.relay, "input_auth_ttl", LONG_TTL)
    ctrl = await _input_pair(env, alice, online_agent)
    sid = new_input_session_id()
    try:
        await _forwarded(ctrl, online_agent, pc, sid, 1)
        await alice.patch(f"/v1/pcs/{pc}", {"enabled": False}, schema="pc")
        await _refused(ctrl, pc, sid, 2, "PC_PLAN_DISABLED")
        await alice.patch(f"/v1/pcs/{pc}", {"enabled": True}, schema="pc")
        await _forwarded(ctrl, online_agent, pc, sid, 2)
        await alice.delete(f"/v1/pcs/{pc}")
        await _refused(ctrl, pc, sid, 3, "ACCOUNT_MISMATCH")
    finally:
        await ctrl.close()


async def _three_phones_after_a_downgrade(
    env: Env, alice: Browser, agent: AgentSim
) -> tuple[ControllerSim, list[ControllerSim]]:
    """Three phones paired while on Pro, then back on Free (two enabled: chosen ones first, then the most recently
    seen): the first one bound a socket, the other two have not yet."""
    sql(env.database_url, "UPDATE accounts SET plan = 'pro' WHERE id = %s", (alice.account_id,))
    ctrl = await _input_pair(env, alice, agent)
    others = [await _input_pair(env, alice, agent, f"Phone {i}", connect=False) for i in range(2)]
    sql(env.database_url, "UPDATE accounts SET plan = 'free' WHERE id = %s", (alice.account_id,))
    return ctrl, others


async def test_plan_selection_applies_to_the_next_batch(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Free allows two phones: the owner's selection in the downgrade window leaves the streaming phone out."""
    pc = online_agent.pc_id
    monkeypatch.setattr(env.services.relay, "input_auth_ttl", LONG_TTL)
    ctrl, others = await _three_phones_after_a_downgrade(env, alice, online_agent)
    sid = new_input_session_id()
    try:
        await _forwarded(ctrl, online_agent, pc, sid, 1)  # enabled: the most recently seen phone first
        sql(env.database_url, "UPDATE accounts SET plan_changed_at = now() WHERE id = %s", (alice.account_id,))
        choice = {"pc_ids": [pc], "controller_ids": [o.controller_id for o in others]}
        await alice.request("PUT", "/v1/account/plan-selection", choice, expect=200)
        await _refused(ctrl, pc, sid, 2, "CONTROLLER_PLAN_DISABLED")
    finally:
        await ctrl.close()


async def test_another_phone_binding_after_a_downgrade_applies_to_the_next_batch(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three phones on Free (two allowed, the most recently seen win): once the other two bind sockets, the phone that
    bound first drops out of the enabled set, and its next batch says so."""
    pc = online_agent.pc_id
    monkeypatch.setattr(env.services.relay, "input_auth_ttl", LONG_TTL)
    ctrl, others = await _three_phones_after_a_downgrade(env, alice, online_agent)
    sid = new_input_session_id()
    try:
        await _forwarded(ctrl, online_agent, pc, sid, 1)
        await others[0].connect()
        await _forwarded(ctrl, online_agent, pc, sid, 2)  # still one of the two most recently seen
        await others[1].connect()
        await _refused(ctrl, pc, sid, 3, "CONTROLLER_PLAN_DISABLED")
    finally:
        for sim in (ctrl, *others):
            await sim.close()


async def test_billing_downgrade_applies_to_the_next_batch(
    env: Env,
    billing: BillingService,
    alice: Browser,
    online_agent: AgentSim,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pro allows five phones, Free two: the subscription ends (webhook) and the least recently seen phone, streaming
    on a cached authorization, is refused on its next batch."""
    pc = online_agent.pc_id
    monkeypatch.setattr(env.services.relay, "input_auth_ttl", LONG_TTL)
    use_clock(billing)
    sub_id = await subscribe(env, billing, alice)
    ctrl = await _input_pair(env, alice, online_agent)
    others = [await _input_pair(env, alice, online_agent, f"Phone {i}") for i in range(2)]
    sid = new_input_session_id()
    try:
        await _forwarded(ctrl, online_agent, pc, sid, 1)  # three phones, all enabled on Pro
        await deliver(env, fake_stripe(billing).delete_subscription(sub_id))
        await _refused(ctrl, pc, sid, 2, "CONTROLLER_PLAN_DISABLED")
    finally:
        for sim in (ctrl, *others):
            await sim.close()


@pytest.mark.parametrize("how", ["grant", "controller", "pc_revoke_frame"])
async def test_revocations_close_every_streaming_socket_of_the_phone(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    pc = online_agent.pc_id
    monkeypatch.setattr(env.services.relay, "input_auth_ttl", LONG_TTL)
    ctrl = await _input_pair(env, alice, online_agent)
    tab = ControllerSim(env, alice, key=ctrl.key, controller_id=ctrl.controller_id)  # the same phone, second tab
    await tab.connect()
    sid = new_input_session_id()
    try:
        await _forwarded(ctrl, online_agent, pc, sid, 1)
        await _forwarded(tab, online_agent, pc, sid, 2)
        if how == "grant":
            await alice.delete(f"/v1/grants/{ctrl.grant_id}")
        elif how == "controller":
            await alice.delete(f"/v1/controllers/{ctrl.controller_id}")
        else:
            await online_agent.send(
                {
                    "type": "revoke_controller",
                    "controller_id": ctrl.controller_id,
                    "kid": ctrl.kid,
                    "reason": "local_revocation",
                }
            )
        for sim in (ctrl, tab):
            assert (await sim.recv_type("revoked"))["type"] == "revoked"
            assert await close_code(sim.ws) == 4003  # type: ignore[arg-type]
            sim.ws = None
        assert _conns(env, ctrl) == []
        await online_agent.recv_type("grants_snapshot")
        await expect_nothing(online_agent.ws, 0.3)  # type: ignore[arg-type]  # no batch after the revocation
    finally:
        await ctrl.close()
        await tab.close()


# ----- changes made behind the relay's back apply within the TTL -----------------------------------

# change -> (statement on one row, which row, the code of the first refusal)
BEHIND_THE_RELAY = {
    "grant_revoked": ("UPDATE grants SET revoked_at = now() WHERE id = %s", "grant", "GRANT_MISSING"),
    "grant_narrowed": (
        "UPDATE grants SET capabilities = '{status,keyboard}' WHERE id = %s",
        "grant",
        "INPUT_NOT_PERMITTED",
    ),
    "pc_disabled": ("UPDATE pcs SET enabled = false WHERE id = %s", "pc", "PC_PLAN_DISABLED"),
    "pc_deleted": ("UPDATE pcs SET deleted_at = now() WHERE id = %s", "pc", "ACCOUNT_MISMATCH"),
    "controller_revoked": (
        "UPDATE controllers SET revoked_at = now() WHERE id = %s",
        "controller",
        "CONTROLLER_REVOKED",
    ),
}


@pytest.mark.parametrize("change", sorted(BEHIND_THE_RELAY))
async def test_changes_behind_the_relay_apply_within_the_ttl(
    env: Env, alice: Browser, online_agent: AgentSim, change: str
) -> None:
    """Nothing tells the relay about a row edited directly in the database; the cached copy expires within the TTL and
    the stream is refused from then on."""
    pc = online_agent.pc_id
    ttl = env.services.relay.input_auth_ttl
    statement, which, code = BEHIND_THE_RELAY[change]
    ctrl = await _input_pair(env, alice, online_agent)
    sid = new_input_session_id()
    stop = asyncio.Event()

    async def drain_agent() -> None:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await online_agent.recv(timeout=0.1)

    drainer = asyncio.create_task(drain_agent())
    try:
        await ctrl.input_batch(pc, sid, 1, [MOVE])
        await asyncio.sleep(0.05)
        row = {"grant": ctrl.grant_id, "pc": pc, "controller": ctrl.controller_id}[which]
        sql(env.database_url, statement, (row,))
        changed_at = time.monotonic()
        seq, refused = 1, None
        while refused is None and time.monotonic() - changed_at < ttl + 2:
            seq += 1
            await ctrl.input_batch(pc, sid, seq, [MOVE])
            with contextlib.suppress(TimeoutError):
                frame = await ctrl.recv(timeout=0.05)
                if frame["type"] == "error":
                    refused = frame
        assert refused is not None and refused["error"]["code"] == code, refused
        assert time.monotonic() - changed_at <= ttl + 0.5
    finally:
        stop.set()
        await drainer
        await ctrl.close()


# ----- races and bounds ----------------------------------------------------------------------------


async def test_a_read_racing_an_invalidation_is_used_once_and_not_cached(
    env: Env, alice: Browser, online_agent: AgentSim, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rows were read before a change committed and the change's invalidation landed while the read was still in
    flight: the batch that triggered the read is routed on it (as without a cache), but the copy is not kept."""
    pc = online_agent.pc_id
    mgr = env.services.relay
    monkeypatch.setattr(mgr, "input_auth_ttl", LONG_TTL)
    ctrl = await _input_pair(env, alice, online_agent)
    sid = new_input_session_id()
    entered, release = asyncio.Event(), asyncio.Event()
    reads: list[float] = []
    original = router.load_input_authorization

    async def held(*args: Any, **kwargs: Any) -> InputAuthorization:
        auth = await original(*args, **kwargs)
        reads.append(time.monotonic())
        if len(reads) == 1:
            entered.set()
            await release.wait()
        return auth

    monkeypatch.setattr(router, "load_input_authorization", held)
    try:
        await ctrl.input_batch(pc, sid, 1, [MOVE])
        await asyncio.wait_for(entered.wait(), 5)
        mgr.invalidate_input_auth(pc_id=uuid.UUID(pc))
        release.set()
        assert loads_strict((await online_agent.recv_type("input_batch"))["envelope"]["payload"])["seq"] == 1
        assert all(uuid.UUID(pc) not in c.input_auth for c in _conns(env, ctrl))
        await _forwarded(ctrl, online_agent, pc, sid, 2)
        await _forwarded(ctrl, online_agent, pc, sid, 3)
        assert len(reads) == 2  # batch 2 read again; batch 3 used the copy batch 2 cached
    finally:
        release.set()
        await ctrl.close()


def test_controller_conn_input_auth_map_is_bounded_and_epoch_guarded() -> None:
    conn = ControllerConn(None, uuid.uuid4(), uuid.uuid4(), "kid")  # type: ignore[arg-type]  # no socket needed
    pcs = [uuid.uuid4() for _ in range(MAX_INPUT_AUTH_PCS + 2)]
    for i, pc in enumerate(pcs):
        conn.remember_input_auth(pc, InputAuthorization(float(i), None), conn.input_auth_epoch)
    assert list(conn.input_auth) == pcs[2:]  # oldest PCs evicted first
    assert conn.cached_input_auth(pcs[-1], float(len(pcs) - 1) + 0.5, 1.0) is not None
    assert conn.cached_input_auth(pcs[-1], float(len(pcs) - 1) + 1.0, 1.0) is None  # expired at the TTL
    assert conn.cached_input_auth(pcs[-1], float(len(pcs) - 1), 0.0) is None  # TTL 0: never served
    stale_epoch = conn.input_auth_epoch
    conn.forget_input_auth(pcs[-1])
    assert pcs[-1] not in conn.input_auth and len(conn.input_auth) == MAX_INPUT_AUTH_PCS - 1
    conn.remember_input_auth(pcs[-1], InputAuthorization(0.0, None), stale_epoch)  # read began before the forget
    assert pcs[-1] not in conn.input_auth
    conn.forget_input_auth()
    assert conn.input_auth == {}


# ----- the frame schema fast path ------------------------------------------------------------------


def test_controller_to_relay_union_is_discriminated_by_type() -> None:
    """What makes ``validate_inbound_frame`` exact: the union is a bare ``oneOf`` of local refs, and every branch
    requires ``type`` and pins it to a const of its own."""
    defs = SCHEMAS.document("relay-frames")["$defs"]
    union = defs["controller_to_relay"]
    assert set(union) == {"oneOf"}
    consts = []
    for branch in union["oneOf"]:
        assert set(branch) == {"$ref"} and branch["$ref"].startswith("#/$defs/")
        body = defs[branch["$ref"].removeprefix("#/$defs/")]
        assert "type" in body["required"]
        consts.append(body["properties"]["type"]["const"])
    assert len(consts) == len(set(consts)) and "input_batch" in consts
    assert {"$ref": "#/$defs/controller_input_batch"} in union["oneOf"]


async def test_input_batch_frames_validate_exactly_like_the_union(env: Env, alice: Browser) -> None:
    ctrl = ControllerSim(env, alice)
    pc = str(uuid.uuid4())
    envelope = ctrl.input_envelope(pc, new_input_session_id(), 1, [MOVE])
    good: dict[str, Any] = {"type": "input_batch", "pc_id": pc, "envelope": envelope}
    frames: list[Any] = [
        good,
        {**good, "extra": 1},
        {**good, "pc_id": pc.upper()},
        {k: v for k, v in good.items() if k != "envelope"},
        {**good, "envelope": {**envelope, "v": "1"}},
        {**good, "envelope": "not an envelope"},
        {"type": "input_batch"},
        {"type": "ping"},
        {"type": "nope", "pc_id": pc},
    ]
    for frame in frames:
        union_error: str | None = None
        try:
            env.services.schemas.validate_frame("controller_to_relay", frame)
        except ProtocolError as exc:
            union_error = f"{exc.code}: {exc.message}"
        fast_error: str | None = None
        try:
            validate_inbound_frame(env.services, frame)
        except ProtocolError as exc:
            fast_error = f"{exc.code}: {exc.message}"
        assert fast_error == union_error, frame
    with pytest.raises(ProtocolError):
        validate_inbound_frame(env.services, {**good, "extra": 1})
    validate_inbound_frame(env.services, good)

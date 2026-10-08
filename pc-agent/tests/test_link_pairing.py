from __future__ import annotations

import asyncio
from typing import Any

import pytest
from dome_protocol import (
    ProtocolError,
    kid_from_jwk,
    normalize_pairing_code,
    pairing_code_handle,
    pairing_verification_code,
)

from dome_agent.api import ApiClient
from dome_agent.identity import Identity
from dome_agent.link import LinkError, link_pc
from dome_agent.settings import Settings
from dome_agent.store import Store
from dome_agent.testing.fake_relay import FakeApi

from .conftest import AgentHarness
from .helpers import Controller, payload_of


async def test_link_flow(settings: Settings, store: Store, fake_api: FakeApi) -> None:
    identity = Identity(settings.state_dir)
    api = ApiClient(fake_api.url)
    shown: list[Any] = []

    async def approve_later() -> None:
        for _ in range(100):
            if shown:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(1.2)  # at least one authorization_pending poll first
        fake_api.approve_link(shown[0].user_code)

    approver = asyncio.create_task(approve_later())
    outcome = await link_pc(
        api,
        identity,
        store,
        platform="development",
        pc_name_hint="Desk PC",
        open_browser=False,
        present=shown.append,
        enable_remote=True,
        max_wait_seconds=30,
    )
    await approver
    assert outcome.pc_id == fake_api.pc_id and outcome.account_id == fake_api.account_id
    assert identity.is_linked and identity.pc_id == fake_api.pc_id
    assert identity.read_credential() in fake_api.credentials
    assert store.remote_enabled is True
    assert next(iter(fake_api.links.values()), None) is None  # consumed exactly once
    with pytest.raises(LinkError):
        await link_pc(
            api,
            identity,
            store,
            platform="development",
            pc_name_hint=None,
            open_browser=False,
            present=shown.append,
            enable_remote=True,
        )
    # secrets live in protected files, never in identity.json
    assert "pc_credential" not in (settings.state_dir / "identity.json").read_text()
    await api.close()


async def test_link_times_out(settings: Settings, store: Store, fake_api: FakeApi) -> None:
    identity = Identity(settings.state_dir)
    api = ApiClient(fake_api.url)
    with pytest.raises(LinkError, match="expired"):
        await link_pc(
            api,
            identity,
            store,
            platform="development",
            pc_name_hint=None,
            open_browser=False,
            present=lambda s: None,
            enable_remote=True,
            max_wait_seconds=2,
        )
    assert not identity.is_linked
    await api.close()


async def test_pairing_happy_path(harness: AgentHarness, fake_api: FakeApi) -> None:
    agent = harness.agent
    assert agent.pairing is not None
    session = await agent.pairing.start()
    assert fake_api.pairing_starts == [session.code_hash] and pairing_code_handle(session.code) == session.code_hash
    assert session.qr_url.startswith(fake_api.url + "/pair#code=") and len(normalize_pairing_code(session.code)) == 20
    phone = Controller(fake_api.account_id, fake_api.pc_id, display_name="Alice's iPhone")
    request = {
        "type": "pairing_request",
        "pairing_id": session.pairing_id,
        "code_hash": session.code_hash,
        "controller_display_name": phone.display_name,
        "public_jwk": phone.jwk,
        "kid": phone.kid,
        "requested_capabilities": ["status", "media", "volume"],
        "expires_at": session.expires_at,
    }
    await harness.relay.send(request)
    for _ in range(100):
        if agent.pairing.pending_requests():
            break
        await asyncio.sleep(0.02)
    pending = agent.pairing.pending_requests()[0]
    # the phone computes the same 6 digits from the code it received out of band
    assert pending.verification_code == pairing_verification_code(
        session.code, session.pairing_id, fake_api.pc_id, phone.kid
    )
    await agent.pairing.approve(session.pairing_id, ["status", "media"])
    decision = await harness.relay.expect("pairing_decision")
    assert decision == {
        "type": "pairing_decision",
        "pairing_id": session.pairing_id,
        "decision": "approve",
        "kid": phone.kid,
        "granted_capabilities": ["status", "media"],
    }
    grant = agent.store.get_grant_by_kid(phone.kid)
    assert (
        grant is not None and grant.capabilities == ("status", "media") and grant.controller_id.startswith("pending-")
    )
    # the relay now names the controller in a fresh snapshot → commands work
    harness.relay.controllers.append(phone.snapshot_entry(capabilities=("status", "media")))
    await harness.relay.send_snapshot()
    await harness.relay.expect("state")
    env = phone.command("system.ping")
    await harness.send_command(env)
    res = await harness.result(payload_of(env)["command_id"])
    assert res["state"] == "succeeded"
    env = phone.command("windows.set_volume", {"value": 1})
    await harness.send_command(env)
    assert (await harness.result(payload_of(env)["command_id"]))["error"]["code"] == "GRANT_MISSING"
    assert agent.pairing.session is None  # code is single use


async def test_pairing_request_mismatches_are_refused(harness: AgentHarness, fake_api: FakeApi) -> None:
    agent = harness.agent
    assert agent.pairing is not None
    session = await agent.pairing.start()
    phone = Controller(fake_api.account_id, fake_api.pc_id)
    base = {
        "type": "pairing_request",
        "pairing_id": session.pairing_id,
        "controller_display_name": "x",
        "public_jwk": phone.jwk,
        "kid": phone.kid,
        "requested_capabilities": ["status"],
        "expires_at": session.expires_at,
    }
    # wrong code hash → ignored (no decision, no pending request)
    await harness.relay.send({**base, "code_hash": pairing_code_handle("A" * 20)})
    await asyncio.sleep(0.2)
    assert agent.pairing.pending_requests() == []
    assert any(e["kind"] == "pairing_request_unmatched" for e in agent.store.list_security_events())
    # kid does not match the JWK → declined
    other = Controller(fake_api.account_id, fake_api.pc_id)
    await harness.relay.send({**base, "code_hash": session.code_hash, "kid": other.kid})
    decision = await harness.relay.expect("pairing_decision")
    assert decision["decision"] == "decline" and agent.store.get_grant_by_kid(other.kid) is None
    assert kid_from_jwk(phone.jwk) == phone.kid
    # decline path
    await harness.relay.send({**base, "code_hash": session.code_hash})
    for _ in range(100):
        if agent.pairing.pending_requests():
            break
        await asyncio.sleep(0.02)
    await agent.pairing.decline(session.pairing_id)
    decision = await harness.relay.expect("pairing_decision")
    assert decision["decision"] == "decline" and agent.store.get_grant_by_kid(phone.kid) is None
    with pytest.raises(ProtocolError):
        await agent.pairing.approve(session.pairing_id)


async def test_pairing_via_control_channel(harness: AgentHarness, fake_api: FakeApi) -> None:
    from dome_agent.control import ControlClient

    ctl = ControlClient(harness.settings.state_dir)
    started = await asyncio.to_thread(ctl.call, "pair_start")
    assert "code" in started and len(normalize_pairing_code(started["code"])) == 20
    phone = Controller(fake_api.account_id, fake_api.pc_id, display_name="Phone")
    await harness.relay.send(
        {
            "type": "pairing_request",
            "pairing_id": started["pairing_id"],
            "code_hash": pairing_code_handle(started["code"]),
            "controller_display_name": "Phone",
            "public_jwk": phone.jwk,
            "kid": phone.kid,
            "requested_capabilities": ["status", "media"],
            "expires_at": started["expires_at"],
        }
    )
    for _ in range(100):
        status = await asyncio.to_thread(ctl.call, "pair_status")
        if status["pending"]:
            break
        await asyncio.sleep(0.02)
    assert status["pending"][0]["verification_code"] == pairing_verification_code(
        started["code"], started["pairing_id"], fake_api.pc_id, phone.kid
    )
    approved = await asyncio.to_thread(ctl.call, "pair_approve", pairing_id=started["pairing_id"])
    assert approved["kid"] == phone.kid
    assert (await harness.relay.expect("pairing_decision"))["decision"] == "approve"
    # status never leaks the code
    status = await asyncio.to_thread(ctl.call, "status")
    assert started["code"] not in str(status)

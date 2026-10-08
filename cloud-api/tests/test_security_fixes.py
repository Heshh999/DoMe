"""Review fixes: trusted-proxy client addresses, per-socket frame budget and bounded security events,
pre-hello capacity accounting, user_code guessing limits and log masking, account-scoped command ids."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from dome_protocol import kid_from_jwk
from pydantic import ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from dome_api.plans import plan_for
from dome_api.security.headers import loggable_path
from dome_api.security.proxy import TrustedProxyMiddleware
from dome_api.security.ratelimit import SlidingWindowLimiter, TokenBucketLimiter
from dome_api.settings import Settings
from tests.conftest import AgentSim, Browser, ControllerSim, Env, close_code, sql

# ----- finding 1: X-Forwarded-For is only honoured from configured proxies --------------------------


async def test_forwarded_for_from_an_untrusted_peer_cannot_evade_per_ip_limits(env: Env, alice: Browser) -> None:
    svc = env.services
    original = svc.link_start_limiter
    svc.link_start_limiter = SlidingWindowLimiter(3, 3600)
    try:
        jwks: list[dict[str, str]] = []
        statuses = []
        for i in range(4):
            agent = AgentSim(env)
            jwks.append(agent.jwk)
            r = await env.http.post(
                "/v1/agent-link/start",
                json={"pc_public_jwk": agent.jwk, "agent_version": "t", "platform": "development"},
                headers={"X-Forwarded-For": f"203.0.113.{i + 1}", "Fly-Client-IP": f"198.51.100.{i + 1}"},
            )
            statuses.append(r.status_code)
        assert statuses == [200, 200, 200, 429], statuses
        # the three stored rows carry the hash of the real peer, not of the forged addresses
        hashes = {
            bytes(row[0])
            for row in sql(
                env.database_url,
                "SELECT requester_ip_hash FROM device_link_codes WHERE kid = ANY(%s)",
                ([kid_from_jwk(j) for j in jwks],),
            )
        }
        assert len(hashes) == 1
    finally:
        svc.link_start_limiter = original


async def _run_middleware(trusted: list[str], peer: str, headers: dict[str, str]) -> tuple[str | None, str]:
    seen: dict[str, Any] = {}

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen["client"] = scope.get("client")
        seen["scheme"] = scope.get("scheme")

    mw = TrustedProxyMiddleware(app, trusted=trusted)
    scope = {
        "type": "http",
        "scheme": "http",
        "client": (peer, 40000),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(_: Any) -> None:
        pass

    await mw(scope, receive, send)
    client = seen["client"]
    return (client[0] if client else None), str(seen["scheme"])


async def test_trusted_proxy_middleware_uses_the_hop_the_proxy_appended() -> None:
    xff = {"X-Forwarded-For": "1.2.3.4, 198.51.100.7", "X-Forwarded-Proto": "https"}
    # untrusted peer: headers ignored entirely
    assert await _run_middleware(["10.0.0.0/8"], "203.0.113.9", xff) == ("203.0.113.9", "http")
    # trusted peer: the right-most hop not itself a proxy wins, never the client-controlled left-most one
    assert await _run_middleware(["10.0.0.0/8"], "10.1.2.3", xff) == ("198.51.100.7", "https")
    chained = {"X-Forwarded-For": "1.2.3.4, 198.51.100.7, 10.9.9.9"}
    assert (await _run_middleware(["10.0.0.0/8", "fdaa::/16"], "10.1.2.3", chained))[0] == "198.51.100.7"
    assert (await _run_middleware(["fdaa::/16"], "fdaa:0:1::2", {"X-Forwarded-For": "198.51.100.8"}))[0] == (
        "198.51.100.8"
    )
    # no proxies configured: pass-through
    assert (await _run_middleware([], "127.0.0.1", xff))[0] == "127.0.0.1"
    with pytest.raises(ValueError):
        TrustedProxyMiddleware(lambda *a: None, trusted=["*"])  # type: ignore[arg-type,return-value]


def test_settings_refuse_wildcard_and_require_proxies_outside_development(settings: Settings) -> None:
    base = settings.model_dump()
    base["session_secret"] = settings.session_secret.get_secret_value()
    base["oidc_client_secret"] = settings.oidc_client_secret.get_secret_value()
    with pytest.raises(ValidationError, match="spoof"):
        Settings(**{**base, "trusted_proxies": "*"})
    with pytest.raises(ValidationError, match="not an IP address"):
        Settings(**{**base, "trusted_proxies": "fly-proxy"})
    with pytest.raises(ValidationError, match="DOME_TRUSTED_PROXIES"):
        Settings(**{**base, "env": "staging"})
    with pytest.raises(ValidationError, match="DOME_TRUSTED_PROXIES"):
        Settings(
            **{
                **base,
                "env": "production",
                "public_origin": "https://dome.example",
                "oidc_issuer": "https://idp.example",
                "session_secret": "a-real-secret-value-0123456789",
            }
        )
    ok = Settings(**{**base, "env": "staging", "trusted_proxies": " fdaa::/16, 10.0.0.1 "})
    assert ok.trusted_proxy_list == ["fdaa::/16", "10.0.0.1"]
    assert Settings(**{**base, "env": "staging", "trusted_proxies": "none"}).trusted_proxy_list == []
    assert Settings(**base).trusted_proxy_list == []


# ----- finding 2: per-socket frame budget, bounded security events, pre-hello capacity -----------------


async def test_controller_frame_flood_is_throttled_without_growing_security_events(
    env: Env, alice: Browser, online_agent: AgentSim
) -> None:
    svc = env.services
    original_frames, original_events = svc.limiters.frames, svc.limiters.events
    svc.limiters.frames = TokenBucketLimiter(per_minute=1, burst=6)
    svc.limiters.events = SlidingWindowLimiter(3, 60)
    before = sql(
        env.database_url,
        "SELECT count(*) FROM security_events WHERE account_id = %s AND actor = 'controller'",
        (alice.account_id,),
    )[0][0]
    ctrl = ControllerSim(env, alice, name="Unpaired flooder")
    try:
        ack = await ctrl.connect()  # session is valid, kid is not paired: every command is GRANT_MISSING
        assert "controller_id" not in ack
        cids = [await ctrl.command(online_agent.pc_id, "system.ping") for _ in range(40)]
        results: dict[str, str] = {}
        while len(results) < len(cids):
            try:
                frame = await ctrl.recv(timeout=3)
            except Exception:  # noqa: BLE001 - socket closed by the relay
                break
            if frame["type"] == "result":
                results[frame["command_id"]] = frame["error"]["code"]
        codes = list(results.values())
        # `burst` rejections still reach routing; the next `burst` frames are answered from memory, then close
        assert codes.count("GRANT_MISSING") == 6, codes
        assert codes.count("RATE_LIMITED") == 6, codes
        assert set(codes) == {"GRANT_MISSING", "RATE_LIMITED"}
        assert await close_code(ctrl.ws) == 4000  # type: ignore[arg-type]
        ctrl.ws = None
        await asyncio.sleep(0.2)
        rows = sql(
            env.database_url,
            "SELECT kind FROM security_events WHERE account_id = %s AND actor = 'controller' ORDER BY id OFFSET %s",
            (alice.account_id, before),
        )
        kinds = [r[0] for r in rows]
        assert kinds.count("command_rejected") == 3  # capped by the per-socket budget
        assert kinds.count("relay_events_throttled") == 1
        assert kinds.count("controller_throttled") == 1
        assert len(kinds) == 5, kinds
    finally:
        svc.limiters.frames, svc.limiters.events = original_frames, original_events
        await ctrl.close()


async def test_legitimate_rate_is_not_throttled_at_the_socket(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    """Sliders (coalescable, 360/min burst 60) stay below the socket budget: pings and commands pass."""
    free = plan_for("free")
    assert env.settings.relay_controller_frame_burst > free.coalescable_command_rate_limit.burst
    assert env.settings.relay_controller_frames_per_minute > (
        free.coalescable_command_rate_limit.per_minute + free.manual_command_rate_limit.per_minute
    )
    for _ in range(20):
        await paired.send({"type": "ping"})
        assert (await paired.recv_type("pong"))["type"] == "pong"
    cid = await paired.command(online_agent.pc_id, "system.ping")
    await online_agent.serve_one()
    assert (await paired.recv_type("result", command_id=cid))["state"] == "succeeded"


async def test_pre_hello_sockets_count_toward_the_connection_cap(env: Env, alice: Browser) -> None:
    relay = env.services.relay
    original = relay.settings
    relay.settings = original.model_copy(update={"relay_max_connections": relay.connection_count() + 1})
    headers = {"Origin": env.origin, "Cookie": f"dome_session={alice.session_cookie}"}
    silent = await connect(env.ws_base + "/ws/controller", additional_headers=headers)  # never sends hello
    try:
        await asyncio.sleep(0.1)
        assert relay.pending_handshakes >= 1
        with pytest.raises(InvalidStatus) as exc:
            await connect(env.ws_base + "/ws/controller", additional_headers=headers)
        assert exc.value.response.status_code == 503
    finally:
        await silent.close()
        relay.settings = original
    await asyncio.sleep(0.1)
    assert relay.pending_handshakes == 0


# ----- finding 4: user_code guessing is rate limited and codes never reach the request log -------------


async def test_unknown_link_codes_are_rate_limited_per_account(env: Env, make_account: Any) -> None:
    guesser: Browser = await make_account()
    limit = env.settings.rate_link_code_failures_per_account
    statuses = [(await guesser.request("GET", f"/v1/agent-link/ZZZZ-{i:04d}")).status_code for i in range(limit)]
    assert statuses == [404] * limit
    r = await guesser.request("GET", "/v1/agent-link/ZZZZ-ZZZ9", expect=429)
    assert r.json()["error"]["code"] == "RATE_LIMITED"
    # approve/deny share the budget, and a real pending code is no longer reachable from this account
    agent = AgentSim(env)
    start = (
        await agent.rest(
            "POST",
            "/v1/agent-link/start",
            {"pc_public_jwk": agent.jwk, "agent_version": "t", "platform": "development"},
            expect=200,
        )
    ).json()
    await guesser.request(
        "POST", f"/v1/agent-link/{start['user_code']}/approve", {"pc_name": "x", "remote_enabled": True}, expect=429
    )
    await guesser.request("POST", f"/v1/agent-link/{start['user_code']}/deny", expect=429)
    events = (await guesser.get("/v1/account/security-events?limit=50"))["events"]
    failed = [e for e in events if e["kind"] == "link_code_lookup_failed"]
    assert len(failed) == limit
    assert [e["detail"] for e in failed] == [{"reason": "unknown"}] * limit, [e["detail"] for e in failed]
    # another account is unaffected and the code is still pending for its owner
    owner: Browser = await make_account()
    preview = await owner.get(f"/v1/agent-link/{start['user_code']}", schema="agent_link_preview_response")
    assert preview["kid"] == agent.kid


def test_request_log_masks_link_codes() -> None:
    assert loggable_path("/v1/agent-link/ABCD-EFGH") == "/v1/agent-link/{user_code}"
    assert loggable_path("/v1/agent-link/abcdefgh/approve") == "/v1/agent-link/{user_code}/approve"
    assert loggable_path("/v1/agent-link/ABCD-EFGH/deny") == "/v1/agent-link/{user_code}/deny"
    assert loggable_path("/v1/agent-link/start") == "/v1/agent-link/start"
    assert loggable_path("/v1/agent-link/poll") == "/v1/agent-link/poll"
    assert (
        loggable_path("/v1/pcs/0f3f6b1a-0000-4000-8000-000000000000") == "/v1/pcs/0f3f6b1a-0000-4000-8000-000000000000"
    )
    assert len(loggable_path("/x" * 1000)) == 256


# ----- finding 6: command ids are looked up within the account only --------------------------------------


async def test_command_id_from_another_account_is_a_plain_reuse_conflict(
    env: Env, alice: Browser, bob: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    bob_agent = AgentSim(env)
    await bob_agent.link(bob, "Bob PC")
    await bob_agent.connect()
    bob_phone = ControllerSim(env, bob, name="Bob phone")
    await bob_phone.pair(bob_agent)
    await bob_phone.connect()
    try:
        shared = str(uuid.uuid4())
        cid = await bob_phone.command(bob_agent.pc_id, "system.ping", command_id=shared)
        await bob_agent.serve_one()
        assert (await bob_phone.recv_type("result", command_id=cid))["state"] == "succeeded"
        # Alice's valid command with the same id: refused as a reuse at insert time, Bob's row untouched
        cid2 = await paired.command(online_agent.pc_id, "system.ping", command_id=shared)
        result = await paired.recv_type("result", command_id=cid2)
        assert result["origin"] == "relay" and result["error"]["code"] == "COMMAND_ID_REUSED"
        rows = sql(env.database_url, "SELECT account_id::text, state FROM commands WHERE id = %s", (shared,))
        assert rows == [(bob.account_id, "succeeded")]
        # and Alice's PC never saw it
        await online_agent.send({"type": "ping"})
        assert (await online_agent.recv())["type"] == "pong"
    finally:
        await bob_phone.close()
        await bob_agent.close()

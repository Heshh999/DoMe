"""Cross-component integration harness.

Re-uses cloud-api's test harness (fresh PostgreSQL database, ``tools/dev-idp`` as the OIDC issuer,
the API under uvicorn in-process, ``Browser`` = a signed-in PWA session, ``ControllerSim`` = a phone
installation with a real ES256 key) and adds :class:`RealAgent`: the actual ``dome-agent`` process,
started with the explicitly isolated fake platform, linked through the real device-code flow,
paired through its local control channel, and bridged to a :class:`FakeExtension` on its
bridge socket. Nothing in cloud-api or the agent is mocked.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from dome_protocol import (
    challenge_digest,
    dumps_compact,
    format_rfc3339,
    now_utc,
    pairing_verification_code,
    sign_payload,
)
from dome_protocol.keys import b64url_encode

REPO = Path(__file__).resolve().parents[1]

# ----- cloud-api harness, loaded by path (its `tests` package name would clash with this one) ------
_spec = importlib.util.spec_from_file_location("dome_api_harness", REPO / "cloud-api" / "tests" / "conftest.py")
assert _spec is not None and _spec.loader is not None
harness = importlib.util.module_from_spec(_spec)
sys.modules["dome_api_harness"] = harness
_spec.loader.exec_module(harness)

# Re-exported fixtures (pytest discovers fixture objects by module attribute).
ports = harness.ports
database_url = harness.database_url
dev_idp = harness.dev_idp
static_dir = harness.static_dir
settings = harness.settings
env = harness.env
make_account = harness.make_account
alice = harness.alice
bob = harness.bob

Browser = harness.Browser
ControllerSim = harness.ControllerSim
AgentSim = harness.AgentSim
Env = harness.Env
check_rest = harness.check_rest
recv_until = harness.recv_until
sql = harness.sql
SCHEMAS = harness.SCHEMAS
REGISTRY = harness.REGISTRY

AGENT_BIN = Path(sys.executable).parent / "dome-agent"
ALL_CAPS = ("status", "media", "volume", "apps", "lock", "power")
MEDIA_CAPS = ("status", "media", "volume")
CODE_LINE = re.compile(r"^\s+([0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4})\s*$")


def _nonce() -> str:
    return b64url_encode(secrets.token_bytes(16))


class RealAgent:
    """The real ``dome-agent`` process on the fake platform, driven exactly as a customer's PC would be."""

    def __init__(self, env: Env, browser: Browser, name: str = "Living room PC") -> None:
        self.env = env
        self.browser = browser
        self.name = name
        # Unix socket paths are limited to ~104 bytes: keep the state directory short.
        self.root = Path(tempfile.mkdtemp(prefix="dome-it-"))
        self.state_dir = self.root / "s"
        self.log_path = self.root / "agent.log"
        self.proc: subprocess.Popen[bytes] | None = None
        self._stderr: Any = None
        self.pc_id = ""
        self.account_id = browser.account_id
        self.extensions: list[Any] = []

    # ----- process ------------------------------------------------------------------------------
    def environ(self) -> dict[str, str]:
        return {
            **os.environ,
            "DOME_AGENT_STATE_DIR": str(self.state_dir),
            "DOME_AGENT_API_URL": self.env.origin,
            "DOME_AGENT_PLATFORM": "fake",  # the explicitly isolated test double; never set by the installer
            "DOME_AGENT_HEADLESS": "1",
            "DOME_AGENT_LOG_LEVEL": "DEBUG",
        }

    def log_tail(self, n: int = 40) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return "<no log>"

    async def link(self) -> dict[str, Any]:
        """`dome-agent link --no-browser` + approval by the signed-in account (device-code flow)."""
        assert AGENT_BIN.exists(), f"{AGENT_BIN} missing: run `uv sync` in tests/"
        with self.log_path.open("ab") as err:
            proc = subprocess.Popen(
                [str(AGENT_BIN), "link", "--no-browser", "--name", self.name, "--timeout", "120"],
                env=self.environ(),
                stdout=subprocess.PIPE,
                stderr=err,
                text=True,
            )
            assert proc.stdout is not None

            def read_code() -> str | None:
                for line in proc.stdout:  # type: ignore[union-attr]
                    m = CODE_LINE.match(line)
                    if m:
                        return m.group(1)
                return None

            user_code = await asyncio.wait_for(asyncio.to_thread(read_code), 30)
            assert user_code, f"link printed no user code; log: {self.log_tail()}"
            preview = await self.browser.get(f"/v1/agent-link/{user_code}", schema="agent_link_preview_response")
            assert preview["platform"] == "development"
            approve = await self.browser.post(
                f"/v1/agent-link/{user_code}/approve",
                {"pc_name": self.name, "remote_enabled": True},
                schema="agent_link_approve_response",
            )
            rc = await asyncio.to_thread(proc.wait, 60)
            rest = proc.stdout.read()
        assert rc == 0, f"link exited {rc}: {rest}\n{self.log_tail()}"
        assert "Linked as" in rest
        self.pc_id = approve["pc_id"]
        return approve

    async def start(self, *, wait_connected: bool = True, timeout: float = 30) -> None:
        self._stderr = self.log_path.open("ab")
        self.proc = subprocess.Popen(
            [str(AGENT_BIN), "run", "--headless"], env=self.environ(), stdout=subprocess.DEVNULL, stderr=self._stderr
        )
        if wait_connected:
            await self.wait_status(lambda s: s["connection"] == "connected" and s["snapshot_received"], timeout)

    async def stop(self, timeout: float = 15) -> None:
        for ext in self.extensions:
            ext.close()
        self.extensions.clear()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                await asyncio.to_thread(self.proc.wait, timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                await asyncio.to_thread(self.proc.wait, 5)
        self.proc = None
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    async def kill(self) -> None:
        """SIGKILL: the crash-between-side-effect-and-result case."""
        assert self.proc is not None
        self.proc.kill()
        await asyncio.to_thread(self.proc.wait, 10)
        self.proc = None
        for ext in self.extensions:
            ext.close()
        self.extensions.clear()

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    # ----- local control channel (what the tray/CLI use) ------------------------------------------
    def _control_sync(self, op: str, args: dict[str, Any]) -> Any:
        from dome_agent.control import ControlClient

        return ControlClient(self.state_dir, timeout=10).call(op, **args)

    async def control(self, op: str, **args: Any) -> Any:
        return await asyncio.to_thread(self._control_sync, op, args)

    async def wait_status(self, predicate: Callable[[dict[str, Any]], bool], timeout: float = 20) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: Any = None
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"agent exited with {self.proc.returncode}; log:\n{self.log_tail()}")
            try:
                last = await self.control("status")
                if predicate(last):
                    return last
            except Exception as exc:  # noqa: BLE001 - control socket not up yet
                last = repr(exc)
            await asyncio.sleep(0.15)
        raise TimeoutError(f"agent status never matched; last={last}\nlog:\n{self.log_tail()}")

    async def displace(self) -> None:
        """Take the PC offline WITHOUT stopping the agent process (its pairing session stays in memory):
        a second connection with this PC's credential supersedes the real socket (close 4001, the
        agent waits for a manual Reconnect), then that connection goes away → the relay marks the
        PC offline. This is the 'PC lost its connection' case the contract's pairing_offline rule
        is about. (On Linux the credential is a 0600 file; on Windows it is DPAPI-protected.)"""
        from dome_agent.identity import Identity

        credential = Identity(self.state_dir).read_credential()
        assert credential, "agent is not linked"
        sim = AgentSim(self.env, pc_id=self.pc_id, account_id=self.account_id, credential=credential)
        await sim.fetch_token()
        await sim.connect()
        await self.wait_status(lambda s: s["connection"] == "superseded", 15)
        await sim.close()

    async def resume(self, timeout: float = 30) -> None:
        """Tray 'Reconnect' after a supersession."""
        await self.control("reconnect")
        await self.wait_status(lambda s: s["connection"] == "connected" and s["snapshot_received"], timeout)

    def store(self) -> Any:
        """Read the agent's SQLite store (WAL: safe to read while the agent runs)."""
        from dome_agent.store import Store

        return Store(self.state_dir / "state.sqlite3")

    # ----- pairing (code generated on the PC, approved on the PC) ----------------------------------
    async def start_pairing(self) -> dict[str, Any]:
        return await self.control("pair_start")

    async def wait_pending(self, pairing_id: str, timeout: float = 15) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = await self.control("pair_status")
            for pending in status.get("pending", []):
                if pending["pairing_id"] == pairing_id:
                    return dict(pending)
            await asyncio.sleep(0.15)
        raise TimeoutError(f"pairing request {pairing_id} never reached the PC")

    async def pair(
        self, ctrl: Any, capabilities: tuple[str, ...] = MEDIA_CAPS, *, granted: list[str] | None = None
    ) -> tuple[dict[str, Any], str]:
        started = await self.start_pairing()
        code = started["code"]
        status = await ctrl.claim(code, capabilities)
        assert status["state"] == "claimed" and status["pairing_id"] == started["pairing_id"], status
        pending = await self.wait_pending(started["pairing_id"])
        # Both screens compute the 6 digits independently from the secret only they share.
        phone_code = pairing_verification_code(code, started["pairing_id"], self.pc_id, ctrl.kid)
        assert pending["verification_code"] == phone_code
        assert pending["display_name"] == ctrl.name
        approved = await self.control("pair_approve", pairing_id=started["pairing_id"], capabilities=granted)
        assert approved["kid"] == ctrl.kid
        final = await wait_pairing_state(ctrl.browser, started["pairing_id"], "approved")
        ctrl.controller_id = final["controller_id"]
        ctrl.grant_id = final["grant_id"]
        return final, code

    # ----- browser bridge -----------------------------------------------------------------------
    async def connect_extension(self, *tabs: Any, **kw: Any) -> Any:
        from dome_agent.testing.fake_extension import FakeExtension

        ext = FakeExtension(self.state_dir, **kw)
        for t in tabs:
            ext.add_tab(t)
        await asyncio.to_thread(ext.connect, 10.0)
        ext.publish_tabs()
        await self.wait_status(lambda s: bool(s["extension_connected"]), 10)
        await asyncio.sleep(0.3)  # let the tabs_changed event land before a command targets a tab
        self.extensions.append(ext)
        return ext


async def wait_pairing_state(browser: Browser, pairing_id: str, state: str, timeout: float = 15) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = await browser.get(f"/v1/pairing/{pairing_id}", schema="pairing_status_response")
        if last["state"] == state:
            return last
        await asyncio.sleep(0.15)
    raise TimeoutError(f"pairing {pairing_id} never reached {state}: {last}")


# ----- controller helpers ---------------------------------------------------------------------------


def youtube_target(ext: Any, tab: Any, *, expected: bool = True) -> dict[str, Any]:
    target = {"browser_instance_id": ext.browser_instance_id, "tab_id": tab.tab_id, "tab_token": tab.tab_token}
    if expected:
        target["expected_video_id"] = tab.video_id
    return target


def confirmation_envelope(
    ctrl: Any,
    pc_id: str,
    command_id: str,
    challenge_text: str,
    *,
    decision: str = "approve",
    digest: str | None = None,
    key: Any = None,
) -> dict[str, Any]:
    from dome_protocol import loads_strict

    challenge = loads_strict(challenge_text)
    now = now_utc()
    payload = {
        "type": "confirmation",
        "protocol_version": REGISTRY.protocol_version,
        "command_id": command_id,
        "challenge_id": challenge["challenge_id"],
        "challenge_digest": digest or challenge_digest(challenge_text),
        "controller_id": ctrl.controller_id,
        "target_pc_id": pc_id,
        "decision": decision,
        "issued_at": format_rfc3339(now),
        "expires_at": format_rfc3339(now + timedelta(seconds=60)),
        "nonce": _nonce(),
    }
    return sign_payload(key or ctrl.key, dumps_compact(payload)).to_dict()


async def run_command(
    ctrl: Any,
    pc_id: str,
    action: str,
    params: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    *,
    timeout: float = 15,
    **kw: Any,
) -> dict[str, Any]:
    """Send a signed command and wait for its terminal result frame."""
    cid = await ctrl.command(pc_id, action, params, target, **kw)
    return await ctrl.recv_type("result", timeout=timeout, command_id=cid)


async def wait_state(ctrl: Any, pc_id: str, predicate: Callable[[dict[str, Any]], bool], timeout: float = 10) -> dict[str, Any]:
    """Receive `state` frames for the PC until one satisfies the predicate (frames arrive on change)."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        frame = await ctrl.recv_type("state", timeout=max(0.1, deadline - time.monotonic()), pc_id=pc_id)
        last = frame
        if predicate(frame["state"]):
            return frame
    raise TimeoutError(f"no matching state frame; last={last}")


async def drain_subscribe(ctrl: Any, pc_id: str) -> dict[str, Any]:
    """subscribe → pc_status (+ cached state when the PC has sent one)."""
    await ctrl.subscribe(pc_id)
    status = await ctrl.recv_type("pc_status", pc_id=pc_id)
    return status


# ----- fixtures -------------------------------------------------------------------------------------


@pytest.fixture
async def agent(env: Env, alice: Browser) -> AsyncIterator[RealAgent]:
    a = RealAgent(env, alice)
    await a.link()
    await a.start()
    try:
        yield a
    finally:
        await a.stop()
        a.cleanup()


@pytest.fixture
async def phone(env: Env, alice: Browser, agent: RealAgent) -> AsyncIterator[Any]:
    """Alice's phone: paired with every capability, connected and subscribed to her PC."""
    ctrl = ControllerSim(env, alice, name="Alice's iPhone")
    await agent.pair(ctrl, ALL_CAPS)
    ack = await ctrl.connect()
    assert ack["controller_id"] == ctrl.controller_id
    status = await drain_subscribe(ctrl, agent.pc_id)
    assert status["connection"] == "online"
    try:
        yield ctrl
    finally:
        await ctrl.close()

"""Device-link flow (ADR-0001 D4, RFC 8628 shape).

``start`` → the agent posts its public key and gets ``device_code`` (secret, stays in memory) +
``user_code`` + ``verification_uri_complete`` → opens the system browser (unless ``--no-browser``)
→ polls ``/v1/agent-link/poll`` at the server's interval (``slow_down`` adds 5 s) until the signed-in
customer approves → stores the PC credential (DPAPI/0600) and the public link record.
The agent never sees the account password.
"""

from __future__ import annotations

import asyncio
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass

from dome_protocol import format_rfc3339, now_utc

from .api import ApiClient, ApiError, LinkStart
from .identity import Identity, LinkRecord
from .logsetup import get_logger
from .store import SETTING_PC_NAME, Store

log = get_logger(__name__)

Presenter = Callable[[LinkStart], None]


@dataclass(frozen=True, slots=True)
class LinkOutcome:
    pc_id: str
    account_id: str
    pc_name: str
    enabled: bool


class LinkError(Exception):
    pass


def open_system_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:  # noqa: BLE001 - no browser is not fatal; the code is shown as well
        return False


async def link_pc(
    api: ApiClient,
    identity: Identity,
    store: Store,
    *,
    platform: str,
    pc_name_hint: str | None,
    open_browser: bool,
    present: Presenter,
    enable_remote: bool,
    max_wait_seconds: int | None = None,
) -> LinkOutcome:
    if identity.is_linked:
        raise LinkError("This PC is already linked. Unlink it first (dome-agent unlink) to link it to another account.")
    start = await api.link_start(identity.public_jwk, platform, pc_name_hint)
    present(start)
    if open_browser:
        if not open_system_browser(start.verification_uri_complete):
            log.warning("could not open the system browser; use the printed link")
    deadline = asyncio.get_running_loop().time() + min(start.expires_in, max_wait_seconds or start.expires_in)
    interval = max(1, start.interval)
    while True:
        if asyncio.get_running_loop().time() >= deadline:
            raise LinkError("The link code expired before it was approved. Run `dome-agent link` again.")
        await asyncio.sleep(interval)
        try:
            outcome = await api.link_poll(start.device_code)
        except ApiError as exc:
            if exc.status in (400, 404, 410):
                raise LinkError(f"The link request was rejected or expired ({exc.code}). Run `dome-agent link` again.") from exc
            if exc.status == 403:
                raise LinkError("Linking was denied in the browser.") from exc
            if exc.is_network_or_server_error:
                log.warning("link poll failed; retrying", code=exc.code)
                continue
            raise LinkError(f"Linking failed: {exc.code}") from exc
        if isinstance(outcome, str):
            if outcome == "slow_down":
                interval += 5
            continue
        record = LinkRecord(
            pc_id=outcome.pc_id,
            account_id=outcome.account_id,
            pc_name=outcome.pc_name or pc_name_hint or "My PC",
            relay_url=outcome.relay_url,
            api_url=outcome.api_url,
            enabled=outcome.enabled,
            linked_at=format_rfc3339(now_utc()),
        )
        identity.store_link(record, outcome.pc_credential)
        store.set_setting(SETTING_PC_NAME, record.pc_name)
        if enable_remote:
            store.set_remote_enabled(True)
        return LinkOutcome(outcome.pc_id, outcome.account_id, record.pc_name, outcome.enabled)

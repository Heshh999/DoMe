"""Entitlement assertions on the PC (ADR-0001 D8, ``schemas/entitlement.schema.json``).

The backend issues short-lived EdDSA (Ed25519) JWS assertions bound to ``account_id`` (``sub``) and
``pc_id`` (``pc``). The agent verifies them with ``joserfc`` against the JWKS published at
``<api>/.well-known/dome-jwks.json``, validates the claims against the schema, refreshes on every
connect and at 80 % of the lifetime, and honours the last verified Pro assertion for at most
``agent_offline_assertion_grace_hours`` (72 h) after ``exp`` ONLY when the refresh failed with a
network error or 5xx. ``assertion: null`` means Free immediately. The Free path never depends on
this module: entitlement only ever gates routine steps.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from dome_protocol import ProtocolError, load_registry, load_schemas

from .logsetup import get_logger
from .store import SETTING_ENTITLEMENT, Store

if TYPE_CHECKING:
    from .api import ApiClient

log = get_logger(__name__)

TYP = "dome-entitlement+jwt"
ALG = "EdDSA"
REFRESH_FRACTION = 0.8
MAX_SKEW_SECONDS = 60


class EntitlementError(ProtocolError):
    def __init__(self, message: str) -> None:
        super().__init__("ENTITLEMENT_REQUIRED", message)


@dataclass(frozen=True, slots=True)
class VerifiedEntitlement:
    plan: str
    claims: dict[str, Any]
    verified_at: float  # unix seconds

    @property
    def exp(self) -> int:
        return int(self.claims["exp"])

    @property
    def iat(self) -> int:
        return int(self.claims["iat"])

    @property
    def routines(self) -> bool:
        return bool(self.claims["limits"]["routines"])


def verify_assertion(
    assertion: str, jwks: dict[str, Any], *, account_id: str, pc_id: str, now: float | None = None
) -> VerifiedEntitlement:
    """Verify signature (EdDSA, JWKS by kid), header ``typ``, schema, binding and time window."""
    from joserfc import jwt
    from joserfc.errors import JoseError
    from joserfc.jwk import KeySet

    now = time.time() if now is None else now
    if not isinstance(assertion, str) or assertion.count(".") != 2 or len(assertion) > 4096:
        raise EntitlementError("entitlement assertion is not a compact JWS")
    try:
        key_set = KeySet.import_key_set(cast(Any, jwks))
        with warnings.catch_warnings():
            # joserfc flags the generic "EdDSA" name (RFC 9864 prefers "Ed25519"); the contract pins EdDSA.
            warnings.simplefilter("ignore")
            token = jwt.decode(assertion, key_set, algorithms=[ALG])
    except (JoseError, ValueError, KeyError, TypeError) as exc:
        raise EntitlementError(f"entitlement assertion did not verify ({exc.__class__.__name__})") from None
    header = token.header
    if header.get("alg") != ALG or header.get("typ") != TYP or not isinstance(header.get("kid"), str):
        raise EntitlementError("entitlement assertion header is not acceptable")
    claims = token.claims
    load_schemas().validate_entitlement_claims(claims)
    if claims["sub"] != account_id or claims["pc"] != pc_id:
        raise EntitlementError("entitlement assertion is bound to a different account or PC")
    if int(claims["iat"]) > now + MAX_SKEW_SECONDS:
        raise EntitlementError("entitlement assertion issued in the future")
    if int(claims["exp"]) <= int(claims["iat"]):
        raise EntitlementError("entitlement assertion has an empty lifetime")
    if int(claims["exp"]) < now - MAX_SKEW_SECONDS:
        raise EntitlementError("entitlement assertion expired")
    return VerifiedEntitlement(plan=str(claims["plan"]), claims=dict(claims), verified_at=now)


class EntitlementManager:
    def __init__(self, store: Store, *, account_id: str | None, pc_id: str | None) -> None:
        self._store = store
        self._account_id = account_id
        self._pc_id = pc_id
        self._current: VerifiedEntitlement | None = None
        self._jwks: dict[str, Any] | None = None
        self._last_refresh_failed_softly = False
        self._refresh_task: asyncio.Task[None] | None = None
        self._api: ApiClient | None = None
        self._token_provider: Any = None
        self._grace_seconds = int(load_registry().plans["billing_grace"]["agent_offline_assertion_grace_hours"]) * 3600
        self._load_persisted()

    # ----- persistence of the last verified assertion (claims only; never the token) ----------------------
    def _load_persisted(self) -> None:
        raw = self._store.get_setting(SETTING_ENTITLEMENT)
        if not raw:
            return
        try:
            data = json.loads(raw)
            self._current = VerifiedEntitlement(
                plan=str(data["plan"]), claims=dict(data["claims"]), verified_at=float(data["verified_at"])
            )
        except (ValueError, KeyError, TypeError):
            self._store.delete_setting(SETTING_ENTITLEMENT)

    def _persist(self) -> None:
        if self._current is None:
            self._store.delete_setting(SETTING_ENTITLEMENT)
        else:
            self._store.set_setting(
                SETTING_ENTITLEMENT,
                json.dumps(
                    {
                        "plan": self._current.plan,
                        "claims": self._current.claims,
                        "verified_at": self._current.verified_at,
                    }
                ),
            )

    # ----- queries -----------------------------------------------------------------------------------------
    @property
    def current(self) -> VerifiedEntitlement | None:
        return self._current

    def effective_plan(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        cur = self._current
        if cur is None:
            return "free"
        if cur.exp >= now - MAX_SKEW_SECONDS:
            return cur.plan
        if self._last_refresh_failed_softly and now <= cur.exp + self._grace_seconds:
            return cur.plan
        return "free"

    def routines_allowed(self, now: float | None = None) -> bool:
        if self.effective_plan(now) != "pro" or self._current is None:
            return False
        return self._current.routines

    def summary(self) -> dict[str, Any]:
        cur = self._current
        return {
            "effective_plan": self.effective_plan(),
            "asserted_plan": cur.plan if cur else None,
            "exp": cur.exp if cur else None,
            "grace_active": bool(cur and self._last_refresh_failed_softly and time.time() > cur.exp),
        }

    # ----- applying assertions ------------------------------------------------------------------------------
    def set_free(self) -> None:
        """A definitive answer that this PC is on Free (assertion null / Free plan)."""
        self._current = None
        self._last_refresh_failed_softly = False
        self._persist()

    def apply_assertion(self, assertion: str | None, jwks: dict[str, Any] | None = None) -> VerifiedEntitlement | None:
        if assertion is None:
            self.set_free()
            return None
        if jwks is not None:
            self._jwks = jwks
        if self._jwks is None:
            raise EntitlementError("no JWKS available to verify the entitlement assertion")
        if self._account_id is None or self._pc_id is None:
            raise EntitlementError("PC is not linked")
        verified = verify_assertion(assertion, self._jwks, account_id=self._account_id, pc_id=self._pc_id)
        self._current = verified
        self._last_refresh_failed_softly = False
        self._persist()
        log.info("entitlement assertion verified", plan=verified.plan, exp=verified.exp)
        return verified

    async def ensure_jwks(self, api: ApiClient) -> None:
        """Fetch the JWKS once (lazily, before the first snapshot assertion is verified)."""
        if self._jwks is None:
            self._jwks = await api.jwks()

    def note_refresh_failure(self, *, soft: bool) -> None:
        """soft = network error or 5xx → grace may apply; hard (4xx) → Free."""
        if soft:
            self._last_refresh_failed_softly = True
        else:
            self.set_free()

    # ----- refresh scheduling --------------------------------------------------------------------------------
    def bind(self, api: ApiClient, token_provider: Any) -> None:
        """``token_provider`` is an async callable returning a valid PC access token."""
        self._api = api
        self._token_provider = token_provider

    async def refresh(self) -> None:
        if self._api is None or self._token_provider is None:
            return
        from .api import ApiError

        try:
            token = await self._token_provider()
            response = await self._api.entitlement(token)
            if response.assertion is None or response.plan == "free":
                self.set_free()
                return
            if self._jwks is None:
                self._jwks = await self._api.jwks()
            try:
                self.apply_assertion(response.assertion)
            except EntitlementError:
                # the signing key may have rotated: fetch the JWKS once more and retry
                self._jwks = await self._api.jwks()
                self.apply_assertion(response.assertion)
        except ApiError as exc:
            log.warning("entitlement refresh failed", code=exc.code, status=exc.status)
            self.note_refresh_failure(soft=exc.is_network_or_server_error)
        except EntitlementError as exc:
            log.warning("entitlement assertion rejected", message=exc.message)
            self.set_free()

    def schedule_refresh(self) -> None:
        """(Re)arm the 80 %-of-lifetime refresh timer for the current assertion."""
        self.cancel_scheduled()
        cur = self._current
        if cur is None:
            return
        lifetime = max(60, cur.exp - cur.iat)
        due = cur.iat + REFRESH_FRACTION * lifetime
        delay = max(5.0, due - time.time())
        self._refresh_task = asyncio.get_running_loop().create_task(
            self._refresh_later(delay), name="dome-entitlement-refresh"
        )

    async def _refresh_later(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self.refresh()
        self.schedule_refresh()

    def cancel_scheduled(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = None

    async def stop(self) -> None:
        task = self._refresh_task
        self.cancel_scheduled()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

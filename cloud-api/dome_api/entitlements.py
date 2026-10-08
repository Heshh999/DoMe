"""Entitlement assertions (ADR-0001 D8): short-lived EdDSA JWS bound to account + PC.

Issued only for plans that carry one (``Plan.issues_entitlement_assertion``); the default plan
returns ``None`` and the agent treats that as "Free immediately". Claims are validated against
``schemas/entitlement.schema.json`` before signing so the agent's verifier and ours agree.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from dome_protocol import load_schemas
from joserfc import jwt
from joserfc.jwk import KeySet, OKPKey

from dome_api.plans import Plan, catalog

TYP = "dome-entitlement+jwt"


class EntitlementSigner:
    def __init__(self, pem: bytes, issuer: str) -> None:
        key = OKPKey.import_key(pem)
        if key.curve_name != "Ed25519" or not key.is_private:  # type: ignore[attr-defined]
            raise ValueError("entitlement key must be an Ed25519 private key")
        self._key = key
        self.kid = key.thumbprint()
        self.issuer = issuer
        self.alg = catalog().entitlement_algorithm
        self.lifetime = catalog().entitlement_lifetime_seconds

    @classmethod
    def from_path(cls, path: Path, issuer: str) -> EntitlementSigner:
        try:
            pem = path.read_bytes()
        except FileNotFoundError:
            raise RuntimeError(
                f"entitlement signing key not found at {path}; run `uv run dome-api-gen-entitlement-key` for development"
            ) from None
        return cls(pem, issuer)

    def jwks(self) -> dict[str, Any]:
        public = self._key.as_dict(private=False)
        public.update({"kid": self.kid, "use": "sig", "alg": self.alg})
        return {"keys": [public]}

    def claims_for(self, account_id: uuid.UUID, pc_id: uuid.UUID, plan: Plan, *, now: int | None = None) -> dict[str, Any] | None:
        if not plan.issues_entitlement_assertion:
            return None
        iat = int(now if now is not None else time.time())
        return {
            "iss": self.issuer,
            "sub": str(account_id),
            "pc": str(pc_id),
            "plan": plan.id,
            "limits": plan.entitlement_limits(),
            "iat": iat,
            "exp": iat + self.lifetime,
            "jti": str(uuid.uuid4()),
        }

    def assertion(self, account_id: uuid.UUID, pc_id: uuid.UUID, plan: Plan) -> str | None:
        claims = self.claims_for(account_id, pc_id, plan)
        if claims is None:
            return None
        load_schemas().validate_entitlement_claims(claims)
        return jwt.encode({"alg": self.alg, "typ": TYP, "kid": self.kid}, claims, self._key)

    def verify(self, assertion: str) -> dict[str, Any]:
        """Verify one of our own assertions (used by tests and diagnostics)."""
        keyset = KeySet.import_key_set(self.jwks())
        tok = jwt.decode(assertion, keyset, algorithms=[self.alg])
        if tok.header.get("typ") != TYP or tok.header.get("kid") != self.kid:
            raise ValueError("unexpected assertion header")
        jwt.JWTClaimsRegistry(iss={"essential": True, "value": self.issuer}, exp={"essential": True}).validate(tok.claims)
        load_schemas().validate_entitlement_claims(tok.claims)
        return dict(tok.claims)

"""Fixtures: a temporary PATH with fake ``flyctl``/``docker`` (tests/fake_flyctl.py), a fake web and DNS,
and a fake clock, so dome_deploy.py runs end to end without Fly.io, GitHub or the network. ``openssl`` is
the real one."""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import stat
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

import dome_deploy
from dome_deploy import HttpFailure, HttpResult

HERE = Path(__file__).resolve().parent
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
# Fake token parts, assembled at run time so no line looks like a real Fly token to a secret scanner.
FM = "fm2" + "_"
FLY_TOKEN_PARTS = (FM + "lJPECAAAAAAAAMfakeTokenPartOne1234567890", FM + "lJPETokenPartTwoabcdefghij1234")
FLY_TOKEN = "Fly" + "V1 " + ",".join(FLY_TOKEN_PARTS)
CLIENT_SECRET = "auth0-client-secret_ABCdef0123456789-xyzXYZ0123456789abcdefABCDEF01"
FALLBACK_DB = "postgresql://fly-user:FallbackPassw0rd987@pgbouncer.fallback.flympg.net/fly-db"
# Staging's Neon database: the DIRECT connection string as Neon's Connect window shows it (fake values).
NEON_HOST = "ep-quiet-sky-a1b2c3d4.us-east-1.aws.neon.tech"
NEON_PASSWORD = "npg_" + "FakeNeonPassw0rd42"
NEON_DB = f"postgresql://neondb_owner:{NEON_PASSWORD}@{NEON_HOST}/neondb?sslmode=require&channel_binding=require"
NEON_PASSWORD_ROTATED = "npg_" + "RotatedNeonPassw0rd77"
NEON_DB_ROTATED = NEON_DB.replace(NEON_PASSWORD, NEON_PASSWORD_ROTATED)
ISSUER_HOST = "domeremote-staging.us.auth0.com"
CLIENT_ID = "AbCdEf0123456789AbCdEf0123456789"
GH_TOKEN = "ghs_" + "FakeJobToken0123456789abcdefABCDEF"
# Fake Stripe values, assembled at run time so no line looks like a real Stripe secret to a scanner.
STRIPE_TEST_KEY = "sk_" + "test_" + "51FakeStripeTestKey0123456789abcdefABCDEFghij"
STRIPE_LIVE_KEY = "sk_" + "live_" + "51FakeStripeLiveKey0123456789abcdefABCDEFghij"
STRIPE_WEBHOOK_SECRET = "whsec_" + "FakeSigningSecret0123456789abcdefAB"
STRIPE_PRICES = {
    "DOME_STRIPE_PRICE_PRO_MONTHLY": "price_1FakeProMonthly0001",
    "DOME_STRIPE_PRICE_PRO_ANNUAL": "price_1FakeProAnnual00002",
    "DOME_STRIPE_PRICE_PRO_PLUS_MONTHLY": "price_1FakePlusMonthly003",
    "DOME_STRIPE_PRICE_PRO_PLUS_ANNUAL": "price_1FakePlusAnnual0004",
    "DOME_STRIPE_PRICE_TOPUP": "price_1FakeTopUp000000005",
}


def stripe_env(key: str = STRIPE_TEST_KEY, webhook: str = STRIPE_WEBHOOK_SECRET, **prices: str) -> dict[str, str]:
    """All seven Stripe values as the deploy step receives them (``prices`` overrides single price variables)."""
    return {"DOME_STRIPE_SECRET_KEY": key, "DOME_STRIPE_WEBHOOK_SECRET": webhook, **STRIPE_PRICES, **prices}


# Fake Cloudflare TURN values (docs/design/screen-view.md section 20), assembled so no line looks like a real token.
TURN_KEY_ID = "0123456789abcdef0123456789abcdef"
TURN_API_TOKEN = "cf-turn-" + "FakeApiToken0123456789abcdefABCDEF"
TURN_ANALYTICS_TOKEN = "cf-analytics-" + "FakeAnalyticsToken0123456789abcdefAB"


def turn_env(
    key_id: str = TURN_KEY_ID,
    token: str = TURN_API_TOKEN,
    analytics: str = TURN_ANALYTICS_TOKEN,
    *,
    enabled: bool = True,
) -> dict[str, str]:
    """The Screen view values as the deploy step receives them: the switch, the key id and the two tokens."""
    return {
        "DOME_SCREEN_VIEW_ENABLED": "true" if enabled else "false",
        "DOME_TURN_KEY_ID": key_id,
        "DOME_TURN_API_TOKEN": token,
        "DOME_TURN_ANALYTICS_TOKEN": analytics,
    }


SECURE_HEADERS = {
    "content-type": "text/html; charset=utf-8",
    "content-security-policy": "default-src 'self'",
    "x-content-type-options": "nosniff",
}


class FakeWeb:
    """URL -> HttpResult, HttpFailure or a callable returning one. Unknown URLs fail like DNS would.
    ``websockets``: host -> the HTTP status a WebSocket upgrade gets (or an HttpFailure)."""

    def __init__(self) -> None:
        self.routes: dict[str, Any] = {}
        self.requests: list[str] = []
        self.no_redirect_requests: list[str] = []
        self.websockets: dict[str, Any] = {}
        self.websocket_requests: list[tuple[str, str]] = []
        self.headers_seen: dict[str, dict[str, str]] = {}

    def __call__(
        self, url: str, *, timeout: float = 0, headers: Any = None, follow_redirects: bool = True
    ) -> HttpResult:
        self.requests.append(url)
        self.headers_seen[url] = dict(headers or {})
        if not follow_redirects:
            self.no_redirect_requests.append(url)
        route = self.routes.get(url)
        if callable(route) and not isinstance(route, HttpResult | HttpFailure):
            route = route()
        if route is None:
            raise HttpFailure("dns", "unknown host")
        if isinstance(route, HttpFailure):
            raise route
        assert isinstance(route, HttpResult)
        return route

    def json(self, url: str, body: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.routes[url] = HttpResult(
            status, {"content-type": "application/json", **(headers or {})}, json.dumps(body).encode()
        )

    def discovery(
        self, host: str = ISSUER_HOST, *, issuer: str | None = None, methods: list[str] | None = None
    ) -> None:
        doc: dict[str, Any] = {
            "issuer": issuer or f"https://{host}/",
            "authorization_endpoint": f"https://{host}/authorize",
            "token_endpoint": f"https://{host}/oauth/token",
        }
        doc["token_endpoint_auth_methods_supported"] = methods or ["client_secret_basic", "client_secret_post"]
        self.json(f"https://{host}/.well-known/openid-configuration", doc)

    def healthz(
        self,
        base: str,
        *,
        commit: str | None = SHA,
        forwarded: str = "ok",
        headers: dict[str, str] | None = None,
        screen_view: str | None = "off",
        turn: str = "unconfigured",
    ) -> None:
        """cloud-api's GET /healthz; ``screen_view=None`` leaves the Screen view fields out (an older build)."""
        body: dict[str, Any] = {
            "status": "ok",
            "version": "0.1.0",
            "commit": commit,
            "database": "ok",
            "forwarded_client": forwarded,
        }
        if screen_view is not None:
            body.update({"screen_view": screen_view, "turn": turn, "turn_month_gb": 0.0, "turn_reconciled_at": None})
        self.json(base + "/healthz", body, headers=headers)

    def plans(self, base: str, mode: str = "off") -> None:
        """The public GET /v1/plans of cloud-api (routes/plans.py), reduced to the billing fields."""
        self.json(base + "/v1/plans", {"billing_enabled": mode != "off", "billing_mode": mode, "plans": []})

    def page(self, base: str, *, robots: bool = True, hsts: bool = False) -> None:
        headers = dict(SECURE_HEADERS)
        if robots:
            headers["x-robots-tag"] = "noindex, nofollow"
        if hsts:
            headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
        self.routes[base + "/"] = HttpResult(200, headers, b"<!doctype html><title>DoMe</title>")

    def login(
        self,
        base: str,
        callback: str,
        *,
        client_id: str = CLIENT_ID,
        authorize: str = f"https://{ISSUER_HOST}/authorize",
        status: int = 303,
    ) -> None:
        """What cloud-api's /v1/auth/login answers: 303 to the provider (authlib's query, PKCE included)."""
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": callback,
                "scope": "openid email profile",
                "state": "st4te-value-0123456789",
                "nonce": "n0nce-value-0123456789",
                "code_challenge": "chall3nge-0123456789",
                "code_challenge_method": "S256",
            }
        )
        headers = {"location": f"{authorize}?{query}", "cache-control": "no-store"} if status == 303 else {}
        self.routes[base + "/v1/auth/login"] = HttpResult(status, headers, b"")

    def websocket_status(self, host: str, path: str, *, timeout: float = 0) -> int:
        self.websocket_requests.append((host, path))
        answer = self.websockets.get(host)
        if answer is None:
            raise HttpFailure("dns", "unknown host")
        if isinstance(answer, HttpFailure):
            raise answer
        assert isinstance(answer, int)
        return answer

    def github_environment(
        self,
        name: str,
        *,
        repo: str = "Heshh999/DoMe",
        branches: list[dict[str, Any]] | None = None,
        reviewers: int = 1,
        prevent_self_review: bool = False,
        custom_branch_policies: bool = True,
        can_admins_bypass: bool | None = False,
    ) -> None:
        """GitHub's REST answers for a GitHub Environment (shapes from docs.github.com, API 2022-11-28)."""
        base = f"https://api.github.com/repos/{repo}/environments/{name}"
        rules: list[dict[str, Any]] = [{"id": 1, "node_id": "x", "type": "branch_policy"}]
        if reviewers >= 0:
            rules.append(
                {
                    "id": 2,
                    "node_id": "y",
                    "type": "required_reviewers",
                    "prevent_self_review": prevent_self_review,
                    "reviewers": [
                        {"type": "User", "reviewer": {"login": f"reviewer{i}", "id": 100 + i}} for i in range(reviewers)
                    ],
                }
            )
        doc: dict[str, Any] = {
            "id": 7,
            "name": name,
            "protection_rules": rules,
            "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": custom_branch_policies},
        }
        if can_admins_bypass is not None:
            doc["can_admins_bypass"] = can_admins_bypass
        self.json(base, doc)
        policies = [{"id": 9, "node_id": "z", "name": "main", "type": "branch"}] if branches is None else branches
        self.json(
            base + "/deployment-branch-policies?per_page=100",
            {"total_count": len(policies), "branch_policies": policies},
        )


@dataclass
class Clock:
    now: float = 1000.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class Run:
    code: int
    stdout: str
    summary: str


@dataclass
class World:
    tmp: Path
    state_path: Path
    log_path: Path
    env: dict[str, str]
    web: FakeWeb
    dns: dict[str, list[str]]
    clock: Clock

    # ----- fake Fly state ------------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.state_path.read_text())
        return data

    def update(self, fn: Callable[[dict[str, Any]], None]) -> None:
        state = self.state()
        fn(state)
        self.state_path.write_text(json.dumps(state))

    def add_app(self, name: str, *, secrets: dict[str, str] | None = None, machines: int = 0, ips: bool = True) -> None:
        def go(state: dict[str, Any]) -> None:
            state["apps"][name] = {
                "org": "personal",
                "secrets": dict(secrets or {}),
                "ips": [
                    {"Address": "2a09:8280:1::4b:1", "Type": "v6"},
                    {"Address": "66.241.124.10", "Type": "shared_v4"},
                ]
                if ips
                else [],
                "machines": [
                    {"id": f"m{i}", "state": "started", "config": {"metadata": {"fly_process_group": "app"}}}
                    for i in range(machines)
                ],
                "certs": [],
            }

        self.update(go)

    def add_cluster(self, name: str, *, status: str = "ready", ready_after_polls: int | None = None) -> str:
        cid = "cl" + name.replace("-", "")[:12]

        def go(state: dict[str, Any]) -> None:
            cluster: dict[str, Any] = {
                "Id": cid,
                "Name": name,
                "Region": "iad",
                "Status": status,
                "Plan": "basic",
                "Organization": {"Slug": "personal"},
            }
            if ready_after_polls is not None:
                cluster["ready_after_polls"] = ready_after_polls
            state["clusters"].append(cluster)

        self.update(go)
        return cid

    def calls(self, tool: str | None = None) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        calls = [json.loads(line) for line in self.log_path.read_text().splitlines() if line.strip()]
        return [c for c in calls if tool is None or c["tool"] == tool]

    def commands(self) -> list[str]:
        """``tool sub1 sub2`` for every call, in order."""
        out = []
        for call in self.calls():
            words = []
            for arg in call["argv"][:2]:
                if arg.startswith("-"):
                    break
                words.append(arg)
            out.append(" ".join([call["tool"], *words]))
        return out

    def reset_log(self) -> None:
        if self.log_path.exists():
            self.log_path.unlink()

    # ----- running --------------------------------------------------------------------------------
    def run(self, command: str, **env: str) -> Run:
        """``env`` overrides; ``"<unset>"`` removes a variable. Like the real GitHub Environments, only staging holds
        the secret DATABASE_URL (Neon) unless a test passes DEPLOY_DATABASE_URL itself."""
        out = io.StringIO()
        summary_path = self.tmp / "summary.md"
        if summary_path.exists():
            summary_path.unlink()
        full_env = {**self.env, "GITHUB_STEP_SUMMARY": str(summary_path), **env}
        if "DEPLOY_DATABASE_URL" not in env:
            full_env["DEPLOY_DATABASE_URL"] = NEON_DB if full_env.get("DEPLOY_ENVIRONMENT") == "staging" else ""
        full_env = {k: v for k, v in full_env.items() if v != "<unset>"}
        code = dome_deploy.main([command], full_env, out)
        summary = summary_path.read_text() if summary_path.exists() else ""
        return Run(code, out.getvalue(), summary)

    def staging_ready_web(
        self,
        app: str = "domeremote-staging",
        *,
        commit: str = SHA,
        billing: str = "off",
        screen_view: str | None = "off",
        turn: str = "unconfigured",
    ) -> None:
        self.web.discovery()
        self.web.healthz(f"https://{app}.fly.dev", commit=commit, screen_view=screen_view, turn=turn)
        self.web.plans(f"https://{app}.fly.dev", billing)
        self.web.page(f"https://{app}.fly.dev")
        self.web.login(f"https://{app}.fly.dev", "https://staging.domeremote.com/v1/auth/callback")
        self.web.websockets[f"{app}.fly.dev"] = 401

    def production_ready_web(
        self, billing: str = "off", *, screen_view: str = "off", turn: str = "unconfigured"
    ) -> None:
        """Staging healthy with this commit (production's precondition) and a healthy production fly.dev."""
        self.staging_ready_web(commit=SHA)
        self.web.healthz("https://domeremote-production.fly.dev", screen_view=screen_view, turn=turn)
        self.web.plans("https://domeremote-production.fly.dev", billing)
        self.web.page("https://domeremote-production.fly.dev", robots=True, hsts=True)
        self.web.login("https://domeremote-production.fly.dev", "https://domeremote.com/v1/auth/callback")
        self.web.websockets["domeremote-production.fly.dev"] = 401


def _install(bin_dir: Path, name: str) -> None:
    path = bin_dir / name
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_flyctl.py"}" {name} "$@"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("flyctl", "docker"):
        _install(bin_dir, tool)
    openssl = shutil.which("openssl")
    assert openssl, "the tests use the real openssl"
    state_path = tmp_path / "fly-state.json"
    state_path.write_text(json.dumps({"apps": {}, "clusters": [], "fail": {}}))
    log_path = tmp_path / "fly-log.jsonl"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.path.dirname(openssl)}{os.pathsep}/usr/bin:/bin",
        "FAKE_FLY_STATE": str(state_path),
        "FAKE_FLY_LOG": str(log_path),
        "GITHUB_REF": "refs/heads/main",
        "DEFAULT_BRANCH": "main",
        "GITHUB_SHA": SHA,
        "GITHUB_REPOSITORY": "Heshh999/DoMe",
        "DEPLOY_ENVIRONMENT": "staging",
        "CREATE_DATABASE": "false",
        "FLY_API_TOKEN": FLY_TOKEN,
        "DOME_OIDC_CLIENT_SECRET": CLIENT_SECRET,
        "DEPLOY_DATABASE_URL": "",  # World.run fills it per environment
        "DOME_OIDC_ISSUER": ISSUER_HOST,
        "DOME_OIDC_CLIENT_ID": CLIENT_ID,
        "FLY_ORG": "",
        "VITE_DOME_SUPPORT_URL": "mailto:support@domeremote.com",
        "DOME_SUPPORT_RESPONSE_EXPECTATION": "",
        "REPO_LEVEL_SECRETS": "",
        # what deploy.yml hands over when the GitHub Environment has no Stripe values: empty strings
        "DOME_STRIPE_SECRET_KEY": "",
        "DOME_STRIPE_WEBHOOK_SECRET": "",
        **{name: "" for name in STRIPE_PRICES},
        "STRIPE_SECRETS_AS_VARIABLES": "",
        # what deploy.yml hands over when the GitHub Environment has no Screen view values: empty strings
        "DOME_SCREEN_VIEW_ENABLED": "",
        "DOME_TURN_KEY_ID": "",
        "DOME_TURN_API_TOKEN": "",
        "DOME_TURN_ANALYTICS_TOKEN": "",
        "TURN_SECRETS_AS_VARIABLES": "",
        "GH_TOKEN": GH_TOKEN,
    }
    web = FakeWeb()
    for name in ("staging", "production"):
        web.github_environment(name)
    dns: dict[str, list[str]] = {}

    def fake_resolve(hostname: str) -> list[str]:
        if hostname not in dns:
            raise OSError("does not resolve")
        return sorted(dns[hostname])

    clock = Clock()
    monkeypatch.setattr(dome_deploy, "http_get", web)
    monkeypatch.setattr(dome_deploy, "websocket_status", web.websocket_status)
    monkeypatch.setattr(dome_deploy, "resolve", fake_resolve)
    monkeypatch.setattr(dome_deploy, "sleep", clock.sleep)
    monkeypatch.setattr(dome_deploy, "monotonic", clock.monotonic)
    yield World(tmp_path, state_path, log_path, env, web, dns, clock)


def decode_key(b64: str) -> bytes:
    return base64.b64decode(b64.encode(), validate=True)

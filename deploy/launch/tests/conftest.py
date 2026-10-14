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
ISSUER_HOST = "domeremote-staging.us.auth0.com"
CLIENT_ID = "AbCdEf0123456789AbCdEf0123456789"

SECURE_HEADERS = {
    "content-type": "text/html; charset=utf-8",
    "content-security-policy": "default-src 'self'",
    "x-content-type-options": "nosniff",
}


class FakeWeb:
    """URL -> HttpResult, HttpFailure or a callable returning one. Unknown URLs fail like DNS would."""

    def __init__(self) -> None:
        self.routes: dict[str, Any] = {}
        self.requests: list[str] = []

    def __call__(self, url: str, *, timeout: float = 0, headers: Any = None) -> HttpResult:
        self.requests.append(url)
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
        doc: dict[str, Any] = {"issuer": issuer or f"https://{host}/", "token_endpoint": f"https://{host}/oauth/token"}
        doc["token_endpoint_auth_methods_supported"] = methods or ["client_secret_basic", "client_secret_post"]
        self.json(f"https://{host}/.well-known/openid-configuration", doc)

    def healthz(
        self, base: str, *, commit: str | None = SHA, forwarded: str = "ok", headers: dict[str, str] | None = None
    ) -> None:
        body = {"status": "ok", "version": "0.1.0", "commit": commit, "database": "ok", "forwarded_client": forwarded}
        self.json(base + "/healthz", body, headers=headers)

    def page(self, base: str, *, robots: bool = True, hsts: bool = False) -> None:
        headers = dict(SECURE_HEADERS)
        if robots:
            headers["x-robots-tag"] = "noindex, nofollow"
        if hsts:
            headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
        self.routes[base + "/"] = HttpResult(200, headers, b"<!doctype html><title>DoMe</title>")


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
        out = io.StringIO()
        summary_path = self.tmp / "summary.md"
        if summary_path.exists():
            summary_path.unlink()
        full_env = {**self.env, "GITHUB_STEP_SUMMARY": str(summary_path), **env}
        full_env = {k: v for k, v in full_env.items() if v != "<unset>"}
        code = dome_deploy.main([command], full_env, out)
        summary = summary_path.read_text() if summary_path.exists() else ""
        return Run(code, out.getvalue(), summary)

    def staging_ready_web(self, app: str = "domeremote-staging", *, commit: str = SHA) -> None:
        self.web.discovery()
        self.web.healthz(f"https://{app}.fly.dev", commit=commit)
        self.web.page(f"https://{app}.fly.dev")


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
        "DOME_FALLBACK_DATABASE_URL": "",
        "DOME_OIDC_ISSUER": ISSUER_HOST,
        "DOME_OIDC_CLIENT_ID": CLIENT_ID,
        "FLY_ORG": "",
        "VITE_DOME_SUPPORT_URL": "mailto:support@domeremote.com",
        "DOME_SUPPORT_RESPONSE_EXPECTATION": "",
    }
    web = FakeWeb()
    dns: dict[str, list[str]] = {}

    def fake_resolve(hostname: str) -> list[str]:
        if hostname not in dns:
            raise OSError("does not resolve")
        return sorted(dns[hostname])

    clock = Clock()
    monkeypatch.setattr(dome_deploy, "http_get", web)
    monkeypatch.setattr(dome_deploy, "resolve", fake_resolve)
    monkeypatch.setattr(dome_deploy, "sleep", clock.sleep)
    monkeypatch.setattr(dome_deploy, "monotonic", clock.monotonic)
    yield World(tmp_path, state_path, log_path, env, web, dns, clock)


def decode_key(b64: str) -> bytes:
    return base64.b64decode(b64.encode(), validate=True)

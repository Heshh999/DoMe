"""deploy/fly.toml is an environment-neutral base with no secrets and no made-up values, and
deploy/launch/environments.toml is consistent with it and with the domain plan (deploy/README.md)."""

from __future__ import annotations

import ipaddress
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

import dome_deploy

ROOT = Path(__file__).resolve().parents[3]
FLY = tomllib.loads((ROOT / "deploy" / "fly.toml").read_text(encoding="utf-8"))
ENVS = dome_deploy.load_environments()
# Fly Managed Postgres regions in North America (fly.io/docs/mpg, read 2026-10-13); ewr is not one.
MPG_REGIONS = {"iad", "ord", "yyz", "dfw", "lax", "sjc"}
SECRET_NAMES = {
    "DOME_SESSION_SECRET",
    "DOME_OIDC_CLIENT_SECRET",
    "DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64",
    "DATABASE_URL",
    "DOME_DATABASE_URL",
    "DOME_STRIPE_SECRET_KEY",
    "DOME_STRIPE_WEBHOOK_SECRET",
}


def env_section() -> dict[str, Any]:
    env = FLY["env"]
    assert isinstance(env, dict)
    return env


def test_no_secrets_placeholders_or_per_environment_values() -> None:
    env = env_section()
    assert not SECRET_NAMES & set(env)
    for key in ("DOME_PUBLIC_ORIGIN", "DOME_OIDC_ISSUER", "DOME_OIDC_CLIENT_ID"):
        assert key not in env, f"{key} must come from the Deploy workflow, not a placeholder"
    assert not any(k.startswith("DOME_AI") for k in env)
    assert "PLACEHOLDER" not in (ROOT / "deploy" / "fly.toml").read_text()
    assert "args" not in FLY.get("build", {}), "build args come from the workflow"
    assert env["DOME_ENV"] == "staging"  # strict floor; production overrides it


def test_client_address_settings_are_private_ranges_and_the_fly_header() -> None:
    env = env_section()
    networks = [ipaddress.ip_network(n) for n in env["DOME_TRUSTED_PROXIES"].split(",")]
    assert networks and all(n.version == 4 and n.is_private for n in networks)
    assert env["DOME_CLIENT_IP_HEADER"] == "fly-client-ip"


def test_runtime_shape() -> None:
    env = env_section()
    port = int(env["DOME_API_BIND"].rsplit(":", 1)[1])
    assert env["DOME_API_BIND"].startswith("0.0.0.0:")  # IPv4 only: the trusted ranges above rely on it
    assert FLY["http_service"]["internal_port"] == port
    assert "--skip-migrations" in FLY["processes"]["app"]
    assert FLY["deploy"]["release_command"] == "alembic -c alembic.ini upgrade head"
    assert FLY["http_service"]["min_machines_running"] == 1
    assert FLY["http_service"]["auto_stop_machines"] == "off"
    assert FLY["http_service"]["force_https"] is True
    assert FLY["app"] == ENVS["staging"].fly_app  # the default is never production


@pytest.mark.parametrize("name", ["staging", "production"])
def test_environments_are_consistent(name: str) -> None:
    cfg = ENVS[name]
    assert cfg.dome_env == name and cfg.release_channel == name
    origin = urlsplit(cfg.public_origin)
    assert origin.scheme == "https" and origin.hostname == cfg.hostname and not origin.path
    assert cfg.region == FLY["primary_region"] and cfg.region in MPG_REGIONS
    assert cfg.hostname == cfg.dns_zone if cfg.dns_name == "@" else cfg.hostname == f"{cfg.dns_name}.{cfg.dns_zone}"
    assert cfg.database_plan == "Basic" and cfg.pg_major_version in (16, 17)
    assert dome_deploy._APP_NAME.match(cfg.fly_app)


def test_production_is_the_canonical_origin_and_requires_staging() -> None:
    prod, staging = ENVS["production"], ENVS["staging"]
    assert prod.public_origin == "https://domeremote.com" and prod.requires_staging_commit
    assert staging.public_origin == "https://staging.domeremote.com" and not staging.requires_staging_commit
    assert prod.fly_app != staging.fly_app and prod.database_cluster != staging.database_cluster
    assert prod.callback_url == "https://domeremote.com/v1/auth/callback"
    assert staging.callback_url == "https://staging.domeremote.com/v1/auth/callback"

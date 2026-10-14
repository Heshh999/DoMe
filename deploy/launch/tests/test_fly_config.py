"""deploy/fly.toml is an environment-neutral base with no secrets and no made-up values, and
deploy/launch/environments.toml is consistent with it and with the domain plan (deploy/README.md). The per-environment
copy the Deploy button writes differs from the base only in machine sleep (staging sleeps, production never does)."""

from __future__ import annotations

import copy
import dataclasses
import ipaddress
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

import dome_deploy

ROOT = Path(__file__).resolve().parents[3]
FLY_TEXT = (ROOT / "deploy" / "fly.toml").read_text(encoding="utf-8")
FLY = tomllib.loads(FLY_TEXT)
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
    # the base is the always-on (production) shape, the safe default for a manual `fly deploy`
    assert FLY["http_service"]["min_machines_running"] == 1
    assert FLY["http_service"]["auto_stop_machines"] == "off"
    assert FLY["http_service"]["auto_start_machines"] is False
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
    assert cfg.pg_major_version in (16, 17)
    assert dome_deploy._APP_NAME.match(cfg.fly_app)
    assert dome_deploy.environment_problems(cfg) == []


def test_production_is_the_canonical_origin_and_requires_staging() -> None:
    prod, staging = ENVS["production"], ENVS["staging"]
    assert prod.public_origin == "https://domeremote.com" and prod.requires_staging_commit
    assert staging.public_origin == "https://staging.domeremote.com" and not staging.requires_staging_commit
    assert prod.fly_app != staging.fly_app
    assert prod.callback_url == "https://domeremote.com/v1/auth/callback"
    assert staging.callback_url == "https://staging.domeremote.com/v1/auth/callback"


def test_databases_follow_the_budget_decision() -> None:
    """2026-10-14, about $50 a month in total: staging on Neon's free plan (external), production on Fly Managed
    Postgres Basic with 10 GB, the only database the Deploy button may create."""
    staging, prod = ENVS["staging"], ENVS["production"]
    assert staging.database_mode == "external" and staging.external_database
    assert staging.external_database_project == "domeremote-staging"
    assert (staging.database_cluster, staging.database_plan, staging.database_volume_gb) == ("", "", 0)
    assert prod.database_mode == "fly-managed" and not prod.external_database
    assert (prod.database_cluster, prod.database_plan, prod.database_volume_gb) == (
        "domeremote-production-db",
        "Basic",
        10,
    )


def test_staging_sleeps_and_production_stays_on() -> None:
    staging, prod = ENVS["staging"], ENVS["production"]
    assert staging.machine_sleep == {
        "auto_stop_machines": "stop",
        "auto_start_machines": True,
        "min_machines_running": 0,
    }
    assert prod.machine_sleep == {"auto_stop_machines": "off", "auto_start_machines": False, "min_machines_running": 1}
    assert prod.machine_sleep == {k: FLY["http_service"][k] for k in dome_deploy.MACHINE_SLEEP_KEYS}


@pytest.mark.parametrize("name", ["staging", "production"])
def test_generated_copy_changes_only_machine_sleep(name: str) -> None:
    cfg = ENVS[name]
    text = dome_deploy.render_fly_config(cfg, FLY_TEXT)
    assert text.startswith("# GENERATED by deploy/launch/dome_deploy.py for " + name)
    expected = copy.deepcopy(FLY)
    expected["http_service"].update(cfg.machine_sleep)
    assert tomllib.loads(text) == expected
    # every other line, comments included, is the base file's
    changed = [line for line in text.splitlines()[2:] if line not in FLY_TEXT.splitlines()]
    assert all(line.strip().split(" = ")[0] in dome_deploy.MACHINE_SLEEP_KEYS for line in changed)


@pytest.mark.parametrize(
    "broken",
    [
        FLY_TEXT.replace('  auto_stop_machines = "off"\n', ""),  # key missing
        FLY_TEXT.replace("  min_machines_running = 1\n", "  min_machines_running = 1\n  min_machines_running = 1\n"),
        FLY_TEXT.replace("[http_service]", "[services_moved]"),  # the keys exist, but not where Fly reads them
    ],
)
def test_generated_copy_refuses_a_base_it_does_not_understand(broken: str) -> None:
    assert broken != FLY_TEXT
    with pytest.raises(dome_deploy.Stop) as caught:
        dome_deploy.render_fly_config(ENVS["staging"], broken)
    assert caught.value.kind == "engineer"


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"database_mode": "neon"}, "database_mode must be one of"),
        ({"database_cluster": "x"}, "an external database has no Fly cluster"),
        ({"external_database_project": ""}, "needs external_database_project"),
        ({"auto_stop_machines": "sleep"}, "auto_stop_machines must be one of"),
        ({"auto_start_machines": False}, "must start again on the next request"),
        ({"min_machines_running": 2}, "must be 0 or 1"),
        ({"dome_env": "production"}, "production must stay on"),
    ],
)
def test_inconsistent_environment_sections_are_refused(changes: dict[str, Any], expected: str) -> None:
    broken = dataclasses.replace(ENVS["staging"], **changes)
    assert any(expected in problem for problem in dome_deploy.environment_problems(broken))


def test_a_managed_database_without_its_cluster_settings_is_refused() -> None:
    broken = dataclasses.replace(ENVS["production"], database_cluster="")
    assert any("needs database_cluster" in p for p in dome_deploy.environment_problems(broken))


def test_load_environments_stops_on_a_bad_file(tmp_path: Path) -> None:
    text = (ROOT / "deploy" / "launch" / "environments.toml").read_text(encoding="utf-8")
    bad = tmp_path / "environments.toml"
    bad.write_text(text.replace('auto_stop_machines = "off"', 'auto_stop_machines = "stop"'), encoding="utf-8")
    with pytest.raises(ValueError, match=r"\[production\]: .*production must stay on"):
        dome_deploy.load_environments(bad)


def test_generated_copies_are_never_committed() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "deploy/fly.*.generated.toml" in ignore
    assert dome_deploy.GENERATED_FLY_CONFIG == "deploy/fly.{name}.generated.toml"

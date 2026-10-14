"""dome_deploy.py end to end against a fake flyctl, a fake web and DNS: order of operations, what a re-run
leaves alone, when it stops before changing anything, and that no secret value ever reaches a command
line, the log or the summary."""

from __future__ import annotations

import base64
import io
import re

import pytest
from conftest import (
    CLIENT_ID,
    CLIENT_SECRET,
    FALLBACK_DB,
    FLY_TOKEN,
    FLY_TOKEN_PARTS,
    FM,
    ISSUER_HOST,
    OTHER_SHA,
    SHA,
    Run,
    World,
)
from fake_flyctl import FLY_DB_PASSWORD, V4, V6

import dome_deploy
from dome_deploy import HttpFailure, SecretBook, normalize_issuer, parse_list

MUTATING = (
    "flyctl apps create",
    "flyctl ips allocate-v6",
    "flyctl ips allocate-v4",
    "flyctl mpg create",
    "flyctl mpg attach",
    "flyctl secrets import",
    "flyctl deploy",
    "flyctl scale count",
    "flyctl certs add",
)


def mutating_calls(world: World) -> list[str]:
    return [c for c in world.commands() if c.startswith(MUTATING)]


def imports(world: World) -> list[dict[str, str]]:
    out = []
    for call in world.calls("flyctl"):
        if call["argv"][:2] == ["secrets", "import"]:
            out.append(dict(line.split("=", 1) for line in call["stdin"].splitlines()))
    return out


def deploy_argv(world: World) -> list[str]:
    [call] = [c for c in world.calls("flyctl") if c["argv"][0] == "deploy"]
    return list(call["argv"])


def all_secret_values(world: World) -> list[str]:
    values = [FLY_TOKEN, CLIENT_SECRET, FALLBACK_DB, FLY_DB_PASSWORD]
    values += list(FLY_TOKEN_PARTS)
    for app in world.state()["apps"].values():
        for name, value in app["secrets"].items():
            values.append(value)
            if name == "DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64":
                pem = base64.b64decode(value).decode()
                values += [line for line in pem.splitlines() if "-----" not in line]
    return [v for v in values if len(v) >= 8]


def assert_hygiene(world: World, run: Run) -> None:
    """No secret value in any argv, in the output (except its own ::add-mask:: line) or in the summary."""
    values = all_secret_values(world)
    for call in world.calls():
        for arg in call["argv"]:
            for value in values:
                assert value not in arg, (call["argv"][:2], "argv carries a secret")
        assert not call["env_has_client_secret"] and not call["env_has_fallback_db"]
    output = "\n".join(line for line in run.stdout.splitlines() if not line.startswith("::add-mask::"))
    for value in values:
        assert value not in output, "a secret value reached the log"
        assert value not in run.summary, "a secret value reached the summary"
    assert "BEGIN PRIVATE KEY" not in output and "BEGIN PRIVATE KEY" not in run.summary


def masked(run: Run) -> set[str]:
    return {line.removeprefix("::add-mask::") for line in run.stdout.splitlines() if line.startswith("::add-mask::")}


# ======================================================================================================
# first run, re-run
# ======================================================================================================


def test_first_staging_run_sets_up_everything_in_order(world: World) -> None:
    world.staging_ready_web()
    run = world.run("deploy", CREATE_DATABASE="true")
    assert run.code == 0, run.summary
    assert world.commands() == [
        "flyctl apps list",
        "flyctl mpg list",
        "flyctl apps create",
        "flyctl ips list",
        "flyctl ips allocate-v6",
        "flyctl ips allocate-v4",
        "flyctl ips list",
        "flyctl mpg create",
        "flyctl mpg list",
        "flyctl mpg attach",
        "flyctl secrets list",
        "flyctl secrets import",
        "docker build",
        "flyctl deploy",
        "flyctl machine list",
        "flyctl certs list",
        "flyctl certs add",
        "flyctl certs check",
    ]
    calls = {" ".join(c["argv"][:2]): c["argv"] for c in world.calls("flyctl")}
    assert calls["apps create"] == ["apps", "create", "domeremote-staging", "--org", "personal"]
    assert calls["ips allocate-v4"] == ["ips", "allocate-v4", "--shared", "-a", "domeremote-staging", "--yes"]
    assert calls["mpg create"] == [
        "mpg", "create", "--name", "domeremote-staging-db", "--org", "personal", "--region", "iad",
        "--plan", "Basic", "--volume-size", "10", "--pg-major-version", "16",
    ]  # fmt: skip
    assert deploy_argv(world) == [
        "deploy", "--config", "deploy/fly.toml", "--app", "domeremote-staging",
        "--image", f"dome-launch:git-{SHA[:12]}", "--local-only", "--image-label", f"git-{SHA[:12]}",
        "--primary-region", "iad", "--ha=false", "--no-public-ips", "--yes",
        "-e", "DOME_ENV=staging", "-e", "DOME_PUBLIC_ORIGIN=https://staging.domeremote.com",
        "-e", f"DOME_OIDC_ISSUER=https://{ISSUER_HOST}", "-e", f"DOME_OIDC_CLIENT_ID={CLIENT_ID}",
    ]  # fmt: skip
    [docker] = world.calls("docker")
    assert docker["argv"] == [
        "build", "--file", "deploy/Dockerfile", "--platform", "linux/amd64", "--tag", f"dome-launch:git-{SHA[:12]}",
        "--build-arg", "VITE_DOME_RELEASE_CHANNEL=staging",
        "--build-arg", "VITE_DOME_SUPPORT_URL=mailto:support@domeremote.com",
        "--build-arg", f"DOME_BUILD_COMMIT={SHA}", ".",
    ]  # fmt: skip
    # one staged import before the deploy, with the generated values and the client secret
    [staged] = imports(world)
    assert set(staged) == {"DOME_SESSION_SECRET", "DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64", "DOME_OIDC_CLIENT_SECRET"}
    assert staged["DOME_OIDC_CLIENT_SECRET"] == CLIENT_SECRET
    assert re.fullmatch(r"[0-9a-f]{64}", staged["DOME_SESSION_SECRET"])
    pem = base64.b64decode(staged["DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64"], validate=True).decode()
    assert pem.startswith("-----BEGIN PRIVATE KEY-----") and pem.rstrip().endswith("-----END PRIVATE KEY-----")
    # generated values are masked before use
    assert staged["DOME_SESSION_SECRET"] in masked(run)
    assert staged["DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64"] in masked(run)
    # the summary: honest wording, the DNS record to add, the callback to check
    assert "Staging deploy finished: running at https://domeremote-staging.fly.dev; no DNS record yet" in run.summary
    assert "Type **CNAME**, Name **staging**, Target **domeremote-staging.fly.dev**" in run.summary
    assert "DNS only" in run.summary and "https://staging.domeremote.com/v1/auth/callback" in run.summary
    assert "live at" not in run.summary and "verified" not in run.summary.lower()
    assert "created (Managed Postgres Basic, iad)" in run.summary
    assert_hygiene(world, run)


def test_rerun_creates_nothing_and_only_refreshes_the_client_secret(world: World) -> None:
    world.staging_ready_web()
    assert world.run("deploy", CREATE_DATABASE="true").code == 0
    first = world.state()["apps"]["domeremote-staging"]["secrets"]
    world.reset_log()
    run = world.run("deploy", CREATE_DATABASE="true")
    assert run.code == 0, run.summary
    assert mutating_calls(world) == ["flyctl secrets import", "flyctl deploy"]
    [staged] = imports(world)
    assert staged == {"DOME_OIDC_CLIENT_SECRET": CLIENT_SECRET}
    after = world.state()["apps"]["domeremote-staging"]["secrets"]
    for name in ("DOME_SESSION_SECRET", "DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64", "DATABASE_URL"):
        assert after[name] == first[name]
    assert "already there, unchanged" in run.summary
    assert_hygiene(world, run)


# ======================================================================================================
# stops before any change
# ======================================================================================================


def test_no_database_and_no_consent_stops_without_changes(world: World) -> None:
    world.staging_ready_web()
    run = world.run("deploy")
    assert run.code == 1
    assert mutating_calls(world) == []
    assert "Stopped: needs you" in run.summary and "$38 a month" in run.summary
    assert "Nothing was created or changed" in run.summary
    assert_hygiene(world, run)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"GITHUB_REF": "refs/heads/feature"}, "default branch"),
        ({"FLY_API_TOKEN": ""}, "`FLY_API_TOKEN`"),
        ({"DOME_OIDC_CLIENT_SECRET": "<unset>"}, "`DOME_OIDC_CLIENT_SECRET`"),
        ({"DOME_OIDC_ISSUER": ""}, "`DOME_OIDC_ISSUER`"),
        ({"DOME_OIDC_ISSUER": "http://idp.example.com"}, "must start with https://"),
        ({"DOME_OIDC_CLIENT_SECRET": "has a space in it 0123"}, "contains a space, a quote or #"),
        ({"DOME_OIDC_CLIENT_SECRET": "abcdef#ghijkl0123"}, "contains a space, a quote or #"),
        ({"FLY_API_TOKEN": "fo1_personalTokenValue0123456789"}, "personal sign-in token"),
        ({"DOME_OIDC_CLIENT_ID": CLIENT_SECRET}, "looks like the Client Secret"),
        ({"DOME_FALLBACK_DATABASE_URL": "mysql://x:y@z/db"}, "does not look like a database address"),
        ({"FLY_STAGING_APP": "Bad_Name"}, "not a valid Fly app name"),
        ({"VITE_DOME_SUPPORT_URL": "javascript:alert(1)"}, "must start with mailto: or https://"),
    ],
)
def test_bad_or_missing_inputs_stop_before_any_command(world: World, env: dict[str, str], expected: str) -> None:
    world.staging_ready_web()
    run = world.run("deploy", CREATE_DATABASE="true", **env)
    assert run.code == 1
    assert world.calls() == []
    assert "Stopped: needs you" in run.summary and expected in run.summary
    assert "Nothing was created or changed" in run.summary
    assert_hygiene(world, run)


def test_missing_values_name_the_exact_github_page_and_tab(world: World) -> None:
    run = world.run("deploy", FLY_API_TOKEN="", DOME_OIDC_CLIENT_ID="")
    assert "https://github.com/Heshh999/DoMe/settings/environments" in run.summary
    assert "**Environment secrets** add: `FLY_API_TOKEN`" in run.summary
    assert "**Environment variables** add: `DOME_OIDC_CLIENT_ID`" in run.summary
    assert "not repository secrets" in run.summary


def test_discovery_problems_stop_before_flyctl(world: World) -> None:
    world.web.discovery(issuer="https://someone-else.us.auth0.com/")
    run = world.run("deploy", CREATE_DATABASE="true")
    assert run.code == 1 and world.calls() == [] and "different issuer" in run.summary
    world.web.discovery(methods=["client_secret_basic"])
    run = world.run("deploy", CREATE_DATABASE="true")
    assert run.code == 1 and world.calls() == [] and "client_secret_post" in run.summary
    del world.web.routes[f"https://{ISSUER_HOST}/.well-known/openid-configuration"]
    run = world.run("deploy", CREATE_DATABASE="true")
    assert run.code == 1 and world.calls() == [] and "could not be reached" in run.summary


@pytest.mark.parametrize("failure", ["exit", "garbage"])
def test_unreadable_secret_list_never_generates_secrets(world: World, failure: str) -> None:
    world.staging_ready_web()
    world.add_app("domeremote-staging", secrets={"DATABASE_URL": "postgresql://u:p4ssw0rd123@h/d"})
    if failure == "exit":
        world.update(lambda s: s["fail"].update({"secrets list": {"exit": 1, "stderr": "Error: 502 from API\n"}}))
    else:
        world.update(lambda s: s.update({"secrets_list_garbage": True}))
    run = world.run("deploy")
    assert run.code == 1
    assert mutating_calls(world) == []
    assert "Could not read the secrets of domeremote-staging" in run.summary
    assert not any(line.startswith("::add-mask::") and len(line) == 12 + 64 for line in run.stdout.splitlines())


def test_production_app_with_machines_but_no_generated_secrets_is_left_alone(world: World) -> None:
    world.staging_ready_web(commit=SHA)
    world.web.discovery()
    world.add_app("domeremote-production", secrets={"DATABASE_URL": "postgresql://u:p4ssw0rd123@h/d"}, machines=1)
    run = world.run("deploy", DEPLOY_ENVIRONMENT="production")
    assert run.code == 1
    assert mutating_calls(world) == []
    assert "sign everyone out" in run.summary and "Failed: needs an engineer" in run.summary


# ======================================================================================================
# database paths
# ======================================================================================================


def test_existing_cluster_is_waited_for_then_attached(world: World) -> None:
    world.staging_ready_web()
    world.add_cluster("domeremote-staging-db", status="provisioning", ready_after_polls=2)
    run = world.run("deploy")
    assert run.code == 0, run.summary
    assert "flyctl mpg create" not in world.commands() and "flyctl mpg attach" in world.commands()
    assert world.clock.sleeps.count(dome_deploy.MPG_POLL_INTERVAL) >= 2
    assert "DATABASE_URL" in world.state()["apps"]["domeremote-staging"]["secrets"]
    assert_hygiene(world, run)


def test_attach_failure_falls_back_to_the_github_secret(world: World) -> None:
    world.staging_ready_web()
    world.add_cluster("domeremote-staging-db")
    world.update(lambda s: s.update({"attach_fails": True}))
    run = world.run("deploy", DOME_FALLBACK_DATABASE_URL=FALLBACK_DB)
    assert run.code == 0, run.summary
    [staged] = imports(world)
    assert staged["DATABASE_URL"] == FALLBACK_DB
    assert "taken from the GitHub secret DATABASE_URL" in run.summary
    assert_hygiene(world, run)


def test_attach_failure_without_fallback_explains_the_dashboard_path(world: World) -> None:
    world.staging_ready_web()
    world.add_cluster("domeremote-staging-db")
    world.update(lambda s: s.update({"attach_fails": True}))
    run = world.run("deploy")
    assert run.code == 1
    assert "flyctl deploy" not in world.commands()
    assert "Set secret and deploy" in run.summary and "Managed Postgres" in run.summary
    assert "Some steps of this run already changed things" in run.summary
    assert "403" not in run.stdout  # attach output is never shown, not even its error text
    assert_hygiene(world, run)


def test_no_cluster_listing_permission_without_fallback_gives_dashboard_steps(world: World) -> None:
    world.staging_ready_web()
    world.update(lambda s: s["fail"].update({"mpg list": {"exit": 1, "stderr": "Error: unauthorized\n"}}))
    run = world.run("deploy", CREATE_DATABASE="true")
    assert run.code == 1 and mutating_calls(world) == []
    assert "Create new cluster" in run.summary and "Fly refused this token" in run.summary


def test_cluster_creation_that_outlasts_its_budget_still_writes_a_summary(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.staging_ready_web()
    world.update(lambda s: s.update({"create_hang": True}))
    monkeypatch.setattr(dome_deploy, "MPG_CREATE_TIMEOUT", 1.0)
    run = world.run("deploy", CREATE_DATABASE="true")
    assert run.code == 1
    assert "still preparing the database" in run.summary and "Run Deploy again in about 10 minutes" in run.summary
    assert "flyctl mpg attach" not in world.commands()


def test_a_run_low_on_time_stops_before_building_and_keeps_its_work(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dome_deploy, "MPG_POLL_INTERVAL", 300.0)  # fewer fake polls, same budget logic
    world.staging_ready_web()
    # the cluster takes long enough that the run's own budget, not GitHub's job limit, ends it
    world.add_cluster("domeremote-staging-db", status="provisioning", ready_after_polls=10_000)
    run = world.run("deploy")
    assert run.code == 1
    assert "docker build" not in world.commands() and "flyctl mpg attach" not in world.commands()
    assert "still preparing the database" in run.summary
    assert (
        world.clock.now - 1000.0
        <= dome_deploy.RUN_BUDGET - dome_deploy.MIN_TIME_TO_DEPLOY + dome_deploy.MPG_POLL_INTERVAL
    )


def test_existing_database_secret_makes_the_github_copy_unused(world: World) -> None:
    world.staging_ready_web()
    world.add_app("domeremote-staging", secrets={"DATABASE_URL": "postgresql://u:p4ssw0rd123@h/d"})
    run = world.run("deploy", DOME_FALLBACK_DATABASE_URL=FALLBACK_DB)
    assert run.code == 0, run.summary
    assert "flyctl mpg list" not in world.commands()
    assert "DATABASE_URL" not in imports(world)[0]
    assert "You can delete that GitHub secret" in run.summary


# ======================================================================================================
# app name, machines, deploy failures
# ======================================================================================================


def test_taken_app_name_points_at_the_override_variable(world: World) -> None:
    world.staging_ready_web()
    world.update(lambda s: s.update({"taken_apps": ["domeremote-staging"]}))
    world.add_cluster("domeremote-staging-db")
    run = world.run("deploy")
    assert run.code == 1
    assert "belongs to someone else" in run.summary and "FLY_STAGING_APP" in run.summary
    world.reset_log()
    world.staging_ready_web(app="domeremote-staging-2")
    run = world.run("deploy", FLY_STAGING_APP="domeremote-staging-2")
    assert run.code == 0, run.summary
    assert ["apps", "create", "domeremote-staging-2", "--org", "personal"] in [c["argv"] for c in world.calls()]


def test_two_machines_after_deploy_are_scaled_to_one(world: World) -> None:
    world.staging_ready_web()
    world.add_cluster("domeremote-staging-db")
    world.update(lambda s: s.update({"machines_after_deploy": 2}))
    run = world.run("deploy")
    assert run.code == 0, run.summary
    assert ["scale", "count", "1", "--process-group", "app", "-a", "domeremote-staging", "--yes"] in [
        c["argv"] for c in world.calls()
    ]
    assert len(world.state()["apps"]["domeremote-staging"]["machines"]) == 1
    assert "scaled from 2 to 1" in run.summary


def test_build_failure_stops_before_deploying(world: World) -> None:
    world.staging_ready_web()
    world.add_cluster("domeremote-staging-db")
    world.update(lambda s: s.update({"docker_fails": True}))
    run = world.run("deploy")
    assert run.code == 1 and "flyctl deploy" not in world.commands()
    assert "did not build" in run.summary


def test_client_address_check_failure_after_deploy_needs_an_engineer(world: World) -> None:
    world.staging_ready_web()
    world.web.healthz("https://domeremote-staging.fly.dev", forwarded="unresolved_private")
    world.add_cluster("domeremote-staging-db")
    run = world.run("deploy")
    assert run.code == 1
    assert "Failed: needs an engineer" in run.summary and "DOME_TRUSTED_PROXIES" in run.summary


def test_old_commit_still_answering_is_retried_then_reported(world: World) -> None:
    world.staging_ready_web(commit=OTHER_SHA)
    world.add_cluster("domeremote-staging-db")
    run = world.run("deploy")
    assert run.code == 1
    assert world.clock.sleeps.count(dome_deploy.SMOKE_INTERVAL) >= dome_deploy.SMOKE_WAIT // dome_deploy.SMOKE_INTERVAL
    assert f"commit {OTHER_SHA[:12]}" in run.summary


# ======================================================================================================
# DNS states
# ======================================================================================================


def test_proxied_cloudflare_record_is_detected(world: World) -> None:
    world.staging_ready_web()
    world.add_cluster("domeremote-staging-db")
    world.dns["staging.domeremote.com"] = ["104.21.5.9", "2606:4700:3030::6815:509"]
    run = world.run("deploy")
    assert run.code == 0, run.summary
    assert "proxied by Cloudflare" in run.summary and "**DNS only** (grey cloud)" in run.summary


def test_correct_dns_but_no_certificate_yet(world: World) -> None:
    world.staging_ready_web()
    world.add_cluster("domeremote-staging-db")
    world.dns["staging.domeremote.com"] = [V4, V6]
    world.web.routes["https://staging.domeremote.com/healthz"] = HttpFailure("tls", "certificate verify failed")
    run = world.run("deploy")
    assert run.code == 0
    assert "certificate not issued yet" in run.summary and "Wait 10 minutes" in run.summary
    assert "live at" not in run.summary


def test_staging_live_on_its_own_name(world: World) -> None:
    world.staging_ready_web()
    world.add_cluster("domeremote-staging-db")
    world.dns["staging.domeremote.com"] = [V4, V6]
    world.web.healthz("https://staging.domeremote.com")
    run = world.run("deploy")
    assert run.code == 0
    assert "Staging deploy finished: live at https://staging.domeremote.com" in run.summary
    assert "this workflow cannot test sign-in" in run.summary


# ======================================================================================================
# production
# ======================================================================================================


def production_world(world: World) -> None:
    world.staging_ready_web(commit=SHA)
    world.web.healthz("https://domeremote-production.fly.dev")
    world.web.page("https://domeremote-production.fly.dev", robots=True, hsts=True)
    world.add_cluster("domeremote-production-db")


@pytest.mark.parametrize(
    ("commit", "forwarded", "expected"),
    [
        (OTHER_SHA, "ok", "Production only receives a commit that staging already runs"),
        (None, "ok", "Production only receives a commit that staging already runs"),
        (SHA, "unresolved_other", "client-address self-check is not ok"),
    ],
)
def test_production_refused_unless_staging_runs_this_commit_healthy(
    world: World, commit: str | None, forwarded: str, expected: str
) -> None:
    production_world(world)
    world.web.healthz("https://domeremote-staging.fly.dev", commit=commit, forwarded=forwarded)
    run = world.run("deploy", DEPLOY_ENVIRONMENT="production", CREATE_DATABASE="true")
    assert run.code == 1
    assert world.calls() == []  # before ANY flyctl call
    assert expected in run.summary
    gate = world.run("gate", DEPLOY_ENVIRONMENT="production")
    assert gate.code == 1 and expected in gate.summary


def test_production_unreachable_staging_is_refused(world: World) -> None:
    production_world(world)
    del world.web.routes["https://domeremote-staging.fly.dev/healthz"]
    run = world.run("deploy", DEPLOY_ENVIRONMENT="production")
    assert run.code == 1 and world.calls() == [] and "not reachable" in run.summary


def test_production_happy_path_goes_live_on_the_apex(world: World) -> None:
    production_world(world)
    world.dns["domeremote.com"] = [V4, V6]
    world.web.healthz(
        "https://domeremote.com", headers={"strict-transport-security": "max-age=31536000; includeSubDomains"}
    )
    run = world.run("deploy", DEPLOY_ENVIRONMENT="production")
    assert run.code == 0, run.summary
    argv = deploy_argv(world)
    assert "DOME_ENV=production" in argv and "DOME_PUBLIC_ORIGIN=https://domeremote.com" in argv
    assert argv[argv.index("--app") + 1] == "domeremote-production"
    [docker] = world.calls("docker")
    assert "VITE_DOME_RELEASE_CHANNEL=production" in docker["argv"]
    assert "Production deploy finished: live at https://domeremote.com" in run.summary
    assert "Restore rehearsal" in run.summary and "not done" in run.summary
    assert_hygiene(world, run)


def test_production_apex_records_are_a_and_aaaa(world: World) -> None:
    production_world(world)
    run = world.run("deploy", DEPLOY_ENVIRONMENT="production")
    assert run.code == 0, run.summary
    assert f"Type **A**, Name **@**, IPv4 address **{V4}**" in run.summary
    assert f"Type **AAAA**, Name **@**, IPv6 address **{V6}**" in run.summary
    assert "first delete any existing A, AAAA or CNAME record named **@**" in run.summary


def test_production_with_search_engine_header_on_apex_is_not_live(world: World) -> None:
    production_world(world)
    world.dns["domeremote.com"] = [V4, V6]
    world.web.healthz("https://domeremote.com", headers={"x-robots-tag": "noindex, nofollow"})
    run = world.run("deploy", DEPLOY_ENVIRONMENT="production")
    assert "live at https://domeremote.com" not in run.summary
    assert "production headers are wrong" in run.summary


def test_gate_passes_for_staging_and_for_production_when_staging_matches(world: World) -> None:
    assert world.run("gate").code == 0
    production_world(world)
    gate = world.run("gate", DEPLOY_ENVIRONMENT="production")
    assert gate.code == 0 and "production may be approved" in gate.summary
    assert world.calls() == []
    assert world.run("gate", GITHUB_REF="refs/heads/other").code == 1


# ======================================================================================================
# status
# ======================================================================================================


def test_status_never_runs_flyctl_and_needs_no_secrets(world: World) -> None:
    world.staging_ready_web()
    world.dns["domeremote-staging.fly.dev"] = [V4, V6]
    no_secrets = {k: "<unset>" for k in ("FLY_API_TOKEN", "DOME_OIDC_CLIENT_SECRET", "DOME_FALLBACK_DATABASE_URL")}
    run = world.run("status", **no_secrets)
    assert run.code == 0
    assert world.calls() == []
    assert "running at https://domeremote-staging.fly.dev; no DNS record yet" in run.summary
    assert "Type **CNAME**, Name **staging**, Target **domeremote-staging.fly.dev**" in run.summary
    # even with a token in its environment, status changes nothing
    assert world.run("status").code == 0 and world.calls() == []


def test_status_reports_not_deployed_and_live(world: World) -> None:
    run = world.run("status")
    assert "Staging is not deployed yet" in run.summary
    world.staging_ready_web()
    world.dns["domeremote-staging.fly.dev"] = [V4, V6]
    world.dns["staging.domeremote.com"] = [V4, V6]
    world.web.healthz("https://staging.domeremote.com")
    run = world.run("status")
    assert f"Staging is live at https://staging.domeremote.com (commit {SHA[:12]})" in run.summary


def test_status_reports_an_unhealthy_app(world: World) -> None:
    world.web.json("https://domeremote-staging.fly.dev/healthz", {"status": "degraded", "database": "unavailable"}, 503)
    run = world.run("status")
    assert "Staging is deployed but not healthy (HTTP 503)" in run.summary and world.calls() == []


def test_production_status_shows_both_commits(world: World) -> None:
    production_world(world)
    world.web.healthz("https://domeremote-production.fly.dev", commit=OTHER_SHA)
    run = world.run("status", DEPLOY_ENVIRONMENT="production")
    assert f"| Staging commit | {SHA[:12]} |" in run.summary
    assert f"| Production commit | {OTHER_SHA[:12]} |" in run.summary


# ======================================================================================================
# units: secrets, parsing, issuer
# ======================================================================================================


def test_secret_book_masks_token_parts_and_refuses_argv() -> None:
    out = io.StringIO()
    book = SecretBook(out)
    book.add_fly_token(FLY_TOKEN)
    lines = out.getvalue().splitlines()
    assert "::add-mask::" + FLY_TOKEN_PARTS[0] in lines
    assert "::add-mask::" + FLY_TOKEN_PARTS[1] in lines
    with pytest.raises(RuntimeError):
        book.check_argv(["flyctl", "--token", FLY_TOKEN_PARTS[1]])
    book.check_argv(["flyctl", "apps", "list"])


def test_redaction_catches_values_it_was_never_told_about() -> None:
    book = SecretBook(io.StringIO())
    book.add("known-secret-value-123", mask=False)
    text = book.redact(
        "x known-secret-value-123 y\n"
        f"  Connection string: postgresql://fly-user:{FLY_DB_PASSWORD}@pgbouncer.abc.flympg.net/fly-db\n"
        "  DATABASE_URL=postgres://a:b@c/d\n"
        f"token Fly{'V1'} {FM}abcdefghijklmnop,{FM}qrstuvwxyz012345 end\n"
        "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIA\n-----END PRIVATE KEY-----\n"
        "DOME_SESSION_SECRET=abc123 and SOME_TOKEN=xyz\n"
    )
    assert "known-secret-value-123" not in text and FLY_DB_PASSWORD not in text
    assert "postgres://a:b@" not in text and FM + "abcdefghijklmnop" not in text and "MC4CAQ" not in text
    assert "abc123" not in text and "xyz" not in text
    assert "postgresql://fly-user:***@pgbouncer.abc.flympg.net/fly-db" in text


def test_child_processes_do_not_get_the_client_secret(world: World) -> None:
    world.staging_ready_web()
    world.add_cluster("domeremote-staging-db")
    world.run("deploy", DOME_FALLBACK_DATABASE_URL=FALLBACK_DB)
    calls = world.calls()
    assert calls and all(not c["env_has_client_secret"] and not c["env_has_fallback_db"] for c in calls)
    assert all(c["env_has_fly_token"] for c in calls if c["tool"] == "flyctl")


def test_mpg_create_and_attach_output_is_never_shown(world: World) -> None:
    world.staging_ready_web()
    run = world.run("deploy", CREATE_DATABASE="true")
    assert run.code == 0
    assert FLY_DB_PASSWORD not in run.stdout and FLY_DB_PASSWORD not in run.summary
    assert "Connection string" not in run.stdout and "The following secret was added" not in run.stdout
    assert "(output hidden: it can contain credentials)" in run.stdout
    assert "flyctl mpg status" not in world.commands()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("domeremote-staging.us.auth0.com", "https://domeremote-staging.us.auth0.com"),
        ("https://domeremote-staging.us.auth0.com/", "https://domeremote-staging.us.auth0.com"),
        ("  https://DoMeRemote.us.auth0.com  ", "https://domeremote.us.auth0.com"),
        ("https://idp.example.com/realms/dome/", "https://idp.example.com/realms/dome"),
    ],
)
def test_issuer_normalization(raw: str, expected: str) -> None:
    assert normalize_issuer(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "http://x.us.auth0.com", "https://x.us.auth0.com:8443", "https://x.us.auth0.com/?a=b", "nodot", "a b.com"],
)
def test_issuer_normalization_refuses(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_issuer(raw)


def test_parse_list_is_lenient_but_never_guesses() -> None:
    assert (
        parse_list("No managed postgres clusters found in organization personal\n", empty_markers=("No managed",)) == []
    )
    assert parse_list("") == [] and parse_list("null") == []
    assert parse_list('[{"Id": "x", "Status": "ready"}]') == [{"Id": "x", "Status": "ready"}]
    assert parse_list('{"certificates": [{"hostname": "a"}]}') == [{"hostname": "a"}]
    assert parse_list("<html>502</html>") is None
    assert parse_list('{"error": "x"}') is None
    assert parse_list("[1, 2]") is None
    assert dome_deploy.field_of({"Id": 1}, "id") == 1


def test_http_get_refuses_plain_http() -> None:
    # no `world` fixture here, so this is the real function; it refuses before any network access
    with pytest.raises(ValueError):
        dome_deploy.http_get("http://example.com/")


def test_multiline_values_are_masked_line_by_line() -> None:
    out = io.StringIO()
    book = SecretBook(out)
    pem = "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIAbcdefghijklmnop\n-----END PRIVATE KEY-----\n"
    book.add(pem, mask=True)
    lines = out.getvalue().splitlines()
    assert lines == ["::add-mask::MC4CAQAwBQYDK2VwBCIEIAbcdefghijklmnop"]
    redacted = book.redact("key: " + pem + "again: " + pem.replace("\n", "\r\n"))
    assert "MC4CAQ" not in redacted and "BEGIN PRIVATE KEY" not in redacted

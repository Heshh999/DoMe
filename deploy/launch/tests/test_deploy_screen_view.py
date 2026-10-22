"""Screen view through the Deploy button (docs/design/screen-view.md §12, §20): the switch DOME_SCREEN_VIEW_ENABLED is a
variable passed to the app either way (off by default, "Screen view: off" in the summary); the relay server values are
both or neither (the variable DOME_TURN_KEY_ID with -e, the secret DOME_TURN_API_TOKEN on stdin), the analytics token
optional; a half set stops before any command; leftovers are removed when GitHub no longer has them; the running app
must report what was configured; status reports it without secrets; the gate refuses repository-level copies; and no
token ever reaches a command line, the log or the summary."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import TURN_ANALYTICS_TOKEN, TURN_API_TOKEN, TURN_KEY_ID, Run, World, turn_env
from fake_flyctl import V4, V6
from test_dome_deploy import assert_hygiene, deploy_argv, imports, mutating_calls

import dome_deploy

ROOT = Path(__file__).resolve().parents[3]
CLOUD_API = ROOT / "cloud-api" / "dome_api"
TOKENS = (TURN_API_TOKEN, TURN_ANALYTICS_TOKEN)
ON = "Screen view: ON (beta)"
OFF = "Screen view: off"
RELAY_ON = "relay server: Cloudflare TURN"
RELAY_OFF = "relay server: none"


def assert_turn_hygiene(world: World, run: Run, *extra: str) -> None:
    """No Cloudflare token (or ``extra`` value) in any argv, any child's environment, the log or the summary."""
    assert_hygiene(world, run)
    values = [*TOKENS, *extra]
    for call in world.calls():
        assert not call["env_has_turn_secret"], call["argv"][:2]
        for arg in call["argv"]:
            assert not any(v in arg for v in values), call["argv"][:2]
    output = "\n".join(line for line in run.stdout.splitlines() if not line.startswith("::add-mask::"))
    for value in values:
        assert value not in output and value not in run.summary


# ======================================================================================================
# on
# ======================================================================================================


def test_switch_and_both_turn_values_turn_screen_view_on_with_the_relay_server(world: World) -> None:
    world.staging_ready_web(screen_view="on", turn="ok")
    run = world.run("deploy", **turn_env())
    assert run.code == 0, run.summary
    [staged] = imports(world)
    assert staged["DOME_TURN_API_TOKEN"] == TURN_API_TOKEN
    assert staged["DOME_TURN_ANALYTICS_TOKEN"] == TURN_ANALYTICS_TOKEN
    assert "DOME_TURN_KEY_ID" not in staged and "DOME_SCREEN_VIEW_ENABLED" not in staged
    argv = deploy_argv(world)
    for name, value in (("DOME_SCREEN_VIEW_ENABLED", "true"), ("DOME_TURN_KEY_ID", TURN_KEY_ID)):
        assert argv[argv.index(f"{name}={value}") - 1] == "-e"
    app = world.state()["apps"]["domeremote-staging"]
    assert app["deployed_env"]["DOME_SCREEN_VIEW_ENABLED"] == "true"
    assert app["deployed_env"]["DOME_TURN_KEY_ID"] == TURN_KEY_ID
    assert app["secrets"]["DOME_TURN_API_TOKEN"] == TURN_API_TOKEN
    assert f"| Screen view | **{ON}**; {RELAY_ON}" in run.summary and "reconciliation on" in run.summary
    assert "| DOME_TURN_API_TOKEN, DOME_TURN_ANALYTICS_TOKEN | updated from GitHub |" in run.summary
    assert (
        "| domeremote-staging.fly.dev Screen view (/healthz) | reports screen view: on (beta), relay server: Cloudflare TURN, as configured |"
        in run.summary
    )
    assert_turn_hygiene(world, run)


def test_relay_server_without_the_analytics_token_says_reconciliation_is_off(world: World) -> None:
    world.staging_ready_web(screen_view="on", turn="ok")
    run = world.run("deploy", **turn_env(analytics=""))
    assert run.code == 0, run.summary
    [staged] = imports(world)
    assert staged["DOME_TURN_API_TOKEN"] == TURN_API_TOKEN and "DOME_TURN_ANALYTICS_TOKEN" not in staged
    assert "reconciliation off: no `DOME_TURN_ANALYTICS_TOKEN`" in run.summary
    assert "| DOME_TURN_API_TOKEN | updated from GitHub |" in run.summary
    assert_turn_hygiene(world, run)


def test_switch_on_without_turn_values_is_honest_about_direct_connections_only(world: World) -> None:
    world.staging_ready_web(screen_view="on", turn="unconfigured")
    run = world.run("deploy", DOME_SCREEN_VIEW_ENABLED="true")
    assert run.code == 0, run.summary
    [staged] = imports(world)
    assert not any(name.startswith("DOME_TURN_") for name in staged)
    argv = deploy_argv(world)
    assert "DOME_SCREEN_VIEW_ENABLED=true" in argv and not any("DOME_TURN_KEY_ID" in a for a in argv)
    assert (
        f"| Screen view | **{ON}**; {RELAY_OFF} (no `DOME_TURN_` values; Screen view works only where a direct connection exists) |"
        in run.summary
    )
    assert "reports screen view: on (beta), relay server: none, as configured" in run.summary


def test_turn_values_with_the_switch_off_keep_screen_view_off(world: World) -> None:
    world.staging_ready_web(screen_view="off", turn="ok")
    run = world.run("deploy", **turn_env(enabled=False))
    assert run.code == 0, run.summary
    assert "DOME_SCREEN_VIEW_ENABLED=false" in deploy_argv(world)
    assert (
        f"| Screen view | **{OFF}** (the variable `DOME_SCREEN_VIEW_ENABLED` is not `true` in this GitHub Environment); {RELAY_ON}"
        in run.summary
    )
    assert_turn_hygiene(world, run)


def test_production_takes_the_same_values(world: World) -> None:
    world.production_ready_web(screen_view="on", turn="ok")
    world.add_cluster("domeremote-production-db")
    run = world.run("deploy", DEPLOY_ENVIRONMENT="production", **turn_env())
    assert run.code == 0, run.summary
    assert imports(world)[0]["DOME_TURN_API_TOKEN"] == TURN_API_TOKEN
    assert ON in run.summary and RELAY_ON in run.summary
    assert_turn_hygiene(world, run)


# ======================================================================================================
# off by default, switching off, leftovers
# ======================================================================================================


def test_no_values_keep_screen_view_off_honestly(world: World) -> None:
    world.staging_ready_web()
    run = world.run("deploy")
    assert run.code == 0, run.summary
    [staged] = imports(world)
    assert not any(name.startswith("DOME_TURN_") for name in staged)
    argv = deploy_argv(world)
    assert "DOME_SCREEN_VIEW_ENABLED=false" in argv and not any("DOME_TURN_" in a for a in argv)
    assert "secrets unset" not in " ".join(world.commands())
    assert (
        f"| Screen view | **{OFF}** (the variable `DOME_SCREEN_VIEW_ENABLED` is not `true` in this GitHub Environment); {RELAY_OFF}"
        in run.summary
    )
    assert (
        "| domeremote-staging.fly.dev Screen view (/healthz) | reports screen view: off, relay server: none, as configured |"
        in run.summary
    )


def test_removing_the_turn_values_from_github_removes_them_from_fly(world: World) -> None:
    world.staging_ready_web(screen_view="on", turn="ok")
    assert world.run("deploy", **turn_env()).code == 0
    world.reset_log()
    world.web.healthz("https://domeremote-staging.fly.dev", screen_view="on", turn="unconfigured")
    run = world.run("deploy", DOME_SCREEN_VIEW_ENABLED="true")
    # without the removal the app would refuse to start (a token and no key id: "half configured")
    assert run.code == 0, run.summary
    assert mutating_calls(world) == ["flyctl secrets import", "flyctl secrets unset", "flyctl deploy"]
    [unset] = [c["argv"] for c in world.calls("flyctl") if c["argv"][:2] == ["secrets", "unset"]]
    assert unset == ["secrets", "unset", "--stage", "-a", "domeremote-staging", "DOME_TURN_API_TOKEN", "DOME_TURN_ANALYTICS_TOKEN"]  # fmt: skip
    secrets = world.state()["apps"]["domeremote-staging"]["secrets"]
    assert not any(name.startswith("DOME_TURN_") for name in secrets)
    assert "| DOME_TURN_API_TOKEN, DOME_TURN_ANALYTICS_TOKEN | removed from Fly with this deploy" in run.summary
    assert "The Screen view relay server was switched **off** by this deploy" in run.summary
    assert_turn_hygiene(world, run)


def test_removing_only_the_analytics_token_removes_only_it(world: World) -> None:
    world.staging_ready_web(screen_view="on", turn="ok")
    assert world.run("deploy", **turn_env()).code == 0
    world.reset_log()
    run = world.run("deploy", **turn_env(analytics=""))
    assert run.code == 0, run.summary
    [unset] = [c["argv"] for c in world.calls("flyctl") if c["argv"][:2] == ["secrets", "unset"]]
    assert unset[5:] == ["DOME_TURN_ANALYTICS_TOKEN"]
    assert "switched **off**" not in run.summary
    assert world.state()["apps"]["domeremote-staging"]["secrets"]["DOME_TURN_API_TOKEN"] == TURN_API_TOKEN


def test_a_key_id_kept_as_a_fly_secret_by_hand_is_removed(world: World) -> None:
    world.staging_ready_web(screen_view="on", turn="ok")
    assert world.run("deploy", **turn_env()).code == 0
    world.update(lambda s: s["apps"]["domeremote-staging"]["secrets"].update(DOME_TURN_KEY_ID="byhand0123456789"))
    world.reset_log()
    run = world.run("deploy", **turn_env())
    assert run.code == 0, run.summary
    [unset] = [c["argv"] for c in world.calls("flyctl") if c["argv"][:2] == ["secrets", "unset"]]
    assert unset[5:] == ["DOME_TURN_KEY_ID"]


# ======================================================================================================
# stops before any command
# ======================================================================================================


@pytest.mark.parametrize("missing", ["DOME_TURN_KEY_ID", "DOME_TURN_API_TOKEN"])
def test_half_configured_relay_server_stops_before_any_command(world: World, missing: str) -> None:
    world.staging_ready_web()
    run = world.run("deploy", **{**turn_env(), missing: ""})
    assert run.code == 1 and world.calls() == []
    assert "Stopped: needs you" in run.summary and "Nothing was created or changed" in run.summary
    assert (
        f"Screen view's relay server is half set up in the staging environment: `{missing}` is missing." in run.summary
    )
    assert "both `DOME_TURN_KEY_ID` and `DOME_TURN_API_TOKEN` or neither" in run.summary
    tab = "secrets" if missing == "DOME_TURN_API_TOKEN" else "variables"
    assert f"**Environment {tab}** add: `{missing}`" in run.summary
    assert "delete every `DOME_TURN_` secret and variable" in run.summary
    assert_turn_hygiene(world, run)


def test_an_analytics_token_alone_stops(world: World) -> None:
    world.staging_ready_web()
    run = world.run("deploy", DOME_TURN_ANALYTICS_TOKEN=TURN_ANALYTICS_TOKEN)
    assert run.code == 1 and world.calls() == []
    assert "`DOME_TURN_ANALYTICS_TOKEN` is set, but the relay server key is not" in run.summary
    assert_turn_hygiene(world, run)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"DOME_SCREEN_VIEW_ENABLED": "yes"}, "must be `true` or `false`"),
        ({"DOME_TURN_KEY_ID": "has space"}, "`DOME_TURN_KEY_ID` is not a TURN key id"),
        ({"DOME_TURN_KEY_ID": "short"}, "`DOME_TURN_KEY_ID` is not a TURN key id"),
        ({"DOME_TURN_API_TOKEN": TURN_API_TOKEN + " x"}, "`DOME_TURN_API_TOKEN` contains a space"),
        ({"DOME_TURN_API_TOKEN": "tiny"}, "`DOME_TURN_API_TOKEN` contains a space, a quote or #, or is too short"),
        ({"DOME_TURN_ANALYTICS_TOKEN": "x#y0123456789abcdef"}, "`DOME_TURN_ANALYTICS_TOKEN` contains a space"),
    ],
)
def test_unusable_screen_view_values_stop_before_any_command(world: World, env: dict[str, str], expected: str) -> None:
    world.staging_ready_web()
    run = world.run("deploy", **{**turn_env(), **env})
    assert run.code == 1 and world.calls() == [], run.summary
    assert "Stopped: needs you" in run.summary and expected in run.summary
    assert "docs/design/screen-view.md section 20" in run.summary
    assert_turn_hygiene(world, run, *(v for v in env.values() if len(v) >= 12))


@pytest.mark.parametrize("names", ["DOME_TURN_API_TOKEN ", "DOME_TURN_API_TOKEN DOME_TURN_ANALYTICS_TOKEN"])
def test_a_token_saved_as_a_variable_must_be_replaced(world: World, names: str) -> None:
    world.staging_ready_web()
    env = turn_env()
    for name in names.split():
        env[name] = ""  # a variable never reaches the program: only its NAME, through the probe
    run = world.run("deploy", TURN_SECRETS_AS_VARIABLES=names, **env)
    assert run.code == 1 and world.calls() == []
    for name in names.split():
        assert f"`{name}` is saved as an Environment **variable**" in run.summary
        assert f"Delete the **variable** `{name}`" in run.summary
    assert "**Realtime** > **TURN Server**" in run.summary
    assert ("**API Tokens**" in run.summary) == ("ANALYTICS" in names)
    assert "half set up" not in run.summary  # not reported twice


# ======================================================================================================
# checks after the deploy, status, gate
# ======================================================================================================


@pytest.mark.parametrize(
    ("screen_view", "turn", "expected"),
    [
        (
            "off",
            "unconfigured",
            "/healthz reports Screen view off with relay server unconfigured, but this deploy configured on with relay server ok",
        ),
        (
            "on",
            "unconfigured",
            "/healthz reports Screen view on with relay server unconfigured, but this deploy configured on with relay server ok",
        ),
        (None, "unconfigured", "/healthz did not answer with the Screen view fields (an older build?)"),
    ],
)
def test_the_running_app_must_report_the_configured_screen_view(
    world: World, screen_view: str | None, turn: str, expected: str
) -> None:
    world.staging_ready_web(screen_view=screen_view, turn=turn)
    run = world.run("deploy", **turn_env())
    assert run.code == 1 and "Failed: needs an engineer" in run.summary
    assert expected in run.summary
    assert_turn_hygiene(world, run)


def test_screen_view_reported_on_although_github_says_off_fails(world: World) -> None:
    world.staging_ready_web(screen_view="on", turn="ok")
    run = world.run("deploy")
    assert run.code == 1
    assert (
        "/healthz reports Screen view on with relay server ok, but this deploy configured off with relay server unconfigured"
        in run.summary
    )


@pytest.mark.parametrize(
    ("screen_view", "turn", "expected"),
    [
        ("off", "unconfigured", f"| Screen view | **{OFF}**; {RELAY_OFF}"),
        ("on", "ok", f"| Screen view | **{ON}**; {RELAY_ON} configured |"),
        ("on", "error", f"| Screen view | **{ON}**; {RELAY_ON} configured but not answering"),
        (None, "unconfigured", "| Screen view | unknown (/healthz has no Screen view fields: an older build) |"),
    ],
)
def test_status_reports_screen_view_without_secrets_or_flyctl(
    world: World, screen_view: str | None, turn: str, expected: str
) -> None:
    world.staging_ready_web(screen_view=screen_view, turn=turn)
    world.dns["domeremote-staging.fly.dev"] = [V4, V6]
    no_secrets = {k: "<unset>" for k in ("FLY_API_TOKEN", "DOME_OIDC_CLIENT_SECRET", *dome_deploy.TURN_NAMES)}
    run = world.run("status", **no_secrets)
    assert run.code == 0 and world.calls() == []
    assert expected in run.summary


@pytest.mark.parametrize(
    ("probe", "steps"),
    [
        ("DOME_TURN_API_TOKEN ", ["**Realtime** > **TURN Server**"]),
        ("DOME_TURN_ANALYTICS_TOKEN", ["**API Tokens**"]),
    ],
)
def test_gate_refuses_turn_repository_secrets(world: World, probe: str, steps: list[str]) -> None:
    run = world.run("gate", REPO_LEVEL_SECRETS=probe)
    assert run.code == 1 and "Stopped: needs you" in run.summary
    for name in probe.split():
        assert f"`{name}`" in run.summary
    for step in steps:
        assert step in run.summary
    assert world.web.requests == [] and world.calls() == []


# ======================================================================================================
# agreement with cloud-api
# ======================================================================================================


def test_names_and_rules_match_cloud_api_settings() -> None:
    text = (CLOUD_API / "settings.py").read_text(encoding="utf-8")
    fields = re.search(r"_TURN_FIELDS = \(([^)]*)\)", text)
    assert fields is not None
    both = {"DOME_" + f.upper() for f in re.findall(r'"([a-z_]+)"', fields.group(1))}
    assert both == {dome_deploy.TURN_KEY_ID, "DOME_TURN_API_TOKEN"}
    assert "turn_analytics_token: SecretStr" in text and "screen_view_enabled: bool = False" in text
    assert 'pattern=r"^[A-Za-z0-9_-]{0,128}$"' in text  # the deploy check is the same shape with a floor
    health = (CLOUD_API / "routes" / "health.py").read_text(encoding="utf-8")
    assert "svc.screen.health()" in health
    service = (CLOUD_API / "screen" / "service.py").read_text(encoding="utf-8")
    assert '"screen_view": "on" if self.config.enabled else "off"' in service
    assert set(dome_deploy.SCREEN_VIEW_TEXT) == {"off", "on"} and set(dome_deploy.TURN_TEXT) == {"off", "on"}

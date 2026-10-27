"""The safety rules of .github/workflows/deploy.yml (and the parts of ci.yml that touch it), as tests.

PyYAML reads the key ``on`` as the boolean True (YAML 1.1); the helper below accepts both."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

import dome_deploy

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
DEPLOY = WORKFLOWS / "deploy.yml"
PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
DOCKERFILE = ROOT / "deploy" / "Dockerfile"
# The one form of `secrets.` allowed outside the deploy step: GitHub evaluates it and the gate receives a NAME.
PROBE = "${{{{ secrets.{0} != '' && '{0}{1}' || '' }}}}"
FLYCTL_URL = "https://github.com/superfly/flyctl/releases/download/v0.4.110/flyctl_0.4.110_Linux_x86_64.tar.gz"
# From flyctl_0.4.110_checksums.txt of the v0.4.110 release (fetched 2026-10-14; the tarball hashed the same).
FLYCTL_SHA256 = "62b48471d4d560831ec27a3c316c402750d34a3f5f8b956459d7cc5f6125ec97"


def repo_level_probe() -> str:
    names = dome_deploy.DEPLOY_SECRET_NAMES
    return "".join(PROBE.format(n, " " if i < len(names) - 1 else "") for i, n in enumerate(names))


def load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def triggers(data: dict[Any, Any]) -> Any:
    return data.get("on", data.get(True))


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("steps", []))


@pytest.fixture(scope="module")
def wf() -> dict[str, Any]:
    return load(DEPLOY)


def test_triggered_by_hand_only(wf: dict[str, Any]) -> None:
    on = triggers(wf)
    assert isinstance(on, dict) and set(on) == {"workflow_dispatch"}
    text = DEPLOY.read_text()
    for forbidden in (
        "pull_request",
        "pull_request_target",
        "push:",
        "schedule:",
        "workflow_run",
        "repository_dispatch",
    ):
        assert forbidden not in text.split("name: Deploy", 1)[1], forbidden


def test_inputs_are_exactly_the_three_buttons(wf: dict[str, Any]) -> None:
    inputs = triggers(wf)["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"environment", "action", "create_database_if_missing"}
    assert inputs["environment"]["type"] == "choice"
    assert inputs["environment"]["options"] == ["staging", "production"]
    assert inputs["environment"]["default"] == "staging"
    assert inputs["action"]["type"] == "choice" and inputs["action"]["options"] == ["deploy", "status"]
    assert inputs["action"]["default"] == "deploy"
    create = inputs["create_database_if_missing"]
    assert create["type"] == "boolean" and create["default"] is False
    assert "about $41 a month" in create["description"] and "about $45 a month" in create["description"]
    # the cost consent is production's only: staging's database is a free Neon database, never created by Deploy
    assert create["description"].startswith("Production only") and "Staging ignores this" in create["description"]


def test_read_only_token_and_three_jobs(wf: dict[str, Any]) -> None:
    assert wf["permissions"] == {"contents": "read"}
    assert set(wf["jobs"]) == {"gate", "deploy", "status"}
    # only the gate reads more: the environments' protection rules (actions: read); nothing can write
    assert wf["jobs"]["gate"]["permissions"] == {"contents": "read", "actions": "read"}
    for name, job in wf["jobs"].items():
        if name != "gate":
            assert "permissions" not in job
        assert isinstance(job.get("timeout-minutes"), int)
    assert "write" not in DEPLOY.read_text().split("name: Deploy", 1)[1]


def test_every_action_is_pinned_to_a_commit(wf: dict[str, Any]) -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = load(path)
        for job in data["jobs"].values():
            for step in steps(job):
                if "uses" in step:
                    assert PINNED.match(step["uses"]), (path.name, step["uses"])
    lines = [line for line in DEPLOY.read_text().splitlines() if "uses:" in line]
    assert lines and all("# " in line for line in lines), "every pin carries its tag as a comment"


def test_flyctl_is_downloaded_and_checked_by_sha256(wf: dict[str, Any]) -> None:
    deploy_steps = steps(wf["jobs"]["deploy"])
    [install] = [s for s in deploy_steps if s.get("name", "").startswith("Install flyctl")]
    assert "uses" not in install and "env" not in install  # no action, and no secret near the download
    script = install["run"]
    assert "set -euo pipefail" in script and FLYCTL_URL in script and "--proto '=https'" in script
    assert f'echo "{FLYCTL_SHA256}  $dir/flyctl.tar.gz" | sha256sum -c -' in script
    # the check comes before the archive is opened, and only flyctl is extracted into a directory of its own
    assert script.index("sha256sum -c") < script.index("tar -xzf")
    assert 'tar -xzf "$dir/flyctl.tar.gz" -C "$dir" flyctl' in script and 'dir="$RUNNER_TEMP/flyctl"' in script
    assert 'echo "$dir" >> "$GITHUB_PATH"' in script
    assert deploy_steps.index(install) < len(deploy_steps) - 1  # installed before the step with the secrets
    assert "setup-flyctl" not in DEPLOY.read_text()


def test_checkout_settings(wf: dict[str, Any]) -> None:
    for job in wf["jobs"].values():
        for step in steps(job):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_deploy_job_shape(wf: dict[str, Any]) -> None:
    job = wf["jobs"]["deploy"]
    assert job["needs"] == "gate"
    assert job["if"] == "inputs.action == 'deploy'"
    assert job["environment"]["name"] == "${{ inputs.environment }}"
    assert job["concurrency"] == {"group": "deploy-${{ inputs.environment }}", "cancel-in-progress": False}
    # no silent skip on another branch: the gate job and the environments' branch rule refuse it visibly
    assert "ref" not in job["if"] and "branch" not in job["if"]
    assert wf["jobs"]["gate"]["if"] == "inputs.action == 'deploy'"
    assert wf["jobs"]["status"]["if"] == "inputs.action == 'status'"


def test_secrets_only_in_the_deploy_step_env(wf: dict[str, Any]) -> None:
    holders = []
    for job_name, job in wf["jobs"].items():
        assert "secrets." not in str({k: v for k, v in job.items() if k != "steps"}), job_name
        for step in steps(job):
            if "secrets." in str(step):
                holders.append((job_name, step["name"]))
                for key, value in step.items():
                    if key != "env":
                        assert "secrets." not in str(value), (job_name, key)
                for name, value in step["env"].items():
                    if "secrets." not in str(value):
                        continue
                    if job_name == "gate":
                        assert name == "REPO_LEVEL_SECRETS" and value == repo_level_probe(), name
                    else:
                        assert re.fullmatch(r"\$\{\{ secrets\.[A-Z_]+ \}\}", value), name
    assert holders == [
        ("gate", "Branch, no repository secrets, protected environments, and for production, staging runs this commit"),
        ("deploy", "Set up what is missing, deploy, check"),
    ]
    env = steps(wf["jobs"]["deploy"])[-1]["env"]
    assert {k for k, v in env.items() if "secrets." in v} == {
        "FLY_API_TOKEN",
        "DOME_OIDC_CLIENT_SECRET",
        "DEPLOY_DATABASE_URL",
        "DOME_STRIPE_SECRET_KEY",
        "DOME_STRIPE_WEBHOOK_SECRET",
        "DOME_TURN_API_TOKEN",
        "DOME_TURN_ANALYTICS_TOKEN",
    }
    assert env["DEPLOY_DATABASE_URL"] == "${{ secrets.DATABASE_URL }}"
    for name in (*dome_deploy.STRIPE_SECRET_NAMES, *dome_deploy.TURN_SECRET_NAMES):
        assert env[name] == f"${{{{ secrets.{name} }}}}"
    # the program drops them (and the client secret) from every child process's environment
    assert {"DEPLOY_DATABASE_URL", "DOME_OIDC_CLIENT_SECRET"} <= dome_deploy.CHILD_ENV_DROP
    assert set(dome_deploy.STRIPE_SECRET_NAMES) <= dome_deploy.CHILD_ENV_DROP
    assert set(dome_deploy.TURN_SECRET_NAMES) <= dome_deploy.CHILD_ENV_DROP


def test_stripe_price_ids_are_variables_and_secrets_saved_as_variables_reach_the_program_as_names_only(
    wf: dict[str, Any],
) -> None:
    env = steps(wf["jobs"]["deploy"])[-1]["env"]
    assert list(dome_deploy.STRIPE_PRICES) == [
        "DOME_STRIPE_PRICE_PRO_MONTHLY",
        "DOME_STRIPE_PRICE_PRO_ANNUAL",
        "DOME_STRIPE_PRICE_PRO_PLUS_MONTHLY",
        "DOME_STRIPE_PRICE_PRO_PLUS_ANNUAL",
        "DOME_STRIPE_PRICE_TOPUP",
    ]
    for name in dome_deploy.STRIPE_PRICES:
        assert env[name] == f"${{{{ vars.{name} }}}}", name
    # a secret saved as a VARIABLE never reaches the step env as a value (variables are printed in the step's log)
    probe = "".join(
        f"${{{{ vars.{n} != '' && '{n}{' ' if i == 0 else ''}' || '' }}}}"
        for i, n in enumerate(dome_deploy.STRIPE_SECRET_NAMES)
    )
    assert env["STRIPE_SECRETS_AS_VARIABLES"] == probe
    text = DEPLOY.read_text()
    for name in dome_deploy.STRIPE_SECRET_NAMES:
        assert f"vars.{name} }}}}" not in text and f"vars.{name}}}}}" not in text
    for name in dome_deploy.STRIPE_PRICES:
        assert f"secrets.{name}" not in text
    # nothing billing-related anywhere else: the gate sees only names, status nothing at all
    assert "STRIPE" not in str(wf["jobs"]["status"])
    assert "DOME_STRIPE_AUTOMATIC_TAX" not in text  # Stripe Tax stays off (prices are tax-inclusive)


def test_screen_view_switch_and_turn_key_id_are_variables_and_the_tokens_secrets(wf: dict[str, Any]) -> None:
    """The Screen view values (docs/design/screen-view.md section 20) travel like the Stripe ones."""
    env = steps(wf["jobs"]["deploy"])[-1]["env"]
    assert env["DOME_SCREEN_VIEW_ENABLED"] == "${{ vars.DOME_SCREEN_VIEW_ENABLED }}"
    assert env["DOME_TURN_KEY_ID"] == "${{ vars.DOME_TURN_KEY_ID }}"
    for name in dome_deploy.TURN_SECRET_NAMES:
        assert env[name] == f"${{{{ secrets.{name} }}}}"
    probe = "".join(
        f"${{{{ vars.{n} != '' && '{n}{' ' if i == 0 else ''}' || '' }}}}"
        for i, n in enumerate(dome_deploy.TURN_SECRET_NAMES)
    )
    assert env["TURN_SECRETS_AS_VARIABLES"] == probe
    text = DEPLOY.read_text()
    for name in dome_deploy.TURN_SECRET_NAMES:
        assert f"vars.{name} }}}}" not in text and f"vars.{name}}}}}" not in text
    for name in ("DOME_TURN_KEY_ID", "DOME_SCREEN_VIEW_ENABLED"):
        assert f"secrets.{name}" not in text
    assert "TURN" not in str(wf["jobs"]["status"]) and "SCREEN_VIEW" not in str(wf["jobs"]["status"])
    # the tuning values stay at the app's defaults: never passed by the Deploy button
    for name in (
        "DOME_TURN_PROVIDER",
        "DOME_SCREEN_TURN_",
        "DOME_SCREEN_FREE_SECONDS_OVERRIDE",
        "DOME_SCREEN_STUN_URLS",
    ):
        assert name not in text


def test_gate_and_status_have_no_environment_and_no_secret_values(wf: dict[str, Any]) -> None:
    for name in ("gate", "status"):
        job = wf["jobs"][name]
        assert "environment" not in job
        assert not any("flyctl" in str(s) for s in steps(job))
    assert "secrets." not in str(wf["jobs"]["status"])
    # the gate sees secrets only through the probe: GitHub evaluates `!= ''` and hands over names, never values
    [probe_step] = [s for s in steps(wf["jobs"]["gate"]) if "env" in s]
    env = probe_step["env"]
    assert env["REPO_LEVEL_SECRETS"] == repo_level_probe()
    assert env["GH_TOKEN"] == "${{ github.token }}"
    assert all("secrets." not in str(v) for k, v in env.items() if k != "REPO_LEVEL_SECRETS")
    assert set(dome_deploy.DEPLOY_SECRET_NAMES) == {
        "FLY_API_TOKEN",
        "DOME_OIDC_CLIENT_SECRET",
        "DATABASE_URL",
        "DOME_STRIPE_SECRET_KEY",
        "DOME_STRIPE_WEBHOOK_SECRET",
        "DOME_TURN_API_TOKEN",
        "DOME_TURN_ANALYTICS_TOKEN",
    }
    deploy_env = steps(wf["jobs"]["deploy"])[-1]["env"]
    probed = {v.split("secrets.")[1].split(" ")[0] for v in deploy_env.values() if "secrets." in v}
    assert probed == set(dome_deploy.DEPLOY_SECRET_NAMES), "every secret the deploy job reads is probed"


def test_no_environment_url_that_would_claim_a_live_site(wf: dict[str, Any]) -> None:
    assert wf["jobs"]["deploy"]["environment"] == {"name": "${{ inputs.environment }}"}
    assert "domeremote.com" not in str(wf["jobs"])


def test_no_expressions_inside_scripts_and_no_tracing() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = load(path)
        for job in data["jobs"].values():
            for step in steps(job):
                run = step.get("run", "")
                if path.name in ("deploy.yml", "ci.yml"):
                    assert "${{" not in run, (path.name, step.get("name"))
                assert "set -x" not in run and "xtrace" not in run
        assert "ACTIONS_STEP_DEBUG" not in path.read_text()
        assert "ACTIONS_RUNNER_DEBUG" not in path.read_text()


def test_each_job_runs_the_program_once_with_its_own_mode(wf: dict[str, Any]) -> None:
    runs = {name: [s["run"] for s in steps(job) if "run" in s] for name, job in wf["jobs"].items()}
    program = [r for job in runs.values() for r in job if "dome_deploy.py" in r]
    assert program == [
        "/usr/bin/python3 -I deploy/launch/dome_deploy.py gate",
        "/usr/bin/python3 -I deploy/launch/dome_deploy.py deploy",
        "/usr/bin/python3 -I deploy/launch/dome_deploy.py status",
    ]
    assert runs["gate"] == [program[0]] and runs["status"] == [program[2]]
    assert runs["deploy"][-1] == program[1] and len(runs["deploy"]) == 2  # flyctl install, then the program


def test_dockerfile_release_channel_defaults_to_production() -> None:
    """A build without the argument (the manual `fly deploy --config deploy/fly.toml` path) must never carry the
    test-only Screen view readout (mobile-app DECISIONS 82); the Deploy workflow passes the environment's channel."""
    text = DOCKERFILE.read_text()
    assert re.findall(r"^ARG VITE_DOME_RELEASE_CHANNEL=(\S*)$", text, re.MULTILINE) == ["production"]


def test_dockerfile_base_images_are_pinned_by_digest() -> None:
    text = DOCKERFILE.read_text()
    args = dict(re.findall(r"^ARG ((?:PYTHON|NODE|UV)_IMAGE)=(\S+)$", text, re.MULTILINE))
    assert set(args) == {"PYTHON_IMAGE", "NODE_IMAGE", "UV_IMAGE"}
    for name, ref in args.items():
        assert re.fullmatch(r"[a-z0-9./-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}", ref), name
    froms = re.findall(r"^FROM (\S+)", text, re.MULTILINE)
    assert froms and all(f.startswith("${") for f in froms), "every stage starts from a pinned ARG"


def test_ai_stays_off_and_no_anthropic_names_anywhere() -> None:
    for path in (DEPLOY, ROOT / "deploy" / "fly.toml", ROOT / "deploy" / "launch" / "dome_deploy.py"):
        text = path.read_text()
        assert "DOME_AI" not in text and "ANTHROPIC" not in text, path.name


def test_ci_tests_the_deploy_tooling_and_lints_every_workflow() -> None:
    ci = load(WORKFLOWS / "ci.yml")
    job = ci["jobs"]["deploy-tooling"]
    runs = "\n".join(s.get("run", "") for s in steps(job))
    assert "uv sync --locked --extra dev" in runs and "uv run pytest" in runs and "uv run mypy" in runs
    assert "actionlint@v1.7.12" in runs
    container = "\n".join(s.get("run", "") for s in steps(ci["jobs"]["container"]))
    assert 'DOME_BUILD_COMMIT="$GITHUB_SHA"' in container and 'test "$commit" = "$GITHUB_SHA"' in container

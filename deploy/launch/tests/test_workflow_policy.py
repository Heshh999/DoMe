"""The safety rules of .github/workflows/deploy.yml (and the parts of ci.yml that touch it), as tests.

PyYAML reads the key ``on`` as the boolean True (YAML 1.1); the helper below accepts both."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
DEPLOY = WORKFLOWS / "deploy.yml"
PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


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
    assert create["type"] == "boolean" and create["default"] is False and "$38" in create["description"]


def test_read_only_token_and_three_jobs(wf: dict[str, Any]) -> None:
    assert wf["permissions"] == {"contents": "read"}
    assert set(wf["jobs"]) == {"gate", "deploy", "status"}
    for job in wf["jobs"].values():
        assert "permissions" not in job  # nothing widens the read-only token
        assert isinstance(job.get("timeout-minutes"), int)


def test_every_action_is_pinned_to_a_commit(wf: dict[str, Any]) -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = load(path)
        for job in data["jobs"].values():
            for step in steps(job):
                if "uses" in step:
                    assert PINNED.match(step["uses"]), (path.name, step["uses"])
    lines = [line for line in DEPLOY.read_text().splitlines() if "uses:" in line]
    assert lines and all("# " in line for line in lines), "every pin carries its tag as a comment"


def test_flyctl_and_checkout_settings(wf: dict[str, Any]) -> None:
    [setup] = [s for s in steps(wf["jobs"]["deploy"]) if "setup-flyctl" in s.get("uses", "")]
    assert setup["with"] == {"version": "0.4.110"}
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
                    if "secrets." in str(value):
                        assert re.fullmatch(r"\$\{\{ secrets\.[A-Z_]+ \}\}", value), name
    assert holders == [("deploy", "Set up what is missing, deploy, check")]
    env = steps(wf["jobs"]["deploy"])[-1]["env"]
    assert {k for k, v in env.items() if "secrets." in v} == {
        "FLY_API_TOKEN",
        "DOME_OIDC_CLIENT_SECRET",
        "DOME_FALLBACK_DATABASE_URL",
    }
    assert env["DOME_FALLBACK_DATABASE_URL"] == "${{ secrets.DATABASE_URL }}"


def test_gate_and_status_have_no_environment_and_no_secrets(wf: dict[str, Any]) -> None:
    for name in ("gate", "status"):
        job = wf["jobs"][name]
        assert "environment" not in job
        assert "secrets." not in str(job)
        assert not any("setup-flyctl" in s.get("uses", "") for s in steps(job))


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
    assert runs == {
        "gate": ["python3 deploy/launch/dome_deploy.py gate"],
        "deploy": ["python3 deploy/launch/dome_deploy.py deploy"],
        "status": ["python3 deploy/launch/dome_deploy.py status"],
    }


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

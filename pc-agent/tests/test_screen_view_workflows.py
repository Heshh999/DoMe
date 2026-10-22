"""The Screen view steps of the two Windows build workflows (WP1.6, design §5.5, §15, §18), checked on their text
like tests/test_release_workflow.py: Screen view is built in only when ``licence_gate.py shipping-av`` says the
LGPL av wheel is pinned, the licence gate runs on ``dist/DoMe`` (``--strict`` for a release) and the frozen
worker's ``--self-test`` runs, both after PyInstaller and before anything is compiled, signed or uploaded, with
every value passed through ``env`` and no secret anywhere near them."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .test_release_workflow import PINNED, run_blocks

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def job_steps(workflow: str, job: str) -> list[str]:
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8").split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([a-z_-]+):\n", text, flags=re.MULTILINE)
    block = dict(zip(parts[1::2], parts[2::2], strict=True))[job]
    return re.split(r"^      - ", block.split("\n    steps:\n", 1)[1], flags=re.MULTILINE)[1:]


def names(steps: list[str]) -> list[str]:
    return [re.match(r"name: (.+)", s).group(1).strip() for s in steps]  # type: ignore[union-attr]


def index(steps: list[str], fragment: str) -> int:
    found = [i for i, s in enumerate(steps) if fragment in s]
    assert len(found) == 1, (fragment, names(steps))
    return found[0]


CASES = [("windows-installer.yml", "installer", False), ("release.yml", "build", True)]


@pytest.mark.parametrize(("workflow", "job", "strict"), CASES)
def test_screen_view_steps_run_in_order_before_anything_is_compiled_or_uploaded(
    workflow: str, job: str, strict: bool
) -> None:
    steps = job_steps(workflow, job)
    decide = index(steps, "licence_gate.py shipping-av")
    sync = index(steps, "uv sync --locked @extras")
    freeze = index(steps, "build_installer.py --pyinstaller-only")
    gate = index(steps, "licence_gate.py check")
    self_test = index(steps, "licence_gate.py screen-self-test")
    upload = index(steps, "pyinstaller-folder" if strict else "DoMe-Setup-*.exe")
    assert decide < sync < freeze < gate < self_test < upload
    if not strict:
        compile_ = index(steps, "build_installer.py --skip-pyinstaller --iscc")
        smoke = index(steps, "smoke_test.py full")
        assert self_test < compile_ < smoke < upload
        assert "id: build" in steps[compile_]
    assert "id: screen" in steps[decide] and "uv run --no-project python -I" in steps[decide]
    assert '--github-output "$env:GITHUB_OUTPUT"' in steps[decide]


@pytest.mark.parametrize(("workflow", "job", "strict"), CASES)
def test_the_screen_extra_only_with_the_lgpl_wheel(workflow: str, job: str, strict: bool) -> None:
    steps = job_steps(workflow, job)
    sync = steps[index(steps, "uv sync --locked @extras")]
    assert "$extras = @('--extra', 'build')" in sync
    assert "if ($env:SCREEN -eq 'present') { $extras += @('--extra', 'screen') }" in sync
    assert "--extra dev" not in "".join(steps)  # the stock PyPI av (dev extra) never reaches a build
    for fragment in ("uv sync --locked @extras", "licence_gate.py check", "licence_gate.py screen-self-test"):
        assert "SCREEN: ${{ steps.screen.outputs.screen }}" in steps[index(steps, fragment)], fragment


@pytest.mark.parametrize(("workflow", "job", "strict"), CASES)
def test_the_gate_and_the_self_test_commands(workflow: str, job: str, strict: bool) -> None:
    steps = job_steps(workflow, job)
    gate = steps[index(steps, "licence_gate.py check")]
    assert 'licence_gate.py collect --folder dist/DoMe --out "$report"' in gate
    assert 'check "$report" --frozen --screen "$env:SCREEN"' in gate
    assert ("--strict" in gate) is strict
    self_test = steps[index(steps, "licence_gate.py screen-self-test")]
    assert '--exe dist/DoMe/DoMe.exe --expect "$env:SCREEN"' in self_test
    assert re.search(r"^        timeout-minutes: \d+$", self_test, re.MULTILINE)
    for step in (gate, self_test, steps[index(steps, "licence_gate.py shipping-av")]):
        assert "secrets." not in step and "vars." not in step
        for block in run_blocks(step):
            assert "${{" not in block
            assert "if ($LASTEXITCODE -ne 0) { throw" in block


@pytest.mark.parametrize(("workflow", "job", "strict"), CASES)
def test_the_reports_are_kept(workflow: str, job: str, strict: bool) -> None:
    steps = job_steps(workflow, job)
    uploads = [s for s in steps if "${{ runner.temp }}/licence-gate/" in s]
    assert len(uploads) == 1 and "if: always()" in uploads[0]
    for line in "".join(steps).splitlines():
        if "uses:" in line:
            assert PINNED.match(line), line


def test_release_folder_upload_waits_for_the_gate_and_the_self_test() -> None:
    steps = job_steps("release.yml", "build")
    assert names(steps)[-1] == "Upload the PyInstaller folder for the sign job"
    assert index(steps, "licence-gate-reports") < len(steps) - 1

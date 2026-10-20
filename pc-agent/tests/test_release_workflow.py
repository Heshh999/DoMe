"""The safety rules of .github/workflows/release.yml, checked on its text (PyYAML is not a pc-agent
dependency): the triggers, the read-only token everywhere but "publish", the release environment and the
OIDC permission only on "sign", the update key in exactly one step's env, pinned actions, no ``${{ }}``
inside a script, the order of the sign job (refuse when unconfigured, install everything, then sign in to
Azure, build, sign out always, sign the manifest), and a publish job that verifies before it creates a draft
and publishes last. windows-installer.yml must stay the unsigned development pipeline."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
PINNED = re.compile(r"^\s*(?:- )?uses: [^@\s]+@[0-9a-f]{40} # v\d+\.\d+\.\d+\s*$")


def text() -> str:
    assert RELEASE.is_file(), f"{RELEASE} is missing: these tests need the whole repository"
    return RELEASE.read_text(encoding="utf-8")


def body() -> str:
    """Everything after the header comment."""
    return text().split("\nname: ", 1)[1]


def jobs() -> dict[str, str]:
    """Each job's block, split at the two-space job keys under ``jobs:``."""
    section = body().split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([a-z_-]+):\n", section, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def steps(job: str) -> list[str]:
    block = jobs()[job]
    return re.split(r"^      - ", block.split("\n    steps:\n", 1)[1], flags=re.MULTILINE)[1:]


def step_name(step: str) -> str:
    match = re.match(r"name: (.+)", step)
    assert match is not None, step[:80]
    return match.group(1).strip()


def run_blocks(source: str) -> list[str]:
    """The text of every ``run:`` (inline or block scalar)."""
    lines = source.splitlines()
    found: list[str] = []
    for i, line in enumerate(lines):
        match = re.match(r"^(\s*)(?:- )?run: ?(.*)$", line)
        if not match:
            continue
        indent, value = len(match.group(1)), match.group(2)
        if value not in ("|", ">", "|-", ">-"):
            found.append(value)
            continue
        block: list[str] = []
        for following in lines[i + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            block.append(following)
        found.append("\n".join(block))
    return found


def test_triggers_are_a_version_tag_and_run_workflow_with_two_inputs() -> None:
    on = body().split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert re.search(r"^  push:\n    tags: \[\"v\[0-9\]\+\.\[0-9\]\+\.\[0-9\]\+\"\]\n", on, re.MULTILINE)
    assert "branches" not in on
    assert re.findall(r"^  ([a-z_]+):", on, re.MULTILINE) == ["push", "workflow_dispatch"]
    inputs = on.split("    inputs:\n", 1)[1]
    assert re.findall(r"^      ([a-z_]+):", inputs, re.MULTILINE) == ["version", "publish"]
    assert "type: boolean" in inputs and "default: false" in inputs
    for forbidden in ("pull_request", "pull_request_target", "schedule:", "workflow_run", "repository_dispatch"):
        assert forbidden not in body(), forbidden


def test_read_only_token_except_publish_and_oidc_only_for_sign() -> None:
    top = body().split("\njobs:\n", 1)[0]
    assert "\npermissions:\n  contents: read\n" in top
    all_jobs = jobs()
    assert list(all_jobs) == ["check", "tests", "build", "sign", "smoke", "publish"]
    for name, block in all_jobs.items():
        assert "if: github.repository == 'Heshh999/DoMe'" in block, name
        assert re.search(r"^    timeout-minutes: \d+$", block, re.MULTILINE), name
        assert bool(re.search(r"^      contents: write$", block, re.MULTILINE)) is (name == "publish"), name
        assert bool(re.search(r"^      id-token: write$", block, re.MULTILINE)) is (name == "sign"), name
        assert ("environment: release" in block) is (name == "sign"), name
        assert "environment:" not in block or name == "sign", name
    assert len(re.findall(r"^\s+contents: write$", body(), re.MULTILINE)) == 1
    assert len(re.findall(r"^\s+id-token: write$", body(), re.MULTILINE)) == 1
    assert "if: github.repository == 'Heshh999/DoMe' && needs.check.outputs.publish == 'true'" in all_jobs["publish"]


def test_the_update_key_appears_in_one_step_env_and_one_name_probe() -> None:
    all_jobs = jobs()
    for name, block in all_jobs.items():
        if name != "sign":
            assert "secrets." not in block, name
    holders = [step_name(s) for s in steps("sign") if "secrets." in s]
    assert holders == [
        "Refuse clearly when release signing is not configured",
        "Sign latest.json with the update key (the only step that holds it)",
    ]
    probe, manifest = (s for s in steps("sign") if "secrets." in s)
    assert re.findall(r"secrets\.[^}]*", probe) == ["secrets.DOME_UPDATE_SIGNING_KEY != '' "]
    assert "        env:\n          DOME_UPDATE_SIGNING_KEY: ${{ secrets.DOME_UPDATE_SIGNING_KEY }}\n" in manifest
    assert manifest.count("secrets.") == 1
    assert body().count("secrets.") == 2
    assert "release_files.py sign" in manifest and "uses:" not in manifest


def test_every_action_is_pinned_to_a_commit_with_its_tag() -> None:
    uses = [line for line in body().splitlines() if "uses:" in line]
    assert uses
    for line in uses:
        assert PINNED.match(line), line
    assert "azure/login@053f7285eb20bf5469ef084617b0b24d3cec53fc # v3.1.0" in body()
    assert "artifact-signing-action" not in body() and "trusted-signing-action" not in body()


def test_no_expressions_inside_scripts_no_tracing_no_debug() -> None:
    blocks = run_blocks(body())
    assert len(blocks) >= 15
    for block in blocks:
        assert "${{" not in block, block[:120]
        assert "set -x" not in block and "xtrace" not in block
    assert "ACTIONS_STEP_DEBUG" not in text() and "ACTIONS_RUNNER_DEBUG" not in text()
    assert "/v /debug" not in body() and "--sign-verbose" not in body()


def test_sign_job_order() -> None:
    names = [step_name(s) for s in steps("sign")]
    first = names[0]
    assert first == "Refuse clearly when release signing is not configured"

    def index(fragment: str) -> int:
        matches = [i for i, n in enumerate(names) if fragment in n]
        assert len(matches) == 1, (fragment, names)
        return matches[0]

    login = index("Sign in to Azure")
    installs = [
        index("Find the Inno Setup"),
        index("Install Microsoft's Artifact Signing client"),
        index("pc-agent - sync"),
    ]
    build = index("Compile DoMe-Setup")
    logout = index("Sign out of Azure")
    refuse = index("Refuse anything that is not signed")
    manifest = index("Sign latest.json")
    upload = index("Upload the signed release files")
    assert max(installs) < login < build < logout < refuse < manifest < upload
    assert "if: always()" in steps("sign")[logout]
    assert "az logout" in steps("sign")[logout]
    configure = steps("sign")[0]
    for name in (
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_SUBSCRIPTION_ID",
        "ARTIFACT_SIGNING_ENDPOINT",
        "ARTIFACT_SIGNING_ACCOUNT",
        "ARTIFACT_SIGNING_PROFILE",
        "DOME_SIGNING_PUBLISHER",
    ):
        assert f"{name}: ${{{{ vars.{name} }}}}" in configure, name
    assert "A release is never published unsigned. See docs/LAUNCH_SETUP.md section 17." in configure
    package = steps("sign")[build]
    assert "build_installer.py --release --skip-pyinstaller" in package
    assert "--sign-artifact-signing" in package and "--publisher" in package and "--without-browser-addon" in package
    assert "pyinstaller-folder" in "".join(steps("sign")) and "--pyinstaller-only" not in jobs()["sign"]
    assert "--extra" not in "".join(steps("sign")), "the sign job installs no test or build extras"
    install = steps("sign")[installs[1]]
    assert "Microsoft.ArtifactSigning.Client" in install and "nuget verify -Signatures" in install
    assert "CN=Microsoft Corporation" in install and "ASC_NUPKG_SHA256" in install
    assert re.search(r'ASC_NUPKG_SHA256: "[0-9a-f]{64}"', jobs()["sign"])
    assert "'AzureCliCredential'" not in install  # the only credential source left in


def test_build_tests_and_smoke_have_no_secrets_and_smoke_checks_signatures() -> None:
    for name in ("check", "tests", "build", "smoke"):
        block = jobs()[name]
        assert "secrets." not in block and "vars." not in block, name
    assert "build_installer.py --pyinstaller-only" in jobs()["build"]
    smoke = jobs()["smoke"]
    assert "smoke_test.py full --release" in smoke and '--publisher "$env:PUBLISHER"' in smoke
    assert "PUBLISHER: ${{ needs.sign.outputs.publisher }}" in smoke


def test_publish_verifies_then_drafts_then_checks_digests_then_publishes() -> None:
    block = jobs()["publish"]
    verify = block.index("release_files.py verify")
    create = block.index("gh release create")
    digests = block.index(".digest")
    publish = block.index("--draft=false")
    assert verify < create < digests < publish
    assert block.count("--draft=false") == 1
    assert all("--draft " in line for line in block.splitlines() if "gh release create" in line)
    assert "--verify-tag" in block and '--target "$GITHUB_SHA"' in block
    for name in ("latest.json", "latest.json.sig", "SHA256SUMS", "BUILD-INFO.txt", "RELEASE-NOTES.md"):
        assert f'"$FILES/{name}"' in block, name


def test_check_job_enforces_version_branch_release_and_key() -> None:
    block = jobs()["check"]
    assert "pc-agent/pyproject.toml" in block and "__version__" in block
    assert "git merge-base --is-ancestor" in block and "DEFAULT_BRANCH" in block
    assert 'gh release view "v$version"' in block
    assert "UPDATE_PUBLIC_KEYS" in block and "production_extension_ids()" in block
    assert "docs/LAUNCH_SETUP.md section 17" in block


@pytest.mark.parametrize("name", ["windows-installer.yml"])
def test_the_development_installer_workflow_stays_unsigned(name: str) -> None:
    source = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert "secrets." not in source and "id-token" not in source and "environment:" not in source
    for block in run_blocks(source):
        assert "--release" not in block and "--sign" not in block


def test_the_run_block_parser() -> None:
    sample = "steps:\n  - run: echo one\n  - name: x\n    run: |\n      a\n      b\n    env:\n      X: 1\n"
    assert run_blocks(sample) == ["echo one", "      a\n      b"]


def test_both_verify_steps_hash_the_installer_itself() -> None:
    """``verify --setup`` hashes DoMe-Setup-X.Y.Z.exe and compares its name, size and SHA-256 with the signed
    latest.json, in the sign job (before the files are uploaded) and in the publish job (before the release)."""
    for job in ("sign", "publish"):
        lines = [line for line in jobs()[job].splitlines() if "release_files.py verify" in line]
        assert len(lines) == 1, job
        assert "--setup" in lines[0] and "DoMe-Setup-" in lines[0] and "RELEASE_VERSION" in lines[0], job
        assert "RELEASE_VERSION: ${{ needs.check.outputs.version }}" in jobs()[job], job

"""The release rules of pc-agent/packaging/installer/build_installer.py and the release phases of
smoke_test.py, on Linux: no unsigned release build, the honest no-add-on release, the signing command Inno
runs, latest.json as the agent's update check reads it, BUILD-INFO.txt, and ``signed=true`` only after
``signtool verify`` said so. Nothing here runs PyInstaller, ISCC or signtool (a fake ``subprocess.run``
stands in)."""

from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dome_agent.update_check import verify_and_parse

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
THUMBPRINT = "0123456789abcdef0123456789abcdef01234567"
SIGNTOOL = r"C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
DLIB = r"C:\a\_temp\asc\Microsoft.ArtifactSigning.Client\bin\x64\Azure.CodeSigning.Dlib.dll"
METADATA = r"C:\a\_temp\asc\metadata.json"
NOW = datetime(2026, 11, 2, 14, 3, 11, tzinfo=UTC)


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bi = load("dome_build_installer", PACKAGING / "installer" / "build_installer.py")
smoke = load("dome_smoke_test", PACKAGING / "installer" / "smoke_test.py")


def run_main(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    rc = bi.main(list(argv))
    return rc, capsys.readouterr().err


# ----- option rules -------------------------------------------------------------------------------------


def test_release_without_signing_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    rc, err = run_main(capsys, "--release")
    assert rc == 1
    assert "never publishes an unsigned release build" in err and "drop --release" in err


def test_release_without_store_id_needs_the_explicit_no_addon_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert bi.production_extension_ids() == ()
    rc, err = run_main(
        capsys, "--release", "--sign-artifact-signing", METADATA, "--dlib", DLIB, "--publisher", "Jane Example"
    )
    assert rc == 1
    assert "PRODUCTION_EXTENSION_IDS is empty" in err and "--without-browser-addon" in err


def test_release_needs_a_publisher(capsys: pytest.CaptureFixture[str]) -> None:
    rc, err = run_main(capsys, "--release", "--without-browser-addon", "--sign-thumbprint", THUMBPRINT)
    assert rc == 1 and "--publisher" in err


def test_no_addon_flag_only_for_a_release(capsys: pytest.CaptureFixture[str]) -> None:
    rc, err = run_main(capsys, "--without-browser-addon")
    assert rc == 1 and "only for --release" in err


def test_no_addon_flag_refused_once_the_store_id_exists(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bi, "production_extension_ids", lambda: ("a" * 32,))
    rc, err = run_main(
        capsys, "--release", "--without-browser-addon", "--sign-thumbprint", THUMBPRINT, "--publisher", "Jane"
    )
    assert rc == 1 and "drop the flag" in err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--sign-thumbprint", THUMBPRINT, "--sign-artifact-signing", METADATA, "--dlib", DLIB], "not both"),
        (["--sign-artifact-signing", METADATA], "needs --dlib"),
        (["--dlib", DLIB], "only used with --sign-artifact-signing"),
        (["--sign-thumbprint", "abc"], "40-character"),
        (["--repo", "not a repo"], "OWNER/REPO"),
        (["--publisher", 'Jane "Q" Example'], "--publisher"),
        (["--publisher", "Jane\nExample"], "--publisher"),
    ],
)
def test_bad_signing_options_are_refused(capsys: pytest.CaptureFixture[str], argv: list[str], message: str) -> None:
    rc, err = run_main(capsys, *argv)
    assert rc == 1 and message in err


def test_valid_release_options_pass() -> None:
    parser_args = {
        "sign_thumbprint": "",
        "sign_artifact_signing": METADATA,
        "dlib": DLIB,
        "repo": "Heshh999/DoMe",
        "publisher": "Jane Example",
        "without_browser_addon": True,
        "release": True,
        "pyinstaller_only": False,
    }
    from argparse import Namespace

    assert bi.check_options(Namespace(**parser_args), ()) is None
    assert bi.check_options(Namespace(**{**parser_args, "without_browser_addon": False}), ("a" * 32,)) is None


# ----- the commands Inno and ISCC run -------------------------------------------------------------------


def test_artifact_signing_command() -> None:
    cmd = bi.sign_tool_command("artifact-signing", signtool=SIGNTOOL, dlib=DLIB, metadata=METADATA)
    assert cmd == (
        f"$q{SIGNTOOL}$q sign /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 "
        f"/dlib $q{DLIB}$q /dmdf $q{METADATA}$q $f"
    )
    assert cmd.endswith(" $f") and "/v" not in cmd.split()
    verbose = bi.sign_tool_command("artifact-signing", signtool=SIGNTOOL, dlib=DLIB, metadata=METADATA, verbose=True)
    assert " sign /v /debug /fd SHA256 " in verbose


def test_thumbprint_command_is_unchanged() -> None:
    assert bi.sign_tool_command("thumbprint", signtool=SIGNTOOL, thumbprint=THUMBPRINT) == (
        f"$q{SIGNTOOL}$q sign /sha1 {THUMBPRINT} /fd sha256 /tr http://timestamp.digicert.com /td sha256 $f"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "artifact-signing", "dlib": DLIB, "metadata": None},
        {"kind": "artifact-signing", "dlib": r"C:\$f\x.dll", "metadata": METADATA},
        {"kind": "artifact-signing", "dlib": 'C:\\"x.dll', "metadata": METADATA},
        {"kind": "thumbprint", "thumbprint": "nope"},
        {"kind": "other"},
    ],
)
def test_signing_commands_refuse_bad_input(kwargs: dict[str, Any]) -> None:
    kind = kwargs.pop("kind")
    with pytest.raises(ValueError):
        bi.sign_tool_command(kind, signtool=SIGNTOOL, **kwargs)


def test_signtool_sdk_build() -> None:
    assert bi.signtool_sdk_build(SIGNTOOL) == 26100
    assert bi.signtool_sdk_build(r"C:\Program Files (x86)\Windows Kits\10\bin\10.0.20348.0\x64\signtool.exe") == 20348
    assert bi.signtool_sdk_build("signtool.exe") is None


def iscc(**kwargs: Any) -> list[str]:
    base: dict[str, Any] = {
        "iscc": "ISCC.exe",
        "version": "0.2.0",
        "build_number": "7",
        "build_tag": "0.2.0+abcdef0",
        "app": Path("/dist/DoMe"),
        "out_dir": Path("/dist/installer"),
    }
    base.update(kwargs)
    return list(bi.iscc_command(**base))


def test_development_iscc_command_is_what_it_always_was() -> None:
    assert iscc(release=False) == [
        "ISCC.exe",
        "/DAppVersion=0.2.0",
        "/DBuildNumber=7",
        "/DBuildTag=0.2.0+abcdef0 (build 7)",
        f"/DAppFiles={Path('/dist/DoMe')}",
        f"/DOutputDir={Path('/dist/installer')}",
        f"/DBrandIcon={bi.ICON}",
        "/DDevParams",
        str(bi.ISS),
    ]


def test_release_iscc_command_without_addon_and_signed() -> None:
    cmd = iscc(release=True, no_addon=True, sign_command="SIGN $f")
    assert "/DDevParams" not in cmd and "/DNoAddon" in cmd
    assert cmd[-3:] == ["/DSign", "/Sdomesign=SIGN $f", str(bi.ISS)]
    assert "/DNoAddon" not in iscc(release=True, sign_command="SIGN $f")


def test_the_inno_script_knows_noaddon_and_the_retry_settings() -> None:
    iss = bi.ISS.read_text(encoding="ascii")
    assert "#ifdef NoAddon" in iss and "#elif defined(NoAddon)" in iss
    assert "SignToolRetryCount=3" in iss and "SignToolMinimumTimeBetween=1000" in iss
    sign_block = iss.split("#ifdef Sign", 1)[1].split("#endif", 1)[0]
    assert "SignToolRetryCount=3" in sign_block and "SignedUninstaller=yes" in sign_block
    no_addon = iss.split("#ifdef NoAddon", 1)[1].split("#endif", 1)[0]
    assert "NativeHostRegistered := False" in no_addon
    assert "install-native-host" not in no_addon.split("#else", 1)[0].replace("does not include", "")
    assert "YouTube control in Chrome or Edge is not part of this version of DoMe." in iss


# ----- latest.json, release notes, BUILD-INFO.txt and signed=true ----------------------------------------


def write_setup(folder: Path, version: str = "0.2.0") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    setup = folder / f"DoMe-Setup-{version}.exe"
    setup.write_bytes(b"MZ" + b"\x00" * 4096)
    return setup


def test_manifest_matches_the_schema_and_the_agent_accepts_it(tmp_path: Path) -> None:
    setup = write_setup(tmp_path)
    digest = bi.sha256(setup)
    path = bi.write_manifest(
        tmp_path,
        version="0.2.0",
        setup=setup,
        digest=digest,
        size=setup.stat().st_size,
        publisher="Jane Example",
        browser_addon=False,
        repo="Heshh999/DoMe",
        now=NOW,
    )
    raw = path.read_bytes()
    assert raw.endswith(b"}\n") and not raw.endswith(b"\n\n")
    data = json.loads(raw)
    assert list(data) == sorted(data)
    assert data == {
        "browser_addon": False,
        "product": "DoMe for Windows",
        "published_at": "2026-11-02T14:03:11Z",
        "publisher": "Jane Example",
        "release_page": "https://github.com/Heshh999/DoMe/releases/tag/v0.2.0",
        "schema": 1,
        "sha256": digest,
        "size": setup.stat().st_size,
        "url": "https://github.com/Heshh999/DoMe/releases/download/v0.2.0/DoMe-Setup-0.2.0.exe",
        "version": "0.2.0",
    }
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    release = verify_and_parse(raw, base64.b64encode(key.sign(raw)), public_keys=(pub,))
    assert release.version == "0.2.0" and release.sha256 == digest


def fake_signtool(ok: bool, calls: list[list[str]]) -> Any:
    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        out = "Successfully verified: " + cmd[-1] if ok else "SignTool Error: No signature found."
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, out, "")

    return run


def finish(tmp_path: Path, *, ok: bool, calls: list[list[str]], **overrides: Any) -> int:
    out_dir = tmp_path / "installer"
    app = tmp_path / "DoMe"
    app.mkdir(exist_ok=True)
    setup = write_setup(out_dir)
    kwargs: dict[str, Any] = {
        "out_dir": out_dir,
        "setup": setup,
        "app": app,
        "version": "0.2.0",
        "build_tag": "0.2.0+abcdef0",
        "build_number": "7",
        "commit": "abcdef0123",
        "release": True,
        "no_addon": True,
        "signing": bi.Signing(
            "artifact-signing", "SIGN $f", 'Azure Artifact Signing, publisher "Jane Example", timestamped'
        ),
        "signtool": SIGNTOOL,
        "publisher": "Jane Example",
        "repo": "Heshh999/DoMe",
        "now": NOW,
        "run": fake_signtool(ok, calls),
    }
    kwargs.update(overrides)
    return int(bi.finish(**kwargs))


@pytest.fixture
def actions_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    output, summary = tmp_path / "github_output", tmp_path / "github_summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    return output, summary


def test_signed_true_only_after_every_signature_verified(tmp_path: Path, actions_files: tuple[Path, Path]) -> None:
    output, _summary = actions_files
    calls: list[list[str]] = []
    assert finish(tmp_path, ok=True, calls=calls) == 0
    assert [c[1:4] for c in calls] == [["verify", "/pa", "/v"]] * 3
    assert [Path(c[-1]).name for c in calls] == ["DoMe-Setup-0.2.0.exe", "DoMe.exe", "dome-native-host.exe"]
    out = output.read_text(encoding="utf-8")
    assert "signed=true\n" in out and "browser_addon=false\n" in out and "publisher=Jane Example\n" in out
    assert f"manifest={tmp_path / 'installer' / 'latest.json'}\n" in out
    info = (tmp_path / "installer" / "BUILD-INFO.txt").read_text(encoding="utf-8")
    assert 'signature: Azure Artifact Signing, publisher "Jane Example", timestamped\n' in info
    assert "browser add-on: not included in this version (no store listing yet)\n" in info
    assert "kind: release build without the browser add-on\n" in info
    notes = (tmp_path / "installer" / "RELEASE-NOTES.md").read_text(encoding="utf-8")
    assert "Signed for Windows by **Jane Example** through Azure Artifact Signing" in notes
    assert "not included in this version" in notes
    assert "Everything else in DoMe is included" in notes and "Everything else works" not in notes
    # signing is not a Smart App Control promise (docs/SECURITY.md T12): the notes say it is not verified
    assert (
        "Not yet verified on Windows 11 PCs with Smart App Control on; if it blocks DoMe, do not turn it off." in notes
    )
    assert "Not tested on this run: any Windows 10/11 PC as a standard user." in notes
    sums = (tmp_path / "installer" / "SHA256SUMS").read_text(encoding="ascii")
    assert sums.endswith("  DoMe-Setup-0.2.0.exe\n")


def test_a_failed_verification_reports_nothing_as_signed(
    tmp_path: Path, actions_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    output, _summary = actions_files
    assert finish(tmp_path, ok=False, calls=[]) == 1
    assert "did not verify" in capsys.readouterr().err
    assert not output.exists()
    assert not (tmp_path / "installer" / "BUILD-INFO.txt").exists()
    assert not (tmp_path / "installer" / "latest.json").exists()


def test_verify_needs_the_success_line_not_only_exit_code_zero(tmp_path: Path) -> None:
    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, "Number of files successfully Verified: 0", "")

    problems = bi.verify_signatures(SIGNTOOL, [tmp_path / "DoMe.exe"], run=run)
    assert len(problems) == 1 and "DoMe.exe" in problems[0]


def test_a_development_build_stays_unsigned_and_writes_no_manifest(
    tmp_path: Path, actions_files: tuple[Path, Path]
) -> None:
    output, _summary = actions_files
    calls: list[list[str]] = []
    assert finish(tmp_path, ok=True, calls=calls, release=False, no_addon=False, signing=None, publisher="") == 0
    assert calls == []
    out = output.read_text(encoding="utf-8")
    assert "signed=false\n" in out and "manifest=" not in out
    info = (tmp_path / "installer" / "BUILD-INFO.txt").read_text(encoding="utf-8")
    assert "signature: NOT signed: Windows SmartScreen will warn (development build)\n" in info
    assert "kind: development build (accepts /ApiUrl= and /ExtensionId=)\n" in info
    assert not (tmp_path / "installer" / "latest.json").exists()
    assert not (tmp_path / "installer" / "RELEASE-NOTES.md").exists()


# ----- smoke_test.py: the release run keeps its signature checks ------------------------------------------


def test_release_smoke_plan_checks_signatures_and_skips_development_phases() -> None:
    release = smoke.phase_plan(True)
    dev = smoke.phase_plan(False)
    assert release[:3] == ["preflight", "install", "signatures"]
    assert "ignored_params" in release and "reinstall" in release
    for development_only in ("insecure_url", "staging", "keeps_address"):
        assert development_only in dev and development_only not in release
    assert "signatures" not in dev
    for shared in ("cli", "bridge", "tray", "upgrade", "not_quitting", "downgrade", "uninstall_keep", "removedata"):
        assert shared in release and shared in dev
    assert release[-2:] == dev[-2:] == ["removedata", "no_empty_state"]
    assert set(release) | set(dev) == set(smoke.PHASES)


@pytest.mark.parametrize(
    ("subject", "publisher", "ok"),
    [
        ("CN=Jane Example, O=Jane Example, L=Austin, S=Texas, C=US", "Jane Example", True),
        ('CN="Example, Jane", O=x, C=US', "Example, Jane", True),
        ("O=Jane Example, CN=Jane Example Two", "Jane Example", False),
        ("CN=Someone Else, O=Jane Example", "Jane Example", False),
        ("", "Jane Example", False),
        ("CN=Jane Example", "", False),
    ],
)
def test_signer_matches_the_cn_exactly(subject: str, publisher: str, ok: bool) -> None:
    assert smoke.signer_matches(subject, publisher) is ok

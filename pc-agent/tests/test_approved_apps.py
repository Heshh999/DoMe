from __future__ import annotations

from pathlib import Path

import pytest
from dome_protocol import ProtocolError

from dome_agent.approved_apps import ApprovalError, ApprovedApps, validate_app_id, validate_executable_path
from dome_agent.store import Store


@pytest.fixture
def exe(tmp_path: Path) -> Path:
    apps = tmp_path / "Program Files" / "Thing"
    apps.mkdir(parents=True)
    path = apps / "thing.exe"
    path.write_bytes(b"MZ fake executable")
    return path


@pytest.fixture
def temp_dirs(tmp_path: Path) -> tuple[Path, ...]:
    t = tmp_path / "Temp"
    t.mkdir()
    return (t,)


def test_valid_path(exe: Path, temp_dirs: tuple[Path, ...]) -> None:
    v = validate_executable_path(str(exe), temp_dirs=temp_dirs)
    assert v.path == str(exe.resolve())
    assert len(v.sha256) == 64


@pytest.mark.parametrize(
    "bad",
    [
        "thing.exe",  # relative
        "Program Files/Thing/thing.exe",  # relative
        "{exe_dir}/thing.exe --remote-debugging-port=9222",  # arguments
        '"{exe}"',  # quoted
        "{exe} /silent",  # switch argument
        "{exe} & calc.exe",  # shell chaining
        "{exe}|more",
        "{exe}\n",
    ],
)
def test_rejects_relative_and_argument_like_paths(bad: str, exe: Path, temp_dirs: tuple[Path, ...]) -> None:
    text = bad.format(exe=exe, exe_dir=exe.parent)
    with pytest.raises(ApprovalError):
        validate_executable_path(text, temp_dirs=temp_dirs)


def test_rejects_non_exe(tmp_path: Path, temp_dirs: tuple[Path, ...]) -> None:
    for name in ("script.ps1", "run.bat", "run.cmd", "tool.com", "noext", "thing.exe.txt"):
        p = tmp_path / name
        p.write_text("x")
        with pytest.raises(ApprovalError, match="only .exe"):
            validate_executable_path(str(p), temp_dirs=temp_dirs)


def test_rejects_missing_and_directory(tmp_path: Path, temp_dirs: tuple[Path, ...]) -> None:
    with pytest.raises(ApprovalError, match="does not exist"):
        validate_executable_path(str(tmp_path / "missing.exe"), temp_dirs=temp_dirs)
    d = tmp_path / "dir.exe"
    d.mkdir()
    with pytest.raises(ApprovalError, match="not a file"):
        validate_executable_path(str(d), temp_dirs=temp_dirs)


def test_rejects_temp_dirs(temp_dirs: tuple[Path, ...]) -> None:
    p = temp_dirs[0] / "dropper.exe"
    p.write_bytes(b"MZ")
    with pytest.raises(ApprovalError, match="temporary"):
        validate_executable_path(str(p), temp_dirs=temp_dirs)


def test_default_temp_dirs_include_system_tempdir(tmp_path: Path) -> None:
    import tempfile

    p = Path(tempfile.gettempdir()) / "dome-test-dropper.exe"
    p.write_bytes(b"MZ")
    try:
        with pytest.raises(ApprovalError, match="temporary"):
            validate_executable_path(str(p))
    finally:
        p.unlink()


def test_app_id_rules() -> None:
    assert validate_app_id("chrome") == "chrome"
    assert validate_app_id("ninja-trader_8") == "ninja-trader_8"
    for bad in ("Chrome", "-x", "a b", "", "x" * 65, "../x"):
        with pytest.raises(ApprovalError):
            validate_app_id(bad)


def test_approve_and_resolve_detects_changed_executable(store: Store, exe: Path, temp_dirs: tuple[Path, ...]) -> None:
    apps = ApprovedApps(store, temp_dirs=temp_dirs)
    row = apps.approve("thing", str(exe))
    assert row.display_name == "thing" and row.exe_path == str(exe.resolve())
    assert apps.resolve_for_launch("thing").exe_sha256 == row.exe_sha256
    exe.write_bytes(b"MZ replaced binary")
    with pytest.raises(ProtocolError) as ei:
        apps.resolve_for_launch("thing")
    assert ei.value.code == "APP_NOT_APPROVED"
    assert any(e["kind"] == "approved_app_hash_changed" for e in store.list_security_events())
    with pytest.raises(ProtocolError) as ei:
        apps.resolve_for_launch("unknown")
    assert ei.value.code == "APP_NOT_APPROVED"
    assert apps.remove("thing") and apps.get("thing") is None

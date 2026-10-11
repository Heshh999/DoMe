from __future__ import annotations

from pathlib import Path

import pytest
from dome_protocol import ProtocolError

from dome_agent.approved_apps import (
    APP_ID_MAX,
    APP_ID_RE,
    AppIdTakenError,
    ApprovalError,
    ApprovedApps,
    app_status,
    clean_display_name,
    explain_approval_error,
    find_approved_by_path,
    nearest_existing_dir,
    normalise_picked_path,
    pick_warning,
    programs_folder,
    suggest_app_id,
    suggest_display_name,
    validate_app_id,
    validate_executable_path,
    version_strings,
)
from dome_agent.store import ApprovedAppRow, Store

ENV = {"WINDIR": "C:\\Windows", "ProgramFiles": "C:\\Program Files", "ProgramData": "C:\\ProgramData"}


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
    ("bad", "code"),
    [
        ("thing.exe", "NOT_ABSOLUTE"),  # relative
        ("Program Files/Thing/thing.exe", "NOT_ABSOLUTE"),  # relative
        ("{exe_dir}/thing.exe --remote-debugging-port=9222", "SHELL_CHARS"),  # arguments
        ('"{exe}"', "SHELL_CHARS"),  # quoted
        ("{exe} /silent", "SHELL_CHARS"),  # switch argument
        ("{exe} & calc.exe", "SHELL_CHARS"),  # shell chaining
        ("{exe}|more", "SHELL_CHARS"),
        ("{exe}\n", "CONTROL_CHARS"),
        ("  ", "NO_PATH"),
    ],
)
def test_rejects_relative_and_argument_like_paths(bad: str, code: str, exe: Path, temp_dirs: tuple[Path, ...]) -> None:
    text = bad.format(exe=exe, exe_dir=exe.parent)
    with pytest.raises(ApprovalError) as ei:
        validate_executable_path(text, temp_dirs=temp_dirs)
    assert ei.value.code == code  # the tray explains each rule in plain words by its code


def test_rejects_non_exe(tmp_path: Path, temp_dirs: tuple[Path, ...]) -> None:
    for name in ("script.ps1", "run.bat", "run.cmd", "tool.com", "noext", "thing.exe.txt"):
        p = tmp_path / name
        p.write_text("x")
        with pytest.raises(ApprovalError, match="only .exe") as ei:
            validate_executable_path(str(p), temp_dirs=temp_dirs)
        assert ei.value.code == "NOT_EXE"


def test_rejects_missing_and_directory(tmp_path: Path, temp_dirs: tuple[Path, ...]) -> None:
    with pytest.raises(ApprovalError, match="does not exist") as ei:
        validate_executable_path(str(tmp_path / "missing.exe"), temp_dirs=temp_dirs)
    assert ei.value.code == "NOT_FOUND"
    d = tmp_path / "dir.exe"
    d.mkdir()
    with pytest.raises(ApprovalError, match="not a file") as ei:
        validate_executable_path(str(d), temp_dirs=temp_dirs)
    assert ei.value.code == "NOT_FILE"


def test_rejects_temp_dirs(temp_dirs: tuple[Path, ...]) -> None:
    p = temp_dirs[0] / "dropper.exe"
    p.write_bytes(b"MZ")
    with pytest.raises(ApprovalError, match="temporary") as ei:
        validate_executable_path(str(p), temp_dirs=temp_dirs)
    assert ei.value.code == "TEMP_DIR"


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
        with pytest.raises(ApprovalError) as ei:
            validate_app_id(bad)
        assert ei.value.code == "BAD_APP_ID"


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


def test_unreadable_file_has_its_own_code(
    exe: Path, temp_dirs: tuple[Path, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(_: Path) -> str:
        raise PermissionError(13, "denied")

    monkeypatch.setattr("dome_agent.approved_apps.sha256_file", denied)
    with pytest.raises(ApprovalError, match="could not be read") as ei:
        validate_executable_path(str(exe), temp_dirs=temp_dirs)
    assert ei.value.code == "UNREADABLE"


# ----- an app_id keeps its program -----------------------------------------------------------------------------
def test_approve_never_silently_points_an_app_id_at_another_program(
    store: Store, exe: Path, tmp_path: Path, temp_dirs: tuple[Path, ...]
) -> None:
    apps = ApprovedApps(store, temp_dirs=temp_dirs)
    first = apps.approve("thing", str(exe), "Thing")
    other = tmp_path / "Program Files" / "Other" / "thing.exe"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"MZ another program")

    with pytest.raises(AppIdTakenError) as ei:
        apps.approve("thing", str(other), "Other thing")
    assert isinstance(ei.value, ApprovalError) and isinstance(ei.value, ValueError)
    assert ei.value.code == "APP_ID_TAKEN" and ei.value.row.exe_path == first.exe_path
    assert "--replace" in str(ei.value)  # the CLI's way out
    assert apps.get("thing") == first  # nothing was written

    # the same file again (an update changed it) is a re-approval, not an overwrite
    exe.write_bytes(b"MZ updated build")
    again = apps.approve("thing", str(exe), "Thing")
    assert again.exe_path == first.exe_path and again.exe_sha256 != first.exe_sha256
    assert apps.resolve_for_launch("thing").exe_sha256 == again.exe_sha256

    # an explicit replace moves it (the tray's "Find it again…", `approve-app --replace`)
    moved = apps.approve("thing", str(other), "Thing", replace=True)
    assert moved.exe_path == str(other.resolve()) and [r.app_id for r in apps.list()] == ["thing"]


def test_approve_cleans_the_display_name(store: Store, exe: Path, temp_dirs: tuple[Path, ...]) -> None:
    apps = ApprovedApps(store, temp_dirs=temp_dirs)
    assert apps.approve("thing", str(exe), "  My\tThing\u202e  ").display_name == "My Thing"
    assert apps.approve("thing", str(exe), "\u200b").display_name == "thing"  # nothing left → the id


# ----- suggestions for a picked program ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "existing", "expected"),
    [
        ("msedge", [], "msedge"),
        ("Code", ["code"], "code-2"),
        ("Code", ["code", "code-2"], "code-3"),
        ("spotify", ["spotify", "spotify-3"], "spotify-2"),
        ("notepad++", ["notepad"], "notepadpp"),
        ("My App (x86) v2.0", [], "my-app-x86-v2-0"),
        ("__init__", [], "init"),
        ("-x-", [], "x"),
        ("Café", [], "cafe"),
        ("微信", [], "app"),
        ("微信", ["app"], "app-2"),
        ("a_b", [], "a_b"),
    ],
)
def test_suggest_app_id(name: str, existing: list[str], expected: str) -> None:
    assert suggest_app_id(name, existing) == expected


@pytest.mark.parametrize(
    "name", ["a" * 200, "Ω" * 10, " ", ".", "WINWORD", "x" * 62 + "-" * 5, "0start", "a" * 63 + "_b"]
)
def test_suggested_ids_are_always_valid_and_unique(name: str) -> None:
    taken: list[str] = []
    for _ in range(12):  # collisions keep producing valid, unused ids of at most 64 characters
        app_id = suggest_app_id(name, taken)
        assert APP_ID_RE.match(app_id) and len(app_id) <= APP_ID_MAX and app_id not in taken
        assert validate_app_id(app_id) == app_id
        taken.append(app_id)


def test_clean_display_name_strips_controls_and_bidi() -> None:
    assert clean_display_name("  Spo\ttify \n Premium ") == "Spo tify Premium"
    assert clean_display_name("Evil\u202eexe.txt") == "Evilexe.txt"
    assert len(clean_display_name("x" * 200)) == 64


@pytest.mark.parametrize(
    ("path", "strings", "expected"),
    [
        (
            "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
            {"FileDescription": "Microsoft Edge"},
            "Microsoft Edge",
        ),
        (
            "C:\\Apps\\notepad++.exe",
            {"FileDescription": "Notepad++ : a free (GNU) source code editor, extended"},
            "Notepad++",
        ),
        ("C:\\Apps\\Code.exe", {"FileDescription": "Visual Studio Code - Insiders"}, "Visual Studio Code - Insiders"),
        ("C:\\Apps\\thing.exe", {"FileDescription": "Electron", "ProductName": "Thing Desktop"}, "Thing Desktop"),
        ("C:\\Apps\\tool.exe", {"FileDescription": "tool.exe"}, "Tool"),
        ("C:\\Apps\\spotify.exe", {}, "Spotify"),  # no version resource: the file name
        ("C:/Apps/VLC.exe", {}, "VLC"),
        ("C:\\Apps\\x\u202egnp.exe", {}, "Xgnp"),  # a bidi override cannot disguise the name
    ],
)
def test_suggest_display_name(path: str, strings: dict[str, str], expected: str) -> None:
    assert suggest_display_name(path, strings) == expected


def test_display_name_falls_back_to_the_file_name_without_windows_version_info(tmp_path: Path) -> None:
    exe = tmp_path / "spotify.exe"
    exe.write_bytes(b"MZ no version resource")
    assert version_strings(str(exe)) == {}  # Linux; on Windows the same file has no resource either
    assert suggest_display_name(str(exe)) == "Spotify"


def test_normalise_picked_path() -> None:
    # Tk's Windows file dialog answers with forward slashes
    assert normalise_picked_path("C:/Program Files/Spotify/Spotify.exe", windows=True) == (
        "C:\\Program Files\\Spotify\\Spotify.exe"
    )
    assert normalise_picked_path("C:/Program Files/./Spotify//Spotify.exe", windows=True) == (
        "C:\\Program Files\\Spotify\\Spotify.exe"
    )
    assert normalise_picked_path("/opt/apps/../apps/tool.exe", windows=False) == "/opt/apps/tool.exe"
    for cancelled in ("", "  ", (), None):  # Cancel gives "" (or an empty tuple on some Tk builds)
        assert normalise_picked_path(cancelled, windows=True) == ""


def _row(app_id: str, name: str, path: str, sha: str = "0" * 64) -> ApprovedAppRow:
    return ApprovedAppRow(app_id, name, path, sha, "2026-10-10T00:00:00Z")


def test_find_approved_by_path_ignores_case_and_slashes() -> None:
    rows = [_row("edge", "Edge", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe")]
    assert find_approved_by_path(rows, "c:/program files (x86)/microsoft/edge/application/MSEDGE.EXE") is rows[0]
    assert find_approved_by_path(rows, "C:\\Other\\msedge.exe") is None


# ----- status of each approval (the tray's list) ---------------------------------------------------------------
def test_status_ok_changed_missing_unreadable(store: Store, exe: Path, temp_dirs: tuple[Path, ...]) -> None:
    apps = ApprovedApps(store, temp_dirs=temp_dirs)
    row = apps.approve("thing", str(exe), "Thing")
    assert app_status(row) == "ok" and apps.statuses() == {"thing": "ok"}
    exe.write_bytes(b"MZ updated")  # needs re-approval
    assert app_status(row) == "changed" and apps.statuses() == {"thing": "changed"}

    def denied(_: Path) -> str:
        raise PermissionError

    assert app_status(row, hasher=denied) == "unreadable"
    exe.unlink()
    assert apps.statuses() == {"thing": "missing"}
    assert not any(e["kind"] == "approved_app_hash_changed" for e in store.list_security_events())  # only launch logs


# ----- plain words --------------------------------------------------------------------------------------------------
def test_every_refusal_has_plain_words(tmp_path: Path) -> None:
    temp = tmp_path / "Temp"
    temp.mkdir()
    (tmp_path / "doc.txt").write_text("x")
    (tmp_path / "dir.exe").mkdir()
    (temp / "setup.exe").write_bytes(b"MZ")
    apostrophe = tmp_path / "Sid Meier's Civilization VI"
    apostrophe.mkdir()
    (apostrophe / "civ.exe").write_bytes(b"MZ")
    dash = tmp_path / "OneDrive - Contoso"
    dash.mkdir()
    (dash / "tool.exe").write_bytes(b"MZ")
    texts: dict[str, str] = {}
    for raw in [
        "",
        "rel.exe",
        str(tmp_path / "doc.txt"),
        str(tmp_path / "missing.exe"),
        str(tmp_path / "dir.exe"),
        str(temp / "setup.exe"),
        str(apostrophe / "civ.exe"),
        "C:\\a\nb.exe",
    ]:
        with pytest.raises(ApprovalError) as ei:
            validate_executable_path(raw, temp_dirs=(temp,))
        texts[ei.value.code] = explain_approval_error(ei.value, raw)
    with pytest.raises(ApprovalError) as ei:
        validate_app_id("Bad Id")
    texts[ei.value.code] = explain_approval_error(ei.value)
    assert set(texts) == {
        "NO_PATH",
        "NOT_ABSOLUTE",
        "NOT_EXE",
        "NOT_FOUND",
        "NOT_FILE",
        "TEMP_DIR",
        "SHELL_CHARS",
        "CONTROL_CHARS",
        "BAD_APP_ID",
    }
    for text in texts.values():
        assert "executable" not in text and not text.startswith("DoMe could not add that program (")
    assert "an apostrophe (')" in texts["SHELL_CHARS"]
    with pytest.raises(ApprovalError) as ei:
        validate_executable_path(str(dash / "tool.exe"), temp_dirs=(temp,))
    assert "a space followed by “-” or “/”" in explain_approval_error(ei.value, str(dash / "tool.exe"))
    unreadable = explain_approval_error(ApprovalError("x", code="UNREADABLE"))
    assert unreadable.startswith("Windows did not let DoMe read")
    taken = explain_approval_error(AppIdTakenError(_row("spotify", "Spotify", "C:\\a\\spotify.exe")))
    assert "“spotify”" in taken and "Nothing was changed" in taken


@pytest.mark.parametrize(
    ("path", "siblings", "blocking", "words"),
    [
        ("C:\\Program Files\\WindowsApps\\SpotifyAB.Spotify_1.2\\Spotify.exe", [], True, "Microsoft Store app"),
        ("C:\\Users\\me\\AppData\\Local\\Microsoft\\WindowsApps\\wt.exe", [], True, "Microsoft Store app"),
        ("C:\\Windows\\Installer\\{90160000}\\wordicon.exe", [], True, "installer cache"),
        ("C:\\Program Files\\Google\\Chrome\\Application\\chrome_proxy.exe", [], True, "web apps"),
        ("C:\\Users\\me\\AppData\\Local\\Discord\\Update.exe", ["app-1.0.9163", "packages"], True, "Discord's updater"),
        ("c:/windows/system32/CALC.EXE", [], False, "Microsoft Store version"),
        ("C:\\Windows\\SysWOW64\\notepad.exe", [], False, "Microsoft Store version"),
    ],
)
def test_pick_warning(path: str, siblings: list[str], blocking: bool, words: str) -> None:
    warning = pick_warning(path, ENV, listdir=lambda _: siblings)
    assert warning is not None and warning.blocking is blocking and words in warning.text


@pytest.mark.parametrize(
    "path",
    [
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Users\\me\\AppData\\Local\\Tool\\Update.exe",  # an Update.exe without app-* folders is just a program
        "C:\\Windows\\InstallerNot\\x.exe",  # the folder must match whole
        "C:\\Tools\\calc.exe",  # a calc.exe that is not the System32 stub
    ],
)
def test_pick_warning_leaves_ordinary_programs_alone(path: str) -> None:
    assert pick_warning(path, ENV, listdir=lambda _: ["Update.exe", "tool.dll"]) is None


def test_pick_warning_survives_an_unreadable_folder_and_upper_case_env() -> None:
    def denied(_: str) -> list[str]:
        raise PermissionError

    assert pick_warning("C:\\Apps\\Update.exe", ENV, listdir=denied) is None
    warning = pick_warning("D:\\Win\\System32\\calc.exe", {"WINDIR": "D:\\Win"})
    assert warning is not None and not warning.blocking


def test_programs_folder_and_nearest_existing_dir() -> None:
    assert programs_folder(ENV, isdir=lambda _: True) == "C:\\Program Files"
    assert programs_folder({"PROGRAMFILES": "D:\\PF"}, isdir=lambda _: True) == "D:\\PF"
    assert programs_folder(ENV, isdir=lambda _: False) == ""
    assert programs_folder({}, isdir=lambda _: True) == ""
    old = "C:\\Users\\me\\AppData\\Local\\Discord\\app-1.0.9163\\Discord.exe"
    discord = "C:\\Users\\me\\AppData\\Local\\Discord"
    assert nearest_existing_dir(old, isdir=lambda p: p == discord) == discord
    assert nearest_existing_dir(old, isdir=lambda _: False) == ""

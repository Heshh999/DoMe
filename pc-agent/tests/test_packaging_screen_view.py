"""Screen view in the Windows build (WP1.6, design §5.1, §5.5, §15, §17.2), on Linux: what the PyInstaller spec
asks for (aiortc.contrib never bundled, aiortc.rtcrtpsender also as source for the PLI hook check, pystray as
replaceable .py files, no Screen view code in the browser helper), the folder checks build_installer.py adds
(notices and licence texts, the programs' archives through a fake reader), the honest Screen view line in
BUILD-INFO.txt and the release notes, and the installer's licence page and notices. PyInstaller and ISCC do not
run here; a Linux build of the real spec was checked by hand (PROGRESS / the packaging report)."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from .test_build_installer import bi, smoke

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
PC_AGENT = PACKAGING.parent
SPEC = (PACKAGING / "dome-agent.spec").read_text(encoding="utf-8")


def spec_list(name: str) -> list[str]:
    """Every string literal assigned or added to ``name`` in the spec (``name = [...]``, ``name += [...]``)."""
    out: list[str] = []
    for node in ast.walk(ast.parse(SPEC)):
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AugAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and target.id == name and isinstance(value, ast.List):
            out += [e.value for e in value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return out


def analysis_kwargs(script: str) -> dict[str, ast.expr]:
    """The keyword arguments of the ``Analysis(...)`` whose first script is ``packaging/<script>``."""
    for node in ast.walk(ast.parse(SPEC)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Analysis" and script in ast.unparse(node):
            return {k.arg: k.value for k in node.keywords if k.arg}
    raise AssertionError(f"no Analysis for {script}")


# ----- dome-agent.spec ------------------------------------------------------------------------------------


def test_the_spec_never_bundles_aiortc_contrib_or_the_test_doubles() -> None:
    excludes = ast.literal_eval(analysis_kwargs("agent_entry.py")["excludes"])
    assert {"aiortc.contrib", "dome_agent.testing"} <= set(excludes)
    assert 'filter=lambda name: not name.startswith("aiortc.contrib")' in SPEC


def test_the_browser_helper_carries_no_screen_view_code() -> None:
    excludes = set(ast.literal_eval(analysis_kwargs("native_host_entry.py")["excludes"]))
    assert {"dome_agent.screen", "aiortc", "aioice", "av", "pylibsrtp", "google_crc32c"} <= excludes


def test_collection_modes_for_the_pli_hook_source_and_replaceable_pystray() -> None:
    assert ast.unparse(analysis_kwargs("agent_entry.py")["module_collection_mode"]) == "agent_collection_mode"
    assert 'agent_collection_mode = {"pystray": "py"}' in SPEC
    screen_block = SPEC.split("if SCREEN_VIEW:", 1)[1].split("\nagent = Analysis(", 1)[0]
    assert 'agent_collection_mode["aiortc.rtcrtpsender"] = "pyz+py"' in screen_block
    assert 'collect_dynamic_libs("google_crc32c")' in screen_block


def test_screen_view_follows_the_build_environment() -> None:
    assert 'SCREEN_VIEW = all(importlib.util.find_spec(name) is not None for name in ("aiortc", "av"))' in SPEC
    # the worker modules that import aiortc are only named when it is there; the dispatch is always in
    always = spec_list("agent_hidden")
    assert "dome_agent.screen.worker.main" in always and "win32job" in always
    screen_block = SPEC.split("if SCREEN_VIEW:", 1)[1].split("\nagent = Analysis(", 1)[0]
    assert (
        "dome_agent.screen.worker.rtc" in screen_block
        and "dome_agent.screen.worker.rtc" not in SPEC.split("if SCREEN_VIEW:", 1)[0]
    )


def test_the_spec_bundles_windows_captures_native_module_alone() -> None:
    """The Windows.Graphics.Capture fallback: only windows-capture's .pyd, where capture_wgc.load_native looks for it;
    its Python wrapper (it imports cv2 and numpy) is never named for analysis, and cv2/numpy are excluded outright."""
    from dome_agent.screen.worker import capture_wgc

    screen_block = SPEC.split("if SCREEN_VIEW:", 1)[1].split("\nagent = Analysis(", 1)[0]
    assert 'wgc_package = importlib.util.find_spec("windows_capture")' in screen_block
    assert 'glob("windows_capture*.pyd")' in screen_block
    assert 'agent_binaries += [(str(path), "windows_capture") for path in wgc_native]' in screen_block
    assert 'if sys.platform == "win32" and not wgc_native:' in screen_block  # a Windows build without it stops
    assert "windows_capture" not in spec_list("agent_hidden")
    assert {"cv2", "numpy"} <= set(ast.literal_eval(analysis_kwargs("agent_entry.py")["excludes"]))
    # the folder the spec writes is the one the loader and the folder check expect
    assert capture_wgc.NATIVE_MODULE == "windows_capture.windows_capture"
    assert bi.WGC_NATIVE.as_posix() == "_internal/windows_capture/windows_capture.pyd"


def test_windows_capture_is_pinned_for_windows_and_opencv_and_numpy_never_resolve() -> None:
    """pyproject.toml pins windows-capture 2.0.1 on Windows in the screen extra and excludes OpenCV and NumPy (its
    wrapper's needs; OpenCV carries its own FFmpeg); both lock files (pc-agent and the integration tests, which
    install pc-agent's screen extra) agree and pin the wheel whose native module DoMe's code was checked against."""
    project = tomllib.loads((PC_AGENT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "windows-capture==2.0.1; sys_platform == 'win32'" in project["optional-dependencies"]["screen"]
    assert not [d for d in project["dependencies"] if d.startswith("windows-capture")]
    for root in (PC_AGENT, PC_AGENT.parent / "tests"):
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        assert sorted(pyproject["tool"]["uv"]["exclude-dependencies"]) == ["numpy", "opencv-python"], root
        lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
        assert sorted(lock["manifest"]["excludes"]) == ["numpy", "opencv-python"], root
        names = {package["name"] for package in lock["package"]}
        assert not names & {"numpy", "opencv-python", "opencv-python-headless"}, root
        [locked] = [package for package in lock["package"] if package["name"] == "windows-capture"]
        assert locked["version"] == "2.0.1" and "dependencies" not in locked
        assert locked["wheels"] == [
            {
                "url": "https://files.pythonhosted.org/packages/6c/02/cdb3c75e60863e94fbd53c2b3607e61af5d464a4f9301e45897"
                "fcb08dbfa/windows_capture-2.0.1-cp39-abi3-win_amd64.whl",
                "hash": "sha256:209c2318e2168802b9afd1005e99499639e8835e71a1e188a3ddc77c7ccc3f04",
                "size": 229121,
                "upload-time": locked["wheels"][0]["upload-time"],
            }
        ]


def test_every_dome_agent_module_the_spec_names_exists() -> None:
    names = [n for n in spec_list("agent_hidden") if n.startswith("dome_agent.")]
    names += re.findall(r'"(dome_agent\.screen\.worker\.\w+)"', SPEC)
    assert names
    for name in names:
        path = PC_AGENT.joinpath(*name.split("."))
        assert path.with_suffix(".py").is_file() or (path / "__init__.py").is_file(), name


# ----- build_installer.py: the folder checks --------------------------------------------------------------


AGENT_MODULES = {"dome_agent", "dome_agent.screen.worker.main", "dome_agent.screen.worker.pipe", "pystray_stub"}
SCREEN_MODULES = {"aiortc", "aiortc.rtcrtpsender", "dome_agent.screen.worker.rtc", "dome_agent.screen.worker.track"}


def test_frozen_modules_without_and_with_screen_view() -> None:
    host = {"dome_agent", "dome_agent.bridge.host"}
    assert bi.check_frozen_modules(AGENT_MODULES, host, screen=False) == []
    assert bi.check_frozen_modules(AGENT_MODULES | SCREEN_MODULES, host, screen=True) == []


@pytest.mark.parametrize(
    ("agent", "host", "screen", "problem"),
    [
        (AGENT_MODULES | SCREEN_MODULES | {"aiortc.contrib.media"}, set(), True, "must not carry aiortc.contrib"),
        (AGENT_MODULES | {"dome_agent.testing.fake_platform"}, set(), False, "must not carry dome_agent.testing"),
        (AGENT_MODULES - {"dome_agent.screen.worker.main"}, set(), False, "screen-worker dispatch"),
        (AGENT_MODULES | {"aiortc"}, set(), False, "left out but DoMe carries aiortc"),
        (AGENT_MODULES | {"aiortc"}, set(), True, "lacks aiortc.rtcrtpsender"),
        (AGENT_MODULES, {"av.codec"}, False, "browser helper must not carry av"),
        (AGENT_MODULES, {"dome_agent.screen.supervisor"}, False, "browser helper must not carry dome_agent.screen"),
    ],
)
def test_frozen_module_problems(agent: set[str], host: set[str], screen: bool, problem: str) -> None:
    problems = bi.check_frozen_modules(agent, host, screen=screen)
    assert any(problem in p for p in problems), problems


def fake_folder(root: Path, *, screen: bool, notices: bool = True, pystray: bool = True) -> Path:
    """A dist/DoMe folder with just what check_folder looks at."""
    app = root / "DoMe"
    contract = app / "_internal" / "dome_protocol" / "_contract"
    (contract / "schemas").mkdir(parents=True)
    for name in ("actions.json", "errors.json", "plans.json", "version.json"):
        (contract / name).write_text("{}", encoding="utf-8")
    (app / "_internal" / "dome_agent" / "assets").mkdir(parents=True)
    (app / "_internal" / "dome_agent" / "assets" / "tray-256.png").write_bytes(b"png")
    for program in ("DoMe", "dome-native-host"):
        (app / bi.exe_name(program)).write_bytes(b"MZ")
    if pystray:
        (app / "_internal" / "pystray").mkdir()
        (app / "_internal" / "pystray" / "__init__.py").write_text("", encoding="utf-8")
    if screen:
        (app / "_internal" / "av").mkdir()
        (app / "_internal" / "aiortc").mkdir()
        (app / "_internal" / "aiortc" / "rtcrtpsender.py").write_text("", encoding="utf-8")
        (app / bi.WGC_NATIVE).parent.mkdir()
        (app / bi.WGC_NATIVE).write_bytes(b"MZ")  # a Windows build's; the Linux check does not ask for it
    if notices:
        gate = bi.licence_gate()
        (app / gate.NOTICES_NAME).write_text("notices", encoding="utf-8")
        (app / "licenses").mkdir()
        for name in gate.LICENCE_FILES:
            (app / "licenses" / name).write_text("text", encoding="utf-8")
    return app


@pytest.fixture
def archives(monkeypatch: pytest.MonkeyPatch) -> dict[str, set[str] | None]:
    """What the fake archive reader returns per program (None: PyInstaller is not installed)."""
    contents: dict[str, set[str] | None] = {"DoMe": set(AGENT_MODULES), "dome-native-host": {"dome_agent"}}
    monkeypatch.setattr(bi, "frozen_modules", lambda program: contents[Path(program).stem])
    return contents


def test_a_complete_folder_passes(tmp_path: Path, archives: dict[str, Any]) -> None:
    assert bi.check_folder(fake_folder(tmp_path, screen=False)) == []
    archives["DoMe"] = AGENT_MODULES | SCREEN_MODULES
    assert bi.check_folder(fake_folder(tmp_path / "s", screen=True)) == []


def test_missing_notices_and_pystray_sources_are_problems(tmp_path: Path, archives: dict[str, Any]) -> None:
    problems = bi.check_folder(fake_folder(tmp_path, screen=False, notices=False, pystray=False))
    assert any(p.endswith("THIRD_PARTY_NOTICES.txt") for p in problems)
    assert any("LGPL-2.1.txt" in p for p in problems)
    assert any("pystray (LGPL-3.0)" in p for p in problems)


def test_screen_view_needs_the_pli_hook_source_and_its_modules(tmp_path: Path, archives: dict[str, Any]) -> None:
    app = fake_folder(tmp_path, screen=True)
    (app / "_internal" / "aiortc" / "rtcrtpsender.py").unlink()
    problems = bi.check_folder(app)
    assert any("rtcrtpsender.py (the PLI hook check) is missing" in p for p in problems)
    assert any("lacks aiortc.rtcrtpsender" in p for p in problems)  # the fake archive has no aiortc either


def test_a_windows_screen_build_needs_the_wgc_native_module_and_never_opencv(
    tmp_path: Path, archives: dict[str, Any]
) -> None:
    archives["DoMe"] = AGENT_MODULES | SCREEN_MODULES
    app = fake_folder(tmp_path, screen=True)
    assert bi.check_folder(app, windows=True) == []
    (app / bi.WGC_NATIVE).unlink()
    assert bi.check_folder(app, windows=False) == []  # a Linux --pyinstaller-only check cannot have it
    [problem] = bi.check_folder(app, windows=True)
    assert "_internal/windows_capture/windows_capture.pyd (the Windows.Graphics.Capture fallback) is missing" in problem
    for name in ("cv2", "numpy"):
        (app / "_internal" / name).mkdir()
        assert any(f"_internal/{name} must not be shipped" in p for p in bi.check_folder(app, windows=False))
    archives["DoMe"] = set(AGENT_MODULES)
    assert bi.check_folder(fake_folder(tmp_path / "plain", screen=False), windows=True) == []  # no Screen view


def test_without_pyinstaller_the_archives_are_not_read(
    tmp_path: Path, archives: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    archives["DoMe"] = None
    archives["dome-native-host"] = None
    assert bi.check_folder(fake_folder(tmp_path, screen=False)) == []
    assert "archives were checked when the folder was built" in capsys.readouterr().out


def test_the_gate_module_is_loaded_once_by_path() -> None:
    gate = bi.licence_gate()
    assert gate is bi.licence_gate() and gate.__file__ == str(bi.LICENCE_GATE)
    assert gate.NOTICES_NAME == "THIRD_PARTY_NOTICES.txt" and "LGPL-2.1.txt" in gate.LICENCE_FILES


# ----- BUILD-INFO.txt and the release notes say whether Screen view is in ----------------------------------


def test_screen_view_note_is_honest(tmp_path: Path) -> None:
    app = tmp_path / "DoMe"
    (app / "_internal").mkdir(parents=True)
    assert bi.screen_view_note(app) == "not included in this build"
    (app / "_internal" / "av").mkdir()
    assert bi.screen_view_note(app) == "included (beta, not yet verified on a device)"
    info = bi.build_info(
        setup=tmp_path / "DoMe-Setup-0.2.0.exe",
        version="0.2.0",
        build_tag="0.2.0+abcdef0",
        build_number="7",
        commit="abcdef0",
        built_from="",
        source="source: push\n",
        digest="0" * 64,
        kind="development build",
        signed_note="NOT signed",
        addon_note="development build",
        screen_note=bi.screen_view_note(app),
    )
    assert "browser add-on: development build\nscreen view: included (beta, not yet verified on a device)\n" in info
    notes = bi.write_release_notes(
        tmp_path,
        version="0.2.0",
        setup=tmp_path / "DoMe-Setup-0.2.0.exe",
        digest="0" * 64,
        size=1,
        publisher="Jane Example",
        browser_addon=False,
        repo="Heshh999/DoMe",
        screen_note="not included in this build",
    ).read_text(encoding="utf-8")
    assert "- Screen view (a live picture of the PC on the phone): not included in this build.\n" in notes


# ----- the installer --------------------------------------------------------------------------------------


def test_the_installer_shows_the_licence_terms_and_installs_the_notices() -> None:
    iss = bi.ISS.read_text(encoding="ascii")
    setup = iss.split("[Setup]", 1)[1].split("\n[", 1)[0]
    assert "LicenseFile=licence-terms.txt" in setup
    files = iss.split("[Files]", 1)[1].split("\n[", 1)[0]
    assert 'Source: "{#AppFiles}\\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"' in files
    assert 'Source: "{#AppFiles}\\licenses\\*.txt"; DestDir: "{app}\\licenses"' in files
    install_delete = iss.split("[InstallDelete]", 1)[1].split("\n[", 1)[0]
    assert 'Name: "{app}\\licenses"' in install_delete


def test_the_licence_terms_permit_what_the_lgpl_asks() -> None:
    terms = (bi.ISS.parent / "licence-terms.txt").read_text(encoding="utf-8")
    assert terms.isascii()
    flat = " ".join(terms.split())
    assert "you may modify DoMe for your own use and reverse engineer it to debug such modifications" in flat
    assert "Nothing in these terms forbids that." in flat
    assert "THIRD_PARTY_NOTICES.txt" in flat and "These terms do not limit the rights those licences give you." in flat
    assert "DRAFT" in terms and "[FOUNDER:" in terms


def test_the_smoke_test_checks_the_installed_notices() -> None:
    source = (PACKAGING / "installer" / "smoke_test.py").read_text(encoding="utf-8")
    block = source.split("def check_installed", 1)[1].split("r.check(", 1)[0]
    for rel in ('"THIRD_PARTY_NOTICES.txt"', r'r"licenses\LGPL-2.1.txt"', r'r"_internal\pystray\__init__.py"'):
        assert rel in block, rel
    assert smoke.phase_plan(False)[0] == "preflight"

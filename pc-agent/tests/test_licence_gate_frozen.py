"""The frozen-app half of pc-agent/packaging/licence_gate.py (Screen view WP1.6, design §5.5, §15, §17.2), on
Linux: ``check --frozen`` against hand-built ``dist/DoMe`` reports (a planted libx264 by name, import and export,
an FFmpeg DLL outside av.libs, an unknown runtime DLL as a warning and, with ``--strict``, a failure, Screen view
present or absent, missing notices), ``shipping-av`` against pyproject/lock pairs, the frozen worker's
``--self-test`` report (with a fake ``DoMe.exe``), and ``bundle-notices`` against the packages installed here.
Nothing here runs PyInstaller or a Windows program; pefile is not installed on Linux, so a real folder scan
reads no PE tables (R9 fails closed) and the rules are tested on reports built by hand."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from .test_licence_gate import GATE_PATH, HASH, gate

PACKAGING = GATE_PATH.parent
FFMPEG = ["avcodec-62", "avdevice-62", "avfilter-11", "avformat-62", "avutil-60", "swresample-6", "swscale-9"]
LGPL_WHEEL = (
    "https://github.com/Heshh999/DoMe/releases/download/av-lgpl-17.1.0/"
    "av-17.1.0%2Blgpl.ffmpeg8.1.3-cp312-cp312-win_amd64.whl"
)
SHA = "sha256:" + "ab" * 32
# windows-capture 2.0.1's native module (Screen view, the Windows.Graphics.Capture fallback): its import table as
# pefile reads it from windows_capture-2.0.1-cp39-abi3-win_amd64.whl (abi3: python3.dll; Rust: VCRUNTIME140.dll)
WGC_PYD = "_internal/windows_capture/windows_capture.pyd"
WGC_IMPORTS = [
    "python3.dll",
    "kernel32.dll",
    "user32.dll",
    "d3d11.dll",
    "dwmapi.dll",
    "coremessaging.dll",
    "api-ms-win-core-synch-l1-2-0.dll",
    "api-ms-win-core-winrt-l1-1-0.dll",
    "combase.dll",
    "ole32.dll",
    "oleaut32.dll",
    "bcryptprimitives.dll",
    "KERNEL32.dll",
    "ntdll.dll",
    "VCRUNTIME140.dll",
    "api-ms-win-crt-string-l1-1-0.dll",
    "api-ms-win-crt-runtime-l1-1-0.dll",
    "api-ms-win-crt-heap-l1-1-0.dll",
]


def pe(
    path: str, imports: list[str] | None = None, exports: list[str] | None = None, parsed: bool = True
) -> dict[str, Any]:
    return {
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "imports": imports or ["KERNEL32.dll"],
        "exports": exports or [],
        "pe_parsed": parsed,
    }


def frozen_report(*, screen: bool) -> dict[str, Any]:
    """A folder report as ``collect --folder dist/DoMe`` writes it for a clean build."""
    dlls = [
        pe("_internal/python312.dll"),
        pe("_internal/python3.dll"),
        pe("_internal/VCRUNTIME140.dll"),
        pe("_internal/VCRUNTIME140_1.dll"),
        pe("_internal/libcrypto-3.dll"),
        pe("_internal/libssl-3.dll"),
        pe("_internal/libffi-8.dll"),
        pe("_internal/sqlite3.dll"),
        pe("_internal/tcl86t.dll"),
        pe("_internal/tk86t.dll"),
        pe("_internal/pywin32_system32/pywintypes312.dll"),
        pe("_internal/pywin32_system32/pythoncom312.dll"),
    ]
    if screen:
        dlls += [pe(f"_internal/av.libs/{n}-{HASH}.dll") for n in FFMPEG]
        dlls += [
            pe(f"_internal/av.libs/libvpx-1-{HASH}.dll"),
            pe(f"_internal/av.libs/libopus-0-{HASH}.dll"),
            pe(f"_internal/av.libs/libgcc_s_seh-1-{HASH}.dll"),
            pe(f"_internal/av.libs/libwinpthread-1-{HASH}.dll"),
            pe("_internal/google_crc32c/extra-dll/crc32c.dll"),
        ]
    binaries = [pe("DoMe.exe"), pe("dome-native-host.exe"), pe("_internal/psutil/_psutil_windows.pyd")]
    if screen:
        binaries += [pe("_internal/av/codec/codec.cp312-win_amd64.pyd"), pe("_internal/pylibsrtp/_binding.pyd")]
        binaries.append(pe(WGC_PYD, imports=WGC_IMPORTS, exports=["PyInit_windows_capture"]))
    return {
        "schema": 1,
        "source": "folder",
        "dlls": dlls,
        "binaries": binaries,
        "denied_names": [],
        "screen_dirs": {"av": screen, "av.libs": screen},
        "notices": {"present": True, "not_found": []},
        "licence_files": sorted(gate.LICENCE_FILES),
    }


def rules(violations: list[Any]) -> set[str]:
    return {v.rule.split()[0] for v in violations}


# --- check --frozen ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("screen", [True, False])
def test_a_clean_frozen_folder_passes_without_warnings(screen: bool) -> None:
    mode = "present" if screen else "absent"
    assert gate.evaluate_frozen(frozen_report(screen=screen), screen=mode) == ([], [])
    assert gate.evaluate_frozen(frozen_report(screen=screen), screen=mode, strict=True) == ([], [])


@pytest.mark.parametrize("where", ["_internal/libx264-165.dll", f"_internal/av.libs/libx264-165-{HASH}.dll"])
def test_a_planted_libx264_anywhere_fails(where: str) -> None:
    report = frozen_report(screen=True)
    report["dlls"].append(pe(where))
    fail, _warn = gate.evaluate_frozen(report, screen="present")
    assert "R1" in rules(fail)
    if "av.libs" in where:
        assert "F1" in rules(fail)  # and it is not on the FFmpeg allow-list either


def test_gpl_codec_found_through_imports_exports_and_names_of_any_file() -> None:
    report = frozen_report(screen=True)
    report["binaries"].append(pe("_internal/av/codec/context.pyd", imports=["libx264-165.dll"]))
    report["dlls"].append(pe(f"_internal/av.libs/avcodec-62-{HASH}.dll", exports=["x264_encoder_open"]))
    report["denied_names"] = ["_internal/av.libs/libx265-216.so", "_internal/av.libs/avcodec-62-x.dll"]
    fail, _warn = gate.evaluate_frozen(report, screen="present")
    assert {"R1", "R3", "R4"} <= rules(fail)
    named = [v.subject for v in fail if v.rule.startswith("R1")]
    assert "_internal/av.libs/libx265-216.so" in named  # a non-PE file is caught by its name alone


def test_an_unreadable_pe_file_fails_closed() -> None:
    report = frozen_report(screen=False)
    report["binaries"].append(pe("_internal/foo.pyd", parsed=False))
    fail, _warn = gate.evaluate_frozen(report, screen="absent")
    assert rules(fail) == {"R9"}


def test_ffmpeg_outside_av_libs_fails() -> None:
    report = frozen_report(screen=True)
    report["dlls"].append(pe("_internal/avcodec-62.dll"))
    fail, _warn = gate.evaluate_frozen(report, screen="present")
    assert rules(fail) == {"F1"} and "outside _internal/av.libs" in fail[0].detail


def test_opencvs_own_ffmpeg_fails_without_strict() -> None:
    """windows-capture's Python wrapper needs OpenCV, whose wheel carries a whole FFmpeg build
    (cv2/opencv_videoio_ffmpeg*.dll): pyproject.toml excludes OpenCV; should it come back, F1 refuses the build
    whatever --strict says (an unknown DLL alone would only warn)."""
    report = frozen_report(screen=True)
    report["dlls"].append(pe("_internal/cv2/opencv_videoio_ffmpeg4120_64.dll"))
    fail, warn = gate.evaluate_frozen(report, screen="present")
    assert rules(fail) == {"F1"} and warn == []


def test_windows_captures_native_module_passes_strict() -> None:
    """The .pyd is not a DLL (no F2 entry needed); R1/R3/R4/R9 apply and pass on its real import table."""
    report = frozen_report(screen=True)
    assert WGC_PYD in [b["path"] for b in report["binaries"]]
    assert gate.evaluate_frozen(report, screen="present", strict=True) == ([], [])
    for dll in ("python3.dll", "VCRUNTIME140.dll"):  # the two it needs from the folder are already covered (F2)
        assert gate._frozen_dll_covered(dll) is not None  # noqa: SLF001


def test_an_unknown_runtime_dll_warns_and_fails_only_when_strict() -> None:
    report = frozen_report(screen=False)
    report["dlls"].append(pe("_internal/mystery-1.dll"))
    fail, warn = gate.evaluate_frozen(report, screen="absent")
    assert fail == [] and rules(warn) == {"F2"} and "THIRD_PARTY_NOTICES.txt" in warn[0].detail
    fail, warn = gate.evaluate_frozen(report, screen="absent", strict=True)
    assert rules(fail) == {"F2"} and warn == []


@pytest.mark.parametrize(
    "name", ["ucrtbase.dll", "api-ms-win-crt-runtime-l1-1-0.dll", "API-MS-WIN-CORE-CONSOLE-L1-1-0.dll"]
)
def test_the_universal_c_runtime_is_named_as_such_and_fails_when_strict(name: str) -> None:
    """The first signed release run (2026-10-30) refused 43 of these: the spec now leaves them out, and the gate says
    why if one comes back."""
    report = frozen_report(screen=False)
    report["dlls"].append(pe(f"_internal/{name}"))
    fail, warn = gate.evaluate_frozen(report, screen="absent")
    assert fail == [] and rules(warn) == {"F2"} and "Universal C Runtime" in warn[0].detail
    fail, warn = gate.evaluate_frozen(report, screen="absent", strict=True)
    assert rules(fail) == {"F2"} and warn == [] and "without_ucrt" in fail[0].detail


def test_import_tables_naming_the_universal_c_runtime_are_fine() -> None:
    """Every PE file imports api-ms-win-* sets (Windows resolves them itself); only a bundled copy is flagged."""
    report = frozen_report(screen=True)
    fail, warn = gate.evaluate_frozen(report, screen="present", strict=True)
    assert fail == [] and warn == []
    assert any(imp.startswith("api-ms-win-crt-") for imp in WGC_IMPORTS)


def test_screen_view_must_match_what_was_built() -> None:
    fail, _warn = gate.evaluate_frozen(frozen_report(screen=True), screen="absent")
    assert rules(fail) == {"F3"}
    fail, _warn = gate.evaluate_frozen(frozen_report(screen=False), screen="present")
    assert rules(fail) == {"F3"}
    report = frozen_report(screen=True)
    report["dlls"] = [d for d in report["dlls"] if not d["name"].startswith("avfilter")]
    [missing] = gate.evaluate_frozen(report, screen="present")[0]
    assert missing.rule == "F3 screen" and "avfilter" in missing.detail
    with pytest.raises(ValueError):
        gate.evaluate_frozen(report, screen="maybe")


def test_notices_and_licence_texts_are_required() -> None:
    report = frozen_report(screen=False)
    report["notices"] = {"present": False, "not_found": []}
    report["licence_files"] = [n for n in gate.LICENCE_FILES if n != "LGPL-2.1.txt"]
    fail, _warn = gate.evaluate_frozen(report, screen="absent")
    assert [v.subject for v in fail] == ["THIRD_PARTY_NOTICES.txt", "licenses"]
    assert "LGPL-2.1.txt" in fail[1].detail
    report = frozen_report(screen=False)
    report["notices"]["not_found"] = ["[NOT FOUND: somepkg 1.0 ships no licence file; add its licence text]"]
    fail, warn = gate.evaluate_frozen(report, screen="absent")
    assert fail == [] and rules(warn) == {"F4"}
    assert rules(gate.evaluate_frozen(report, screen="absent", strict=True)[0]) == {"F4"}


def test_an_empty_or_foreign_report_fails_closed() -> None:
    fail, _warn = gate.evaluate_frozen({"schema": 1, "source": "wheel-env"}, screen="absent")
    assert "R0" in rules(fail)
    assert any(v.subject == "files" for v in fail)


def test_collect_folder_reports_paths_parts_and_notices(tmp_path: Path) -> None:
    (tmp_path / "_internal" / "av.libs").mkdir(parents=True)
    (tmp_path / "_internal" / "av").mkdir()
    (tmp_path / "_internal" / "av.libs" / f"avcodec-62-{HASH}.dll").write_bytes(b"MZ")
    (tmp_path / "_internal" / "av" / "codec.pyd").write_bytes(b"MZ")
    (tmp_path / "DoMe.exe").write_bytes(b"MZ")
    (tmp_path / "_internal" / "libx264.so.165").write_bytes(b"\x7fELF")
    (tmp_path / "licenses").mkdir()
    (tmp_path / "licenses" / "LGPL-2.1.txt").write_text("text", encoding="utf-8")
    (tmp_path / "THIRD_PARTY_NOTICES.txt").write_text("Part 1\n[NOT FOUND: x 1 ships no licence file]\n", "utf-8")
    report = gate.collect_folder(tmp_path)
    assert [d["path"] for d in report["dlls"]] == [f"_internal/av.libs/avcodec-62-{HASH}.dll"]
    assert [b["path"] for b in report["binaries"]] == ["DoMe.exe", "_internal/av/codec.pyd"]
    assert report["denied_names"] == ["_internal/libx264.so.165"]
    assert report["screen_dirs"] == {"av": True, "av.libs": True}
    assert report["notices"] == {"present": True, "not_found": ["[NOT FOUND: x 1 ships no licence file]"]}
    assert report["licence_files"] == ["LGPL-2.1.txt"]
    fail, _warn = gate.evaluate_frozen(report, screen="present")
    assert {"R1", "R9", "F3", "F4"} <= rules(fail)  # no pefile here: every PE file fails closed


def run_gate(*argv: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(GATE_PATH), *argv], capture_output=True, text=True, check=False, env=env
    )


def test_check_frozen_cli(tmp_path: Path) -> None:
    report = frozen_report(screen=False)
    report["dlls"].append(pe("_internal/mystery-1.dll"))
    path = tmp_path / "frozen.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    done = run_gate("check", str(path), "--frozen", "--screen", "absent")
    assert done.returncode == 0, done.stdout
    assert "WARN F2 runtime-dlls: _internal/mystery-1.dll" in done.stdout
    assert "Screen view absent): passed (0 violation(s), 1 warning(s))" in done.stdout
    strict = run_gate("check", str(path), "--frozen", "--screen", "absent", "--strict")
    assert strict.returncode == 1 and "FAIL F2 runtime-dlls" in strict.stdout
    assert run_gate("check", str(path), "--frozen").returncode == 2  # --screen is required
    # on the runner the lines become annotations
    env = {**os.environ, "GITHUB_ACTIONS": "true"}
    annotated = run_gate("check", str(path), "--frozen", "--screen", "absent", env=env)
    assert "::warning title=licence gate::WARN F2 runtime-dlls" in annotated.stdout


# --- shipping-av -------------------------------------------------------------------------------------------


def pyproject(av: str | None) -> dict[str, Any]:
    sources = 'dome-protocol = { path = "../shared/python", editable = true }\n' + (f"av = {av}\n" if av else "")
    return tomllib.loads(f'[project]\nname = "dome-agent"\n\n[tool.uv.sources]\n{sources}')


def lock(url: str = LGPL_WHEEL, version: str = "17.1.0+lgpl.ffmpeg8.1.3", hash_: str = SHA) -> dict[str, Any]:
    return tomllib.loads(
        f'version = 1\n\n[[package]]\nname = "av"\nversion = "17.1.0"\nsource = {{ registry = "https://pypi.org/simple" }}\n\n'
        f'[[package]]\nname = "av"\nversion = "{version}"\nsource = {{ url = "{url}" }}\n'
        f'wheels = [\n    {{ url = "{url}", hash = "{hash_}" }},\n]\n'
    )


GOOD_ENTRY = f'[{{ url = "{LGPL_WHEEL}", marker = "sys_platform == \'win32\'" }}]'


def test_shipping_av_absent_without_a_source_entry() -> None:
    screen, why = gate.shipping_av(pyproject(None), lock())
    assert screen == "absent" and "stock PyPI wheel" in why


@pytest.mark.parametrize(
    "entry",
    [
        GOOD_ENTRY,
        f'{{ url = "{LGPL_WHEEL}", marker = "sys_platform==\\"win32\\"" }}',
    ],
)
def test_shipping_av_present_for_a_locked_hash_pinned_lgpl_release_asset(entry: str) -> None:
    assert gate.shipping_av(pyproject(entry), lock()) == ("present", LGPL_WHEEL)


@pytest.mark.parametrize(
    ("entry", "lock_kwargs", "message"),
    [
        (f'[{{ url = "{LGPL_WHEEL}" }}]', {}, "exactly the keys url and marker"),
        (f'[{{ url = "{LGPL_WHEEL}", marker = "sys_platform != \'win32\'" }}]', {}, "must be exactly"),
        (f'[{{ url = "{LGPL_WHEEL}", marker = "python_version >= \'3.12\'" }}]', {}, "must be exactly"),
        (f'[{{ url = "{LGPL_WHEEL}", marker = "sys_platform == \'win32\'", index = "x" }}]', {}, "exactly the keys"),
        (GOOD_ENTRY[:-1] + f', {{ url = "{LGPL_WHEEL}", marker = "sys_platform == \'darwin\'" }}]', {}, "exactly one"),
        (
            '[{ url = "http://github.com/a/b/releases/download/t/av-17-cp312-cp312-win_amd64.whl", '
            "marker = \"sys_platform == 'win32'\" }]",
            {},
            "not an https GitHub release asset",
        ),
        (
            '[{ url = "https://example.com/av-17.1.0-cp312-cp312-win_amd64.whl", '
            "marker = \"sys_platform == 'win32'\" }]",
            {},
            "not an https GitHub release asset",
        ),
        (GOOD_ENTRY.replace("win_amd64", "manylinux_2_28_x86_64"), {}, "not an https GitHub release asset"),
        (GOOD_ENTRY, {"url": LGPL_WHEEL.replace("17.1.0", "17.0.0")}, "run `uv lock`"),
        (GOOD_ENTRY, {"version": "17.1.0"}, "not DoMe's +lgpl build"),
        (GOOD_ENTRY, {"hash_": "md5:abc"}, "SHA-256"),
    ],
)
def test_shipping_av_refuses_anything_in_between(entry: str, lock_kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(gate.GateError, match=re.escape(message)):
        gate.shipping_av(pyproject(entry), lock(**lock_kwargs))


def test_the_repository_itself_never_trips_shipping_av() -> None:
    """Today pyproject.toml has no av source (Screen view is left out of Windows builds); once WP0.1 pins the LGPL
    wheel it must say present. Either way it must not refuse the repository's own files."""
    root = PACKAGING.parent
    screen, _detail = gate.shipping_av(
        tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")),
        tomllib.loads((root / "uv.lock").read_text(encoding="utf-8")),
    )
    assert screen in ("present", "absent")


def test_shipping_av_cli_writes_the_github_output(tmp_path: Path) -> None:
    project, locked, output = tmp_path / "pyproject.toml", tmp_path / "uv.lock", tmp_path / "out"
    project.write_text(f'[project]\nname = "x"\n\n[tool.uv.sources]\nav = {GOOD_ENTRY}\n', encoding="utf-8")
    locked.write_text(
        f'version = 1\n\n[[package]]\nname = "av"\nversion = "17.1.0+lgpl.ffmpeg8.1.3"\nsource = {{ url = "{LGPL_WHEEL}" }}\n'
        f'wheels = [{{ url = "{LGPL_WHEEL}", hash = "{SHA}" }}]\n',
        encoding="utf-8",
    )
    done = run_gate("shipping-av", "--pyproject", str(project), "--lock", str(locked), "--github-output", str(output))
    assert done.returncode == 0 and done.stdout.startswith("screen=present\n")
    assert output.read_text(encoding="utf-8") == "screen=present\n"
    project.write_text('[project]\nname = "x"\n\n[tool.uv.sources]\nav = { url = "https://x" }\n', encoding="utf-8")
    refused = run_gate(
        "shipping-av", "--pyproject", str(project), "--lock", str(locked), "--github-output", str(output)
    )
    assert refused.returncode == 1 and "shipping-av: FAILED" in refused.stdout
    assert output.read_text(encoding="utf-8") == "screen=present\n"  # nothing appended on a refusal


# --- screen-self-test --------------------------------------------------------------------------------------


def self_test_report(**overrides: Any) -> dict[str, Any]:
    """What ``DoMe.exe screen-worker --self-test`` prints for a build with the LGPL wheel on a GPU-less runner."""
    checks: dict[str, Any] = {
        "imports": {"ok": True, "value": {"aiortc": "1.15.0", "av": "17.1.0+lgpl.ffmpeg8.1.3"}},
        "encoders": {
            "ok": True,
            "value": [
                {"encoder": "h264_mf hw", "kind": "h264", "available": False, "note": "SCREEN_ENCODER_UNAVAILABLE"},
                {"encoder": "h264_mf", "kind": "h264", "available": False, "note": "SCREEN_ENCODER_UNAVAILABLE"},
                {"encoder": "vp8 (aiortc libvpx)", "kind": "vp8", "available": True, "note": ""},
            ],
        },
        "decoders": {"ok": True, "value": {"count": 1, "exactly_wrapped_avframe": True, "sample": ["wrapped_avframe"]}},
        "lavfi_testsrc_frame": {"ok": True, "value": {"width": 64, "height": 64, "format": "yuv420p"}},
        "pli_hook": {"ok": True, "value": True},
        "wgc_native": {
            "ok": True,
            "value": {"classes": ["NativeCaptureControl", "NativeMappedFrame", "NativeWindowsCapture"]},
        },
        "ddagrab_graph_options": {"ok": True, "value": {"filters": ["ddagrab", "scale_d3d11", "hwdownload", "format"]}},
    }
    checks.update(overrides)
    return {"ok": all(c.get("ok") for c in checks.values()), "checks": checks}


ABSENT = {
    "ok": False,
    "checks": {
        name: {"ok": False, "error": f"ModuleNotFoundError: No module named '{module}'"}
        for name, module in (
            ("imports", "aiortc"),
            ("encoders", "av"),
            ("decoders", "av"),
            ("lavfi_testsrc_frame", "av"),
            ("pli_hook", "aiortc"),
        )
    },
}


def failed(rows: list[Any]) -> set[str]:
    return {r.name for r in rows if r.required and not r.ok}


def test_self_test_with_screen_view_passes_and_h264_is_only_reported() -> None:
    rows = gate.evaluate_screen_self_test(self_test_report(), 0, expect="present", crash_log=False)
    assert failed(rows) == set()
    [h264] = [r for r in rows if not r.required]
    assert h264.status == "REPORTED (failed)" and "h264_mf hw: SCREEN_ENCODER_UNAVAILABLE" in h264.detail


def test_self_test_refuses_the_stock_pypi_wheel_and_a_missing_pli_hook() -> None:
    stock = self_test_report(
        imports={"ok": True, "value": {"aiortc": "1.15.0", "av": "17.1.0"}},
        decoders={"ok": True, "value": {"count": 539, "exactly_wrapped_avframe": False, "sample": ["012v"]}},
        pli_hook={"ok": True, "value": False},
    )
    rows = gate.evaluate_screen_self_test(stock, 0, expect="present", crash_log=False)
    assert failed(rows) == {
        "av is DoMe's LGPL build (+lgpl)",
        "decoders == {wrapped_avframe}",
        "PLI hook point (RTCRtpSender._send_keyframe and its PLI dispatch)",
    }


def test_self_test_refuses_a_wrong_aiortc_no_vp8_and_a_failed_check() -> None:
    report = self_test_report(
        imports={"ok": True, "value": {"aiortc": "1.14.0", "av": "17.1.0+lgpl.ffmpeg8.1.3"}},
        encoders={"ok": True, "value": [{"encoder": "vp8 (aiortc libvpx)", "kind": "vp8", "available": False}]},
        lavfi_testsrc_frame={"ok": False, "error": "RuntimeError: lavfi testsrc delivered no frame"},
    )
    rows = gate.evaluate_screen_self_test(report, 1, expect="present", crash_log=False)
    assert failed(rows) == {
        "aiortc 1.15.0 imports",
        "VP8 encoder available",
        "lavfi testsrc -> one decoded frame",
        "the worker's own verdict (exit code 0)",
    }


WGC_ROW = "Windows.Graphics.Capture fallback (windows-capture's native module loads)"


@pytest.mark.parametrize(
    "wgc_native",
    [
        None,  # the build left it out: the field result 2026-10-26 ("support is not installed")
        {"ok": False, "error": "ImportError: windows-capture's native module is not installed"},
        {"ok": True, "value": {"classes": ["NativeMappedFrame"]}},
    ],
)
def test_self_test_needs_the_windows_graphics_capture_fallback(wgc_native: dict[str, Any] | None) -> None:
    report = self_test_report()
    if wgc_native is None:
        del report["checks"]["wgc_native"]
    else:
        report["checks"]["wgc_native"] = wgc_native
    rows = gate.evaluate_screen_self_test(report, 0, expect="present", crash_log=False)
    assert WGC_ROW in failed(rows)


GRAPH_ROW = "ddagrab graph options parse (the worker's own Desktop Duplication graph)"


@pytest.mark.parametrize(
    "graph_check",
    [
        None,  # a worker that does not run the check
        {"ok": False, "error": "ValueError: scale_d3d11: No such option: w.; Option not found (errno 1414549496)"},
    ],
)
def test_self_test_needs_the_desktop_duplication_graph_to_parse(graph_check: dict[str, Any] | None) -> None:
    """Device round 2: an option name FFmpeg does not have made Desktop Duplication fail on every PC; the frozen
    worker's own graph is now parsed on the build machine (no desktop or GPU needed) and a failure stops the build."""
    report = self_test_report()
    if graph_check is None:
        del report["checks"]["ddagrab_graph_options"]
    else:
        report["checks"]["ddagrab_graph_options"] = graph_check
        report["ok"] = False
    rows = gate.evaluate_screen_self_test(report, 0 if graph_check is None else 1, expect="present", crash_log=False)
    assert GRAPH_ROW in failed(rows)
    [row] = [r for r in rows if r.name == GRAPH_ROW]
    assert row.status == "FAIL" and ("Option not found" in row.detail if graph_check else "not reported" in row.detail)


def test_self_test_shows_ffmpegs_reason_for_an_unavailable_encoder() -> None:
    report = self_test_report(
        encoders={
            "ok": True,
            "value": [
                {
                    "encoder": "h264_mf hw",
                    "kind": "h264",
                    "available": False,
                    "note": "SCREEN_ENCODER_UNAVAILABLE",
                    "reason": "open: Invalid argument (errno 22)",
                },
                {"encoder": "vp8 (aiortc libvpx)", "kind": "vp8", "available": True, "note": ""},
            ],
        }
    )
    [h264] = [r for r in gate.evaluate_screen_self_test(report, 0, expect="present", crash_log=False) if not r.required]
    assert "h264_mf hw: open: Invalid argument (errno 22)" in h264.detail


def test_self_test_without_screen_view() -> None:
    assert failed(gate.evaluate_screen_self_test(ABSENT, 1, expect="absent", crash_log=False)) == set()
    # a build that was meant to leave Screen view out but carries it
    rows = gate.evaluate_screen_self_test(self_test_report(), 0, expect="absent", crash_log=False)
    assert failed(rows) == {
        "Screen view is not in this build (aiortc and av cannot be imported)",
        "the worker says it is not ready (exit code 1)",
    }
    # and the other way round
    assert "aiortc 1.15.0 imports" in failed(
        gate.evaluate_screen_self_test(ABSENT, 1, expect="present", crash_log=False)
    )


def test_self_test_without_a_report_or_with_a_crash_log_fails() -> None:
    for expect in ("present", "absent"):
        rows = gate.evaluate_screen_self_test(None, 70, expect=expect, crash_log=True)
        assert failed(rows) == {"the worker answered with its JSON report", "no crash log"}
    with pytest.raises(ValueError):
        gate.evaluate_screen_self_test(ABSENT, 1, expect="maybe", crash_log=False)


def test_parse_self_test_takes_the_last_json_line() -> None:
    assert gate.parse_self_test('noise\n{"ok": false}\n{"ok": true, "checks": {}}\n') == {"ok": True, "checks": {}}
    assert gate.parse_self_test("Traceback ...\n") is None
    assert gate.parse_self_test("{not json\n") is None
    assert gate.parse_self_test("[1, 2]\n") is None


FAKE_EXE = """#!{python}
import json, os, pathlib, sys
assert sys.argv[1:] == ["screen-worker", "--self-test"], sys.argv
state = pathlib.Path(os.environ["DOME_AGENT_STATE_DIR"])
leaked = sorted(k for k in os.environ if k.upper().startswith("DOME_") and k != "DOME_AGENT_STATE_DIR")
if os.environ.get("FAKE_CRASH"):
    (state / "logs").mkdir(parents=True)
    (state / "logs" / "crash.log").write_text("ValueError: boom", encoding="utf-8")
print("a log line on stdout", flush=True)
print("worker log line", file=sys.stderr)
print(json.dumps({{"leaked": leaked, **json.loads(os.environ["FAKE_REPORT"])}}))
sys.exit(int(os.environ["FAKE_RC"]))
"""


def fake_exe(tmp_path: Path) -> Path:
    exe = tmp_path / "DoMe"
    exe.write_text(FAKE_EXE.format(python=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


@pytest.mark.skipif(sys.platform == "win32", reason="the fake DoMe.exe is a script with a #! line")
def test_screen_self_test_cli_runs_the_program_clean_and_keeps_its_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe, out = fake_exe(tmp_path), tmp_path / "logs" / "self-test.json"
    monkeypatch.setenv("FAKE_REPORT", json.dumps(ABSENT))
    monkeypatch.setenv("FAKE_RC", "1")
    monkeypatch.setenv("DOME_SCREEN_DEV_H264", "1")  # never reaches the frozen worker
    monkeypatch.setenv("DOME_AGENT_API_URL", "https://staging.example")
    argv = [sys.executable, "-I", str(GATE_PATH), "screen-self-test", "--exe", str(exe), "--expect", "absent"]
    done = subprocess.run([*argv, "--out", str(out)], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout
    assert "screen-worker self-test (Screen view absent): passed" in done.stdout
    kept = json.loads(out.read_text(encoding="utf-8"))
    assert kept["leaked"] == [] and kept["checks"]["imports"]["ok"] is False
    # a crash log in the throw-away state folder fails the run and is shown with the worker's stderr
    monkeypatch.setenv("FAKE_CRASH", "1")
    crashed = subprocess.run([*argv, "--out", str(out)], capture_output=True, text=True, check=False)
    assert crashed.returncode == 1 and re.search(r"^FAIL +no crash log", crashed.stdout, re.MULTILINE)
    assert "--- crash.log (last 40 lines) ---\nValueError: boom" in crashed.stdout
    assert "worker log line" in crashed.stdout


# --- bundle-notices ----------------------------------------------------------------------------------------


def meta(**fields: str | list[str]) -> Message:
    message = Message()
    for key, value in fields.items():
        for item in value if isinstance(value, list) else [value]:
            message[key.replace("_", "-")] = item
    return message


def test_distribution_licence_prefers_the_expression_then_a_short_field_then_classifiers() -> None:
    assert gate.distribution_licence(meta(License_Expression="BSD-3-Clause", License="BSD")) == "BSD-3-Clause"
    assert gate.distribution_licence(meta(License="MIT")) == "MIT"
    long_text = meta(
        License="Permission is hereby granted\nfree of charge",
        Classifier=[
            "Programming Language :: Python",
            "License :: OSI Approved :: MIT License",
        ],
    )
    assert gate.distribution_licence(long_text) == "MIT License"
    assert gate.distribution_licence(meta()).startswith("not declared")


def test_distribution_texts_reads_license_file_entries_then_falls_back_to_dist_info_names() -> None:
    files = {"licenses/LICENSE": "MIT text", "COPYING": "GPL text", "COPYING.LGPL": "LGPL text"}
    pep639 = SimpleNamespace(metadata=meta(License_File="LICENSE"), read_text=files.get, files=[])
    assert gate.distribution_texts(pep639) == (("LICENSE", "MIT text"),)
    old = SimpleNamespace(metadata=meta(License_File=["COPYING", "COPYING.LGPL"]), read_text=files.get, files=[])
    assert gate.distribution_texts(old) == (("COPYING", "GPL text"), ("COPYING.LGPL", "LGPL text"))
    listed = SimpleNamespace(
        metadata=meta(),
        read_text=files.get,
        files=["pkg/__init__.py", "pkg-1.0.dist-info/COPYING", "pkg-1.0.dist-info/RECORD"],
    )
    assert gate.distribution_texts(listed) == (("COPYING", "GPL text"),)


def test_installed_packages_here_have_their_texts_and_skip_build_tools() -> None:
    packages = {p.name.lower(): p for p in gate.installed_packages(PACKAGING / "licenses" / "python")}
    assert not {"pytest", "mypy", "ruff", "dome-agent", "dome-protocol", "pyinstaller"} & set(packages)
    assert not [name for name in packages if name.startswith("types-")]
    aiortc = packages["aiortc"]
    assert aiortc.version == "1.15.0" and aiortc.licence == "BSD-3-Clause" and aiortc.texts
    pystray = packages["pystray"]
    assert {name for name, _text in pystray.texts} == {"COPYING", "COPYING.LGPL"}
    # dnspython 2.9.0 ships no licence file: the text from its own repository fills the gap
    [(note, text)] = packages["dnspython"].texts
    assert "from the project's source repository" in note and "ISC License" in text
    assert gate.installed_packages()  # without fallbacks it still lists everything


def test_render_bundle_notices_lists_packages_and_marks_what_is_missing() -> None:
    packages = [
        gate.PackageNotice("aiortc", "1.15.0", "BSD-3-Clause", (("LICENSE", "Copyright aiortc"),)),
        gate.PackageNotice("nolicence", "0.1", "MIT", ()),
    ]
    text, missing = gate.render_bundle_notices("HEAD\nPART 2\n======\n", packages, ("LICENSE.txt", "PSF"), "3.12.13")
    assert text.startswith("HEAD\nPART 2\n======\n\nPython packages in this build")
    assert "  aiortc 1.15.0: BSD-3-Clause\n  nolicence 0.1: MIT\n" in text
    assert "== Python runtime: CPython 3.12.13 (LICENSE.txt) ==\nPSF\n" in text
    assert "== aiortc 1.15.0: BSD-3-Clause ==\n-- LICENSE --\nCopyright aiortc\n" in text
    assert missing == ["nolicence"] and gate.NOT_FOUND + ": nolicence 0.1 ships no licence file" in text
    _text, missing = gate.render_bundle_notices("H", [], ("LICENSE.txt", None), "3.12.13")
    assert missing == ["the Python runtime's licence"]


def test_bundle_notices_writes_the_file_and_the_licence_texts(tmp_path: Path) -> None:
    missing = gate.bundle_notices(PACKAGING / "THIRD_PARTY_NOTICES.txt", PACKAGING / "licenses", tmp_path)
    text = (tmp_path / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
    assert text.startswith((PACKAGING / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8").rstrip("\n"))
    assert "== aiortc 1.15.0: BSD-3-Clause ==" in text and "== Python runtime: CPython 3.12." in text
    assert sorted(p.name for p in (tmp_path / "licenses").iterdir()) == sorted(gate.LICENCE_FILES)
    assert missing == [] and gate.NOT_FOUND not in text
    report = gate.collect_folder(tmp_path)
    assert report["notices"] == {"present": True, "not_found": []}
    assert report["licence_files"] == sorted(gate.LICENCE_FILES)


# --- the shipped texts ---------------------------------------------------------------------------------------


CANONICAL = {
    # gnu.org lgpl-2.1.txt / gpl-3.0.txt and apache.org LICENSE-2.0.txt
    "LGPL-2.1.txt": "dc626520dcd53a22f727af3ee42c770e56c97a64fe3adb063799d8ab032fe551",
    "GPL-3.0.txt": "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986",
    "Apache-2.0.txt": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}


def test_the_licence_folder_holds_exactly_the_named_texts() -> None:
    folder = PACKAGING / "licenses"
    assert sorted(p.name for p in folder.glob("*.txt")) == sorted(gate.LICENCE_FILES)
    for name, digest in CANONICAL.items():
        data = (folder / name).read_bytes().replace(b"\r\n", b"\n")  # a Windows checkout may have CRLF
        assert hashlib.sha256(data).hexdigest() == digest, name
    assert "GCC RUNTIME LIBRARY EXCEPTION" in (folder / "GCC-RUNTIME-LIBRARY-EXCEPTION-3.1.txt").read_text("utf-8")
    assert "The WebM Project authors" in (folder / "libvpx-LICENSE.txt").read_text("utf-8")
    assert "Additional IP Rights Grant (Patents)" in (folder / "libvpx-PATENTS.txt").read_text("utf-8")
    assert "Xiph.Org" in (folder / "opus-COPYING.txt").read_text("utf-8")
    assert "mingw-w64 project" in (folder / "winpthreads-COPYING.txt").read_text("utf-8")


def test_the_notices_head_names_every_text_and_ends_with_part_2() -> None:
    head = (PACKAGING / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
    assert head.isascii()
    for name in gate.LICENCE_FILES:
        assert f"licenses\\{name}" in head, name
    assert head.endswith("\n\nPART 2\n======\n")
    for promise in (
        "You may modify DoMe for your own use and reverse engineer it to debug such modifications.",
        "Written offer: for at least three years",
        "_internal\\av.libs",
        "aiortc.contrib) are left out",
        "_internal\\pystray as plain .py files",
        "DRAFT",
    ):
        assert promise in " ".join(head.split()), promise
